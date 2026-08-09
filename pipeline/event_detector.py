"""Pure-math sports event heuristics; no neural model is loaded here."""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from config.settings import RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, read_json, report, stop_if_requested)
from utils.logger import setup_logger

LOG = setup_logger("events")


def _center(detection: dict[str, Any]) -> tuple[float, float]:
    x, y, width, height = detection["bbox"]
    return x + width / 2, y + height / 2


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax, ay, aw, ah = a["bbox"]; bx, by, bw, bh = b["bbox"]
    intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - intersection
    return intersection / max(union, 1e-6)


def _cluster(players: list[dict[str, Any]], threshold: float) -> tuple[int, tuple[float, float] | None]:
    """Return the largest connected center cluster and its centroid."""
    points = [_center(player) for player in players]
    if not points:
        return 0, None
    unvisited = set(range(len(points))); components: list[list[int]] = []
    while unvisited:
        seed = unvisited.pop(); component = [seed]; queue = [seed]
        while queue:
            current = queue.pop()
            neighbors = [index for index in list(unvisited)
                         if _distance(points[current], points[index]) <= threshold]
            for index in neighbors:
                unvisited.remove(index); component.append(index); queue.append(index)
        components.append(component)
    largest = max(components, key=len)
    centroid = (sum(points[i][0] for i in largest) / len(largest),
                sum(points[i][1] for i in largest) / len(largest))
    return len(largest), centroid


class EventDetector:
    """Detect conservative event candidates from sparse player/ball geometry."""

    COOLDOWNS = {
        "goal": 30.0, "shot_on_target": 8.0, "foul": 12.0,
        "corner_kick": 15.0, "counter_attack": 15.0,
        "high_pressure": 20.0, "general_excitement": 10.0,
    }

    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self.input_dir = TEMP_DIR / "tracks"
        self.output_path = TEMP_DIR / "events.json"
        self.history: deque[dict[str, Any]] = deque(maxlen=8)
        self.last_event: dict[str, float] = defaultdict(lambda: -1e9)
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _goal_zone(point: tuple[float, float] | None, width: float, height: float,
                   threshold: float) -> bool:
        if point is None:
            return False
        x, y = point
        # Wide broadcast shots place each goal near a lateral 15% boundary. The
        # literal lower-15% zone is also accepted for behind-goal camera angles.
        side_goal = (x <= width * threshold or x >= width * (1 - threshold)) and y >= height * 0.40
        lower_goal = y >= height * (1 - threshold) and (x <= width * 0.30 or x >= width * 0.70)
        return side_goal or lower_goal

    @staticmethod
    def _corner_zone(point: tuple[float, float] | None, width: float, height: float) -> bool:
        if point is None:
            return False
        x, y = point
        return (x <= width * .12 or x >= width * .88) and (y <= height * .18 or y >= height * .82)

    def _add(self, timestamp: float, event_type: str, confidence: float, description: str,
             signals: list[str]) -> None:
        if timestamp - self.last_event[event_type] < self.COOLDOWNS[event_type]:
            return
        if self.settings.key_events_only and event_type not in {"goal", "shot_on_target", "corner_kick"}:
            return
        # Frequency 1 is conservative and 10 includes more ambiguous events.
        minimum = 0.86 - 0.04 * max(1, min(10, self.settings.commentary_frequency))
        if event_type == "goal":
            minimum = min(minimum, 0.68)
        if confidence < minimum:
            return
        event = {
            "timestamp": round(timestamp, 2),
            "type": event_type,
            "confidence": round(min(0.99, confidence), 2),
            "description": description,
            "signals": signals,
        }
        self.events.append(event); self.last_event[event_type] = timestamp
        LOG.info("Event at %.1fs: %s (%.0f%%)", timestamp, event_type, confidence * 100)

    def _analyze(self, record: dict[str, Any]) -> None:
        timestamp = float(record.get("timestamp_sec", 0))
        width, height = [float(value or 1) for value in record.get("image_size", [1, 1])]
        detections = record.get("detections", [])
        players = [item for item in detections if item.get("class") == "person"]
        balls = sorted((item for item in detections if item.get("class") == "sports ball"),
                       key=lambda item: item.get("confidence", 0), reverse=True)
        ball = _center(balls[0]) if balls else None
        threshold = self.settings.cluster_distance * max(width / 1280.0, 0.5)
        cluster_size, cluster_center = _cluster(players, threshold)
        current = {
            "timestamp": timestamp, "width": width, "height": height,
            "players": players, "ball": ball, "cluster_size": cluster_size,
            "cluster_center": cluster_center,
        }
        previous = self.history[-1] if self.history else None

        speed = 0.0; velocity = (0.0, 0.0)
        if previous and ball and previous["ball"]:
            delta_t = max(0.1, timestamp - previous["timestamp"])
            velocity = ((ball[0] - previous["ball"][0]) / delta_t,
                        (ball[1] - previous["ball"][1]) / delta_t)
            speed = math.hypot(*velocity)

        # GOAL: require at least two of entering the goal zone, disappearance,
        # and player convergence around that area. This is intentionally marked
        # "likely" because 1-FPS geometry cannot verify the score.
        if previous:
            entered_goal = self._goal_zone(previous["ball"], width, height,
                                           self.settings.goal_area_threshold)
            disappeared = previous["ball"] is not None and ball is None
            converged = bool(cluster_center and previous["ball"] and cluster_size >= max(3, len(players) // 3)
                             and _distance(cluster_center, previous["ball"]) < width * .25)
            goal_signals = [name for name, value in (
                ("ball entered goal area", entered_goal),
                ("ball disappeared inside goal area", disappeared),
                ("players converged near goal", converged),
            ) if value]
            if len(goal_signals) >= 2:
                self._add(timestamp, "goal", 0.62 + 0.10 * len(goal_signals),
                          "Likely goal: " + ", ".join(goal_signals) + ".", goal_signals)

        # SHOT ON TARGET: fast, mostly straight motion toward either end line.
        if ball and previous and previous["ball"] and speed >= self.settings.ball_speed_threshold:
            toward_goal = ((ball[0] < width / 2 and velocity[0] < 0) or
                           (ball[0] >= width / 2 and velocity[0] > 0))
            horizontal = abs(velocity[0]) > abs(velocity[1]) * 1.25
            if toward_goal and horizontal and (ball[0] < width * .30 or ball[0] > width * .70):
                self._add(timestamp, "shot_on_target", min(.9, .58 + speed / max(width, 1)),
                          "The ball moved rapidly on a straight trajectory toward the goal area.",
                          ["fast ball", "straight goalward trajectory"])

        # FOUL: collision, fallen geometry, and a stopped/clustered phase. Two
        # signals are required to reduce false calls from perspective overlap.
        overlap = any(_iou(players[i], players[j]) > .32 for i in range(len(players))
                      for j in range(i + 1, len(players)))
        fallen = any(p["bbox"][2] > p["bbox"][3] * 1.15 and
                     p["bbox"][1] + p["bbox"][3] > height * .65 for p in players)
        recent_speeds = []
        ball_records = [item for item in list(self.history)[-3:] + [current] if item["ball"]]
        for first, second in zip(ball_records, ball_records[1:]):
            recent_speeds.append(_distance(first["ball"], second["ball"]) /
                                 max(.1, second["timestamp"] - first["timestamp"]))
        play_stopped = bool(recent_speeds and max(recent_speeds) < self.settings.ball_speed_threshold * .25
                            and cluster_size >= 3)
        foul_signals = [name for name, value in (("player collision", overlap),
                        ("player appears down", fallen), ("play stopped", play_stopped)) if value]
        if len(foul_signals) >= 2:
            self._add(timestamp, "foul", .56 + .09 * len(foul_signals),
                      "Possible foul: " + ", ".join(foul_signals) + ".", foul_signals)

        # CORNER: disappearance at a corner followed by a penalty-area cluster.
        if previous:
            corner_exit = previous["ball"] is not None and ball is None and self._corner_zone(previous["ball"], width, height)
            penalty_cluster = cluster_size >= 4 and cluster_center is not None and (
                cluster_center[0] < width * .30 or cluster_center[0] > width * .70)
            if corner_exit and penalty_cluster:
                self._add(timestamp, "corner_kick", .76,
                          "The ball exited near the corner and players gathered in the penalty area.",
                          ["corner exit", "penalty-area cluster"])

        # COUNTER: crossing from one pitch third to the opposite in <= 6 sec,
        # with comparatively few players between the ball and destination goal.
        if ball:
            for old in reversed(self.history):
                if not old["ball"] or timestamp - old["timestamp"] > 6:
                    continue
                crossed = ((old["ball"][0] < width / 3 and ball[0] > width * 2 / 3) or
                           (old["ball"][0] > width * 2 / 3 and ball[0] < width / 3))
                if crossed:
                    direction_right = ball[0] > old["ball"][0]
                    ahead = sum(1 for player in players if
                                (_center(player)[0] > ball[0] if direction_right else _center(player)[0] < ball[0]))
                    if ahead <= max(3, len(players) // 3):
                        self._add(timestamp, "counter_attack", .78,
                                  "The ball swept from one third to the opposite third with few defenders ahead.",
                                  ["rapid third-to-third transition", "few defenders ahead"])
                    break

        # Sustained dense group means territorial pressure rather than one-frame noise.
        pressure_now = len(players) >= 5 and cluster_size >= max(4, math.ceil(len(players) * .55))
        pressure_history = [item for item in list(self.history)[-2:]
                            if len(item["players"]) >= 5 and item["cluster_size"] >= max(4, math.ceil(len(item["players"]) * .55))]
        if pressure_now and len(pressure_history) >= 2:
            self._add(timestamp, "high_pressure", .70,
                      "A sustained high-density player cluster indicates mounting pressure.",
                      ["sustained player density"])

        # Excitement: sharp direction/speed change plus a visible density shift.
        if len(self.history) >= 2 and ball and previous and previous["ball"]:
            before = self.history[-2]
            if before["ball"]:
                dt = max(.1, previous["timestamp"] - before["timestamp"])
                old_v = ((previous["ball"][0] - before["ball"][0]) / dt,
                         (previous["ball"][1] - before["ball"][1]) / dt)
                old_speed = math.hypot(*old_v)
                dot = old_v[0] * velocity[0] + old_v[1] * velocity[1]
                denominator = max(1e-6, old_speed * speed)
                angle_change = math.degrees(math.acos(max(-1, min(1, dot / denominator)))) if speed and old_speed else 0
                density_shift = abs(cluster_size - previous["cluster_size"]) >= 3
                if speed >= self.settings.ball_speed_threshold and (angle_change > 70 or density_shift):
                    self._add(timestamp, "general_excitement", .67 if density_shift else .62,
                              "Rapid ball movement and a dramatic shift in player density raised the tempo.",
                              ["rapid direction change", "player density shift"] if density_shift else ["rapid direction change"])

        self.history.append(current)

    def detect(self, progress: ProgressCallback | None = None,
               stop_check: StopCheck | None = None) -> dict[str, Any]:
        files = sorted(self.input_dir.glob("track_*.json"))
        if not files:
            # Tracking is optional for imported detections; use those directly.
            files = sorted((TEMP_DIR / "detections").glob("det_*.json"))
        if not files:
            raise RuntimeError("No tracking or detection records found.")
        self.events = []; self.history.clear(); self.last_event.clear()
        started = time.monotonic(); total = len(files)
        for index, path in enumerate(files, 1):
            stop_if_requested(stop_check)
            record = read_json(path, {})
            self._analyze(record)
            if index == 1 or index % 100 == 0 or index == total:
                report(progress, index, total,
                       f"Detecting events: {index}/{total} ({int(index * 100 / total)}%) - "
                       f"{eta_text(started, index, total)}")
        result = {
            "events": sorted(self.events, key=lambda event: event["timestamp"]),
            "method": "rule_based_geometry_v1",
            "sample_count": total,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        atomic_write_json(self.output_path, result)
        LOG.info("Rule engine saved %d events to %s (zero GPU used).", len(self.events), self.output_path)
        return result

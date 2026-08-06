"""One-event-at-a-time local Ollama commentary generation."""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from config.settings import RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, read_json, report, stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("commentary")
SYSTEM_PROMPT = """You are an enthusiastic professional sports commentator.
Generate 1-3 short punchy sentences of live commentary for this event.
Be exciting but concise. Vary your language.
Do not use player names unless provided.
Respond with ONLY the commentary text, nothing else."""

TEMPLATES = {
    "goal": [
        "WHAT A GOAL! The pressure pays off in spectacular fashion!",
        "It's in! A huge moment, and the crowd erupts!",
        "GOAL! That is a decisive finish when it mattered most!",
    ],
    "shot_on_target": [
        "A fierce effort toward goal! That demanded an answer!",
        "The shot is on target—real danger there!",
        "They open up the angle and let fly!",
    ],
    "foul": [
        "A heavy coming-together stops the move. The official has a decision to make.",
        "Contact in the challenge, and play comes to a halt.",
    ],
    "corner_kick": [
        "Corner kick—another chance to load the danger area!",
        "It's gone behind, and everyone is crowding the box for the corner.",
    ],
    "counter_attack": [
        "Here comes the counter! Space is opening up at breathtaking speed!",
        "A rapid break from one end to the other—this could be dangerous!",
    ],
    "high_pressure": [
        "The pressure is building now. There is barely room to breathe!",
        "Bodies crowd the area as the attacking pressure intensifies.",
    ],
    "general_excitement": [
        "The tempo suddenly surges! The momentum is shifting fast.",
        "A sharp change of direction brings the contest to life!",
    ],
}


class CommentaryGenerator:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None,
                 ollama_url: str | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.events_path = TEMP_DIR / "events.json"
        self.output_path = TEMP_DIR / "commentary" / "commentary.json"
        self.ollama_url = (ollama_url or os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
        if not self.ollama_url.startswith(("http://", "https://")):
            self.ollama_url = "http://" + self.ollama_url

    def _request(self, model: str, prompt: str, keep_alive: str | int = "10m") -> str:
        payload = {
            "model": model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": self.settings.llm_temperature,
                "num_predict": self.settings.llm_max_tokens,
                "num_thread": self.settings.cpu_threads,
                "top_p": 0.9,
            },
        }
        request = urllib.request.Request(
            self.ollama_url + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = str(data.get("response", "")).strip()
        if not text:
            raise RuntimeError(f"Ollama model {model} returned empty text.")
        # Remove common formatting despite an explicit plain-text prompt.
        return text.strip().strip('"').replace("**", "")

    @staticmethod
    def _prompt(event: dict[str, Any], sport: str, style: str) -> str:
        timestamp = int(float(event.get("timestamp", 0)))
        minutes, seconds = divmod(timestamp, 60)
        return (
            f"Sport: {sport}\nCommentary style: {style}\n"
            f"Event: {event.get('type', 'event')} at {minutes}:{seconds:02d}\n"
            f"Context: {event.get('description', 'The action develops.')}\n"
            f"Match time: {minutes} minutes into the match\nGenerate commentary:"
        )

    @staticmethod
    def _template(event: dict[str, Any], style: str) -> str:
        choices = TEMPLATES.get(event.get("type"), ["The action is developing, and the intensity rises!"])
        # Stable choice allows resumable/reproducible output.
        seed = f"{event.get('timestamp')}:{event.get('type')}:{style}"
        text = random.Random(seed).choice(choices)
        if style == "Professional":
            return text.replace("WHAT A GOAL!", "A superb goal.").replace("GOAL!", "Goal.")
        if style == "Casual":
            return text.replace("breathtaking", "incredible")
        return text

    def _generate_one(self, event: dict[str, Any]) -> tuple[str, str]:
        prompt = self._prompt(event, self.settings.sport_type, self.settings.commentary_style)
        errors = []
        models = list(dict.fromkeys([self.settings.ollama_model, self.settings.ollama_fallback]))
        for model in models:
            try:
                return self._request(model, prompt), model
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                errors.append(f"{model}: {exc}")
                LOG.warning("Ollama generation with %s failed: %s", model, exc)
        # A local rule template lets export complete if Ollama was stopped during
        # a multi-hour job. The manifest clearly identifies this fallback.
        LOG.error("Ollama unavailable; using built-in emergency commentary. %s", " | ".join(errors))
        return self._template(event, self.settings.commentary_style), "built_in_fallback"

    def _unload(self, model: str) -> None:
        if model == "built_in_fallback":
            return
        # Ollama's documented unload request is an empty generate payload with
        # keep_alive=0. Do not call _request here because no text is expected.
        try:
            request = urllib.request.Request(
                self.ollama_url + "/api/generate",
                data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except Exception:
            # On old Ollama versions the ten-minute keep_alive eventually frees it.
            pass

    def generate(self, resume: bool = False, progress: ProgressCallback | None = None,
                 stop_check: StopCheck | None = None) -> dict[str, Any]:
        events = read_json(self.events_path, {}).get("events", [])
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = read_json(self.output_path, {}).get("commentary", []) if resume else []
        by_key = {(float(item.get("timestamp", -1)), item.get("event")): item for item in existing}
        output = []
        total = len(events); started = time.monotonic(); used_models: set[str] = set()
        if total == 0:
            result = {"commentary": [], "model": self.settings.ollama_model,
                      "warning": "No events met the selected confidence threshold."}
            atomic_write_json(self.output_path, result)
            return result
        try:
            for index, event in enumerate(events, 1):
                stop_if_requested(stop_check)
                key = (float(event.get("timestamp", -1)), event.get("type"))
                if key in by_key and by_key[key].get("text"):
                    item = by_key[key]
                else:
                    text, model = self._generate_one(event)
                    used_models.add(model)
                    item = {
                        "timestamp": event["timestamp"], "text": text,
                        "event": event["type"], "model": model,
                    }
                output.append(item)
                # Save every line so a laptop shutdown loses at most one event.
                atomic_write_json(self.output_path, {"commentary": output, "models": sorted(used_models)})
                report(progress, index, total,
                       f"Generating commentary: {index}/{total} ({int(index * 100 / total)}%) - "
                       f"{eta_text(started, index, total)}")
                self.memory.check_resources()
            return {"commentary": output, "models": sorted(used_models)}
        finally:
            for model in used_models:
                self._unload(model)
            self.memory.release("Ollama commentary stage complete")
            LOG.info("Commentary stage complete; requested Ollama model unload.")

    def regenerate(self, row_index: int) -> dict[str, Any]:
        data = read_json(self.output_path, {"commentary": []})
        events = read_json(self.events_path, {"events": []}).get("events", [])
        if row_index < 0 or row_index >= len(events):
            raise IndexError(f"Commentary row must be between 1 and {len(events)}.")
        text, model = self._generate_one(events[row_index])
        item = {"timestamp": events[row_index]["timestamp"], "text": text,
                "event": events[row_index]["type"], "model": model}
        lines = data.get("commentary", [])
        while len(lines) <= row_index:
            lines.append({})
        lines[row_index] = item; data["commentary"] = lines
        atomic_write_json(self.output_path, data)
        self._unload(model)
        return item

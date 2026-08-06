"""Memory, thermal, and battery safeguards for long CPU-only jobs."""
from __future__ import annotations

import gc
import os
import time
from pathlib import Path
from typing import Callable

import psutil

from utils.logger import setup_logger

LOG = setup_logger("resources")


class MemoryManager:
    """Checks system health only between work items/batches.

    It never keeps model references.  Calling ``release`` after a model stage
    makes reference-counted objects collectible and asks libc to return free
    heap pages on Linux when possible.
    """

    def __init__(self, max_ram_percent: float = 70.0,
                 thermal_limit_c: float = 90.0,
                 pause_check: Callable[[], bool] | None = None):
        self.max_ram_percent = max_ram_percent
        self.thermal_limit_c = thermal_limit_c
        self.pause_check = pause_check or (lambda: False)
        self._last_ram_warning = 0.0
        self._battery_warned = False

    @staticmethod
    def ram_status() -> dict[str, float]:
        vm = psutil.virtual_memory()
        gib = 1024 ** 3
        return {
            "percent": float(vm.percent),
            "total_gb": vm.total / gib,
            "available_gb": vm.available / gib,
        }

    @staticmethod
    def cpu_temperature() -> float | None:
        # psutil supports common Linux hwmon drivers.  The fallback reads sysfs.
        try:
            values = []
            for entries in (psutil.sensors_temperatures() or {}).values():
                values.extend(float(entry.current) for entry in entries
                              if entry.current is not None and 0 < entry.current < 150)
            if values:
                return max(values)
        except (AttributeError, OSError):
            pass
        for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                value = float(path.read_text().strip())
                value = value / 1000 if value > 1000 else value
                if 0 < value < 150:
                    return value
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def battery_status() -> tuple[bool, float | None] | None:
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, OSError):
            battery = None
        if battery is None:
            return None
        return bool(not battery.power_plugged), float(battery.percent)

    def warn_if_on_battery(self) -> bool:
        state = self.battery_status()
        if state and state[0] and not self._battery_warned:
            LOG.warning("Laptop is on battery (%.0f%%). Plug in for long processing jobs.", state[1])
            self._battery_warned = True
            return True
        return bool(state and state[0])

    def check_resources(self) -> None:
        """Pause safely for user requests, high memory, or excess heat."""
        while self.pause_check():
            LOG.info("Pause requested; waiting at a safe checkpoint...")
            time.sleep(1)

        status = self.ram_status()
        LOG.debug("RAM %.1f%% used, %.2f GiB available", status["percent"], status["available_gb"])
        if status["available_gb"] < 1.0:
            LOG.warning("Critically low RAM: only %.2f GiB available.", status["available_gb"])

        # RAM pressure can be temporary. Retry after collecting; do not hang a
        # machine forever because unrelated applications own most memory.
        if status["percent"] >= self.max_ram_percent:
            LOG.warning("RAM usage %.1f%% exceeds %.1f%%; collecting and pausing.",
                        status["percent"], self.max_ram_percent)
            self.release("RAM pressure")
            for _ in range(6):
                if self.pause_check():
                    return
                time.sleep(5)
                if psutil.virtual_memory().percent < self.max_ram_percent:
                    break
            else:
                LOG.warning("RAM remains above target; continuing one disk-backed batch cautiously.")

        temperature = self.cpu_temperature()
        if temperature is not None and temperature >= self.thermal_limit_c:
            LOG.warning("CPU temperature %.1f C exceeds %.1f C; cooling for 30 seconds.",
                        temperature, self.thermal_limit_c)
            # Check once per second so an application pause remains responsive.
            for _ in range(30):
                if self.pause_check():
                    return
                time.sleep(1)

    def log_usage(self, stage: str) -> None:
        status = self.ram_status()
        temp = self.cpu_temperature()
        suffix = f", CPU {temp:.1f} C" if temp is not None else ""
        LOG.info("Resources after %s: RAM %.1f%%, %.2f GiB available%s",
                 stage, status["percent"], status["available_gb"], suffix)

    @staticmethod
    def release(reason: str = "stage boundary") -> None:
        collected = gc.collect()
        # malloc_trim is Linux/glibc-only and entirely optional.
        if os.name == "posix":
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except (OSError, AttributeError):
                pass
        LOG.debug("Released memory at %s (%d cyclic objects collected)", reason, collected)

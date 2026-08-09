"""Portable hardware discovery and ONNX Runtime provider selection."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

import psutil

from config.settings import PROJECT_ROOT
from utils.logger import setup_logger

LOG = setup_logger("hardware")


def _run(command: list[str], timeout: int = 4) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _cpu_model() -> str:
    system = platform.system()
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if system == "Windows":
        value = _run(["powershell", "-NoProfile", "-Command",
                      "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
        if value:
            return value
    if system == "Darwin":
        value = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if value:
            return value
    return platform.processor() or platform.machine() or "Unknown CPU"


def _gpu_names() -> list[str]:
    system = platform.system()
    text = ""
    if system == "Linux":
        text = _run(["lspci"], 5)
        lines = [line.split(": ", 1)[-1] for line in text.splitlines()
                 if any(tag in line.lower() for tag in ("vga", "3d controller", "display controller"))]
        return lines or ["Integrated/unknown GPU"]
    if system == "Windows":
        text = _run(["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | Select-Object -Expand Name"])
        return [line.strip() for line in text.splitlines() if line.strip()] or ["Unknown GPU"]
    if system == "Darwin":
        text = _run(["system_profiler", "SPDisplaysDataType"])
        return [line.split(":", 1)[1].strip() for line in text.splitlines() if "Chipset Model:" in line]
    return ["Unknown GPU"]


def _ort_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except (ImportError, OSError):
        return ["CPUExecutionProvider"]


def _has_command(name: str, probe_args: list[str]) -> bool:
    if not shutil.which(name):
        return False
    try:
        return subprocess.run([name, *probe_args], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_compute_backend() -> tuple[str, str]:
    """Choose ROCm > Vulkan > CUDA > CPU when that ORT provider is installed.

    A driver alone is insufficient: normal ``onnxruntime`` wheels expose CPU
    only. Users may install a vendor wheel and this function will use it without
    any code changes.
    """
    providers = _ort_providers()
    priorities = (
        ("ROCm", "ROCMExecutionProvider"),
        ("Vulkan", "VulkanExecutionProvider"),
        ("CUDA", "CUDAExecutionProvider"),
    )
    for backend, provider in priorities:
        if provider in providers:
            LOG.info("Compute backend: %s (%s)", backend, provider)
            return backend, provider
    LOG.info("Compute backend: CPU (CPUExecutionProvider)")
    return "CPU", "CPUExecutionProvider"


def detect_hardware(print_summary: bool = True) -> dict[str, Any]:
    physical = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
    logical = psutil.cpu_count(logical=True) or physical
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(PROJECT_ROOT)
    gpu_names = _gpu_names()
    gpu_text = " | ".join(gpu_names)
    providers = _ort_providers()
    backend, provider = detect_compute_backend()
    battery = None
    try:
        battery_info = psutil.sensors_battery()
        if battery_info:
            battery = {"percent": battery_info.percent, "plugged": battery_info.power_plugged}
    except (AttributeError, OSError):
        pass

    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": _cpu_model(),
        "physical_cores": physical,
        "logical_cores": logical,
        "ram_total_gb": round(vm.total / 1024 ** 3, 2),
        "ram_available_gb": round(vm.available / 1024 ** 3, 2),
        "gpus": gpu_names,
        "rocm_driver": _has_command("rocminfo", []),
        "vulkan_driver": _has_command("vulkaninfo", ["--summary"]),
        "cuda_driver": _has_command("nvidia-smi", []),
        "ort_providers": providers,
        "backend": backend,
        "execution_provider": provider,
        "disk_free_gb": round(disk.free / 1024 ** 3, 2),
        "battery": battery,
    }

    # Physical cores avoid oversubscription. On battery, half the cores preserve
    # responsiveness and reduce heat/drain.
    threads = max(1, physical)
    if battery and not battery["plugged"]:
        threads = max(1, physical // 2)
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
    os.environ["OMP_WAIT_POLICY"] = "PASSIVE"

    low_ram = vm.total <= 16 * 1024 ** 3
    overrides: dict[str, Any] = {
        "cpu_threads": threads,
        "compute_backend": backend,
        "execution_provider": provider,
        "low_ram_mode": low_ram,
    }
    if low_ram:
        overrides.update({
            "frame_batch_size": 50,
            "ollama_model": "tinyllama",
            "yolo_input_size": 320,
            "use_voice_cloning": False,
        })
    if vm.total < 8 * 1024 ** 3:
        overrides.update({"frame_batch_size": 25, "frame_extraction_fps": 0.5})
    info["settings_overrides"] = overrides

    if print_summary:
        line = "=" * 62
        print(f"\n{line}\nAI SPORTS COMMENTATOR - HARDWARE SUMMARY\n{line}")
        print(f"OS:              {info['os']}")
        print(f"CPU:             {info['cpu']}")
        print(f"CPU cores:       {physical} physical / {logical} logical (using {threads})")
        print(f"RAM:             {info['ram_total_gb']:.1f} GiB total / {info['ram_available_gb']:.1f} GiB available")
        print(f"GPU:             {gpu_text}")
        print(f"Drivers:         ROCm={info['rocm_driver']} Vulkan={info['vulkan_driver']} CUDA={info['cuda_driver']}")
        print(f"ONNX providers:  {', '.join(providers)}")
        print(f"Selected:        {backend} / {provider}")
        print(f"Disk free:       {info['disk_free_gb']:.1f} GiB")
        if battery and not battery["plugged"]:
            print(f"WARNING: Running on battery ({battery['percent']:.0f}%). Please plug in.")
        if low_ram:
            print("LOW RAM MODE: 320px detection, batch 50, TinyLlama, built-in voice.")
        if vm.total < 8 * 1024 ** 3:
            print("WARNING: Less than 8 GiB RAM is unsupported; 0.5 FPS mode enabled.")
        print(line)
    return info


if __name__ == "__main__":
    print(json.dumps(detect_hardware(True), indent=2))

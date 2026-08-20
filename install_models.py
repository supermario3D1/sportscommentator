#!/usr/bin/env python3
"""Download/export compact local models and verify every resulting file."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
PIPER = MODELS / "piper"
CHECKSUMS = MODELS / "checksums.json"
USER_AGENT = "sports-commentator/1.0"
YOLO_PT_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
    "en_US-ryan-medium": "en/en_US/ryan/medium/en_US-ryan-medium",
    "en_GB-alba-medium": "en/en_GB/alba/medium/en_GB-alba-medium",
    "en_GB-alan-medium": "en/en_GB/alan/medium/en_GB-alan-medium",
}
OPENVOICE_URL = "https://myshell-public-repo-host.s3.amazonaws.com/openvoice/checkpoints_v2_0417.zip"

_TLS_HELP = (
    "TLS certificate verification failed for every available trust source "
    "(Python defaults, the operating-system store, and the bundled CA bundle). "
    "This is almost always caused by antivirus 'HTTPS/SSL scanning' (Kaspersky, "
    "ESET, Avast, Bitdefender, ...) or a corporate proxy re-signing certificates "
    "in a way strict OpenSSL builds reject. Fixes, in order of preference: "
    "1) exclude python.exe from antivirus web/SSL scanning or pause that feature, "
    "then rerun; 2) on a company-managed PC ask IT to permit downloads from "
    "huggingface.co and github.com; 3) last resort, accept the intercepted "
    "certificates by rerunning with SC_INSECURE_TLS=1 in the environment "
    "(downloads remain SHA-256-verified whenever a checksum is known)."
)
_trusted_context: tuple[str, ssl.SSLContext] | None = None


def _candidate_contexts() -> list[tuple[str, ssl.SSLContext]]:
    """TLS trust sources to try, most standard first.

    Interception software (antivirus web shields, corporate proxies) often
    presents re-signed certificate chains that strict OpenSSL refuses even
    though the operating system itself trusts them, and some Python installs
    ship without usable default CA paths. Extra sources are optional: each is
    used only when importable.
    """
    candidates: list[tuple[str, ssl.SSLContext]] = [
        ("Python default certificates", ssl.create_default_context())
    ]
    try:
        import truststore

        candidates.append(
            ("operating-system certificates (truststore)",
             truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        )
    except Exception:
        pass
    try:
        import certifi

        candidates.append(
            ("bundled certifi certificates",
             ssl.create_default_context(cafile=certifi.where()))
        )
    except Exception:
        pass
    if os.environ.get("SC_INSECURE_TLS") == "1":
        print("! SC_INSECURE_TLS=1 set: certificate checks are DISABLED for model downloads.")
        unverified = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        unverified.check_hostname = False
        unverified.verify_mode = ssl.CERT_NONE
        candidates.append(("unverified TLS (SC_INSECURE_TLS=1)", unverified))
    return candidates


def _open_https(url: str, method: str, timeout: float):
    """urlopen that survives strict-OpenSSL vs. intercepted-certificate conflicts.

    Tries each trust source in turn, but only after TLS-level failures: server
    answers (HTTP errors) and connectivity problems surface immediately. The
    first source that works is remembered for the rest of the install; if the
    remembered source later rejects a different site's certificate, every
    remaining source is probed again before giving up.
    """
    global _trusted_context
    attempts: list[tuple[str, ssl.SSLContext]] = (
        [_trusted_context] if _trusted_context else list(_candidate_contexts())
    )
    first_label = attempts[0][0]
    refill_with_fresh_sources = _trusted_context is not None
    last_tls_failure: Exception | None = None
    while attempts:
        label, context = attempts.pop(0)
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            response = urllib.request.urlopen(request, timeout=timeout, context=context)
        except urllib.error.HTTPError:
            raise  # The server answered, so certificate trust already succeeded.
        except (urllib.error.URLError, ssl.SSLError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, ssl.SSLError):
                print(f"! TLS rejected by {label}: {reason}")
                last_tls_failure = exc
                if not attempts and refill_with_fresh_sources:
                    refill_with_fresh_sources = False
                    print("! Previously working trust source failed here; probing the remaining sources...")
                    attempts = [candidate for candidate in _candidate_contexts()
                                if candidate[0] != label]
                continue
            raise
        if label != first_label:
            print(f"✓ TLS established using {label}.")
        _trusted_context = (label, context)
        return response
    raise RuntimeError(f"{_TLS_HELP} Underlying error: {last_tls_failure}") from last_tls_failure


class _NativeHttpError(RuntimeError):
    """The native downloader reached the server, which refused the request."""


def _find_curl() -> str | None:
    """Locate curl, preferring the build Windows itself ships (Schannel TLS).

    Windows' own curl.exe validates certificates exactly like the browser
    does, so it accepts the re-signed chains of antivirus web shields that
    strict OpenSSL builds reject. A curl earlier on PATH (for example Git's
    OpenSSL build) may not have that property, hence the explicit lookup.
    """
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for candidate in (Path(root) / "System32" / "curl.exe",
                          Path(root) / "Sysnative" / "curl.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("curl")


def _curl_download(url: str, dest: Path) -> None:
    executable = _find_curl()
    if not executable:
        raise RuntimeError("curl was not found")
    command = [executable, "-L", "--fail", "-S", "--progress-bar",
               "--retry", "3", "--retry-delay", "5",
               "--connect-timeout", "20", "--speed-time", "60", "--speed-limit", "1024",
               "-A", USER_AGENT, "-o", str(dest), url]
    if os.name == "nt":
        # Intercepting proxies carry no revocation information for the very
        # certificates they issue; Schannel's revocation check would trip
        # over that before the chain itself is even evaluated.
        command.append("--ssl-no-revoke")
    result = subprocess.run(command, stdin=subprocess.DEVNULL)
    if result.returncode == 22:
        raise _NativeHttpError(f"the server rejected the download (curl exit code 22)")
    if result.returncode != 0:
        raise RuntimeError(f"curl exited with code {result.returncode}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("curl finished without writing any data")


def _powershell_download(url: str, dest: Path) -> None:
    """Download through PowerShell's WebClient (Schannel TLS on Windows)."""
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        raise RuntimeError("PowerShell was not found")

    def quote(value: str) -> str:  # escape for a single-quoted PowerShell string
        return value.replace("'", "''")

    script = ("$ErrorActionPreference='Stop';"
              "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
              "$client=New-Object System.Net.WebClient;"
              f"$client.Headers.Add('User-Agent','{USER_AGENT}');"
              f"$client.DownloadFile('{quote(url)}','{quote(str(dest))}')")
    result = subprocess.run(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        details = (result.stderr or "").strip().splitlines()
        reason = details[-1] if details else f"exit code {result.returncode}"
        raise RuntimeError(f"PowerShell download failed: {reason}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("PowerShell finished without writing any data")


def _native_download(url: str, dest: Path) -> None:
    """Last-resort download through the operating system's own TLS engine.

    Security software and corporate proxies re-sign certificates in ways
    strict OpenSSL refuses even when the operating system itself trusts
    them. Windows' bundled curl and PowerShell both validate with Schannel
    -- the browser's TLS engine -- instead of OpenSSL, so they still see the
    original trust decision. Whichever transport wins, the caller verifies
    the downloaded bytes against the SHA-256 checksum afterwards.
    """
    failures: list[str] = []
    for label, fetch in (("curl", _curl_download), ("PowerShell", _powershell_download)):
        try:
            fetch(url, dest)
        except _NativeHttpError:
            dest.unlink(missing_ok=True)
            raise  # the server itself refused; switching transport cannot help
        except Exception as exc:
            dest.unlink(missing_ok=True)
            failures.append(f"{label}: {exc}")
            continue
        print(f"✓ Downloaded with the system-native {label} downloader.")
        return
    summary = "; ".join(failures)
    raise RuntimeError(
        f"{_TLS_HELP} The system-native downloaders (curl and PowerShell, which "
        f"use the operating system's own TLS engine instead of OpenSSL) were "
        f"also tried and failed ({summary})."
    )


def _parse_header_blocks(raw: str) -> dict[str, str]:
    """Collapse 'curl -I' output (one header block per redirect hop) to a dict."""
    headers: dict[str, str] = {}
    for block in re.split(r"\r?\n\s*\r?\n", raw):
        for line in block.splitlines():
            name, separator, value = line.partition(":")
            if separator and not name.startswith("HTTP/"):
                headers.setdefault(name.strip().lower(), value.strip())
    return headers


def _native_head(url: str) -> dict[str, str]:
    """Best-effort HEAD through the system curl; empty when unavailable."""
    executable = _find_curl()
    if not executable:
        return {}
    command = [executable, "-sS", "--fail", "-L", "-I", "--connect-timeout", "20",
               "--max-time", "60", "-A", USER_AGENT, url]
    if os.name == "nt":
        command.append("--ssl-no-revoke")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=90, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    return _parse_header_blocks(result.stdout)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_sha256(url: str) -> str | None:
    """Read Hugging Face LFS's linked SHA-256 when the server provides it."""
    try:
        with _open_https(url, "HEAD", 30) as response:
            values = [response.headers.get("x-linked-etag"), response.headers.get("etag")]
    except (OSError, RuntimeError, urllib.error.URLError):
        # Python cannot establish TLS with this host at all; ask the system
        # curl (Schannel on Windows) for the same headers so the download
        # below is still checksum-verified end to end.
        native = _native_head(url)
        values = [native.get("x-linked-etag"), native.get("etag")]
    for value in values:
        cleaned = (value or "").strip('"').replace("sha256:", "")
        if len(cleaned) == 64 and all(char in "0123456789abcdefABCDEF" for char in cleaned):
            return cleaned.lower()
    return None


def _stream_to_file(url: str, dest: Path) -> None:
    """Fetch a URL into dest: Python first, the system downloader as backup."""
    try:
        with _open_https(url, "GET", 120) as response, dest.open("wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            received = 0; last = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk); received += len(chunk)
                percent = int(received * 100 / total) if total else 0
                if percent >= last + 5:
                    print(f"  {received / 1024**2:7.1f} MiB" + (f" / {total / 1024**2:.1f} MiB ({percent}%)" if total else ""))
                    last = percent
        return
    except RuntimeError:
        # Every Python trust source refused this site's certificate chain.
        print("! Python cannot verify this site's TLS certificate; switching to the")
        print("  system-native downloader. Downloaded bytes are still SHA-256 checked.")
    dest.unlink(missing_ok=True)
    _native_download(url, dest)


def download(url: str, target: Path, known: dict[str, str]) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = remote_sha256(url) or known.get(str(target.relative_to(MODELS)))
    if target.is_file():
        current = sha256(target)
        if not expected or current == expected:
            print(f"✓ {target.name} already present (SHA-256 {current[:12]}…)")
            return current
        print(f"! Checksum mismatch for {target}; downloading a clean copy.")
        target.unlink()
    partial = target.with_suffix(target.suffix + ".part")
    print(f"↓ Downloading {target.name}")
    for attempt in range(3):
        try:
            _stream_to_file(url, partial)
            partial.replace(target)
            break
        except RuntimeError:
            # TLS trust guidance already explains the fix; retrying cannot help.
            partial.unlink(missing_ok=True)
            raise
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            wait = 5 * (attempt + 1)
            print(f"! Transfer interrupted; retrying in {wait}s (attempt {attempt + 2} of 3)...")
            time.sleep(wait)
    actual = sha256(target)
    if expected and actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 verification failed for {target.name}: expected {expected}, got {actual}")
    print(f"✓ Verified {target.name}: SHA-256 {actual}")
    return actual


def install_yolo(known: dict[str, str], hashes: dict[str, str]) -> None:
    target = MODELS / "yolov8n.onnx"
    if target.is_file():
        try:
            import onnxruntime as ort
            ort.InferenceSession(str(target), providers=["CPUExecutionProvider"])
            hashes[str(target.relative_to(MODELS))] = sha256(target)
            print(f"✓ YOLOv8n ONNX graph validated: {target}")
            return
        except Exception as exc:
            print(f"! Existing ONNX graph is invalid ({exc}); re-exporting.")
            target.unlink(missing_ok=True)
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Ultralytics is required once to export YOLO. Run setup.sh first.") from exc
    pt = MODELS / "yolov8n.pt"
    hashes[str(pt.relative_to(MODELS))] = download(YOLO_PT_URL, pt, known)
    print("→ Exporting YOLOv8 NANO to dynamic ONNX on CPU (one-time step)...")
    model = YOLO(str(pt), task="detect")
    exported = Path(model.export(format="onnx", imgsz=640, dynamic=True, simplify=False,
                                 opset=12, device="cpu", half=False))
    if exported.resolve() != target.resolve():
        shutil.move(str(exported), target)
    try:
        import onnxruntime as ort
        session = ort.InferenceSession(str(target), providers=["CPUExecutionProvider"])
        output_shape = session.get_outputs()[0].shape
        del session
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Exported YOLO ONNX validation failed: {exc}") from exc
    hashes[str(target.relative_to(MODELS))] = sha256(target)
    print(f"✓ YOLO graph validated; output shape {output_shape}, SHA-256 {hashes[str(target.relative_to(MODELS))]}")


def install_voices(known: dict[str, str], hashes: dict[str, str], all_voices: bool) -> None:
    names = list(VOICES) if all_voices else ["en_US-lessac-medium", "en_GB-alan-medium"]
    for name in names:
        remote = VOICES[name]
        for suffix in (".onnx", ".onnx.json"):
            target = PIPER / f"{name}{suffix}"
            url = f"{PIPER_BASE}/{remote}{suffix}?download=true"
            hashes[str(target.relative_to(MODELS))] = download(url, target, known)
        # Structural checks supplement checksums and catch HTML/error downloads.
        config_path = PIPER / f"{name}.onnx.json"
        with config_path.open(encoding="utf-8") as handle:
            json.load(handle)
        if (PIPER / f"{name}.onnx").stat().st_size < 10 * 1024 * 1024:
            raise RuntimeError(f"Piper ONNX file appears truncated: {name}")
        print(f"✓ Piper pair validated: {name}")


def ollama_ready() -> bool:
    try:
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(request, timeout=3):
            return True
    except Exception:
        return False


def install_ollama_models() -> None:
    executable = shutil.which("ollama") or shutil.which("ollama.exe")
    if not executable:
        raise RuntimeError("Ollama is not installed. Install from https://ollama.com/download and rerun.")
    server = None
    if not ollama_ready():
        print("→ Starting a temporary Ollama server...")
        server = subprocess.Popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if ollama_ready():
                break
            time.sleep(1)
        else:
            server.terminate()
            raise RuntimeError("Ollama server did not start within 30 seconds.")
    try:
        for model in ("phi3:mini", "tinyllama"):
            print(f"↓ Pulling Ollama model {model} (progress is shown by Ollama)...")
            subprocess.run([executable, "pull", model], check=True)
            print(f"✓ Ollama model ready: {model}")
    finally:
        if server:
            server.terminate()


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe archive member: {member.filename}")
        zipped.extractall(destination)


def install_openvoice(known: dict[str, str], hashes: dict[str, str]) -> None:
    print("→ Installing optional OpenVoice Python package from its official repository...")
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "git+https://github.com/myshell-ai/OpenVoice.git"], check=True)
    archive = MODELS / "openvoice_v2.zip"
    hashes[str(archive.relative_to(MODELS))] = download(OPENVOICE_URL, archive, known)
    destination = MODELS / "openvoice"
    safe_extract(archive, destination)
    required = destination / "checkpoints_v2" / "converter" / "checkpoint.pth"
    if not required.is_file():
        raise RuntimeError(f"OpenVoice archive did not contain {required}")
    print(f"✓ OpenVoice V2 checkpoints installed under {destination}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-yolo", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-voices", action="store_true")
    parser.add_argument("--default-voices-only", action="store_true", help="Install two voices instead of all four UI choices")
    parser.add_argument("--openvoice", action="store_true", help="Install optional ~400MB voice-cloning model")
    args = parser.parse_args()
    MODELS.mkdir(parents=True, exist_ok=True); PIPER.mkdir(parents=True, exist_ok=True)
    try:
        known = json.loads(CHECKSUMS.read_text()) if CHECKSUMS.is_file() else {}
        hashes = dict(known)
        if not args.skip_yolo:
            install_yolo(known, hashes)
        if not args.skip_voices:
            install_voices(known, hashes, not args.default_voices_only)
        if not args.skip_llm:
            install_ollama_models()
        if args.openvoice:
            install_openvoice(known, hashes)
        CHECKSUMS.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
        total = sum(path.stat().st_size for path in MODELS.rglob("*") if path.is_file())
        print(f"\nAll requested models are ready. Local model files: {total / 1024**3:.2f} GiB")
        print(f"Checksums recorded in {CHECKSUMS}")
        return 0
    except (RuntimeError, OSError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(f"\nMODEL INSTALL FAILED: {exc}", file=sys.stderr)
        print("Already verified downloads are retained; rerun this command to resume.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

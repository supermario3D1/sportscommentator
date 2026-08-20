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
import tarfile
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
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
YOLO_PT_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
    "en_US-ryan-medium": "en/en_US/ryan/medium/en_US-ryan-medium",
    "en_GB-alba-medium": "en/en_GB/alba/medium/en_GB-alba-medium",
    "en_GB-alan-medium": "en/en_GB/alan/medium/en_GB-alan-medium",
}
# Voices preinstalled by the default setup: both are also available from
# rhasspy's GitHub releases (see PIPER_TARBALLS), so a default install can
# always complete even where huggingface.co is filtered (403) by antivirus
# or corporate web security.
DEFAULT_VOICES = ("en_US-lessac-medium", "en_US-ryan-medium")
PIPER_MIRRORS = (
    ("huggingface.co", PIPER_BASE, USER_AGENT),
    ("huggingface.co (browser user-agent)", PIPER_BASE, BROWSER_USER_AGENT),
    ("hf-mirror.com", "https://hf-mirror.com/rhasspy/piper-voices/resolve/main", USER_AGENT),
)
PIPER_TARBALLS = {
    "en_US-lessac-medium": "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-lessac-medium.tar.gz",
    "en_US-ryan-medium": "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-ryan-medium.tar.gz",
}
OPENVOICE_URL = "https://myshell-public-repo-host.s3.amazonaws.com/openvoice/checkpoints_v2_0417.zip"

# Trust anchors: official digests of every pinned artifact, recorded from the
# serving platform's own metadata APIs. Every source below (primary host,
# mirrors, GitHub release tarballs) must deliver exactly these bytes, which is
# what makes falling back to a mirror safe. yolov8n.pt is the artifact served
# by github.com/ultralytics/assets v8.3.0; the voice digests come from
# huggingface.co's repository tree API (LFS SHA-256 for the models, git blob
# SHA-1 plus size for the plain-text configs).
PINNED: dict[str, dict[str, Any]] = {
    "yolov8n.pt": {
        "sha256": "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"},
    "piper/en_US-lessac-medium.onnx": {
        "sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"},
    "piper/en_US-ryan-medium.onnx": {
        "sha256": "abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a"},
    "piper/en_GB-alba-medium.onnx": {
        "sha256": "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b"},
    "piper/en_GB-alan-medium.onnx": {
        "sha256": "0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330"},
    "piper/en_US-lessac-medium.onnx.json": {
        "git_sha1": "c67cea2c9a7a6501d89f7b2cdff411bc49e54a28", "size": 4885},
    "piper/en_US-ryan-medium.onnx.json": {
        "git_sha1": "90e07066093f756cf2e0d7b973cbf176b586dddf", "size": 4883},
    "piper/en_GB-alba-medium.onnx.json": {
        "git_sha1": "c0969252c640fd7c2765baa62936a1106ca856d7", "size": 4888},
    "piper/en_GB-alan-medium.onnx.json": {
        "git_sha1": "31f864659bfb7678af373cf4e58a7a0866ff52af", "size": 4888},
}

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


def _open_https(url: str, method: str, timeout: float, user_agent: str = USER_AGENT):
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
        request = urllib.request.Request(url, method=method,
                                         headers={"User-Agent": user_agent})
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


def _curl_download(url: str, dest: Path, user_agent: str = USER_AGENT) -> None:
    executable = _find_curl()
    if not executable:
        raise RuntimeError("curl was not found")
    command = [executable, "-L", "--fail", "-S", "--progress-bar",
               "--retry", "3", "--retry-delay", "5",
               "--connect-timeout", "20", "--speed-time", "60", "--speed-limit", "1024",
               "-A", user_agent, "-o", str(dest), url]
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


def _powershell_download(url: str, dest: Path, user_agent: str = USER_AGENT) -> None:
    """Download through PowerShell's WebClient (Schannel TLS on Windows)."""
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        raise RuntimeError("PowerShell was not found")

    def quote(value: str) -> str:  # escape for a single-quoted PowerShell string
        return value.replace("'", "''")

    script = ("$ErrorActionPreference='Stop';"
              "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
              "$client=New-Object System.Net.WebClient;"
              f"$client.Headers.Add('User-Agent','{user_agent}');"
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


def _native_download(url: str, dest: Path, user_agent: str = USER_AGENT) -> None:
    """Last-resort download through the operating system's own TLS engine.

    Security software and corporate proxies re-sign certificates in ways
    strict OpenSSL refuses even when the operating system itself trusts
    them. Windows' bundled curl and PowerShell both validate with Schannel
    -- the browser's TLS engine -- instead of OpenSSL, so they still see the
    original trust decision. Whichever transport wins, the caller verifies
    the downloaded bytes against the SHA-256 checksum afterwards.
    """
    failures: list[str] = []
    fetches = (("curl", lambda u, d: _curl_download(u, d, user_agent)),
               ("PowerShell", lambda u, d: _powershell_download(u, d, user_agent)))
    for label, fetch in fetches:
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


def _native_head(url: str, user_agent: str = USER_AGENT) -> dict[str, str]:
    """Best-effort HEAD through the system curl; empty when unavailable."""
    executable = _find_curl()
    if not executable:
        return {}
    command = [executable, "-sS", "--fail", "-L", "-I", "--connect-timeout", "20",
               "--max-time", "60", "-A", user_agent, url]
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


def _verify_pinned(path: Path, rel: str) -> None:
    """Enforce the repo's pinned official digest for a downloaded artifact.

    Applies to every source alike -- primary host, mirror, or GitHub release
    tarball -- so switching source never weakens integrity checking.
    """
    pin = PINNED.get(rel)
    if not pin:
        return
    if "sha256" in pin:
        actual = sha256(path)
        if actual != pin["sha256"]:
            path.unlink(missing_ok=True)
            raise RuntimeError(
                f"{path.name} does not match the pinned official SHA-256 "
                f"(expected {pin['sha256'][:16]}…, got {actual[:16]}…); "
                f"the downloaded copy was discarded.")
        return
    data = path.read_bytes()
    digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if digest != pin["git_sha1"] or len(data) != pin["size"]:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{path.name} does not match the pinned official git checksum; "
            f"the downloaded copy was discarded.")


def _existing_file_ok(path: Path, rel: str, expected: str | None) -> bool:
    if rel in PINNED:
        try:
            _verify_pinned(path, rel)
            return True
        except RuntimeError:
            return False
    return not expected or sha256(path) == expected


def remote_sha256(url: str, user_agent: str = USER_AGENT) -> str | None:
    """Read Hugging Face LFS's linked SHA-256 when the server provides it."""
    try:
        with _open_https(url, "HEAD", 30, user_agent) as response:
            values = [response.headers.get("x-linked-etag"), response.headers.get("etag")]
    except (OSError, RuntimeError, urllib.error.URLError):
        # Python cannot establish TLS with this host at all; ask the system
        # curl (Schannel on Windows) for the same headers so the download
        # below is still checksum-verified end to end.
        native = _native_head(url, user_agent)
        values = [native.get("x-linked-etag"), native.get("etag")]
    for value in values:
        cleaned = (value or "").strip('"').replace("sha256:", "")
        if len(cleaned) == 64 and all(char in "0123456789abcdefABCDEF" for char in cleaned):
            return cleaned.lower()
    return None


def _stream_to_file(url: str, dest: Path, user_agent: str = USER_AGENT) -> None:
    """Fetch a URL into dest: Python first, the system downloader as backup."""
    try:
        with _open_https(url, "GET", 120, user_agent) as response, dest.open("wb") as output:
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
    _native_download(url, dest, user_agent)


def download(url: str, target: Path, known: dict[str, str],
             user_agent: str = USER_AGENT) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        rel = str(target.relative_to(MODELS))
    except ValueError:  # target outside the models tree: nothing can be pinned
        rel = ""
    pin = PINNED.get(rel)
    # Pinned digests are authoritative and host-independent: no metadata
    # request is needed (and a blocked host cannot poison the expectation).
    if pin:
        expected: str | None = pin.get("sha256")
    else:
        expected = remote_sha256(url, user_agent) or known.get(rel)
    if target.is_file():
        if _existing_file_ok(target, rel, expected):
            print(f"✓ {target.name} already present (SHA-256 {sha256(target)[:12]}…)")
            return sha256(target)
        print(f"! Checksum mismatch for {target}; downloading a clean copy.")
        target.unlink(missing_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    print(f"↓ Downloading {target.name}")
    for attempt in range(3):
        try:
            _stream_to_file(url, partial, user_agent)
            partial.replace(target)
            break
        except RuntimeError:
            # TLS guidance already explains the fix; retrying cannot help.
            partial.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            partial.unlink(missing_ok=True)
            # A refused request (403 Forbidden, 404, ...) is final for this
            # source; only throttle-class statuses are worth waiting out.
            if exc.code not in (408, 429) and exc.code < 500:
                raise
            if attempt == 2:
                raise
            wait = 5 * (attempt + 1)
            print(f"! Server trouble (HTTP {exc.code}); retrying in {wait}s (attempt {attempt + 2} of 3)...")
            time.sleep(wait)
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            wait = 5 * (attempt + 1)
            print(f"! Transfer interrupted; retrying in {wait}s (attempt {attempt + 2} of 3)...")
            time.sleep(wait)
    _verify_pinned(target, rel)
    actual = sha256(target)
    if not pin and expected and actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 verification failed for {target.name}: expected {expected}, got {actual}")
    note = " (matches the pinned official checksum)" if pin else ""
    print(f"✓ Verified {target.name}: SHA-256 {actual}{note}")
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
    names = list(VOICES) if all_voices else list(DEFAULT_VOICES)
    for name in names:
        try:
            _install_voice(name, known, hashes)
        except RuntimeError:
            if name in DEFAULT_VOICES:
                raise  # a default voice must install or setup cannot continue
            # The extra UI voices have no GitHub fallback; on a filtered
            # network they degrade to a warning instead of blocking setup.
            print(f"⚠ Optional voice '{name}' could not be installed and will be")
            print(f"  unavailable in the voice picker. The two built-in voices are")
            print(f"  complete. To add it later, rerun setup or place its files in")
            print(f"  models/piper manually; they will be checksum-verified.")


def _validate_voice_pair(name: str) -> None:
    """Structural checks supplement checksums and catch HTML/error downloads."""
    config_path = PIPER / f"{name}.onnx.json"
    with config_path.open(encoding="utf-8") as handle:
        json.load(handle)
    if (PIPER / f"{name}.onnx").stat().st_size < 10 * 1024 * 1024:
        raise RuntimeError(f"Piper ONNX file appears truncated: {name}")


def _install_voice(name: str, known: dict[str, str], hashes: dict[str, str]) -> None:
    """Fetch one voice pair, moving through mirrors until a source delivers.

    Some networks answer huggingface.co with '403 Forbidden' (antivirus or
    corporate web filtering) even after TLS succeeds, so each mirror is
    tried in turn and every delivered file still has to match the pinned
    official checksum. The two default voices have a further last resort:
    rhasspy's own GitHub release tarballs (see PIPER_TARBALLS).
    """
    remote = VOICES[name]
    failures: list[str] = []
    for label, base, user_agent in PIPER_MIRRORS:
        try:
            for suffix in (".onnx", ".onnx.json"):
                target = PIPER / f"{name}{suffix}"
                url = f"{base}/{remote}{suffix}?download=true"
                hashes[str(target.relative_to(MODELS))] = download(url, target, known, user_agent)
            _validate_voice_pair(name)
            print(f"✓ Piper pair validated: {name} (source: {label})")
            return
        except Exception as exc:
            failures.append(f"{label}: {exc}")
            print(f"! {name} was not obtainable from {label} ({exc})")
    if name in PIPER_TARBALLS:
        try:
            _install_voice_from_tarball(name, known, hashes)
            return
        except Exception as exc:
            failures.append(f"github.com release tarball: {exc}")
            print(f"! {name} was not obtainable from the GitHub release ({exc})")
    summary = "; ".join(failures)
    raise RuntimeError(
        f"Every download source failed for the Piper voice '{name}' ({summary}). "
        f"This network is answering model hosts with 403/refusals -- antivirus "
        f"or corporate web filtering is the usual cause. As a workaround you "
        f"can download '{name}.onnx' and '{name}.onnx.json' with a browser "
        f"from https://huggingface.co/rhasspy/piper-voices/tree/main/{remote} "
        f"into the models/piper folder and rerun; the installer will verify "
        f"them and continue.")


def _extract_tarball(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe archive member: {member.name}")
        try:
            tar.extractall(destination, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filters
            tar.extractall(destination)


def _install_voice_from_tarball(name: str, known: dict[str, str], hashes: dict[str, str]) -> None:
    """Recover a voice from rhasspy's official GitHub release tarball.

    The piper v0.0.2 archives hold the same voice under a dashed file name;
    extracted files must still match the pinned official digests, so any
    differing re-packaging fails cleanly instead of installing something
    unverified.
    """
    archive = MODELS / "downloads" / PIPER_TARBALLS[name].rsplit("/", 1)[1]
    download(PIPER_TARBALLS[name], archive, known)
    staging = MODELS / "downloads" / f"{name}-extracted"
    shutil.rmtree(staging, ignore_errors=True)
    _extract_tarball(archive, staging)
    files = [path for path in staging.rglob("*") if path.is_file()]
    model_file = next((p for p in files if p.name.endswith(".onnx")), None)
    config_file = next((p for p in files if p.name.endswith(".onnx.json")), None)
    if not model_file or not config_file:
        raise RuntimeError("tarball did not contain a Piper voice pair")
    for source, suffix in ((model_file, ".onnx"), (config_file, ".onnx.json")):
        target = PIPER / f"{name}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        rel = str(target.relative_to(MODELS))
        _verify_pinned(target, rel)
        hashes[rel] = sha256(target)
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    _validate_voice_pair(name)
    print(f"✓ Piper pair validated: {name} (source: github.com rhasspy/piper release)")


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
            try:
                subprocess.run([executable, "pull", model], check=True)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"Ollama could not pull '{model}' (exit code {exc.returncode}). "
                    f"If this is a network block, the same antivirus/proxy that "
                    f"filters huggingface.co may also filter registry.ollama.ai. "
                    f"Allow that host or run 'ollama pull {model}' manually, "
                    f"then rerun this setup.") from exc
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

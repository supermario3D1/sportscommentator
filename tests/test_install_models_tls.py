"""TLS fallback behaviour of the model installer.

Interception software (antivirus web shields, corporate proxies) re-signs
certificates in ways strict OpenSSL rejects ("Missing Authority Key
Identifier"); the installer must retry with friendlier trust sources and,
when every Python trust source refuses, fall back to the operating
system's own TLS engine (Windows curl/PowerShell via Schannel) while still
verifying the downloaded bytes with SHA-256.
"""
from __future__ import annotations

import hashlib
import http.server
import os
import shutil
import ssl
import subprocess
import threading
import urllib.error

import pytest

import install_models


class FakeResponse:
    def __init__(self, body: bytes = b"model-bytes", headers: dict | None = None):
        self._body = body
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = self._body, b""
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fresh_tls_state():
    install_models._trusted_context = None
    yield
    install_models._trusted_context = None


def _cert_error() -> ssl.SSLCertVerificationError:
    return ssl.SSLCertVerificationError(
        "certificate verify failed: Missing Authority Key Identifier"
    )


def test_candidate_contexts_standard_first_and_verified():
    candidates = install_models._candidate_contexts()
    labels = [label for label, _ in candidates]
    assert labels[0] == "Python default certificates"
    assert len(set(labels)) == len(labels)
    assert all(isinstance(context, ssl.SSLContext) for _, context in candidates)
    # Every source is verified unless the user explicitly opts out.
    assert all(context.verify_mode == ssl.CERT_REQUIRED for _, context in candidates)


def test_insecure_env_adds_unverified_context_last(monkeypatch):
    monkeypatch.setenv("SC_INSECURE_TLS", "1")
    candidates = install_models._candidate_contexts()
    label, context = candidates[-1]
    assert "SC_INSECURE_TLS" in label
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False
    # The opt-in never replaces the verified sources, only supplements them.
    assert all(c.verify_mode == ssl.CERT_REQUIRED for _, c in candidates[:-1])


def test_open_https_falls_back_after_certificate_error(monkeypatch):
    attempts: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(context)
        if len(attempts) == 1:
            raise urllib.error.URLError(_cert_error())
        return FakeResponse(b"ok")

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    with install_models._open_https("https://example.com/file", "GET", 30) as response:
        assert response.read() == b"ok"
    assert len(attempts) == 2  # default rejected, second source succeeded


def test_open_https_reuses_the_working_context(monkeypatch):
    attempts: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(context)
        if len(attempts) == 1:
            raise urllib.error.URLError(_cert_error())
        return FakeResponse(b"ok")

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    install_models._open_https("https://example.com/a", "GET", 30).close()
    assert len(attempts) == 2  # default rejected, operating-system store won
    install_models._open_https("https://example.com/b", "HEAD", 30).close()
    # The second call must reuse the winner directly, without re-probing.
    assert len(attempts) == 3
    assert attempts[2] is attempts[1]
    assert install_models._trusted_context[1] is attempts[-1]


def test_open_https_reprobes_sources_after_cached_winner_rejects(monkeypatch):
    """A trust source that worked for site A may reject site B (selective
    interception); the installer must probe the other sources again instead
    of failing with the cached one."""
    probed: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        probed.append(context)
        # Probes (1-based): A->default rejected, A->OS store accepted,
        # B->OS store (cached winner) rejected, B->default rejected,
        # B->certifi accepted.
        if len(probed) in (1, 3, 4):
            raise urllib.error.URLError(_cert_error())
        return FakeResponse(b"ok")

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    install_models._open_https("https://a.example/file", "GET", 30).close()
    assert install_models._trusted_context[0] == "operating-system certificates (truststore)"
    install_models._open_https("https://b.example/file", "GET", 30).close()
    assert len(probed) == 5
    assert install_models._trusted_context[0] == "bundled certifi certificates"


def test_open_https_does_not_retry_plain_http_errors(monkeypatch):
    attempts: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(context)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        install_models._open_https("https://example.com/file", "GET", 30)
    assert len(attempts) == 1  # server answered; trust sources are irrelevant


def test_open_https_does_not_retry_connectivity_errors(monkeypatch):
    attempts: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(context)
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.URLError):
        install_models._open_https("https://example.com/file", "GET", 30)
    assert len(attempts) == 1  # not a certificate problem; other sources cannot help


def test_open_https_reports_guidance_when_every_source_fails(monkeypatch):
    def fake_urlopen(request, timeout, context):
        raise urllib.error.URLError(_cert_error())

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        install_models._open_https("https://example.com/file", "GET", 30)
    message = str(excinfo.value)
    assert "antivirus" in message
    assert "SC_INSECURE_TLS" in message
    assert "Missing Authority Key Identifier" in message


def test_download_recovers_from_transient_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "MODELS", tmp_path)
    monkeypatch.setattr(install_models, "remote_sha256",
                        lambda url, user_agent=install_models.USER_AGENT: None)
    sleeps: list[float] = []
    monkeypatch.setattr(install_models.time, "sleep", sleeps.append)
    opens: list[str] = []

    def fake_open(url, method, timeout, user_agent=install_models.USER_AGENT):
        opens.append(method)
        if len(opens) == 1:
            raise urllib.error.URLError(ConnectionResetError("reset by peer"))
        return FakeResponse(b"verified-payload")

    monkeypatch.setattr(install_models, "_open_https", fake_open)

    digest = install_models.download("https://example.com/model.bin",
                                     tmp_path / "model.bin", {})
    assert (tmp_path / "model.bin").read_bytes() == b"verified-payload"
    assert not (tmp_path / "model.bin.part").exists()
    assert opens == ["GET", "GET"]
    assert sleeps  # backed off between attempts
    assert digest  # SHA-256 of the payload was returned


def test_download_does_not_retry_tls_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "MODELS", tmp_path)
    monkeypatch.setattr(install_models, "remote_sha256",
                        lambda url, user_agent=install_models.USER_AGENT: None)
    monkeypatch.setattr(install_models.time, "sleep", lambda seconds: None)
    opens: list[str] = []
    native_calls: list[str] = []

    def fake_open(url, method, timeout, user_agent=install_models.USER_AGENT):
        opens.append(method)
        raise RuntimeError("TLS certificate verification failed for every trust source")

    def fake_native(url, dest, user_agent=install_models.USER_AGENT):
        native_calls.append(url)
        raise RuntimeError("system-native downloaders ... were also tried and failed")

    monkeypatch.setattr(install_models, "_open_https", fake_open)
    monkeypatch.setattr(install_models, "_native_download", fake_native)

    with pytest.raises(RuntimeError):
        install_models.download("https://example.com/model.bin", tmp_path / "model.bin", {})
    assert opens == ["GET"]  # guidance failure is final; no pointless retries
    assert native_calls == ["https://example.com/model.bin"]  # native path attempted once
    assert not list(tmp_path.iterdir())  # partial file cleaned up


def test_download_survives_intercepted_tls_end_to_end(monkeypatch, tmp_path):
    """The exact reported failure: default OpenSSL rejects a re-signed chain,
    the operating-system store accepts it, and the file still checksum-verifies."""
    import hashlib

    monkeypatch.setattr(install_models, "MODELS", tmp_path)
    body = b"piper-onnx-weights" * 4096
    digest = hashlib.sha256(body).hexdigest()
    probed: list[ssl.SSLContext] = []

    def fake_urlopen(request, timeout, context):
        probed.append(context)
        if len(probed) == 1:
            raise urllib.error.URLError(_cert_error())
        headers = {"Content-Length": str(len(body))}
        if request.method == "HEAD":
            headers["x-linked-etag"] = f'"{digest}"'
            return FakeResponse(b"")
        return FakeResponse(body, headers)

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)

    result = install_models.download(
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/x.onnx",
        tmp_path / "x.onnx", {})
    assert result == digest
    assert (tmp_path / "x.onnx").read_bytes() == body
    # HEAD (remote checksum) and GET each probed only the default source once,
    # then settled on the OS-store context for every later request.
    assert probed[1] is probed[-1] and probed[1] is not probed[0]


def test_download_survives_when_only_native_downloader_works(monkeypatch, tmp_path):
    """Every Python trust source rejects the chain (strict OpenSSL vs. the
    antivirus), but the OS's own TLS engine accepts it; the download then
    completes through the native transport and still checksum-verifies."""
    monkeypatch.setattr(install_models, "MODELS", tmp_path)
    monkeypatch.setattr(install_models, "remote_sha256",
                        lambda url, user_agent=install_models.USER_AGENT: None)
    native_calls: list[tuple[str, Path]] = []

    def rejected(url, method, timeout, user_agent=install_models.USER_AGENT):
        raise RuntimeError("TLS certificate verification failed for every trust source")

    def fake_native(url, dest, user_agent=install_models.USER_AGENT):
        native_calls.append((url, dest))
        dest.write_bytes(b"native-transport-payload")

    monkeypatch.setattr(install_models, "_open_https", rejected)
    monkeypatch.setattr(install_models, "_native_download", fake_native)

    digest = install_models.download("https://example.com/voice.onnx",
                                     tmp_path / "voice.onnx", {})
    assert native_calls and native_calls[0][0] == "https://example.com/voice.onnx"
    assert native_calls[0][1].name == "voice.onnx.part"  # written via the .part file
    assert (tmp_path / "voice.onnx").read_bytes() == b"native-transport-payload"
    assert not (tmp_path / "voice.onnx.part").exists()
    assert digest == hashlib.sha256(b"native-transport-payload").hexdigest()


def test_remote_sha256_verifies_via_native_head_when_python_tls_fails(monkeypatch):
    digest = "ab" * 32

    def rejected(url, method, timeout, user_agent=install_models.USER_AGENT):
        raise RuntimeError("TLS certificate verification failed")

    monkeypatch.setattr(install_models, "_open_https", rejected)
    monkeypatch.setattr(install_models, "_native_head",
                        lambda url, user_agent=None: {"x-linked-etag": f'"sha256:{digest}"'})
    assert install_models.remote_sha256("https://example.com/m.onnx") == digest

    monkeypatch.setattr(install_models, "_native_head",
                        lambda url, user_agent=None: {})
    assert install_models.remote_sha256("https://example.com/m.onnx") is None


def test_native_download_tries_curl_then_powershell(monkeypatch, tmp_path):
    order: list[str] = []
    monkeypatch.setattr(install_models, "_find_curl",
                        lambda: "C:\\Windows\\System32\\curl.exe")
    monkeypatch.setattr(install_models.shutil, "which",
                        lambda name: "powershell.exe" if name == "powershell" else None)

    def fake_run(command, **kwargs):
        tool = "curl" if "curl" in command[0].lower() else "powershell"
        order.append(tool)
        if tool == "curl":
            return subprocess.CompletedProcess(command, 35, "", "curl: (35) SSL connect error")
        (tmp_path / "f.bin").write_bytes(b"downloaded-by-powershell")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(install_models.subprocess, "run", fake_run)
    install_models._native_download("https://example.com/f.bin", tmp_path / "f.bin")
    assert order == ["curl", "powershell"]
    assert (tmp_path / "f.bin").read_bytes() == b"downloaded-by-powershell"


def test_native_download_reports_guidance_when_no_tool_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "_find_curl", lambda: None)
    monkeypatch.setattr(install_models.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as excinfo:
        install_models._native_download("https://example.com/f.bin", tmp_path / "f.bin")
    message = str(excinfo.value)
    assert "antivirus" in message
    assert "SC_INSECURE_TLS" in message
    assert "system-native downloaders" in message
    assert not (tmp_path / "f.bin").exists()


def test_native_http_refusal_is_not_masked_as_tls_problem(monkeypatch, tmp_path):
    attempted: list[str] = []
    monkeypatch.setattr(install_models, "_find_curl", lambda: "curl")

    def fake_run(command, **kwargs):
        attempted.append(command[0])
        return subprocess.CompletedProcess(command, 22, "", "The requested URL returned error: 404")

    monkeypatch.setattr(install_models.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as excinfo:
        install_models._native_download("https://example.com/missing.onnx",
                                        tmp_path / "missing.onnx")
    assert attempted == ["curl"]  # PowerShell cannot fix a 404; never attempted
    assert "exit code 22" in str(excinfo.value)
    assert not (tmp_path / "missing.onnx").exists()


def test_curl_download_command_flags(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(install_models, "_find_curl",
                        lambda: "C:\\Windows\\System32\\curl.exe")

    def fake_run(command, **kwargs):
        seen["command"] = command
        (tmp_path / "f.bin").write_bytes(b"x")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(install_models.subprocess, "run", fake_run)
    install_models._curl_download("https://example.com/f.bin", tmp_path / "f.bin")
    command = seen["command"]
    assert command[0] == "C:\\Windows\\System32\\curl.exe"
    for flag in ("-L", "--fail", "-S", "--progress-bar", "--retry",
                 "-A", install_models.USER_AGENT, "-o"):
        assert flag in command
    assert str(tmp_path / "f.bin") in command
    assert command[-1] == "https://example.com/f.bin"
    if os.name == "nt":  # Schannel revocation checks break behind interceptors
        assert "--ssl-no-revoke" in command


def test_parse_header_blocks_keeps_huggingface_checksum_from_first_hop():
    digest = "ab" * 32
    raw = ("HTTP/2 302\r\n"
           f"x-linked-etag: \"sha256:{digest}\"\r\n"
           "location: https://cdn.example/file?token=1\r\n"
           "\r\n"
           "HTTP/2 200\r\n"
           "etag: W/\"weak-etag\"\r\n"
           "content-length: 12345\r\n"
           "\r\n")
    headers = install_models._parse_header_blocks(raw)
    assert headers["x-linked-etag"] == f'"sha256:{digest}"'
    assert headers["content-length"] == "12345"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is required")
def test_native_download_transfers_real_bytes_over_local_http(tmp_path):
    """End-to-end mechanics of the native path: a real curl subprocess pulls
    a real HTTP payload into the destination file."""
    payload = b"native-download-payload" * 512

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/model.onnx"
        dest = tmp_path / "model.onnx"
        install_models._native_download(url, dest)
        assert dest.read_bytes() == payload
    finally:
        server.shutdown()
        thread.join(timeout=5)

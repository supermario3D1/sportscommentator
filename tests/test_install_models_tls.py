"""TLS fallback behaviour of the model installer.

Interception software (antivirus web shields, corporate proxies) re-signs
certificates in ways strict OpenSSL rejects ("Missing Authority Key
Identifier"); the installer must retry with friendlier trust sources before
giving up with actionable guidance.
"""
from __future__ import annotations

import ssl
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
    monkeypatch.setattr(install_models, "remote_sha256", lambda url: None)
    sleeps: list[float] = []
    monkeypatch.setattr(install_models.time, "sleep", sleeps.append)
    opens: list[str] = []

    def fake_open(url, method, timeout):
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
    monkeypatch.setattr(install_models, "remote_sha256", lambda url: None)
    monkeypatch.setattr(install_models.time, "sleep", lambda seconds: None)
    opens: list[str] = []

    def fake_open(url, method, timeout):
        opens.append(method)
        raise RuntimeError("TLS certificate verification failed for every trust source")

    monkeypatch.setattr(install_models, "_open_https", fake_open)

    with pytest.raises(RuntimeError):
        install_models.download("https://example.com/model.bin", tmp_path / "model.bin", {})
    assert opens == ["GET"]  # guidance failure is final; no pointless retries
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

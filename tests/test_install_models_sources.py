"""Multi-source downloading and pinned-checksum enforcement.

Networks filtered by antivirus or corporate web security answer model hosts
with '403 Forbidden' even after TLS succeeds. The installer must move
through mirrors, never retry a refusal pointlessly, and accept whatever
source delivers only bytes that match the pinned official digests.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import urllib.error

import pytest

import install_models


def _body_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "MODELS", tmp_path)
    monkeypatch.setattr(install_models, "PIPER", tmp_path / "piper")
    monkeypatch.setattr(install_models, "_trusted_context", None)
    monkeypatch.setattr(install_models, "PINNED", {})  # tests pin explicitly
    monkeypatch.setattr(install_models, "remote_sha256", lambda url, ua=None: None)
    monkeypatch.setattr(install_models.time, "sleep", lambda seconds: None)
    return tmp_path


def _fake_stream(body: bytes, calls: list | None = None):
    def stream(url, dest, user_agent=install_models.USER_AGENT):
        if calls is not None:
            calls.append((url, user_agent))
        dest.write_bytes(body)
    return stream


def test_pinned_sha256_rejects_mismatched_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "PINNED", {
        "model.onnx": {"sha256": _body_digest(b"official-bytes")}})
    monkeypatch.setattr(install_models, "_stream_to_file",
                        _fake_stream(b"tampered-bytes"))
    with pytest.raises(RuntimeError, match="pinned official SHA-256"):
        install_models.download("https://mirror.example/model.onnx",
                                tmp_path / "model.onnx", {})
    assert not (tmp_path / "model.onnx").exists()  # tainted copy discarded
    assert not (tmp_path / "model.onnx.part").exists()


def test_pinned_sha256_accepts_matching_bytes_from_any_mirror(monkeypatch, tmp_path):
    body = b"official-bytes" * 100
    monkeypatch.setattr(install_models, "PINNED", {
        "model.onnx": {"sha256": _body_digest(body)}})
    monkeypatch.setattr(install_models, "_stream_to_file", _fake_stream(body))
    digest = install_models.download("https://hf-mirror.example/model.onnx",
                                     tmp_path / "model.onnx", {})
    assert digest == _body_digest(body)


def test_git_blob_pin_verifies_json_configs(monkeypatch, tmp_path):
    config = json.dumps({"audio": {"sample_rate": 22050}}).encode()
    monkeypatch.setattr(install_models, "PINNED", {
        "voice.onnx.json": {"git_sha1": _git_blob_sha1(config), "size": len(config)}})
    monkeypatch.setattr(install_models, "_stream_to_file", _fake_stream(config))
    install_models.download("https://mirror.example/voice.onnx.json",
                            tmp_path / "voice.onnx.json", {})
    # A modified config must be refused even though it is valid JSON.
    (tmp_path / "voice.onnx.json").unlink()
    monkeypatch.setattr(install_models, "_stream_to_file",
                        _fake_stream(json.dumps({"audio": {}}).encode()))
    with pytest.raises(RuntimeError, match="pinned official git checksum"):
        install_models.download("https://mirror.example/voice.onnx.json",
                                tmp_path / "voice.onnx.json", {})
    assert not (tmp_path / "voice.onnx.json").exists()


def test_http_refusals_are_final_not_retried(monkeypatch, tmp_path):
    attempts: list[str] = []

    def refused(url, dest, user_agent=install_models.USER_AGENT):
        attempts.append(url)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(install_models, "_stream_to_file", refused)
    with pytest.raises(urllib.error.HTTPError):
        install_models.download("https://hf.example/voice.onnx",
                                tmp_path / "voice.onnx", {})
    assert attempts == ["https://hf.example/voice.onnx"]  # no 3x pointless retry
    assert not list(tmp_path.iterdir())


def test_http_throttling_is_retried(monkeypatch, tmp_path):
    body = b"payload"
    attempts: list[str] = []

    def flaky(url, dest, user_agent=install_models.USER_AGENT):
        attempts.append(url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        dest.write_bytes(body)

    monkeypatch.setattr(install_models, "_stream_to_file", flaky)
    digest = install_models.download("https://hf.example/voice.onnx",
                                     tmp_path / "voice.onnx", {})
    assert len(attempts) == 2
    assert digest == _body_digest(body)


def test_existing_pinned_file_is_reused_or_replaced(monkeypatch, tmp_path):
    good = b"official-bytes"
    monkeypatch.setattr(install_models, "PINNED", {
        "model.onnx": {"sha256": _body_digest(good)}})
    target = tmp_path / "model.onnx"
    target.write_bytes(b"corrupt")
    monkeypatch.setattr(install_models, "_stream_to_file", _fake_stream(good))
    install_models.download("https://hf.example/model.onnx", target, {})
    assert target.read_bytes() == good  # corrupt copy replaced, not kept


def test_user_agent_reaches_the_request(monkeypatch, tmp_path):
    seen: list[tuple[str, str]] = []
    request_headers: dict[str, str] = {}

    def fake_urlopen(request, timeout, context):
        request_headers.update(request.headers)
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(install_models.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        install_models.download("https://hf.example/v.onnx", tmp_path / "v.onnx", {},
                                user_agent=install_models.BROWSER_USER_AGENT)
    assert request_headers["User-agent"] == install_models.BROWSER_USER_AGENT


def test_default_voices_are_covered_by_the_github_tarballs():
    assert set(install_models.DEFAULT_VOICES) <= set(install_models.PIPER_TARBALLS)
    assert len(install_models.DEFAULT_VOICES) == 2


def test_install_voice_moves_to_next_mirror_on_403(monkeypatch, tmp_path):
    model = b"m" * (11 * 1024 * 1024)  # above the truncation threshold
    config = json.dumps({"audio": {"sample_rate": 22050}}).encode()
    monkeypatch.setattr(install_models, "PINNED", {
        "piper/en_US-lessac-medium.onnx": {"sha256": _body_digest(model)},
        "piper/en_US-lessac-medium.onnx.json": {
            "git_sha1": _git_blob_sha1(config), "size": len(config)},
    })
    monkeypatch.setattr(install_models, "PIPER_MIRRORS", (
        ("blocked-hf", "https://blocked.example/resolve/main", install_models.USER_AGENT),
        ("working-mirror", "https://mirror.example/resolve/main", install_models.USER_AGENT),
    ))
    calls: list[str] = []

    def fake_download(url, target, known, user_agent=install_models.USER_AGENT):
        calls.append(url)
        if "blocked.example" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        stem = str(target.name).removesuffix(".json")
        body = model if target.name.endswith(".onnx") else config
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        install_models._verify_pinned(target, str(target.relative_to(tmp_path)))
        return _body_digest(body)

    monkeypatch.setattr(install_models, "download", fake_download)
    hashes: dict[str, str] = {}
    install_models._install_voice("en_US-lessac-medium", {}, hashes)
    assert (tmp_path / "piper" / "en_US-lessac-medium.onnx").read_bytes() == model
    assert (tmp_path / "piper" / "en_US-lessac-medium.onnx.json").read_bytes() == config
    assert len(hashes) == 2
    # The blocked host refused the first file and the whole mirror was
    # skipped; the working mirror then delivered both files.
    assert sum("blocked.example" in url for url in calls) == 1
    assert sum("mirror.example" in url for url in calls) == 2


def test_install_voice_reports_manual_fallback_when_all_sources_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(install_models, "PIPER_MIRRORS", (
        ("blocked-hf", "https://blocked.example/resolve/main", install_models.USER_AGENT),))
    monkeypatch.setattr(install_models, "PIPER_TARBALLS", {})  # e.g. en_GB voices

    def refused(url, target, known, user_agent=install_models.USER_AGENT):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(install_models, "download", refused)
    with pytest.raises(RuntimeError) as excinfo:
        install_models._install_voice("en_GB-alan-medium", {}, {})
    message = str(excinfo.value)
    assert "403" in message or "Forbidden" in message
    assert "models/piper" in message          # manual placement instructions
    assert "blocked-hf" in message            # every attempted source listed


def test_install_voice_recovers_from_github_tarball(monkeypatch, tmp_path):
    import io
    model = b"m" * (11 * 1024 * 1024)
    config = json.dumps({"audio": {"sample_rate": 22050}}).encode()
    monkeypatch.setattr(install_models, "PINNED", {
        "piper/en_US-lessac-medium.onnx": {"sha256": _body_digest(model)},
        "piper/en_US-lessac-medium.onnx.json": {
            "git_sha1": _git_blob_sha1(config), "size": len(config)},
    })
    monkeypatch.setattr(install_models, "PIPER_MIRRORS", (
        ("blocked-hf", "https://blocked.example/resolve/main", install_models.USER_AGENT),))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:  # dashed inner names, like piper
        for name, data in (("en-us-lessac-medium.onnx", model),
                           ("en-us-lessac-medium.onnx.json", config),
                           ("etc/sample.wav", b"RIFF-sample")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tarball = buffer.getvalue()

    def fake_download(url, target, known, user_agent=install_models.USER_AGENT):
        if "tar.gz" in url:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tarball)
            return _body_digest(tarball)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(install_models, "download", fake_download)
    monkeypatch.setattr(install_models, "PIPER_TARBALLS", {
        "en_US-lessac-medium": "https://github.example/voice-en-us-lessac-medium.tar.gz"})
    hashes: dict[str, str] = {}
    install_models._install_voice("en_US-lessac-medium", {}, hashes)
    assert (tmp_path / "piper" / "en_US-lessac-medium.onnx").read_bytes() == model
    assert (tmp_path / "piper" / "en_US-lessac-medium.onnx.json").read_bytes() == config
    assert len(hashes) == 2
    assert not (tmp_path / "downloads").exists() or not any(
        (tmp_path / "downloads").iterdir())  # staging cleaned up


def test_install_voice_tarball_rejects_unpinnable_contents(monkeypatch, tmp_path):
    import io
    model = b"different-training-run" * 1024
    config = b"{}"
    monkeypatch.setattr(install_models, "PINNED", {
        "piper/en_US-lessac-medium.onnx": {"sha256": _body_digest(b"expected-official")},
        "piper/en_US-lessac-medium.onnx.json": {
            "git_sha1": _git_blob_sha1(config), "size": len(config)},
    })
    monkeypatch.setattr(install_models, "PIPER_MIRRORS", ())
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in (("en-us-lessac-medium.onnx", model),
                           ("en-us-lessac-medium.onnx.json", config)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    def fake_download(url, target, known, user_agent=install_models.USER_AGENT):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(buffer.getvalue())
        return _body_digest(buffer.getvalue())

    monkeypatch.setattr(install_models, "download", fake_download)
    monkeypatch.setattr(install_models, "PIPER_TARBALLS", {
        "en_US-lessac-medium": "https://github.example/voice.tar.gz"})
    with pytest.raises(RuntimeError, match="pinned official"):
        install_models._install_voice("en_US-lessac-medium", {}, {})
    assert not (tmp_path / "piper" / "en_US-lessac-medium.onnx").exists()


def test_ollama_pull_failure_names_the_network_cause(monkeypatch):
    import subprocess as real_subprocess
    monkeypatch.setattr(install_models.shutil, "which", lambda name: "ollama")
    monkeypatch.setattr(install_models, "ollama_ready", lambda: True)

    def fake_run(command, **kwargs):
        assert command[1:] == ["pull", "phi3:mini"]
        raise real_subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(install_models.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="registry.ollama.ai"):
        install_models.install_ollama_models()


def test_optional_voice_failure_degrades_but_default_voice_is_required(monkeypatch, tmp_path):
    """With all four voices requested, a blocked optional voice (no GitHub
    fallback) must not abort setup; a blocked default voice must."""
    blocked = {"en_GB-alba-medium"}

    def fake_install(name, known, hashes):
        if name in blocked:
            raise RuntimeError("blocked")
        hashes[f"piper/{name}.onnx"] = "digest"

    monkeypatch.setattr(install_models, "_install_voice", fake_install)

    hashes: dict[str, str] = {}
    install_models.install_voices({}, hashes, all_voices=True)  # alba fails, tolerated
    assert "piper/en_GB-alan-medium.onnx" in hashes
    assert "piper/en_GB-alba-medium.onnx" not in hashes

    blocked.add("en_US-lessac-medium")
    with pytest.raises(RuntimeError):
        install_models.install_voices({}, {}, all_voices=False)  # lessac is required

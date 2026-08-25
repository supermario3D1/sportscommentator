"""Port resolution must never turn a busy port into a crash."""
from __future__ import annotations

import os
import socket

import pytest

from utils import networking
from utils.networking import (DEFAULT_PORT, PortChoice, display_host,
                              find_free_port, is_our_interface, pick_port,
                              port_busy, public_url)


@pytest.fixture()
def occupied() -> int:
    """Bind a real listener and yield its port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        yield int(server.getsockname()[1])


def test_free_port_is_used_unchanged():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        free = int(server.getsockname()[1])
    choice = pick_port("0.0.0.0", free)
    assert choice.port == free
    assert choice.error is None
    assert choice.messages == []


def test_busy_foreign_port_falls_back_with_an_explanation(occupied, monkeypatch):
    monkeypatch.setattr(networking, "read_server_config", lambda *a, **k: None)
    choice = pick_port("0.0.0.0", occupied)
    assert choice.port is not None and choice.port != occupied
    assert choice.running_url is None
    assert f"port {choice.port} instead" in choice.messages[0].lower()


def test_busy_port_serving_this_interface_is_reused(occupied, monkeypatch):
    monkeypatch.setattr(networking, "read_server_config",
                        lambda *a, **k: {"title": "AI Sports Commentator"})
    choice = pick_port("0.0.0.0", occupied)
    assert choice.port is None
    assert choice.running_url == f"http://localhost:{occupied}"
    assert "already running" in choice.messages[0].lower()
    assert choice.usable  # not an error: the user can open the existing URL


def test_strict_mode_refuses_to_move(occupied, monkeypatch):
    monkeypatch.setattr(networking, "read_server_config", lambda *a, **k: None)
    choice = pick_port("0.0.0.0", occupied, strict=True)
    assert choice.port is None and choice.running_url is None
    assert choice.error is not None
    assert str(occupied) in choice.error
    assert not choice.usable


def test_port_zero_asks_the_operating_system():
    choice = pick_port("0.0.0.0", 0)
    assert isinstance(choice.port, int) and 1024 <= choice.port <= 65535
    assert not port_busy("0.0.0.0", choice.port)


def test_exhausted_scan_reports_an_actionable_error(monkeypatch):
    monkeypatch.setattr(networking, "port_busy", lambda *a, **k: True)
    monkeypatch.setattr(networking, "read_server_config", lambda *a, **k: None)
    choice = pick_port("0.0.0.0", DEFAULT_PORT, limit=3)
    assert choice.port is None
    assert choice.error is not None
    assert "--port 0" in choice.error


def test_port_busy_detects_a_real_listener(occupied):
    assert port_busy("127.0.0.1", occupied)
    assert port_busy("0.0.0.0", occupied)


def test_reserved_and_out_of_range_ports_count_as_busy():
    assert port_busy("127.0.0.1", 0)
    assert port_busy("127.0.0.1", 65536)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can bind privileged ports")
def test_unbindable_privileged_port_counts_as_busy():
    # Without root, port 1 cannot be bound, so it must be treated as unusable
    # instead of being handed to Gradio (which would then fail to start).
    assert port_busy("127.0.0.1", 1)


def test_invalid_ports_count_as_busy():
    assert port_busy("0.0.0.0", 0)
    assert port_busy("0.0.0.0", 70000)


def test_find_free_port_skips_occupied(occupied):
    found = find_free_port("0.0.0.0", occupied, limit=10)
    assert found is not None and found > occupied


def test_urls_and_hosts_render_for_humans():
    assert public_url("0.0.0.0", 7860) == "http://localhost:7860"
    assert public_url("127.0.0.1", 7860) == "http://127.0.0.1:7860"
    assert public_url("192.168.1.20", 8000) == "http://192.168.1.20:8000"
    assert display_host("::") == "localhost"


def test_is_our_interface_ignores_other_gradio_apps(monkeypatch):
    monkeypatch.setattr(networking, "read_server_config", lambda *a, **k: {"title": "Stable Diffusion"})
    assert not is_our_interface("0.0.0.0", 7860)
    monkeypatch.setattr(networking, "read_server_config", lambda *a, **k: None)
    assert not is_our_interface("0.0.0.0", 7860)
    monkeypatch.setattr(networking, "read_server_config",
                        lambda *a, **k: {"root": {"props": {"title": "ai sports commentator"}}})
    assert is_our_interface("0.0.0.0", 7860)


def test_describe_port_owner_never_raises(occupied):
    # psutil is optional and needs privileges on Windows; both outcomes are fine.
    assert networking.describe_port_owner(occupied) is None or "PID" in str(
        networking.describe_port_owner(occupied)
    )


def test_port_choice_defaults_are_unusable():
    assert not PortChoice().usable

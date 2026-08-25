"""Local port discovery for the Gradio server.

The UI used to bind a hard-coded port and crashed with a raw ``OSError`` when
anything else already held it — most often a previous copy of this application
that was never closed.  These helpers answer three questions before launching:

1. Is the requested port free?
2. If it is busy, is the thing listening there already *this* interface?
3. If not, which nearby port can be used instead?

Nothing here raises: every probe returns ``None``/``False`` on failure so a
locked-down machine still gets a clear message instead of a traceback.
"""
from __future__ import annotations

import errno
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from utils.logger import setup_logger

LOG = setup_logger("network")

DEFAULT_PORT = 7860
#: How many consecutive ports are probed before giving up.
PORT_SCAN_LIMIT = 40
#: Title passed to ``gr.Blocks``; used to recognize our own running server.
APP_TITLE = "AI Sports Commentator"
WILDCARD_HOSTS = {"", "0.0.0.0", "*", "::", "[::]"}


@dataclass
class PortChoice:
    """Outcome of a port request: reuse, fallback, or an actionable error."""

    port: int | None = None
    running_url: str | None = None
    messages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.port is not None or self.running_url is not None


def _address_candidates(host: str) -> list[tuple[socket.AddressFamily, str]]:
    """Addresses to probe for ``host``; wildcards also cover the loopback."""
    host = (host or "").strip()
    if host in {"::", "[::]"}:
        return [(socket.AF_INET6, "::"), (socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1")]
    if host in {"", "0.0.0.0", "*"}:
        return [(socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1")]
    if host.startswith("[") and host.endswith("]"):
        return [(socket.AF_INET6, host[1:-1])]
    if ":" in host:
        return [(socket.AF_INET6, host)]
    return [(socket.AF_INET, host)]


def loopback_for(host: str) -> str:
    """Address a local client would dial to reach a server bound to ``host``."""
    host = (host or "").strip()
    if host in WILDCARD_HOSTS:
        return "127.0.0.1"
    return host.strip("[]")


def display_host(host: str) -> str:
    """Hostname to show a user; wildcards read as ``localhost``."""
    return "localhost" if (host or "").strip() in WILDCARD_HOSTS else host.strip("[]")


def public_url(host: str, port: int) -> str:
    """Browser URL for a server bound to ``host``."""
    return f"http://{display_host(host)}:{port}"


def _listening(host: str, port: int, timeout: float = 0.4) -> bool:
    """True when something answers a TCP connection on host:port."""
    try:
        with socket.create_connection((loopback_for(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def _bindable(host: str, port: int) -> bool:
    """True when the kernel hands out host:port to a new listener."""
    checked = 0
    for family, address in _address_candidates(host):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                # No SO_REUSEADDR: on Windows it would allow binding a port that
                # is already in use and defeat the whole check.
                probe.bind((address, port))
        except OSError as exc:
            if exc.errno == errno.EAFNOSUPPORT:
                continue  # no IPv6 stack here; the remaining candidates still count
            return False
        checked += 1
    return checked > 0


def port_busy(host: str, port: int, timeout: float = 0.4) -> bool:
    """Conservative occupancy test: answered *or* refused counts as busy."""
    if not isinstance(port, int) or not 0 < port < 65536:
        return True
    return _listening(host, port, timeout) or not _bindable(host, port)


def os_assigned_port(host: str) -> int | None:
    """Let the operating system choose a free port (``--port 0``)."""
    for family, address in _address_candidates(host):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((address, 0))
                return int(probe.getsockname()[1])
        except OSError:
            continue
    return None


def find_free_port(host: str, start: int = DEFAULT_PORT,
                   limit: int = PORT_SCAN_LIMIT) -> int | None:
    """First free port at or above ``start``; ``None`` when the range is full."""
    start = max(1, int(start))
    last = min(65535, start + max(1, int(limit)) - 1)
    for port in range(start, last + 1):
        if not port_busy(host, port):
            return port
    return None


def read_server_config(host: str, port: int, timeout: float = 3.0) -> dict[str, Any] | None:
    """Fetch Gradio's ``/config`` document, or ``None`` when it is not Gradio."""
    url = f"{public_url(host, port)}/config"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (http://localhost only)
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def is_our_interface(host: str, port: int, timeout: float = 3.0) -> bool:
    """True when host:port already serves this application's Gradio UI."""
    config = read_server_config(host, port, timeout)
    if not config:
        return False
    titles = [str(config.get("title") or "")]
    root = config.get("root")
    if isinstance(root, dict):
        props = root.get("props")
        if isinstance(props, dict):
            titles.append(str(props.get("title") or ""))
    return any(APP_TITLE.lower() in title.lower() for title in titles)


def describe_port_owner(port: int) -> str | None:
    """Best-effort ``"name (PID 1234)"`` of the process listening on ``port``."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for connection in psutil.net_connections(kind="inet"):
            local = getattr(connection, "laddr", None)
            if not local or local.port != port:
                continue
            status = getattr(connection, "status", None)
            if status not in (None, psutil.CONN_LISTEN):
                continue
            if not connection.pid:
                return None
            try:
                return f"{psutil.Process(connection.pid).name()} (PID {connection.pid})"
            except Exception:  # psutil.NoSuchProcess / AccessDenied
                return f"PID {connection.pid}"
    except Exception:  # AccessDenied without administrator rights
        return None
    return None


def _owner_suffix(port: int) -> str:
    owner = describe_port_owner(port)
    return f" (held by {owner})" if owner else ""


def pick_port(host: str, requested: int = DEFAULT_PORT, *,
              strict: bool = False, limit: int = PORT_SCAN_LIMIT) -> PortChoice:
    """Resolve ``requested`` into a port that can actually be bound.

    A free port is used as-is.  A busy port that already serves this interface
    is reported for reuse.  Anything else falls through to the next free port
    unless ``strict`` was requested.
    """
    choice = PortChoice()

    if requested == 0:
        assigned = os_assigned_port(host)
        if assigned is None:
            choice.error = "The operating system refused to assign a free port."
            return choice
        choice.port = assigned
        choice.messages.append(f"Using operating-system assigned port {assigned}.")
        return choice

    if not port_busy(host, requested):
        choice.port = int(requested)
        return choice

    url = public_url(host, requested)
    if is_our_interface(host, requested):
        choice.running_url = url
        choice.messages.append(
            f"This interface is already running at {url}; no second copy was started."
        )
        return choice

    suffix = _owner_suffix(requested)
    if strict:
        choice.error = (
            f"Port {requested} is already in use{suffix}. Close the other program or the "
            f"previous window of this app, then start again. To keep going anyway use "
            f"--port 0 (pick any free port) or --port <number>."
        )
        return choice

    start = min(requested + 1, max(1, 65536 - limit))
    fallback = find_free_port(host, start, limit)
    if fallback is None:
        choice.error = (
            f"Ports {start}-{min(65535, start + limit - 1)} are all in use{suffix}. "
            f"Close the other copies of this application, free a port, or start again "
            f"with --port 0 to let Windows choose one."
        )
        return choice

    choice.port = fallback
    choice.messages.append(
        f"Port {requested} is already in use{suffix}; starting on port {fallback} instead."
    )
    return choice

from __future__ import annotations

import socket
from typing import Tuple

from .config import load_runtime_settings


def can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def resolve_server_binding(max_attempts: int = 25) -> Tuple[str, int]:
    settings = load_runtime_settings()
    host = settings["server_host"]
    preferred_port = int(settings["server_port"])
    auto_port = bool(settings["auto_port"])

    if can_bind(host, preferred_port):
        return host, preferred_port
    if not auto_port:
        raise RuntimeError(
            f"Configured port {preferred_port} is not available. "
            "Enable automatic port fallback or choose another port in the dashboard settings."
        )

    for offset in range(1, max_attempts + 1):
        candidate = preferred_port + offset
        if candidate > 65535:
            break
        if can_bind(host, candidate):
            return host, candidate

    raise RuntimeError(
        f"Could not find a free port starting at {preferred_port}. "
        "Adjust the configured port range in the dashboard settings."
    )

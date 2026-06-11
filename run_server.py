from __future__ import annotations

import socket

import uvicorn

from doc_automation.config import load_runtime_settings


def reserve_socket() -> tuple[socket.socket, str, int]:
    settings = load_runtime_settings()
    host = str(settings["server_host"])
    preferred_port = int(settings["server_port"])
    auto_port = bool(settings["auto_port"])
    max_attempts = 25

    for offset in range(max_attempts + 1):
        port = preferred_port + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
            sock.listen(2048)
            return sock, host, port
        except OSError:
            sock.close()
            if not auto_port:
                raise RuntimeError(
                    f"Configured port {preferred_port} is not available. "
                    "Choose another port in the dashboard settings or enable automatic port fallback."
                ) from None
            continue

    raise RuntimeError(
        f"Could not reserve a free port starting at {preferred_port}. "
        "Adjust the preferred port in the dashboard settings."
    )


if __name__ == "__main__":
    reserved_socket, host, port = reserve_socket()
    print(f"Starting dashboard on http://{host}:{port}")
    config = uvicorn.Config("main:app", host=host, port=port, reload=False, use_colors=False)
    server = uvicorn.Server(config)
    server.run(sockets=[reserved_socket])

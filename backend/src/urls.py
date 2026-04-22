"""Сборка WebSocket URL в том же виде, что и у proxy apollo."""


def build_websocket_url(base: str, port: int, path: str) -> str:
    """
    Для apollo-прокси: wss://host/.../proxy + /{port} + /path
    Пример: wss://example/proxy/8001/ws
    """
    p = path if path.startswith("/") else f"/{path}"
    b = base.rstrip("/")
    if b.startswith(("wss://", "ws://")):
        return f"{b}/{port}{p}"
    return f"ws://{b}:{port}{p}"

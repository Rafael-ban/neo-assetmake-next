"""Pure configuration helpers for the device HTTP MJPEG stream."""

DEFAULT_HOST = "192.168.137.2"
DEFAULT_STREAM_PORT = 80
DEFAULT_STREAM_PATH = "/api/v1/stream.mjpg"
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_INTERVAL = 2.0
FPS_WINDOW_SIZE = 30
CONNECT_TIMEOUT = 5


def build_stream_url(
    host: str = DEFAULT_HOST,
    stream_port: int = DEFAULT_STREAM_PORT,
) -> str:
    return f"http://{host}:{stream_port}{DEFAULT_STREAM_PATH}"

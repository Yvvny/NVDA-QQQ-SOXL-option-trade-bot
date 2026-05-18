import socket

from trading_bot.desktop import _available_port, _is_port_available


def test_available_port_returns_preferred_when_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        preferred = int(sock.getsockname()[1])

    assert _available_port(preferred) == preferred


def test_available_port_falls_back_when_preferred_is_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        busy_port = int(sock.getsockname()[1])

        assert _is_port_available(busy_port) is False
        fallback = _available_port(busy_port)

    assert fallback != busy_port
    assert fallback > 0

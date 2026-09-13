from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from radio_logger.config import UdpConfig

log = logging.getLogger(__name__)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[bytes, tuple[str, int]], None]):
        self.callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:  # type: ignore[override]
        try:
            self.callback(data, addr)
        except Exception:
            log.exception("UDP callback failed")


async def start_udp_listener(
    config: UdpConfig,
    callback: Callable[[bytes, tuple[str, int]], None],
) -> tuple[asyncio.DatagramTransport, str]:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    host = config.host
    port = config.port
    try:
        if config.multicast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
            mreq = socket.inet_aton(host) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            bound = f"multicast {host}:{port}"
        else:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
            bound = f"{host}:{port}"
        sock.setblocking(False)
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _Protocol(callback), sock=sock
        )
    except BaseException:
        sock.close()
        raise
    log.info("WSJT-X UDP listening on %s", bound)
    return transport, bound

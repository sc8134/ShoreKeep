from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


def assert_public_http_url(url: str) -> None:
    """Reject URLs pointing at loopback/private/link-local addresses (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only http and https URLs are supported")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith((".local", ".internal")):
        raise UnsafeURLError("URL targets a non-public host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURLError("URL resolves to a non-public address")

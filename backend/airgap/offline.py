"""Process-level offline guard (defence in depth on top of the internal Docker network).

``enforce_offline()`` sets the Hugging Face offline variables and patches socket connects so
that any connection to a non-private address raises ``EgressBlocked`` and is logged loudly.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any

log = logging.getLogger(__name__)

OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}
_original_connect = socket.socket.connect
_installed = False


class EgressBlocked(ConnectionError):
    pass


def is_internal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # unresolved hostname: connect() always receives an IP here
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if self.family in (socket.AF_INET, socket.AF_INET6):
        host = str(address[0])
        if not is_internal(host):
            log.critical("blocked outbound connection", extra={"destination": host})
            raise EgressBlocked(f"outbound connection to {host} blocked (air-gapped mode)")
    _original_connect(self, address)


def enforce_offline() -> None:
    global _installed
    for key, value in OFFLINE_ENV.items():
        os.environ[key] = value
    if not _installed:
        socket.socket.connect = _guarded_connect  # type: ignore[method-assign,assignment]
        _installed = True


def check_offline_env() -> list[str]:
    """Names of offline variables that are not set to 1."""
    return [k for k in OFFLINE_ENV if os.environ.get(k) != "1"]

"""Shared stdlib HTTP helper — no requests, no external deps."""
from __future__ import annotations

import ssl
import urllib.request

USER_AGENT = "FinField/0.1 (open financial facts; contact: develuse@gmail.com)"


def ssl_context() -> ssl.SSLContext:
    try:  # python.org macOS builds ship without system CA certs
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        return resp.read()

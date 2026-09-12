"""Protected web tools. Fail closed when offline or unconfigured."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


USER_AGENT = "PersistentWorkerAgent/1.0"


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_blocked_host(host: str) -> bool:
    """Fail closed: block loopback/private/link-local targets (incl. cloud
    metadata endpoints like 169.254.169.254) and anything that fails to
    resolve. Not a defense against DNS rebinding mid-connection — this tool
    already sits behind a human permission prompt for every call — but it
    stops the common case of pointing the agent at localhost/internal hosts.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host or host == "localhost":
        return True
    try:
        return _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            return True
    return False


def web_search(query: str, num_results: int = 5) -> dict[str, Any]:
    key = os.environ.get("TAVILY_API_KEY") or os.environ.get("SERPER_API_KEY")
    if not key:
        return {
            "ok": False,
            "error": "no_search_backend",
            "hint": "Set TAVILY_API_KEY or SERPER_API_KEY. Offline agents should use files instead.",
            "query": query,
        }
    # Minimal Tavily-shaped call; keep dependency-free.
    if os.environ.get("TAVILY_API_KEY"):
        payload = json.dumps({"api_key": key, "query": query, "max_results": num_results}).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            results = [
                {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in data.get("results", [])[:num_results]
            ]
            return {"ok": True, "results": results}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "unsupported_search_backend"}


def fetch_url(url: str, max_chars: int = 12000) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "only http(s) urls are allowed"}
    host = urllib.parse.urlsplit(url).hostname or ""
    if _is_blocked_host(host):
        return {"ok": False, "error": "url resolves to a private/local address; blocked"}
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final_host = urllib.parse.urlsplit(resp.geturl()).hostname or ""
            if final_host != host and _is_blocked_host(final_host):
                return {"ok": False, "error": "redirected to a private/local address; blocked"}
            raw = resp.read(max_chars * 2)
            content_type = resp.headers.get("Content-Type", "")
        text = raw.decode("utf-8", errors="replace")[:max_chars]
        return {"ok": True, "url": url, "content_type": content_type, "content": text}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc)}

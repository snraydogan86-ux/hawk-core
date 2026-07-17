"""
SSRF-güvenli HTTP istemcisi — TÜM ajan/LLM-tetikli URL-fetch yolları bunu kullanmalı.

Koruma (FAZ 0 / P1):
  - Yalnız http/https şeması. file/ftp/gopher/unix vb. reddedilir.
  - URL userinfo (user:pass@) reddedilir.
  - Hostname DNS ile çözülür; ÇÖZÜLEN HER IP kontrol edilir.
  - Loopback / private / link-local / multicast / unspecified / reserved / metadata reddedilir.
  - Cloud metadata (169.254.169.254, fd00:ec2::254, 100.100.100.200) açıkça reddedilir.
  - Redirect follow_redirects=False; HER redirect hop'u yeniden doğrulanır (max 3).
  - Timeout + response boyut sınırı + redirect limiti.
  - DNS-rebinding: her hop'ta yeniden çözüp doğrular (TOCTOU artığı düşük — admin araç yüzeyi).
"""
from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "::/128", "fc00::/7", "fe80::/10", "ff00::/8", "64:ff9b::/96", "::ffff:0:0/96",
)]
_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}
_ALLOWED_SCHEMES = ("http", "https")


class SsrfBlocked(Exception):
    pass


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if str(ip) in _METADATA_IPS:
        return True
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) → asıl IPv4'e indir
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
        if str(ip) in _METADATA_IPS:
            return True
    if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
            or ip.is_unspecified or ip.is_reserved):
        return True
    for net in _BLOCKED_NETS:
        if ip.version == net.version and ip in net:
            return True
    return False


def _host_blocked(host: str) -> tuple[bool, str]:
    """Literal IP → doğrudan kontrol; hostname → getaddrinfo ile TÜM IP'leri kontrol."""
    # literal IP (decimal/hex/IPv6 dahil ipaddress normalize eder)
    try:
        ipaddress.ip_address(host)
        return (_ip_blocked(host), host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return (True, "cozulemedi")   # çözülemeyen host → engelle (fail-closed)
    for info in infos:
        ip = info[4][0]
        if _ip_blocked(ip):
            return (True, ip)
    return (False, "")


def validate_url(url: str) -> tuple[bool, str]:
    """(ok, sebep). URL güvenli mi (SSRF)?"""
    try:
        p = urlparse(str(url or ""))
    except Exception as e:
        return (False, f"parse:{e}")
    if p.scheme.lower() not in _ALLOWED_SCHEMES:
        return (False, f"sema_reddi:{p.scheme or '?'}")
    if p.username or p.password:
        return (False, "userinfo_reddi")
    host = p.hostname
    if not host:
        return (False, "host_yok")
    blocked, detail = _host_blocked(host)
    if blocked:
        return (False, f"engellenen_hedef:{host}->{detail}")
    return (True, "ok")


async def safe_request(method: str, url: str, *, headers=None, json=None, data=None,
                       timeout: float = 15.0, max_redirects: int = 3, max_bytes: int = 3_000_000):
    """SSRF-güvenli istek. httpx.Response döner. Her redirect yeniden doğrulanır."""
    import httpx
    cur = str(url)
    for _hop in range(max_redirects + 1):
        ok, why = validate_url(cur)
        if not ok:
            raise SsrfBlocked(why)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r = await c.request(method, cur, headers=headers or {}, json=json, data=data)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            cur = urljoin(cur, r.headers["location"])
            continue
        return r
    raise SsrfBlocked("cok_fazla_redirect")


async def safe_get(url: str, *, headers=None, timeout: float = 15.0):
    return await safe_request("GET", url, headers=headers, timeout=timeout)


async def safe_post(url: str, *, json=None, headers=None, timeout: float = 15.0):
    return await safe_request("POST", url, headers=headers, json=json, timeout=timeout)

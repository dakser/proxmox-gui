"""Where an admin may point a cluster registration (F-13, SSRF).

Cluster registration makes the backend open TLS connections to an admin-chosen host:port. Even
for admins that is a server-side request primitive, so the target is constrained:

* the host must be a plain DNS name or IP literal (no URL, credentials, ports, whitespace);
* never loopback, link-local (incl. the cloud metadata address 169.254.169.254), unspecified,
  multicast or reserved addresses — neither literally nor after DNS resolution (every returned
  record is checked, which also defeats names that point at loopback);
* ports 443 or 1024-65535 only (no 22/25/80/3306/6379/... probing).

RFC 1918 / ULA addresses ARE allowed: that is where Proxmox lives. DNS rebinding between the
check and the later connect is not fully closable here; the pinned certificate fingerprint (D-20)
is the second line of defence.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket

_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_FORBIDDEN_NAMES = {"localhost", "ip6-localhost", "ip6-loopback", "metadata", "metadata.google.internal"}
_MAX_HOST = 253
#: Well-known service ports above 1023 that a Proxmox API is never served on (probe/relay abuse).
_DENY_PORTS = {1433, 1521, 2049, 2375, 2376, 3306, 3389, 5432, 6379, 6443, 9200, 11211, 27017}
_EXTRA_FORBIDDEN_NETS = (ipaddress.ip_network("fd00:ec2::/32"),)  # AWS IPv6 metadata


class TargetNotAllowed(ValueError):
    """The requested Proxmox target is not permitted."""


def _ip_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _EXTRA_FORBIDDEN_NETS if net.version == ip.version):
        return True
    return (
        ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
        or ip.is_reserved or (isinstance(ip, ipaddress.IPv4Address) and str(ip) == "255.255.255.255")
    )


def check_host_literal(host: str) -> str:
    """Syntactic + literal-address policy; returns the host or raises ``ValueError``."""
    if not isinstance(host, str) or not host or len(host) > _MAX_HOST:
        raise ValueError("Host must be a hostname or IP address")
    if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in host):
        raise ValueError("Host contains invalid characters")
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    # An IPv6 literal is the only thing allowed to contain ':'.
    if ":" in candidate:
        try:
            ip6 = ipaddress.IPv6Address(candidate)
        except ValueError as exc:
            raise ValueError("Host is not a valid IPv6 address") from exc
        if _ip_forbidden(ip6):
            raise ValueError("That address is not allowed as a Proxmox target")
        return host
    if any(c in candidate for c in "/@?#\\[]%"):
        raise ValueError("Use a bare hostname or IP, not a URL")
    # Dotted-quad literals: strict decimal only (rejects 0177.0.0.1, 0x7f.0.0.1, 2130706433, 1.2.3).
    if re.fullmatch(r"[0-9.]+", candidate) or re.match(r"^0[xX]", candidate):
        try:
            ip4 = ipaddress.IPv4Address(candidate)
        except ValueError as exc:
            raise ValueError("Host is not a valid IPv4 address") from exc
        if _ip_forbidden(ip4):
            raise ValueError("That address is not allowed as a Proxmox target")
        return host
    lowered = candidate.lower()
    if lowered in _FORBIDDEN_NAMES or lowered.endswith((".localhost", ".local.")) or lowered.endswith("."):
        raise ValueError("That hostname is not allowed as a Proxmox target")
    labels = lowered.split(".")
    if not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("Host is not a valid hostname")
    return host


def check_port(port: int) -> int:
    if (port != 443 and not 1024 <= port <= 65535) or port in _DENY_PORTS or 5900 <= port <= 5999:
        raise ValueError("That port is not allowed for a Proxmox target (use 443 or 8006)")
    return port


async def check_target(host: str, port: int) -> None:
    """Resolve ``host`` and refuse if ANY record is a forbidden address. Unresolvable names pass
    (the connection attempt itself will fail)."""
    check_host_literal(host)
    check_port(port)
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
        return  # literal already checked
    except ValueError:
        pass
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, candidate, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return
    for _fam, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        except ValueError:
            continue
        if _ip_forbidden(ip):
            raise TargetNotAllowed("That host resolves to an address that is not allowed as a Proxmox target")

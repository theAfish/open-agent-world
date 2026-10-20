"""Command-scoped public-IPv4 TCP proxy for the macOS Seatbelt backend.

Seatbelt admits only this proxy's exact loopback ports. The proxy itself is a
trusted host component: it authenticates every client, resolves names itself,
checks the *resolved* IPv4 address, and dials that same numeric address. It
never uses process-global proxy settings or a parent proxy. Direct sockets and
UDP in the untrusted process remain denied by Seatbelt.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import secrets
import socket
from urllib.parse import urlsplit

from .linux_network import NON_PUBLIC_IPV4
from .macos_container_network import host_ipv4_addresses
from .models import SandboxNetworkError


_DENIED_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in NON_PUBLIC_IPV4)
_MAX_HEADER = 16 * 1024
_MAX_CLIENTS = 64
_CONNECT_TIMEOUT = 10


def _port(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError("invalid destination port")
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError("invalid destination port")
    return result


def _authority(value: str) -> tuple[str, int]:
    """Parse CONNECT host:port without accepting URL credentials or paths."""
    if not value or any(char in value for char in "/@?#\\ \t\r\n"):
        raise ValueError("invalid destination authority")
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or value[end + 1:end + 2] != ":":
            raise ValueError("invalid destination authority")
        return value[1:end], _port(value[end + 2:])
    if value.count(":") != 1:
        raise ValueError("invalid destination authority")
    host, port = value.rsplit(":", 1)
    if not host:
        raise ValueError("invalid destination authority")
    return host, _port(port)


def _allowed(address: str, host_addresses: set[str]) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return address not in host_addresses and not any(parsed in network for network in _DENIED_NETWORKS)


async def _destination(host: str, port: int) -> tuple[str, int]:
    """Resolve once; no DNS result is ever passed back to the dialer as a name."""
    if not host or any(char in host for char in "\x00/\\@#%"):
        raise ValueError("invalid destination hostname")
    try:
        numeric = ipaddress.ip_address(host)
    except ValueError:
        numeric = None
    if numeric is not None and numeric.version != 4:
        raise ValueError("IPv6 destinations are blocked")
    try:
        addresses = await asyncio.to_thread(host_ipv4_addresses)
    except SandboxNetworkError:
        raise
    host_set = set(addresses)
    if numeric is not None:
        candidate = str(numeric)
        if not _allowed(candidate, host_set):
            raise ValueError("non-public or host destination is blocked")
        return candidate, port
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid destination hostname") from exc
    if len(encoded) > 253:
        raise ValueError("destination hostname is too long")
    answers = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        encoded, port, family=socket.AF_INET, type=socket.SOCK_STREAM), _CONNECT_TIMEOUT)
    for answer in answers:
        candidate = answer[4][0]
        if _allowed(candidate, host_set):
            return candidate, port
    raise ValueError("destination has no permitted public IPv4 address")


async def _dial(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    address, checked_port = await _destination(host, port)
    return await asyncio.wait_for(asyncio.open_connection(
        address, checked_port, family=socket.AF_INET), _CONNECT_TIMEOUT)


async def _relay(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(65536):
            target.write(chunk)
            await target.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            target.write_eof()
        except (AttributeError, OSError, RuntimeError):
            pass


async def _tunnel(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter,
    remote_r: asyncio.StreamReader, remote_w: asyncio.StreamWriter) -> None:
    try:
        await asyncio.gather(_relay(client_r, remote_w), _relay(remote_r, client_w))
    finally:
        remote_w.close()
        await remote_w.wait_closed()


def _listen_pair() -> tuple[socket.socket, socket.socket, int]:
    """Reserve the same port on both loopback families before admitting work."""
    ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    ipv4: socket.socket | None = None
    try:
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind(("::1", 0))
        port = ipv6.getsockname()[1]
        ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ipv4.bind(("127.0.0.1", port))
        for listener in (ipv6, ipv4):
            listener.listen(64)
            listener.setblocking(False)
        return ipv6, ipv4, port
    except BaseException:
        ipv6.close()
        if ipv4 is not None:
            ipv4.close()
        raise


class SeatbeltProxySession:
    """Two authenticated local listeners owned by one Sandbox command."""

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self.http_port = 0
        self.socks_port = 0
        self._servers: list[asyncio.AbstractServer] = []
        self._clients: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._servers:
            raise RuntimeError("proxy session is already started")
        try:
            for protocol, handler in (("http", self._http), ("socks", self._socks)):
                ipv6, ipv4, port = _listen_pair()
                if protocol == "http":
                    self.http_port = port
                else:
                    self.socks_port = port
                for listener in (ipv6, ipv4):
                    try:
                        server = await asyncio.start_server(
                            lambda reader, writer, handle=handler: self._accept(handle, reader, writer),
                            sock=listener, limit=_MAX_HEADER + 1)
                    except BaseException:
                        listener.close()
                        raise
                    self._servers.append(server)
        except BaseException as exc:
            await self.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SandboxNetworkError(f"Cannot start Seatbelt network proxy: {exc}") from exc

    async def close(self) -> None:
        for server in self._servers:
            server.close()
        tasks = tuple(self._clients)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(server.wait_closed() for server in self._servers),
            return_exceptions=True)
        self._servers.clear()

    @property
    def ports(self) -> tuple[int, int]:
        if not self._servers:
            raise SandboxNetworkError("Seatbelt network proxy is not serving")
        return self.http_port, self.socks_port

    def environment(self) -> dict[str, str]:
        http = f"http://oaw:{self.token}@127.0.0.1:{self.http_port}"
        socks = f"socks5h://oaw:{self.token}@127.0.0.1:{self.socks_port}"
        return {"HTTP_PROXY": http, "HTTPS_PROXY": http, "ALL_PROXY": socks,
            "http_proxy": http, "https_proxy": http, "all_proxy": socks,
            "NO_PROXY": "", "no_proxy": ""}

    async def _accept(self, handler, reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None or len(self._clients) >= _MAX_CLIENTS:
            writer.close()
            await writer.wait_closed()
            return
        self._clients.add(task)
        try:
            await handler(reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            ConnectionError, OSError, TimeoutError, ValueError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            finally:
                self._clients.discard(task)

    async def _http(self, reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter) -> None:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        if len(header) > _MAX_HEADER:
            return
        lines = header[:-4].split(b"\r\n")
        first = lines[0].split(b" ")
        if len(first) != 3 or first[2] not in {b"HTTP/1.0", b"HTTP/1.1"}:
            return
        method, target, version = first
        auth = b""
        forwarded = []
        for line in lines[1:]:
            key, separator, value = line.partition(b":")
            if not separator or not key:
                return
            if key.strip().lower() == b"proxy-authorization":
                auth = value.strip()
            elif key.strip().lower() not in {b"proxy-connection", b"connection"}:
                forwarded.append(line)
        expected = b"Basic " + base64.b64encode(f"oaw:{self.token}".encode("ascii"))
        if not hmac.compare_digest(auth, expected):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b"Proxy-Authenticate: Basic realm=\"OAW Sandbox\"\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        try:
            if method.upper() == b"CONNECT":
                host, port = _authority(target.decode("ascii"))
                remote_r, remote_w = await _dial(host, port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                parsed = urlsplit(target.decode("ascii"))
                if parsed.scheme.lower() != "http" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("unsupported proxy request")
                port = parsed.port or 80
                remote_r, remote_w = await _dial(parsed.hostname, port)
                path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                remote_w.write(method + b" " + path.encode("ascii") + b" " + version + b"\r\n"
                    + b"\r\n".join(forwarded) + b"\r\nConnection: close\r\n\r\n")
                await remote_w.drain()
        except (OSError, TimeoutError, ValueError, SandboxNetworkError) as exc:
            code = b"403 Forbidden" if isinstance(exc, (ValueError, SandboxNetworkError)) else b"502 Bad Gateway"
            writer.write(b"HTTP/1.1 " + code + b"\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        await writer.drain()
        await _tunnel(reader, writer, remote_r, remote_w)

    async def _socks(self, reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter) -> None:
        version, count = await asyncio.wait_for(reader.readexactly(2), 5)
        if version != 5:
            return
        methods = await asyncio.wait_for(reader.readexactly(count), 5)
        if 2 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x02")
        await writer.drain()
        auth_version, username_length = await asyncio.wait_for(reader.readexactly(2), 5)
        username = await asyncio.wait_for(reader.readexactly(username_length), 5)
        password_length = (await asyncio.wait_for(reader.readexactly(1), 5))[0]
        password = await asyncio.wait_for(reader.readexactly(password_length), 5)
        if auth_version != 1 or not hmac.compare_digest(username, b"oaw") or not hmac.compare_digest(
            password, self.token.encode("ascii")):
            writer.write(b"\x01\x01")
            await writer.drain()
            return
        writer.write(b"\x01\x00")
        await writer.drain()
        version, command, reserved, address_type = await asyncio.wait_for(reader.readexactly(4), 5)
        if version != 5 or command != 1 or reserved != 0:
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        if address_type == 1:
            host = socket.inet_ntoa(await asyncio.wait_for(reader.readexactly(4), 5))
        elif address_type == 3:
            length = (await asyncio.wait_for(reader.readexactly(1), 5))[0]
            host = (await asyncio.wait_for(reader.readexactly(length), 5)).decode("ascii")
        else:
            writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        port = int.from_bytes(await asyncio.wait_for(reader.readexactly(2), 5), "big")
        try:
            if port == 0:
                raise ValueError("invalid destination port")
            remote_r, remote_w = await _dial(host, port)
        except (OSError, TimeoutError, ValueError, SandboxNetworkError):
            writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await _tunnel(reader, writer, remote_r, remote_w)

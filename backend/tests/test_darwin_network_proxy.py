"""Host-side tests for the command-scoped Seatbelt TCP proxy."""

import asyncio
import base64
from urllib.parse import urlsplit

import pytest

from backend.sandbox import darwin_network_proxy as proxy_module
from backend.sandbox.darwin_network_proxy import (
    SeatbeltProxySession, _allowed, _authority, _destination,
)


def test_address_policy_and_authority():
    assert _allowed("8.8.8.8", set())
    assert not _allowed("8.8.8.8", {"8.8.8.8"})
    for address in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "::1"):
        assert not _allowed(address, set())
    assert _authority("example.org:443") == ("example.org", 443)
    for bad in ("example.org:0", "example.org:65536", "a@b:443", "example.org:443/x"):
        with pytest.raises(ValueError):
            _authority(bad)


@pytest.mark.asyncio
async def test_destination_rejects_private_host_and_ipv6(monkeypatch):
    monkeypatch.setattr("backend.sandbox.darwin_network_proxy.host_ipv4_addresses",
        lambda: {"8.8.8.8"})
    for address in ("127.0.0.1", "10.0.0.1", "8.8.8.8", "::1"):
        with pytest.raises(ValueError):
            await _destination(address, 443)


@pytest.mark.asyncio
async def test_destination_filters_dns_answers_and_pins_numeric_address(monkeypatch):
    monkeypatch.setattr("backend.sandbox.darwin_network_proxy.host_ipv4_addresses",
        lambda: {"9.9.9.9"})

    async def answers(host, port, **kwargs):
        assert host == "example.org" and port == 443
        return [(0, 0, 0, "", ("127.0.0.1", 443)),
            (0, 0, 0, "", ("9.9.9.9", 443)),
            (0, 0, 0, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    assert await _destination("example.org", 443) == ("8.8.8.8", 443)


@pytest.mark.asyncio
async def test_http_auth_and_private_target_denial(monkeypatch):
    monkeypatch.setattr("backend.sandbox.darwin_network_proxy.host_ipv4_addresses", lambda: set())
    proxy = SeatbeltProxySession()
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.http_port)
        writer.write(b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org\r\n\r\n")
        await writer.drain()
        assert b"407 Proxy Authentication Required" in await asyncio.wait_for(reader.read(), 3)
        writer.close()
        await writer.wait_closed()

        credentials = base64.b64encode(f"oaw:{proxy.token}".encode())
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.http_port)
        writer.write(b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nProxy-Authorization: Basic "
            + credentials + b"\r\n\r\n")
        await writer.drain()
        assert b"403 Forbidden" in await asyncio.wait_for(reader.read(), 3)
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_socks_auth_and_private_target_denial(monkeypatch):
    monkeypatch.setattr("backend.sandbox.darwin_network_proxy.host_ipv4_addresses", lambda: set())
    proxy = SeatbeltProxySession()
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.socks_port)
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x02"
        writer.write(b"\x01\x03oaw\x03bad")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x01\x01"
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.socks_port)
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x02"
        token = proxy.token.encode()
        writer.write(b"\x01\x03oaw" + bytes((len(token),)) + token)
        await writer.drain()
        assert await reader.readexactly(2) == b"\x01\x00"
        writer.write(b"\x05\x01\x00\x01\x7f\x00\x00\x01\x00\x50")
        await writer.drain()
        assert (await reader.readexactly(10))[:2] == b"\x05\x02"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_http_and_socks_forward_through_authenticated_proxy(monkeypatch):
    async def echo(reader, writer):
        writer.write(await reader.read(100))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    remote = await asyncio.start_server(echo, "127.0.0.1", 0)
    remote_port = remote.sockets[0].getsockname()[1]

    async def fake_dial(host, port):
        assert (host, port) == ("example.org", 443)
        return await asyncio.open_connection("127.0.0.1", remote_port)

    monkeypatch.setattr(proxy_module, "_dial", fake_dial)
    proxy = SeatbeltProxySession()
    await proxy.start()
    try:
        credentials = base64.b64encode(f"oaw:{proxy.token}".encode())
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.http_port)
        writer.write(b"CONNECT example.org:443 HTTP/1.1\r\nProxy-Authorization: Basic "
            + credentials + b"\r\n\r\n")
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection Established\r\n\r\n"
        writer.write(b"hello")
        await writer.drain()
        assert await reader.readexactly(5) == b"hello"
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.socks_port)
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x02"
        token = proxy.token.encode()
        writer.write(b"\x01\x03oaw" + bytes((len(token),)) + token)
        await writer.drain()
        assert await reader.readexactly(2) == b"\x01\x00"
        writer.write(b"\x05\x01\x00\x03\x0bexample.org\x01\xbb")
        await writer.drain()
        assert (await reader.readexactly(10))[:2] == b"\x05\x00"
        writer.write(b"world")
        await writer.drain()
        assert await reader.readexactly(5) == b"world"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        remote.close()
        await remote.wait_closed()


def test_proxy_environment_uses_authenticated_local_ports():
    proxy = SeatbeltProxySession()
    proxy.http_port = 12345
    proxy.socks_port = 23456
    environment = proxy.environment()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        url = urlsplit(environment[key])
        assert (url.hostname, url.port, url.username, url.password) == (
            "127.0.0.1", 12345, "oaw", proxy.token)
    assert environment["ALL_PROXY"].startswith("socks5h://oaw:")
    assert environment["NO_PROXY"] == environment["no_proxy"] == ""

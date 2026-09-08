"""Disposable private-peer HTTP controls; no file serving or host operations."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import time
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.bind)
    if address.is_loopback or not address.is_private:
        raise ValueError("Bind a real private guest interface, not loopback")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if len(self.path) > 256 or len(requests) >= 2048:
                self.send_error(413)
                return
            if urlsplit(self.path).path == "/evidence":
                data = json.dumps(requests).encode()
            else:
                requests.append({"path": self.path, "peer": self.client_address[0]})
                print(json.dumps(requests[-1]), flush=True)
                data = b"OAW_PRIVATE_PEER_OK"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with HTTPServer((str(address), 0), Handler) as server:
        server.timeout = 0.5
        print(json.dumps({"url": f"http://{address}:{server.server_port}/check", "pid": __import__('os').getpid()}), flush=True)
        deadline = time.monotonic() + 3600
        try:
            while time.monotonic() < deadline:
                server.handle_request()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

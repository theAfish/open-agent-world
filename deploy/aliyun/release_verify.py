"""Trusted stdlib-only verification of the hosted worker wire protocol.

Run as the registered verification identity in the independently prepared sandbox.
No application source, Pack code, Python environment or hosting tree is imported.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import http.client
import json
import os
from pathlib import Path
import socket
import time


def worker_token(profile):
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
    now = int(time.time())
    payload = {'v': 1, 'profile_id': profile['profile_id'], 'user_id': profile['user_id'],
               'display_name': None, 'iat': now, 'exp': now + 30}
    body = encode(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
    signature = encode(hmac.digest(bytes.fromhex(profile['secret']),
        b'oaw-hosted-worker-v1\x00' + body.encode(), 'sha256'))
    return body + '.' + signature


def request_worker(path, route, token=None):
    connection = http.client.HTTPConnection('localhost', timeout=120)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(120)
    try:
        connection.sock.connect(path)
        connection.request('GET', route, headers={'Authorization': 'Bearer ' + token} if token else {})
        response = connection.getresponse()
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError('Worker verification response exceeds limits')
        return response.status, body
    finally:
        connection.close()


def verify(snapshot):
    selected = json.loads(snapshot.read_text(encoding='utf-8'))['profiles']
    results = []
    for profile in selected:
        path = '/workspace/sockets/' + profile['profile_id'] + '.sock'
        if request_worker(path, '/api/deployment')[0] != 401:
            raise ValueError('Worker admitted a request without gateway identity')
        status, body = request_worker(path, '/api/deployment', worker_token(profile))
        if status != 200 or json.loads(body)['mode'] != profile['mode']:
            raise ValueError('Worker startup or deployment mode check failed')
        route = '/api/runtime-app' if profile['mode'] == 'runtime' else '/api/card-library'
        if request_worker(path, route, worker_token(profile))[0] != 200:
            raise ValueError('Worker application bootstrap failed')
        if profile['mode'] == 'runtime' and request_worker(path, '/api/world', worker_token(profile))[0] != 404:
            raise ValueError('Runtime exposed its engineering canvas')
        results.append({'profile': profile['profile_id'], 'mode': profile['mode'], 'ready': True})
    return {'verified_profiles': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError('Release verification cannot run as root')
    print(json.dumps(verify(args.snapshot)), flush=True)


if __name__ == '__main__':
    main()

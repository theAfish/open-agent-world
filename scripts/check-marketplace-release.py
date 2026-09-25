"""Release gate: never ship an official Store build with an invented/unbound origin."""
import ipaddress
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.official_marketplace import OFFICIAL_MARKETPLACE_URL  # noqa: E402


def main():
    url = urlsplit(OFFICIAL_MARKETPLACE_URL or '')
    valid = (url.scheme == 'https' and url.hostname and not url.username and not url.password
             and not url.query and not url.fragment and url.path in ('', '/'))
    host = url.hostname or ''
    if host == 'localhost' or host.endswith(('.localhost', '.test', '.invalid', '.example')):
        valid = False
    try:
        if not ipaddress.ip_address(host).is_global:
            valid = False
    except ValueError:
        pass
    if not valid:
        raise SystemExit('Official Marketplace is unbound: deploy, run production smoke, then set backend/official_marketplace.py before a release.')
    print('Official Marketplace origin configured: ' + OFFICIAL_MARKETPLACE_URL)


if __name__ == '__main__':
    main()

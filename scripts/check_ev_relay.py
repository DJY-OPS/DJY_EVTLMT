"""Check configured ngrok/PC/ESP agreement without inserting vehicle samples.

Uses deliberately invalid JSON for the authenticated POST probe: authentication
must pass, then JSON parsing must reject it before any telemetry is stored.
No control commands, recording actions, or firmware writes are performed.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the private relay token to a redirected host.


def request(url, token=None, malformed=False):
    headers = {'ngrok-skip-browser-warning': '1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if malformed:
        headers['Content-Type'] = 'application/json'
    req = Request(url, headers=headers, data=b'not-json' if malformed else None)
    try:
        with build_opener(NoRedirect).open(req, timeout=4) as response:
            return response.status, response.headers.get('Content-Type', ''), response.read()
    except HTTPError as error:
        return error.code, error.headers.get('Content-Type', ''), error.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    settings_path = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'telemetry-tools/ev-relay.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig'))
    public = settings['public_url'].rstrip('/')
    parsed = urlsplit(public)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('Configure a valid public HTTPS origin first.')
    token = settings['relay_token']
    if len(token) < 16 or token.startswith('REPLACE_'):
        raise ValueError('Configure a private relay token first.')
    config = (ROOT / 'firmware/esp32_ev_gateway/include/config.h').read_text(encoding='utf-8-sig')
    def define(name):
        found = re.search(r'^\s*#define\s+' + re.escape(name) + r'\s+([^\r\n]+)', config, re.M)
        return found.group(1).strip() if found else ''
    if (define('EV_RELAY_URL') != json.dumps(public + '/api/vehicle/exchange')
            or define('EV_RELAY_TOKEN') != json.dumps(token)
            or define('EV_RELAY_ENABLED') != '1'):
        raise ValueError('ESP source settings differ from PC settings. Run configure_ev_relay.ps1.')
    print('PASS PC/ESP source URL and token match; relay is enabled.')
    failures = []
    def check(label, callback):
        try:
            callback()
            print('PASS ' + label, flush=True)
            return True
        except (ValueError, AssertionError, URLError, OSError) as error:
            failures.append(label)
            # Do not dump request headers or settings containing credentials.
            print('FAIL ' + label + ': ' + str(error), flush=True)
            return False
    def health(base):
        code, mime, body = request(base + '/health')
        if code != 200 or 'application/json' not in mime or json.loads(body).get('ok') is not True:
            raise ValueError(f'Unexpected health response: HTTP {code}')
    def auth(base, authorized):
        code, _, _ = request(base + '/api/vehicle/exchange', token if authorized else None, True)
        expected = 400 if authorized else 401
        if code != expected:
            raise ValueError(f'Expected HTTP {expected}, received {code}')
    bases = [('local', 'http://127.0.0.1:8766')]
    if not args.local_only:
        bases.append(('public', public))
    for label, base in bases:
        if not check(label + ' health', lambda: health(base)):
            print('SKIP remaining ' + label + ' checks: endpoint is unreachable.', flush=True)
            continue
        check(label + ' relay accepts configured token (invalid JSON rejected)', lambda: auth(base, True))
        check(label + ' relay rejects missing token', lambda: auth(base, False))
        for page in ('pit', 'battery', 'records', 'map', 'phone', 'driver', 'diagnostic'):
            def page_check():
                code, mime, body = request(base + '/' + page)
                if code != 200 or 'text/html' not in mime or b'<!doctype html' not in body.lower():
                    raise ValueError(f'Unexpected page response: HTTP {code}')
            check(base + '/' + page, page_check)
    if not args.local_only:
        def tunnel():
            code, _, body = request('http://127.0.0.1:4040/api/tunnels')
            if code != 200:
                raise ValueError('Local ngrok API unavailable')
            matches = [t for t in json.loads(body).get('tunnels', [])
                       if t.get('public_url', '').rstrip('/') == public
                       and urlsplit(t.get('config', {}).get('addr', '')).port == 8766]
            if not matches:
                raise ValueError('Configured public URL is not tunneled to port 8766')
        check('ngrok endpoint matches configured URL and port', tunnel)
    print('ESP flash contents and live vehicle delivery require the connected board.')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as error:
        print('FAIL configuration: ' + str(error), file=sys.stderr)
        sys.exit(1)

"""Isolated onboard HUD checks. No vehicle connection or production database."""
import math
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path.home() / 'telemetry-tools/browser-tests'))
sys.path.insert(0, str(ROOT / 'gateway'))
from playwright.sync_api import sync_playwright
from ev_gateway import EVGateway, make_handler


with tempfile.TemporaryDirectory() as folder:
    gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(folder) / 'test.db')
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f'http://127.0.0.1:{server.server_port}'
    packet = {'stm_online': True, 'rpm_left': 0, 'rpm_right': 0,
              'motor_left_ok': True, 'motor_right_ok': True, 'tps_ok': True, 'tps_pct': 0,
              'imu_ok': True, 'yaw_rate_rad_s': math.pi / 2,
              'lateral_accel_m_s2': 9.80665, 'longitudinal_accel_m_s2': -9.80665,
              'sas_deg': 0, 'sas_ok': True}
    out = ROOT / '.local'
    out.mkdir(exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            errors, writes = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: writes.append(r.url) if r.method != 'GET' else None)
            page.goto(root + '/onboard')
            gateway.store.update(packet)
            page.wait_for_function("document.querySelector('#speed').textContent === '0'")
            assert page.locator('#power').inner_text() == '—'
            assert page.locator('#lat').inner_text() == '1.00'
            assert page.locator('#long').inner_text() == '-1.00'
            assert page.locator('#yaw').inner_text() == '90.0'
            assert page.locator('#throttle').inner_text() == '0'
            assert page.locator('#gDot').get_attribute('cx') == '99'
            assert page.locator('#gDot').get_attribute('cy') == '99'
            gateway.store.update({**packet, 'vehicle_power_on': False})
            page.wait_for_function("document.querySelector('#power').textContent === 'OFF'")
            gateway.store.update({**packet, 'vehicle_power_on': True, 'rpm_left': 5000, 'rpm_right': 5200,
                                  'tps_pct': 80, 'lateral_accel_m_s2': 0, 'longitudinal_accel_m_s2': 0})
            page.wait_for_function("document.querySelector('#power').textContent === 'ON'")
            assert page.locator('#rpmLeftBar .lit').count() == 13
            assert page.locator('#throttleFill').evaluate('(e)=>e.style.height') == '80%'
            page.screenshot(path=str(out / 'onboard-live.png'), full_page=True)
            # An alive HTTP server cannot keep an expired sensor reading alive.
            page.wait_for_function("document.querySelector('#speed').textContent === '—'", timeout=5000)
            assert page.locator('#power').inner_text() == '—'
            assert page.locator('#gDot').get_attribute('visibility') == 'hidden'
            gateway.store.update({**packet, 'motor_left_ok': False, 'imu_ok': False})
            page.wait_for_function("document.querySelector('#rpmRight').textContent === '0'")
            assert page.locator('#rpmLeft').inner_text() == '—'
            assert page.locator('#speed').inner_text() == '—'
            assert page.locator('#yaw').inner_text() == '—'
            page.route('**/api/onboard', lambda route: route.abort())
            page.wait_for_function("document.querySelector('#link').textContent === 'GATEWAY DISCONNECTED'")
            assert page.locator('#rpmRight').inner_text() == '—'
            page.unroute('**/api/onboard')
            demo = browser.new_page(viewport={'width': 1920, 'height': 1080})
            demo_requests = []
            demo.on('request', lambda r: demo_requests.append(r.url))
            demo.on('pageerror', lambda e: errors.append(str(e)))
            demo.goto(root + '/onboard?demo=1')
            demo.wait_for_function("document.querySelector('#power').textContent === 'ON'")
            demo.wait_for_timeout(300)
            assert not any('/api/' in url for url in demo_requests), demo_requests
            assert demo.locator('#demoBadge').is_visible()
            demo.screenshot(path=str(out / 'onboard-desktop.png'), full_page=True)
            demo.set_viewport_size({'width': 390, 'height': 844})
            demo.wait_for_timeout(150)
            assert demo.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert demo.locator('.hud').bounding_box()['x'] >= 0
            demo.screenshot(path=str(out / 'onboard-mobile.png'), full_page=True)
            demo.goto(root + '/onboard?overlay=1&demo=1')
            assert demo.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
            assert demo.locator('.toolbar').is_hidden()
            assert demo.locator('#placeholder').is_hidden()
            assert demo.locator('#demoBadge').is_visible()
            demo.set_viewport_size({'width': 1920, 'height': 1080})
            demo.screenshot(path=str(out / 'onboard-overlay.png'), omit_background=True)
            # A local file stays local; an unsupported clip returns to the placeholder.
            demo.goto(root + '/onboard?demo=1')
            demo.locator('#videoFile').set_input_files({'name': 'unsupported.webm', 'mimeType': 'video/webm', 'buffer': b'unsupported'})
            demo.wait_for_function("document.querySelector('#video').error !== null", timeout=5000)
            assert demo.locator('#placeholder').is_visible()
            assert demo.locator('#play').is_disabled()
            assert '재생할 수 없습니다' in demo.locator('#placeholder p').inner_text()
            assert demo.locator('#video').get_attribute('src').startswith('blob:')
            assert not errors, errors
            assert not writes, writes
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        gateway.database.close()
        gateway.forward_socket.close()
print('ONBOARD PASS: live values, quality, stale/offline, explicit power, demo isolation, transparent overlay, mobile, local video failure handling')

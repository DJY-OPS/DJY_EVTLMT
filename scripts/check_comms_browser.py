"""Exercise real pit/driver HTTP communication on an isolated gateway."""
import json
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path.home() / 'telemetry-tools/browser-tests'))
sys.path.insert(0, str(ROOT / 'gateway'))
from playwright.sync_api import sync_playwright
from ev_gateway import EVGateway, make_handler

gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004)
server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(extra_http_headers={'X-Forwarded-For': '203.0.113.5'})
        pit = context.new_page()
        driver = context.new_page()
        errors = []
        for page in (pit, driver):
            page.on('pageerror', lambda e: errors.append(str(e)))
        pit.goto(base + '/comms')
        driver.goto(base + '/driver')
        pit.wait_for_function("!document.querySelector('[data-flag=YELLOW]').disabled")
        for flag, style, title in [('YELLOW', 'yellow', 'YELLOW FLAG'), ('RED', 'red', 'RED FLAG'),
                                   ('BOX', 'box', 'BOX BOX'), ('GREEN', 'green', 'GREEN FLAG'),
                                   ('STOP', 'stop', 'STOP'), ('CHECKERED', 'checkered', 'CHEQUERED')]:
            pit.locator(f'[data-flag={flag}]').click()
            driver.wait_for_selector('#alert.show.' + style)
            assert driver.locator('#alertTitle').inner_text() == title
            assert not driver.locator('#test').is_visible()
            pit.wait_for_function("document.querySelector('#send').disabled")
        pit.reload()
        pit.wait_for_function("document.querySelector('#current').textContent==='CHEQUERED'")
        pit.locator('#clear').click()
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')")
        pit.locator('#custom').fill('가' * 48)
        pit.locator('#send').click()
        driver.wait_for_selector('#alert.show.adjust')
        for width, height in ((844, 390), (390, 844)):
            driver.set_viewport_size({'width': width, 'height': height})
            assert driver.locator('#alert span').evaluate('(n)=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}')
        pit.locator('#clear').click()
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')")
        pit.locator('#custom').fill('NEXT LAP PUSH')
        pit.locator('#send').click()
        driver.wait_for_selector('#alert.show.adjust')
        assert driver.locator('#alertTitle').inner_text() == 'NEXT LAP PUSH'
        # Even when polling fails, a temporary message must expire locally.
        driver.route('**/api/telemetry', lambda route: route.abort())
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')", timeout=5500)
        driver.unroute('**/api/telemetry')
        pit.wait_for_function("document.querySelector('#current').textContent==='발령 없음'")
        pit.locator('[data-message="REGEN +1"]').click()
        driver.wait_for_function("document.querySelector('#alertTitle').textContent==='REGEN +1'")
        pit.locator('[data-flag=RED]').click()
        driver.wait_for_selector('#alert.show.red')
        # Flags also disappear after four seconds even if the next fetch never succeeds.
        driver.route('**/api/telemetry', lambda route: route.abort())
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')", timeout=5500)
        pit.wait_for_function("document.querySelector('#current').textContent==='발령 없음'")
        driver.unroute('**/api/telemetry')
        driver.wait_for_timeout(500)
        assert not driver.locator('#alert').is_visible()
        pit.route('**/api/driver-message', lambda route: route.abort())
        pit.wait_for_function("document.querySelector('#connection').textContent.includes('확인 필요')")
        assert pit.locator('[data-flag=BOX]').is_disabled()
        pit.unroute('**/api/driver-message')
        pit.wait_for_function("!document.querySelector('[data-flag=BOX]').disabled")
        pit.locator('[data-flag=BOX]').click()
        driver.wait_for_selector('#alert.show.box')
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')", timeout=5500)
        pit.wait_for_function("document.querySelector('#current').textContent==='발령 없음'")
        pit.locator('[data-flag=BOX]').click()
        driver.wait_for_selector('#alert.show.box')
        out = ROOT / '.local/comms-audit'
        out.mkdir(exist_ok=True)
        for width, height in ((1280, 900), (390, 844), (844, 390)):
            pit.set_viewport_size({'width': width, 'height': height})
            assert pit.evaluate('document.documentElement.scrollWidth<=innerWidth')
            pit.screenshot(path=str(out / f'comms-{width}x{height}.png'), full_page=True)
        driver.set_viewport_size({'width': 844, 'height': 390})
        driver.screenshot(path=str(out / 'driver-box.png'))
        pit.locator('#clear').click()
        driver.wait_for_function("!document.querySelector('#alert').classList.contains('show')")
        pit.goto(base + '/test')
        assert pit.locator('a[href="/comms"]').count() == 1
        assert pit.locator('[data-flag]').count() == 0
        assert not errors, errors
        browser.close()
    print(json.dumps({'passed': True, 'checks': ['six flags from public phone', 'clear', 'message presets',
        'flag and message 4-second expiry during connection loss', 'no expired flag replay', 'flag resend', 'no display-test marker',
        'sender reconnect', 'reload retains unexpired flag', 'mobile layouts', 'test panel migration']}))
finally:
    server.shutdown()
    server.server_close()
    gateway.forward_socket.close()

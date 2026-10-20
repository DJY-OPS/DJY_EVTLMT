"""Exercise geometry inputs in a real browser against a temporary local database."""
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gateway'))
sys.path.insert(0, str(Path.home() / 'telemetry-tools/browser-tests'))
from playwright.sync_api import sync_playwright
from ev_gateway import EVGateway, make_handler


def main():
    with tempfile.TemporaryDirectory() as folder:
        gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(folder) / 'browser.db')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/pit')
                page.wait_for_function("!document.getElementById('geometrySave').disabled")
                assert float(page.locator('#tireDiameterCm').input_value()) == 45
                page.locator('#gearRatio').fill('5')
                page.locator('#tireDiameterCm').fill('46')
                page.locator('#geometrySave').click()
                page.wait_for_function("document.getElementById('geometryStatus').textContent.includes('저장 완료')")
                assert gateway.store.geometry()['tire_diameter_m'] == .46
                gateway.set_recording(True)
                packet = {'seq': 1, 'rpm_left': 800, 'rpm_right': 1200,
                          'motor_left_ok': True, 'motor_right_ok': True}
                gateway.accept(packet)
                page.wait_for_function("document.getElementById('speedGeometry').textContent.includes('46 cm')")
                assert '5:1' in page.locator('#speedGeometry').inner_text()
                page.reload()
                page.wait_for_function("!document.getElementById('geometrySave').disabled")
                assert page.locator('#gearRatio').input_value() == '5'
                assert page.locator('#tireDiameterCm').input_value() == '46'
                page.locator('#gearRatio').fill('0')
                page.locator('#geometrySave').click()
                assert not page.locator('#gearRatio').evaluate('(el) => el.checkValidity()')
                assert gateway.store.geometry()['gear_ratio'] == 5
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.goto(base + '/battery')
                page.wait_for_function("document.getElementById('packTemperature') !== null")
                assert page.locator('#batterySpeed').count() == 0
                assert not errors, errors
                browser.close()
                print('PASS: settings form, persistence, validation, sample labels, mobile layout, battery navigation')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            gateway.database.close()
            gateway.forward_socket.close()


if __name__ == '__main__':
    main()

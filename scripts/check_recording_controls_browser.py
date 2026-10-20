"""Check recording acknowledgements and failed requests using only a temporary DB."""
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path.home()/'telemetry-tools/browser-tests'))
sys.path.insert(0, str(ROOT/'gateway'))
from playwright.sync_api import sync_playwright
from ev_gateway import EVGateway, make_handler
from recording_view import archive_page

with tempfile.TemporaryDirectory() as directory:
    gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(directory)/'test.db')
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            for route in ('pit', 'battery'):
                page = browser.new_page(extra_http_headers={'X-Forwarded-For':'203.0.113.9'})
                page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
                errors = []
                dialogs = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
                page.goto(f'http://127.0.0.1:{server.server_port}/{route}')
                page.wait_for_function("!document.querySelector('#record').disabled")
                page.locator('#label').fill('가속도테스트')
                def reject(request):
                    request.fulfill(status=403, body='forbidden')
                page.route('**/api/records/control', reject)
                page.locator('#record').click()
                page.wait_for_function("document.querySelector('#recordStatus').textContent.includes('확인 실패') && !document.querySelector('#record').disabled")
                assert dialogs and '403' in dialogs[-1]
                assert page.locator('#recordFeedback').get_attribute('data-state') == 'error'
                assert not gateway.database.status()['recording_active']
                page.unroute('**/api/records/control', reject)
                pending = []
                def delay(request):
                    pending.append(request)
                page.route('**/api/records/control', delay)
                page.locator('#record').click()
                page.wait_for_timeout(400)  # Polls must not re-enable an unacknowledged request.
                assert page.locator('#record').is_disabled()
                assert page.locator('#recordFeedbackTitle').inner_text() == '시작 확인 중…'
                assert page.locator('#record').get_attribute('aria-busy') == 'true'
                assert len(pending) == 1
                pending[0].continue_()
                page.unroute('**/api/records/control', delay)
                page.wait_for_function("document.querySelector('#notice').textContent.includes('기록 시작 확인') && !document.querySelector('#record').disabled")
                sid = gateway.database.status()['recording_session_id']
                assert gateway.database.list_sessions()[0]['label'] == '가속도테스트'
                assert page.locator('#recordFeedbackTitle').inner_text() == '● 기록 중'
                assert page.locator('#recordFeedback').get_attribute('data-state') == 'recording'
                page.wait_for_function("document.querySelector('#recordFeedbackClock').textContent !== '00:00:00'")
                page.wait_for_function("document.querySelector('#recordStatus').textContent.includes('차량 센서 유효값 없음')")
                gateway.accept({'seq':sid, 'imu_online':True, 'imu_ok':True,
                                'longitudinal_accel_m_s2':1.25, 'lateral_accel_m_s2':-.5,
                                'bms_online':True, 'battery_pack_voltage_v':57.8, 'battery_current_a':-2})
                page.wait_for_function("document.querySelector('#recordStatus').textContent.includes('1개')")
                assert page.locator('#recordFeedbackCount').inner_text() == '1개 저장'
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#record').scroll_into_view_if_needed()
                assert page.locator('#recordFeedback').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(ROOT/'.local'/f'recording-feedback-{route}.png'))
                page.locator('#record').click()
                page.wait_for_function("document.querySelector('#notice').textContent.includes('기록 종료 확인')")
                assert page.locator('#recordFeedbackTitle').inner_text() == '■ 기록 종료'
                saved = archive_page(gateway.database, sid)['samples']
                assert len(saved) == 1
                assert saved[0]['values']['longitudinal_accel_m_s2'] == 1.25
                assert saved[0]['values']['battery_pack_voltage_v'] == 57.8
                assert not errors, errors
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        gateway.database.close()
        gateway.forward_socket.close()
print('PASS: pit/BMS recording failure alert, start/stop acknowledgement, named session, valid acceleration/BMS storage.')

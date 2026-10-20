"""Coaching dashboard checks on an isolated server with explicit test telemetry."""
import json
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

vehicle = {'online': True, 'values': {'speed_kmh':54, 'rpm_left':3300, 'rpm_right':3320,
           'tps_pct':80, 'sas_deg':15, 'lateral_accel_m_s2':9.80665, 'longitudinal_accel_m_s2':-9.80665}}
point = {'id':1, 'lat':35.15, 'lon':126.93, 'accuracy_m':3, 'speed_kmh':36,
         'accepted':True, 'segment':0, 'source_id':'TEST', 'stream_id':'TEST'}
gps = {'epoch':'test', 'cursor':1, 'more':False, 'points':[point], 'latest':point,
       'live':True, 'active':True, 'lap_count':2, 'lap_time_s':61.23, 'last_lap_s':62.,
       'best_lap_s':60., 'reason':'TIMING', 'gate':{'a':[35.15,126.9299], 'b':[35.15,126.9301]}}

with tempfile.TemporaryDirectory() as folder:
    gateway = EVGateway('127.0.0.1',0,'127.0.0.1',19004,database_path=Path(folder)/'test.db')
    server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(gateway))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge',headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1050})
            errors, writes = [], []
            page.on('pageerror', lambda error: (errors.append(str(error)), print('PAGE ERROR:', error, flush=True)))
            page.on('request', lambda request: writes.append(request.url) if request.method != 'GET' else None)
            page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
            page.route('**/api/onboard', lambda route: route.fulfill(json=vehicle))
            page.route('**/api/gnss/live?**', lambda route: route.fulfill(json={**gps,'points':gps['points'] if 'after=0' in route.request.url else []}))
            response = page.goto(f'http://127.0.0.1:{server.server_port}/coach')
            assert response.status == 200, response.status
            page.wait_for_function("document.querySelector('#speed').textContent==='54.0'")
            assert page.locator('#throttle').inner_text() == '80'
            assert page.locator('#steering').inner_text() == '+15.0'
            assert page.locator('#lateralG').inner_text() == '+1.00'
            assert page.locator('#longitudinalG').inner_text() == '-1.00'
            assert float(page.locator('#gDot').get_attribute('cx')) == 206
            assert float(page.locator('#gDot').get_attribute('cy')) == 206
            assert float(page.locator('#gVector').get_attribute('x2')) == 206
            assert float(page.locator('#gVector').get_attribute('y2')) == 206
            assert page.locator('.g-grid circle').count() == 1
            assert page.locator('#gRing').text_content() == '2.0 G'
            vehicle['values'].update(lateral_accel_m_s2=0,longitudinal_accel_m_s2=0)
            page.wait_for_function("document.querySelector('#gDot').getAttribute('cx')==='150' && document.querySelector('#gDot').getAttribute('cy')==='150'")
            assert page.locator('#gVector').get_attribute('x2') == '150'
            assert page.locator('#gVector').get_attribute('y2') == '150'
            assert page.locator('#gCenter').get_attribute('cx') == '150'
            page.wait_for_function("document.querySelector('#currentLap').textContent==='1:01.23'")
            assert page.locator('#lapDelta').inner_text() == '+2.00 s'
            page.locator('#pause').click()
            frozen = page.locator('#throttleChart').evaluate('(c)=>c.toDataURL()')
            vehicle['values']['tps_pct'] = 25
            page.wait_for_function("document.querySelector('#throttle').textContent==='25'")
            assert page.locator('#throttleChart').evaluate('(c)=>c.toDataURL()') == frozen
            page.locator('#pause').click()
            vehicle['values']['speed_kmh'] = None
            page.wait_for_function("document.querySelector('#speedSource').textContent==='GPS 속도'")
            assert page.locator('#speed').inner_text() == '36.0'
            vehicle['values']['speed_kmh'] = 0
            page.wait_for_function("document.querySelector('#speed').textContent==='0.0'")
            assert page.locator('#speedSource').inner_text() == 'RPM 환산'
            vehicle['values']['lateral_accel_m_s2'] = 9.80665*3
            vehicle['values']['longitudinal_accel_m_s2'] = 0
            page.wait_for_function("document.querySelector('#gStatus').textContent.includes('범위 초과')")
            assert float(page.locator('#gDot').get_attribute('cx')) == 262
            vehicle['online'] = False
            gps['live'] = False
            page.wait_for_function("document.querySelector('#speed').textContent==='—'")
            assert page.locator('#gDot').get_attribute('visibility') == 'hidden'
            assert page.locator('#gVector').get_attribute('visibility') == 'hidden'
            assert page.locator('#throttle').inner_text() == '—'
            assert page.locator('#currentLap').inner_text() == '—:—.—'
            assert page.locator('#lastLap').inner_text() == '1:02.00'
            vehicle['online'] = True
            vehicle['values'].update(speed_kmh=54,lateral_accel_m_s2=4.5,longitudinal_accel_m_s2=-2.2)
            gps['live'] = True
            page.wait_for_function("document.querySelector('#speed').textContent==='54.0'")
            out = ROOT / '.local/coach'
            out.mkdir(exist_ok=True)
            page.screenshot(path=str(out/'desktop.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_timeout(300)
            page.screenshot(path=str(out/'mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.route('**/api/onboard',lambda route:route.abort())
            page.route('**/api/gnss/live?**',lambda route:route.abort())
            page.wait_for_function("document.querySelector('#throttle').textContent==='—'")
            page.wait_for_function("document.querySelector('#speed').textContent==='—'")
            assert not errors, errors
            assert not writes, writes
            browser.close()
        print(json.dumps({'passed':True,'checks':['G conversion and direction','out-of-range G','TPS and steering','lap timing','zero speed','GPS fallback source','stale and disconnected inputs','chart freeze while gauges stay live','desktop/mobile layout','no writes'],'page_errors':errors}))
    finally:
        server.shutdown();server.server_close();gateway.database.close();gateway.forward_socket.close()

"""Driver UI regression checks on an isolated server; no live vehicle writes."""
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

packet = dict(online=True, vehicle_age_ms=0, stm_online=True, speed_kmh=42,
              rpm_left=3000, rpm_right=3100, battery_soc_pct=70, wifi_connected=True,
              wifi_rssi_dbm=-52, front_sensor_online=True, tps_ok=True, sas_ok=True,
              imu_ok=True, bms_ok=True, motor_left_ok=True, motor_right_ok=True,
              gnss_epoch='test', gnss_run_id=1, gnss_lap_count=1, gnss_online=True,
              gnss_age_ms=0, gnss_lap_active=True, lap_time_s=12.345,
              last_lap_s=62.0, best_lap_s=62.0, previous_lap_s=None, lap_delta_s=None)
with tempfile.TemporaryDirectory() as folder:
    gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(folder)/'test.db')
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width':844, 'height':390})
            errors, writes = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: writes.append(r.url) if r.method != 'GET' else None)
            page.add_init_script('window.testOffset=0;const nativeNow=performance.now.bind(performance);performance.now=()=>nativeNow()+window.testOffset;')
            page.route('**/api/telemetry', lambda route: route.fulfill(json=packet))
            page.goto(f'http://127.0.0.1:{server.server_port}/driver')
            page.wait_for_function("document.querySelector('#speed').textContent==='42'")
            assert page.locator('#currentLap').inner_text() == '0:12.345'
            assert page.locator('#lapDelta').inner_text() == '—'
            assert not page.locator('#lapResult').is_visible()
            assert page.locator('.signal-bars i.on').count() == 4
            # A completed lap compares the two server-recorded laps, even after missed polls.
            packet.update(gnss_lap_count=2,last_lap_s=60.,previous_lap_s=62.,lap_delta_s=-2.,best_lap_s=60.)
            page.wait_for_selector('#lapResult.show.faster')
            assert page.locator('#resultDelta').inner_text() == '▲ −2.000'
            assert '1:02.000' in page.locator('#resultTimes').inner_text()
            assert page.locator('#lapResult').evaluate("n=>getComputedStyle(n).animationName") == 'lapblink'
            page.evaluate('window.testOffset+=3600')
            page.wait_for_function("!document.querySelector('#lapResult').classList.contains('show')")
            packet.update(gnss_lap_count=3,last_lap_s=63.,previous_lap_s=60.,lap_delta_s=3.)
            page.wait_for_selector('#lapResult.show.slower')
            packet.update(flag='RED')
            page.wait_for_selector('#alert.show.red')
            assert page.locator('#alert').evaluate("n=>+getComputedStyle(n).zIndex") > page.locator('#lapResult').evaluate("n=>+getComputedStyle(n).zIndex")
            packet.update(flag='CLEAR',gnss_run_id=2,gnss_lap_count=0,last_lap_s=0,previous_lap_s=None,lap_delta_s=None)
            page.wait_for_function("document.querySelector('#lapDelta').textContent==='—'")
            assert not page.locator('#lapResult').is_visible()
            # Invalid/unknown RSSI must never appear as a strong signal.
            for rssi, bars in ((-62,3),(-72,2),(-83,1),(-127,0),(0,0)):
                packet['wifi_rssi_dbm']=rssi
                page.wait_for_function('n=>document.querySelectorAll(".signal-bars i.on").length===n',arg=bars)
            # Stale snapshots arriving successfully must still time out.
            packet.update(online=False,vehicle_age_ms=2500,gnss_online=False,gnss_age_ms=4000)
            page.wait_for_function("document.querySelector('#link').textContent.includes('수신 지연')")
            assert not page.locator('#offline').is_visible()
            assert page.locator('#speed').inner_text() == '42'
            assert page.locator('#currentLap').inner_text() == '--:--.---'
            packet['vehicle_age_ms']=11000
            page.wait_for_function("document.querySelector('#link').textContent==='● 연결 끊김'")
            assert page.locator('#offline').count() == 0
            assert page.locator('#speed').is_visible()
            assert page.locator('#speed').inner_text() == '--'
            packet.update(online=True,vehicle_age_ms=0,gnss_online=True,gnss_age_ms=0,wifi_rssi_dbm=-52)
            page.wait_for_function("document.querySelector('#link').textContent==='● LIVE'")
            assert not page.locator('#offline').is_visible()
            # A hung fetch cannot freeze the independent connection watchdog.
            page.evaluate("()=>{window.realFetch=fetch;window.fetch=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('timeout'))));}")
            page.wait_for_timeout(200)
            page.evaluate('window.testOffset+=2500')
            page.wait_for_function("document.querySelector('#link').textContent.includes('수신 지연')")
            assert not page.locator('#offline').is_visible()
            page.evaluate('window.testOffset+=8000')
            page.wait_for_function("document.querySelector('#link').textContent==='● 연결 끊김'")
            assert page.locator('#offline').count() == 0
            assert page.locator('.main').is_visible()
            page.evaluate('()=>{window.fetch=window.realFetch;}')
            page.wait_for_function("document.querySelector('#link').textContent==='● LIVE'",timeout=5000)
            # 90 minutes on the monotonic clock does not create a session timeout.
            page.evaluate('window.testOffset+=90*60*1000')
            page.wait_for_timeout(250)
            assert not page.locator('#offline').is_visible()
            assert page.evaluate("formatLap(59.9996)") == '1:00.000'
            out=ROOT/'.local/driver-audit';out.mkdir(exist_ok=True)
            for width,height in ((844,390),(667,375),(390,844)):
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate("[...document.querySelectorAll('.main,.lap-stack,.controls,.connection')].every(n=>{const r=n.getBoundingClientRect();return r.right<=innerWidth && r.bottom<=innerHeight})")
                page.screenshot(path=str(out/f'driver-{width}x{height}.png'))
            assert not errors, errors
            assert not writes, writes
            browser.close()
        print(json.dumps({'passed':True,'checks':['lap comparison colors and 3.5s expiry','flag priority','run reset','Wi-Fi bars','short dropouts','stale snapshot timeout','hung fetch and recovery','90-minute clock','mobile layouts','no writes']}))
    finally:
        server.shutdown();server.server_close();gateway.forward_socket.close();gateway.database.close()

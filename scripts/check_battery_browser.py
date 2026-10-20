"""Isolated BMS UI/CSV/history checks. No live vehicle data or commands."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path.home()/'telemetry-tools/browser-tests'))
sys.path.insert(0, str(ROOT/'gateway'))
from playwright.sync_api import sync_playwright
from ev_gateway import EVGateway, make_handler
from battery import CHANNELS


def packet(seq):
    return {'seq': seq, 'timestamp_ms': seq*200, 'sample_time_ms': clock+seq*200,
            'battery_pack_voltage_v': 72, 'battery_current_a': 100, 'battery_soc_pct': 82,
            'bms_online': True, 'bms_ok': True, 'bms_fault': False,
            'bms_state': 'DISCHARGING', 'bms_age_ms': 20,
            'bms_cell_voltages_v': [3.70+i*.001 for i in range(24)], 'bms_cell_count': 24,
            'bms_max_cell_voltage_v': 3.723, 'bms_min_cell_voltage_v': 3.7,
            'bms_cell_delta_mv': 23, 'bms_temp_min_c': 25, 'bms_temp_max_c': 28,
            'bms_charge_mos_on': True, 'bms_discharge_mos_on': True,
            'bms_cycle_count': 18, 'bms_model': 'DALY R24TS', 'bms_protocol': 'DALY CAN',
            'power_left_kw': 2.5, 'power_right_kw': 4.5, 'delta_power_kw': 2,
            'tqv_internal_online': True, 'desired_yaw_rad_s': .4,
            'yaw_rate_rad_s': .35, 'yaw_error_rad_s': .05, 'imu_ok': True,
            'rpm_left': 800, 'rpm_right': 1200, 'motor_left_ok': True, 'motor_right_ok': True,
            'tps_pct': 70, 'tps_ok': True}


clock = int(time.time()*1000)-210000
with tempfile.TemporaryDirectory() as folder:
    gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(folder)/'test.db')
    gateway.set_recording(True, 'BMS UI TEST ONLY')
    for seq in range(1005):
        gateway.accept(packet(seq))
    gateway.set_recording(False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1080})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/battery')
            count = sum(c[0].startswith(('battery_', 'bms_')) and c[0] != 'battery_power_w' for c in CHANNELS)
            page.wait_for_function(f"document.querySelectorAll('#channels input').length === {count}")
            page.locator('#session').select_option('1')
            page.wait_for_function("state.mode==='history' && state.history.length===1000")
            assert page.locator('#packWatts').inner_text() == '7200 W'
            assert page.locator('#packKw').inner_text() == '7.200 kW'
            assert page.locator('#commandTotal, #batterySpeed').count() == 0
            assert page.locator('#packTemperature').inner_text() == '25.0 °C / 28.0 °C'
            assert page.locator('#packImbalance').inner_text() == '23 mV'
            assert page.evaluate("state.channels.every(c=>/^(battery_|bms_)/.test(c.key))")
            assert page.locator('#cells .cell').count() == 24
            assert page.locator('#plots canvas').count() == 4
            assert page.locator('#plot-electrical #plot-value-battery_current_a').count() == 1
            assert page.locator('#plot-cells #plot-value-bms_cell_delta_mv').count() == 1
            assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(0, 0, 0)'
            page.locator('#next').click()
            page.wait_for_function('state.page===1 && !state.busyHistory')
            assert page.locator('#next').is_disabled()
            with page.expect_download() as download:
                page.locator('#export').click()
            rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
            assert any(float(r['battery_power_w']) == 7200 for r in rows)
            assert len(json.loads(rows[0]['bms_cell_voltages_v'])) == 24
            assert 'power_command_total_kw' in rows[0]
            out = ROOT/'.local'
            out.mkdir(exist_ok=True)
            page.locator('#prev').click()
            page.wait_for_function('state.page===0 && !state.busyHistory')
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(out/'bms-ui-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(200)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            page.screenshot(path=str(out/'bms-ui-mobile.png'), full_page=True)
            page.locator('#live').click()
            gateway.accept({**packet(1010), 'sample_time_ms': int(time.time()*1000)})
            page.wait_for_function("document.querySelector('#packWatts').textContent==='7200 W'")
            page.locator('#pause').click()
            frozen = page.evaluate('state.pausedRows.length')
            before = page.evaluate('state.live.length')
            gateway.accept({**packet(1011), 'battery_current_a': 0, 'sample_time_ms': int(time.time()*1000)})
            page.wait_for_function(f'state.live.length > {before}')
            assert page.evaluate('state.pausedRows.length') == frozen
            page.locator('#pause').click()
            page.wait_for_function("document.querySelector('#packWatts').textContent==='0 W'")
            gateway.accept({**packet(1012), 'bms_online': False, 'sample_time_ms': int(time.time()*1000)})
            page.wait_for_function("document.querySelector('#packWatts').textContent==='미수신'")
            assert page.locator('#cells .cell').count() == 0
            # Runtime uses a separate current-only endpoint, even while graphs are paused/history.
            page.locator('#predictionSettings').evaluate("form => form.closest('details').open = true")
            page.locator('#predictionSettings [name=reserve_soc_pct]').fill('20')
            page.locator('#predictionSettings button').click()
            page.wait_for_function("document.querySelector('#predictionEditStatus').textContent.includes('저장 완료')")
            now = time.time()*1000
            remaining = 40.0
            def prediction_packet(seq, stamp, amps, capacity):
                return {**packet(seq), 'sample_time_ms':stamp, 'battery_current_a':amps,
                        'battery_soc_pct':capacity/80*100, 'bms_remaining_capacity_ah':capacity,
                        'bms_power_timing':[1,seq,0,seq,0,200,200,200]}
            for i in range(91):
                amps = -80 if i < 60 else -40
                if i:
                    remaining += amps/3600
                gateway.accept(prediction_packet(2000+i, now-90000+i*1000, amps, remaining))
            # Keep real API freshness alive while exercising the browser.
            stop_prediction = threading.Event()
            def update_prediction():
                seq = 2091
                while not stop_prediction.wait(.2):
                    stamp = time.time()*1000
                    gateway.accept(prediction_packet(seq, stamp, -40, remaining-40*(stamp-now)/3600000))
                    seq += 1
            predictor_worker = threading.Thread(target=update_prediction, daemon=True)
            predictor_worker.start()
            try:
                page.wait_for_function("document.querySelector('#runtimeMinutes').textContent.includes('분')")
                page.wait_for_function("document.querySelector('#efficiencyVerdict').textContent==='좋음'")
                assert 'Wh/km' in page.locator('#efficiencyWhKm').inner_text()
                assert '20%' in page.locator('#runtimeReserve').inner_text()
                page.locator('#pause').click()
                page.wait_for_function("document.querySelector('#runtimeMinutes').textContent.includes('분')")
                page.screenshot(path=str(out/'bms-prediction-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                page.set_viewport_size({'width':1440, 'height':1080})
                page.locator('#predictionTitle').scroll_into_view_if_needed()
                page.screenshot(path=str(out/'bms-prediction-desktop.png'))
                page.route('**/api/battery/prediction', lambda route: route.abort())
                page.wait_for_function("document.querySelector('#predictionReason').textContent.includes('연결 끊김')")
                assert page.locator('#runtimeMinutes').inner_text() == '—'
                assert page.locator('#efficiencyVerdict').inner_text() == '비교 대기'
                page.locator('#pause').click()
            finally:
                stop_prediction.set()
                predictor_worker.join()
            page.route('**/api/battery/live*', lambda route: route.abort())
            page.wait_for_function("document.querySelector('#link').textContent==='게이트웨이 연결 끊김'")
            assert page.locator('#packWatts').inner_text() == '미수신'
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        gateway.database.close()
        gateway.forward_socket.close()
print('BMS UI PASS: power, history, CSV, mobile, pause, offline, runtime, efficiency, prediction settings')

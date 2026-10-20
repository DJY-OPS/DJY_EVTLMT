from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from battery_prediction import BatteryPrediction, DEFAULTS
from ev_gateway import EVGateway, make_handler


def packet(index, stamp, current=-80, speed=40, remaining=40, **extra):
    return dict(bms_online=True, bms_fault=False, battery_current_a=current,
                battery_pack_voltage_v=50, battery_soc_pct=remaining/80*100,
                bms_remaining_capacity_ah=remaining, speed_online=True, speed_kmh=speed,
                sample_time_ms=stamp, bms_power_timing=[1,index,0,index,0,1000,1000,1000], **extra)


def feed(predictor, start, seconds, current=-80, speed=40, remaining=40):
    for i in range(seconds+1):
        p = packet(i, start+i*1000, current=current, speed=speed, remaining=remaining+current*i/3600)
        predictor.observe(p)
    return p


class BatteryPredictionTests(unittest.TestCase):
    def test_runtime_units_reserve_efficiency_and_stale(self):
        model = BatteryPrediction()
        p = feed(model, 100000, 60)
        result = model.read(p, now_ms=p['sample_time_ms'])
        self.assertEqual(result['state'], 'ready')
        self.assertAlmostEqual(result['mean_current_a'], 80)
        self.assertAlmostEqual(result['remaining_minutes'], (40-80*60/3600-8)/80*60)
        self.assertAlmostEqual(result['wh_km'], 100)
        self.assertEqual(result['efficiency'], 'similar')
        self.assertEqual(model.read(p, now_ms=p['sample_time_ms']+3000)['state'], 'waiting')

    def test_duplicates_backfill_gaps_and_restart(self):
        model = BatteryPrediction()
        p = feed(model, 100000, 20)
        n = len(model.intervals)
        model.observe(p)
        model.observe(dict(p, sample_time_ms=p['sample_time_ms']+50))  # Same CAN RX count.
        model.observe(packet(1, 101000))
        self.assertEqual(len(model.intervals), n)
        p = packet(21, 130000)
        model.observe(p)
        self.assertIsNone(model.read(p, now_ms=130000)['remaining_minutes'])
        self.assertEqual(len(model.intervals), 0)
        p = feed(model, 131000, 11)
        self.assertEqual(model.read(p, now_ms=p['sample_time_ms'])['state'], 'ready')
        p = packet(0, p['sample_time_ms']+1000, stream_id='new')
        model.observe(p)
        self.assertEqual(len(model.intervals), 0)

    def test_inconsistent_capacity_detects_real_log_failure_mode(self):
        model = BatteryPrediction()
        for i in range(61):
            p = packet(i, 100000+i*1000, current=-80, remaining=40-160*i/3600)
            model.observe(p)
        result = model.read(p, now_ms=p['sample_time_ms'])
        self.assertEqual(result['state'], 'inconsistent')
        self.assertIsNone(result['remaining_minutes'])
        self.assertAlmostEqual(result['wh_km'], 100)

    def test_soc_fallback_and_missing_invalid_stale_measurements(self):
        model = BatteryPrediction()
        p = feed(model, 100000, 15)
        p.pop('bms_remaining_capacity_ah')
        result = model.read(p, now_ms=p['sample_time_ms'])
        self.assertEqual(result['capacity_source'], 'soc')
        self.assertIsNotNone(result['remaining_minutes'])
        self.assertEqual(model.read(dict(p, bms_remaining_capacity_ah=-1), now_ms=p['sample_time_ms'])['state'], 'inconsistent')
        for field in ('battery_current_a','battery_pack_voltage_v'):
            for bad in (None, True, float('nan'), float('inf')):
                self.assertIsNone(model.read(dict(p, **{field:bad}), now_ms=p['sample_time_ms'])['remaining_minutes'])
        self.assertIsNone(model.read(dict(p, bms_online=False), now_ms=p['sample_time_ms'])['remaining_minutes'])
        p['bms_power_timing'][4] = 2000
        self.assertIsNone(model.read(p, now_ms=p['sample_time_ms'])['remaining_minutes'])

    def test_low_load_regen_reserve_missing_speed_and_alarm(self):
        for amps in (0, -.5, 20):
            model = BatteryPrediction()
            p = feed(model, 100000, 30, current=amps)
            result = model.read(p, now_ms=p['sample_time_ms'])
            self.assertEqual(result['state'], 'low_load')
            self.assertIsNone(result['remaining_minutes'])
        model = BatteryPrediction()
        p = feed(model, 100000, 15, remaining=8.1)
        self.assertEqual(model.read(p, now_ms=p['sample_time_ms'])['remaining_minutes'], 0)
        model = BatteryPrediction()
        p = feed(model, 100000, 30, speed=None)
        result = model.read(dict(p, bms_fault=True), now_ms=p['sample_time_ms'])
        self.assertIsNone(result['wh_km'])
        self.assertIsNotNone(result['remaining_minutes'])
        self.assertIn('경보', result['quality'])

    def test_efficiency_compares_nonoverlapping_similar_speed_windows(self):
        for amps, speed, verdict in ((-40,40,'good'),(-120,40,'poor'),(-40,80,'waiting')):
            model = BatteryPrediction()
            for i in range(91):
                p = packet(i, 100000+i*1000, current=-80 if i<60 else amps, speed=40 if i<60 else speed)
                model.observe(p)
            result = model.read(p, now_ms=p['sample_time_ms'])
            self.assertEqual(result['efficiency'], verdict)

    def test_http_persistence_validation_local_guard_and_gateway_ingest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'test.db'
            gateway = EVGateway('127.0.0.1',0,'127.0.0.1',19004,database_path=path)
            server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(gateway))
            worker = threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}/api/battery/prediction'
            def request(payload=None, **headers):
                req = Request(url,data=None if payload is None else json.dumps(payload).encode(),
                              headers={'Content-Type':'application/json',**headers})
                with urlopen(req,timeout=3) as response:
                    return json.load(response)
            try:
                self.assertTrue(request()['can_edit'])
                self.assertFalse(request(**{'X-Forwarded-For':'203.0.113.9'})['can_edit'])
                request(dict(DEFAULTS, reserve_soc_pct=20))
                base = time.time()*1000-20000
                for i in range(21):
                    gateway.accept(packet(i,base+i*1000,remaining=40-80*i/3600))
                self.assertEqual(request()['state'],'ready')
                self.assertEqual(request()['settings']['reserve_soc_pct'],20)
                for headers,code in (({'X-Forwarded-For':'203.0.113.9'},403),({'Origin':'https://foreign.example'},400)):
                    with self.assertRaises(HTTPError) as error:
                        request(DEFAULTS,**headers)
                    self.assertEqual(error.exception.code,code)
                    error.exception.close()
                for key,bad in [('configured_capacity_ah',0),('configured_capacity_ah',float('nan')),
                                ('reserve_soc_pct',True),('reserve_soc_pct',91),('discharge_sign',0)]:
                    with self.assertRaises(HTTPError) as error:
                        request(dict(DEFAULTS,**{key:bad}))
                    self.assertEqual(error.exception.code,400)
                    error.exception.close()
            finally:
                server.shutdown();server.server_close();worker.join()
                gateway.database.close();gateway.forward_socket.close()
            reopened = EVGateway('127.0.0.1',0,'127.0.0.1',19004,database_path=path)
            try:
                self.assertEqual(reopened.battery_prediction.settings['reserve_soc_pct'],20)
                self.assertEqual(len(reopened.battery_prediction.intervals),0)
            finally:
                reopened.database.close();reopened.forward_socket.close()

    def test_committed_batch_keeps_every_electrical_sample_not_only_last(self):
        gateway = EVGateway('127.0.0.1',0,'127.0.0.1',19004)
        try:
            samples = []
            for i in range(21):
                p = packet(i, 100000+i*1000, current=-40 if i % 2 else -120)
                p.update(seq=i+1, timestamp_ms=i*1000)
                samples.append(p)
            gateway.telemetry_exchange(dict(stream_id='0123456789abcdef', sent_ms=20000, samples=samples), 'USB')
            model = gateway.battery_prediction
            self.assertEqual(len(model.intervals),20)
            self.assertAlmostEqual(model._window(model.last_time,30)['current_a'],80)
        finally:
            gateway.forward_socket.close()


if __name__ == '__main__':
    unittest.main()

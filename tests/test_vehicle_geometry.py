import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from ev_gateway import EVGateway, make_handler
from recording_view import archive_page
from vehicle_geometry import DEFAULT_GEOMETRY, validate_geometry


class GeometryTests(unittest.TestCase):
    def test_settings_persist_and_history_keeps_each_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'telemetry.db'
            gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=path)
            packet = {'rpm_left': 800, 'rpm_right': 1200, 'motor_left_ok': True,
                      'motor_right_ok': True, 'speed_kmh': 88, 'vehicle_speed_m_s': 99}
            try:
                gateway.set_recording(True)
                gateway.accept(dict(packet, seq=1))
                geometry = gateway.set_geometry({'gear_ratio': 5, 'tire_diameter_m': .46})
                # A settings change cannot relabel or rewrite the last sample.
                self.assertEqual(gateway.snapshot()['speed_geometry'], DEFAULT_GEOMETRY)
                gateway.accept(dict(packet, seq=2))
                expected = round(1000 / 5 * math.pi * .46 * .06, 3)
                self.assertEqual(gateway.snapshot()['speed_kmh'], expected)
                self.assertEqual(gateway.snapshot()['vehicle_speed_m_s'], 99)
                self.assertEqual(gateway.snapshot()['speed_reported_kmh'], 88)
                gateway.accept({'seq': 3, 'battery_pack_voltage_v': 72, 'bms_online': True})
                self.assertEqual(gateway.snapshot()['speed_geometry'], geometry)
                rows = archive_page(gateway.database, 1)['samples']
                self.assertEqual(rows[0]['status']['speed_geometry'], DEFAULT_GEOMETRY)
                self.assertEqual(rows[1]['status']['speed_geometry'], geometry)
                self.assertAlmostEqual(rows[0]['values']['speed_kmh'], 17.943, places=3)
                self.assertEqual(rows[1]['values']['speed_kmh'], expected)
                # Delayed tracked packets use the configured geometry too.
                gateway.telemetry_exchange({'stream_id': '0123456789abcdef', 'sent_ms': 20,
                    'samples': [dict(packet, seq=2, timestamp_ms=20)]}, 'USB')
                gateway.telemetry_exchange({'stream_id': '0123456789abcdef', 'sent_ms': 20,
                    'samples': [dict(packet, seq=1, timestamp_ms=10)]}, 'USB')
                last = gateway.database.list_samples(1)['samples'][-1]['telemetry']
                self.assertEqual(last['speed_geometry'], geometry)
                self.assertEqual(last['speed_kmh'], expected)
            finally:
                gateway.database.close()
                gateway.forward_socket.close()
            reopened = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=path)
            try:
                self.assertEqual(reopened.store.geometry(), geometry)
                self.assertEqual(archive_page(reopened.database, 1)['samples'][0]['status']['speed_geometry'], DEFAULT_GEOMETRY)
            finally:
                reopened.database.close()
                reopened.forward_socket.close()

    def test_http_validation_and_local_write_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004, database_path=Path(folder) / 'test.db')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}/api/vehicle/geometry'
            def request(data=None, **headers):
                req = Request(url, data=None if data is None else json.dumps(data).encode(),
                              headers={'Content-Type': 'application/json', **headers})
                with urlopen(req, timeout=3) as response:
                    return json.load(response)
            try:
                self.assertTrue(request()['can_edit'])
                self.assertFalse(request(**{'X-Forwarded-For': '203.0.113.9'})['can_edit'])
                value = {'gear_ratio': 5, 'tire_diameter_m': .46}
                self.assertEqual(request(value)['geometry']['gear_ratio'], 5)
                for headers, code in (({'X-Forwarded-For': '203.0.113.9'}, 403),
                                      ({'Origin': 'https://foreign.example'}, 400)):
                    with self.assertRaises(HTTPError) as error:
                        request({'gear_ratio': 6, 'tire_diameter_m': .5}, **headers)
                    self.assertEqual(error.exception.code, code)
                    error.exception.close()
                for key in value:
                    for bad in (0, -1, True, '45', None, float('nan'), float('inf'), 1000):
                        with self.subTest(key=key, bad=bad), self.assertRaises(HTTPError) as error:
                            request({**value, key: bad})
                        self.assertEqual(error.exception.code, 400)
                        error.exception.close()
                self.assertEqual(gateway.store.geometry(), validate_geometry(value))
                self.assertEqual(gateway.database.geometry(), validate_geometry(value))
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
                gateway.database.close()
                gateway.forward_socket.close()


if __name__ == '__main__':
    unittest.main()

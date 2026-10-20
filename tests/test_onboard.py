import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from ev_gateway import EVGateway, TelemetryStore, make_handler
from onboard import KEYS, project_onboard


class OnboardTests(unittest.TestCase):
    def test_quality_zero_expiry_and_explicit_power(self):
        store = TelemetryStore()
        packet = {'stm_online': True, 'rpm_left': 0, 'rpm_right': 0,
                  'motor_left_ok': True, 'motor_right_ok': True, 'tps_pct': 0,
                  'tps_ok': True, 'imu_ok': True, 'yaw_rate_rad_s': 0,
                  'lateral_accel_m_s2': 9.80665, 'longitudinal_accel_m_s2': 0}
        store.update(packet, now=10)
        result = project_onboard(store.snapshot(now=10))
        self.assertTrue(result['online'])
        self.assertEqual(result['values']['speed_kmh'], 0)
        self.assertEqual(result['values']['rpm_left'], 0)
        self.assertEqual(result['values']['yaw_rate_rad_s'], 0)
        self.assertIsNone(result['power_on'])
        self.assertIsNone(result['values']['sas_deg'])
        self.assertEqual(set(result['values']), set(KEYS))
        for power in (True, False, 1, 'ON'):
            store.update({**packet, 'vehicle_power_on': power}, now=10)
            actual = project_onboard(store.snapshot(now=10))['power_on']
            self.assertIs(actual, power if type(power) is bool else None)
        store.update({**packet, 'vehicle_power_on': False}, now=10)
        stale = project_onboard(store.snapshot(now=12))
        self.assertFalse(stale['online'])
        self.assertIsNone(stale['power_on'])
        self.assertTrue(all(v is None for v in stale['values'].values()))
        store.update({**packet, 'motor_left_ok': False, 'imu_ok': False}, now=12)
        values = project_onboard(store.snapshot(now=12))['values']
        self.assertIsNone(values['rpm_left'])
        self.assertIsNone(values['speed_kmh'])
        self.assertIsNone(values['lateral_accel_m_s2'])

    def test_http_excludes_display_overrides_and_private_data(self):
        gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 19004)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        root = f'http://127.0.0.1:{server.server_port}'
        try:
            gateway.store.update({'rpm_left': 800, 'rpm_right': 1200,
                                  'motor_left_ok': True, 'motor_right_ok': True,
                                  'vehicle_power_on': True, 'private': 'hidden'})
            with patch.object(gateway.display_test, 'snapshot', return_value={'speed_kmh': 999}) as overrides:
                with urlopen(root + '/api/onboard', timeout=3) as response:
                    result = json.load(response)
                overrides.assert_not_called()
            self.assertTrue(result['online'])
            self.assertTrue(result['power_on'])
            self.assertAlmostEqual(result['values']['speed_kmh'], 17.943, places=3)
            self.assertNotIn('hidden', json.dumps(result))
            for path, mime in (('/onboard?demo=1', 'text/html'),
                               ('/onboard?overlay=1', 'text/html'),
                               ('/onboard/app.js', 'text/javascript'),
                               ('/onboard/hud.css', 'text/css')):
                with urlopen(root + path, timeout=3) as response:
                    self.assertIn(mime, response.headers['Content-Type'])
                    self.assertTrue(response.read())
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            gateway.forward_socket.close()


if __name__ == '__main__':
    unittest.main()

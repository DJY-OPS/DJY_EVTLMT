import io
import json
import sys
import threading
import time
import unittest
from unittest.mock import Mock
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from ev_gateway import EVGateway, make_handler


class TvPanelTests(unittest.TestCase):
    def setUp(self):
        self.gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 9004, enable_control=True)
        self.gateway.serial_stream = io.BytesIO()
        self.gateway.store.update({'seq': 1, 'stm_online': True, 'fault_code': 0,
                                   'rpm_left': 120, 'live_control_allowed': True})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.gateway))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.gateway.forward_socket.close()

    def get(self, path, headers=None):
        with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers or {})) as r:
            return r.read()

    def test_status_uses_real_values_not_display_test(self):
        self.gateway.display_test.apply({'action': 'set_rpm', 'value': 9999})
        actual = json.loads(self.get('/api/tv'))
        self.assertEqual(actual['rpm_left'], 120)
        self.assertTrue(actual['control_client_allowed'])
        self.gateway.enable_control = False
        self.assertFalse(json.loads(self.get('/api/tv'))['control_client_allowed'])

    def test_public_view_cannot_send_control(self):
        headers = {'X-Forwarded-For': '203.0.113.5'}
        self.assertFalse(json.loads(self.get('/api/tv', headers))['control_client_allowed'])
        request = urllib.request.Request(self.base + '/api/control', data=json.dumps({
            'type': 'live_tv', 'strength_pct': 50, 'limit_pct': 80, 'tv_enable': True,
        }).encode(), headers={**headers, 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.gateway.serial_stream.getvalue(), b'')

    def test_panel_assets_and_fault_neutral_command(self):
        self.assertIn(b'/tv/app.js', self.get('/tv'))
        self.assertIn(b'/api/control', self.get('/tv/app.js'))
        self.gateway.store.update({'seq': 2, 'stm_online': True, 'fault_code': 1})
        def send(strength):
            body = json.dumps({'type': 'live_tv', 'request_id': 34, 'strength_pct': strength,
                               'limit_pct': 100, 'tv_enable': True}).encode()
            req = urllib.request.Request(self.base + '/api/control', data=body,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        self.assertFalse(send(50)['ok'])
        self.assertTrue(send(0)['ok'])
        self.assertEqual(json.loads(self.gateway.serial_stream.getvalue())['strength_pct'], 0)

    def test_versioned_api_catalog_and_live_update(self):
        catalog=json.loads(self.get('/api/control/catalog'))
        self.assertEqual(catalog['schema_version'],1)
        self.assertFalse(next(p for p in catalog['parameters'] if p['key']=='pid_kp')['writable'])
        body={'type':'parameter_update','schema_version':1,'request_id':19,
              'values':{'strength_pct':35,'limit_pct':60,'tv_enable':True}}
        req=urllib.request.Request(self.base+'/api/control',data=json.dumps(body).encode(),
                                   headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['ok'])
        wire=json.loads(self.gateway.serial_stream.getvalue())
        self.assertEqual(wire['type'],'live_tv')
        self.assertEqual(wire['strength_pct'],35)
        self.assertEqual(wire['limit_pct'],60)

    def test_fresh_local_wifi_preferred_over_relay_then_falls_back(self):
        gateway = self.gateway
        gateway.serial_stream = None
        gateway.forward_socket.close()
        gateway.forward_socket = Mock()
        gateway._queue_relay_command = Mock(return_value=True)
        gateway.latest_udp_sender = ('172.20.10.8', 12345)
        gateway.transport_last['WIFI_UDP'] = time.monotonic()
        command = {'type':'live_tv', 'strength_pct':0, 'limit_pct':100, 'tv_enable':False}
        self.assertIn('local Wi-Fi', gateway._send_vehicle_command(command))
        self.assertEqual(gateway.forward_socket.sendto.call_args.args[1], ('172.20.10.8',9006))
        gateway._queue_relay_command.assert_not_called()
        gateway.transport_last['WIFI_UDP'] = time.monotonic()-2
        self.assertEqual(gateway._send_vehicle_command(command), 'ESP32 internet relay')
        gateway._queue_relay_command.assert_called_once_with(command)

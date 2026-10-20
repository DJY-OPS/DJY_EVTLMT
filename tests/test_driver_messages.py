import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from ev_gateway import DriverMessages, EVGateway, make_handler


class DriverMessageTests(unittest.TestCase):
    def test_flag_and_message_expiry_resend_and_clear(self):
        state = DriverMessages()
        with patch('ev_gateway.time.monotonic', return_value=10):
            self.assertEqual(state.apply({'action': 'set_adjustment', 'value': 'PUSH'})['remaining_ms'], 4000)
        with patch('ev_gateway.time.monotonic', return_value=15):
            self.assertEqual(state.snapshot()['message'], '')
            self.assertEqual(state.apply({'action': 'set_flag', 'value': 'BOX'})['flag'], 'BOX')
        with patch('ev_gateway.time.monotonic', return_value=16):
            self.assertEqual(state.snapshot()['flag'], 'BOX')
            with self.assertRaises(ValueError):
                state.apply({'action': 'set_adjustment', 'value': 'HIDDEN'})
            self.assertEqual(state.apply({'action': 'set_flag', 'value': 'BOX'})['remaining_ms'], 4000)
        with patch('ev_gateway.time.monotonic', return_value=19.999):
            self.assertEqual(state.snapshot()['flag'], 'BOX')
        with patch('ev_gateway.time.monotonic', return_value=20):
            self.assertEqual(state.snapshot()['flag'], 'CLEAR')
            self.assertEqual(state.snapshot()['remaining_ms'], 0)
            self.assertEqual(state.apply({'action': 'set_adjustment', 'value': 'PUSH'})['message'], 'PUSH')
            state.apply({'action': 'clear_alert'})
            self.assertEqual(state.snapshot()['flag'], 'CLEAR')
            self.assertEqual(state.snapshot()['message'], '')
            self.assertEqual(state.snapshot()['version'], 5)
        self.assertEqual(DriverMessages().snapshot()['flag'], 'CLEAR')

    def test_every_flag_expires_without_needing_a_poll_or_clear(self):
        for flag in ('YELLOW', 'RED', 'BOX', 'GREEN', 'STOP', 'CHECKERED'):
            state = DriverMessages()
            with patch('ev_gateway.time.monotonic', return_value=10):
                self.assertEqual(state.apply({'action': 'set_flag', 'value': flag})['remaining_ms'], 4000)
            with patch('ev_gateway.time.monotonic', return_value=14):
                # A new message must be accepted even if nobody polled the expired flag.
                result = state.apply({'action': 'set_adjustment', 'value': 'NEXT'})
                self.assertEqual(result['flag'], 'CLEAR')
                self.assertEqual(result['message'], 'NEXT')

    def test_rejects_invalid_actions_and_messages_without_changes(self):
        state = DriverMessages()
        for action, value in [('set_rpm', 5000), ('set_flag', []), ('set_flag', 'DANGER'),
                              ('set_adjustment', None), ('set_adjustment', ' '),
                              ('set_adjustment', 'A' * 49)]:
            with self.assertRaises(ValueError):
                state.apply({'action': action, 'value': value})
        self.assertEqual(state.snapshot()['version'], 0)

    def test_public_http_communication_is_separate_from_vehicle_and_test_controls(self):
        gateway = EVGateway('127.0.0.1', 0, '127.0.0.1', 9004, enable_control=True)
        gateway.serial_stream = io.BytesIO()
        gateway.store.update({'seq': 1, 'rpm_left': 123})
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gateway))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.5', 'Origin': base}

        def post(path, data, extra=None):
            req = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                         headers={**headers, **(extra or {})})
            with urllib.request.urlopen(req) as response:
                return json.load(response)

        try:
            for flag in ('YELLOW', 'RED', 'BOX', 'GREEN', 'STOP', 'CHECKERED'):
                result = post('/api/driver-message', {'action': 'set_flag', 'value': flag})
                self.assertEqual(result['flag'], flag)
                with urllib.request.urlopen(base + '/api/telemetry') as response:
                    telemetry = json.load(response)
                self.assertEqual(telemetry['driver_message']['flag'], flag)
                self.assertEqual(telemetry['rpm_left'], 123)
                self.assertFalse(telemetry['test_override'])
            for path, data, extra, code in (
                ('/api/test', {'action': 'set_rpm', 'value': 9999}, {}, 403),
                ('/api/control', {}, {}, 403),
                ('/api/driver-message', {'action': 'clear_alert'}, {'Origin': 'https://untrusted.invalid'}, 400),
                ('/api/driver-message', {'action': 'clear_alert'}, {'Content-Type': 'text/plain'}, 400),
                ('/api/driver-message', {'action': 'set_rpm', 'value': 9999}, {}, 400),
            ):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(path, data, extra)
                self.assertEqual(error.exception.code, code)
            self.assertEqual(gateway.driver_messages.snapshot()['flag'], 'CHECKERED')
            post('/api/driver-message', {'action': 'clear_alert'})
            self.assertEqual(post('/api/driver-message', {'action': 'set_adjustment', 'value': 'PUSH'})['message'], 'PUSH')
            self.assertEqual(gateway.serial_stream.getvalue(), b'')
            self.assertEqual(gateway.display_test.snapshot()['display_command_version'], 0)
            with urllib.request.urlopen(base + '/comms') as response:
                self.assertIn(b'BOX BOX', response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            gateway.forward_socket.close()

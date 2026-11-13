"""Protocol/security checks; run: py -3 -m unittest -v"""
import http.client
import threading
import time
import unittest
import shutil
import subprocess
import json
import tempfile
from pathlib import Path
from receiver import Receiver, MAX_FRAME, PAGE, AUTH_ATTEMPTS

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / 'work/test-tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.data = tempfile.TemporaryDirectory(dir=temporary_root)
        self.server = Receiver(('127.0.0.1', 0), '0427', data_dir=self.data.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.data.cleanup()

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        result = r.status, r.read()
        c.close()
        return result

    def test_auth_and_latest_frame_and_expiry(self):
        frame = b'\xff\xd8test\xff\xd9'  # Envelope fixture, not a decodable JPEG.
        headers = {'Authorization': 'Bearer ' + '0427', 'Content-Type': 'image/jpeg'}
        self.assertEqual(self.request('POST', '/upload', frame)[0], 401)
        self.assertEqual(self.request('GET', '/frame')[0], 204)
        self.assertEqual(self.request('POST', '/upload', frame, headers)[0], 204)
        self.assertEqual(self.request('GET', '/frame'), (200, frame))
        with self.server.lock:
            self.server.updated = time.monotonic() - 3
        self.assertEqual(self.request('GET', '/frame')[0], 204)

    def test_reject_bad_input(self):
        headers = {'Authorization': 'Bearer ' + '0427', 'Content-Type': 'image/jpeg'}
        self.assertEqual(self.request('POST', '/upload', b'nope', headers)[0], 400)
        headers['Content-Length'] = str(MAX_FRAME + 1)
        self.assertEqual(self.request('POST', '/upload', b'', headers)[0], 413)
        headers['Content-Length'] = 'bad'
        self.assertEqual(self.request('POST', '/upload', b'', headers)[0], 400)

    def test_phone_allowlist(self):
        self.server.phone = '192.0.2.2'  # RFC 5737 documentation-only test address.
        self.assertEqual(self.request('POST', '/upload', b'anything')[0], 403)

    def test_viewer_host_and_routes(self):
        status, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertEqual(page, PAGE)
        self.assertEqual(self.request('GET', '/', headers={'Host': 'attacker.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/missing')[0], 404)
        self.assertEqual(self.request('POST', '/missing', b'')[0], 404)

    def test_reject_media_type_and_chunked_transfer(self):
        headers = {'Authorization': 'Bearer ' + '0427', 'Content-Type': 'text/plain'}
        self.assertEqual(self.request('POST', '/upload', b'\xff\xd8\xff\xd9', headers)[0], 415)
        headers['Content-Type'] = 'image/jpeg'
        headers['Transfer-Encoding'] = 'chunked'
        self.assertEqual(self.request('POST', '/upload', b'\xff\xd8\xff\xd9', headers)[0], 400)

    def test_code_guessing_lockout_and_expiry(self):
        frame = b'\xff\xd8test\xff\xd9'
        wrong = {'Authorization': 'Bearer 9999', 'Content-Type': 'image/jpeg'}
        correct = {'Authorization': 'Bearer 0427', 'Content-Type': 'image/jpeg'}
        for _ in range(AUTH_ATTEMPTS - 1):
            self.assertEqual(self.request('POST', '/upload', frame, wrong)[0], 401)
        self.assertEqual(self.request('POST', '/upload', frame, wrong)[0], 429)
        self.assertEqual(self.request('POST', '/upload', frame, correct)[0], 429)
        self.assertIsNone(self.server.phone)
        with self.server.lock:
            self.server.auth_blocked_until = time.monotonic() - 1
        self.assertEqual(self.request('POST', '/upload', frame, correct)[0], 204)
        self.assertEqual(self.server.phone, '127.0.0.1')
        self.assertEqual(self.server.auth_failures, 0)

    def test_invalid_frame_does_not_pair_sender(self):
        headers = {'Authorization': 'Bearer 0427', 'Content-Type': 'image/jpeg'}
        self.assertEqual(self.request('POST', '/upload', b'nope', headers)[0], 400)
        self.assertIsNone(self.server.phone)

    def test_code_must_have_four_digits(self):
        for invalid in ('123', '12345', 'abcd', '１２３４', 'a' * 32):
            with self.assertRaises(ValueError):
                Receiver(('127.0.0.1', 0), invalid)

    def test_desktop_status_and_clean_obs_view(self):
        info = json.loads(self.request('GET', '/api/status')[1])
        self.assertEqual(info['state'], 'waiting')
        self.assertEqual(info['code'], '0427')
        self.assertEqual(self.request('GET', '/app')[0], 200)
        self.assertNotIn(b'/api/status', self.request('GET', '/')[1])
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': 'attacker.invalid'})[0], 403)
        headers = {'Authorization': 'Bearer 0427', 'Content-Type': 'image/jpeg', 'X-Phone-Name': 'Test+phone'}
        self.assertEqual(self.request('POST', '/upload', b'\xff\xd8test\xff\xd9', headers)[0], 204)
        info = json.loads(self.request('GET', '/api/status')[1])
        self.assertEqual(info['state'], 'connected')
        self.assertEqual(info['phone_name'], 'Test phone')
        with self.server.lock:
            self.server.updated = time.monotonic() - 3
        self.assertEqual(json.loads(self.request('GET', '/api/status')[1])['state'], 'lost')

    def test_snapshot_requires_explicit_local_action(self):
        action = {'X-Camera-Action': 'snapshot'}
        self.assertEqual(self.request('POST', '/api/snapshot')[0], 403)
        self.assertEqual(self.request('POST', '/api/snapshot', headers=action)[0], 409)
        frame = b'\xff\xd8test\xff\xd9'
        headers = {'Authorization': 'Bearer 0427', 'Content-Type': 'image/jpeg'}
        self.assertEqual(self.request('POST', '/upload', frame, headers)[0], 204)
        self.assertEqual(self.request('POST', '/api/snapshot', headers={**action, 'Host': 'attacker.invalid'})[0], 403)
        status, body = self.request('POST', '/api/snapshot', headers=action)
        self.assertEqual(status, 200)
        filename = json.loads(body)['filename']
        self.assertEqual((self.server.capture_dir / filename).read_bytes(), frame)

    def test_shutdown_requires_explicit_local_action(self):
        self.assertEqual(self.request('POST', '/api/shutdown')[0], 403)
        status, body = self.request('POST', '/api/shutdown', headers={'X-Camera-Action': 'shutdown'})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['stopped'])
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

class ViewerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is optional; needed only for viewer logic tests')
    def test_expiry_and_reconnect(self):
        script = PAGE.decode('utf-8').split('<script>', 1)[1].split('</script>', 1)[0]
        result = subprocess.run(
            [shutil.which('node'), str(Path(__file__).with_name('test_viewer.js'))],
            input=script, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__':
    unittest.main()

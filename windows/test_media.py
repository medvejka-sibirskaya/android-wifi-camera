"""Audio protocol, recording lifecycle and independent AVI decoding checks."""
import http.client
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from PIL import Image
from receiver import Receiver
from media import AviWriter, MediaHub, RATE, MAX_AUDIO


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work/test-tmp')
        self.server = Receiver(('127.0.0.1', 0), '1234', data_dir=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.headers = {'Authorization': 'Bearer 1234', 'X-Media-Session': 'test-session'}
        self.video_headers = {**self.headers, 'Content-Type': 'image/jpeg'}
        self.audio_headers = {**self.headers, 'Content-Type': 'audio/pcm;rate=16000;channels=1;bits=16'}
        buffer = io.BytesIO()
        Image.new('RGB', (320, 180), '#2088aa').save(buffer, 'JPEG')
        self.jpeg = buffer.getvalue()
        self.pcm = struct.pack('<1600h', *(round(6000 * math.sin(2 * math.pi * 440 * n / RATE)) for n in range(1600)))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=12)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        value = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return value

    def video(self, at=None):
        return self.request('POST', '/upload', self.jpeg,
                            {**self.video_headers, 'X-Media-Time': str((at or time.monotonic()) * 1000)})

    def audio(self, at=None):
        return self.request('POST', '/audio', self.pcm,
                            {**self.audio_headers, 'X-Media-Time': str((at or time.monotonic()) * 1000)})

    def test_audio_pairing_validation_and_local_playback(self):
        self.assertEqual(self.audio()[0], 409)
        self.assertIsNone(self.server.phone)
        self.assertEqual(self.video()[0], 204)
        self.assertEqual(self.request('POST', '/audio', self.pcm, {**self.audio_headers, 'Authorization': 'Bearer 9999'})[0], 401)
        self.assertEqual(self.request('POST', '/audio', b'123', self.audio_headers)[0], 400)
        self.assertEqual(self.request('POST', '/audio', self.pcm, {**self.audio_headers, 'X-Media-Time': 'NaN'})[0], 400)
        self.assertEqual(self.request('POST', '/audio', self.pcm, {**self.audio_headers, 'Content-Type': 'audio/wav'})[0], 415)
        self.assertEqual(self.audio()[0], 204)
        code, data, headers = self.request('GET', '/api/audio')
        self.assertEqual(code, 200)
        self.assertEqual(data, self.pcm)
        seq = headers['X-Audio-Sequence']
        self.assertEqual(self.request('GET', '/api/audio', headers={'X-Audio-After': seq})[1], b'')
        self.assertEqual(self.request('GET', '/api/audio', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/audio', self.pcm, {**self.audio_headers, 'X-Media-Session': 'other'})[0], 409)
        self.assertFalse(self.server.media.directory.exists())

    def test_record_requires_local_action_video_and_audio(self):
        self.assertEqual(self.request('POST', '/api/record/start')[0], 403)
        action = {'X-Camera-Action': 'record-start'}
        self.assertEqual(self.request('POST', '/api/record/start', headers=action)[0], 409)
        self.video()
        self.assertEqual(self.request('POST', '/api/record/start', headers=action)[0], 409)
        self.assertEqual(self.request('POST', '/api/record/start', headers={**action, 'Host': 'evil.example'})[0], 403)
        self.assertFalse(self.server.media.directory.exists())

    def test_timeline_silence_and_bounded_buffers(self):
        hub = self.server.media
        at = time.monotonic()
        hub.video_frame(self.jpeg, 's', at, at)
        hub.audio_frame(self.pcm, 's', at)
        self.assertEqual(hub.pcm_window(at, .1), self.pcm)
        self.assertEqual(hub.pcm_window(at + 1, .1), bytes(3200))
        for number in range(100):
            hub.audio_frame(self.pcm, 's', at + number * .0001)
            hub.video_frame(self.jpeg, 's', at, at)
        self.assertLessEqual(len(hub.audio), 30)
        self.assertLessEqual(len(hub.video), 8)
        hub.video_frame(self.jpeg, 'new-session', at, at)
        self.assertEqual(hub.playback(0)[1], b'')

    def test_avi_is_decodable_with_audio_and_video(self):
        # Verify actual file with an independent libav decoder, not our writer.
        try:
            import av
        except ImportError:
            self.skipTest('Install av in work/av-verification and add to PYTHONPATH for independent media decoding')
        self.video()
        self.audio()
        stop = threading.Event()
        errors = []
        def produce():
            next_at = time.monotonic() + .1
            stop.wait(.1)
            while not stop.is_set():
                at = next_at
                video_status = self.video(at)[0]
                audio_status = self.audio(at)[0]
                if video_status != 204 or audio_status != 204:
                    errors.append((video_status, audio_status))
                next_at += .1
                stop.wait(max(0, next_at - time.monotonic()))
        producer = threading.Thread(target=produce)
        producer.start()
        try:
            status, body, _ = self.request('POST', '/api/record/start', headers={'X-Camera-Action': 'record-start'})
            self.assertEqual(status, 200, body)
            filename = json.loads(body)['filename']
            self.assertEqual(self.request('POST', '/api/record/start', headers={'X-Camera-Action': 'record-start'})[0], 409)
            time.sleep(1.7)
            status, body, _ = self.request('POST', '/api/record/stop', headers={'X-Camera-Action': 'record-stop'})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)['state'], 'saved')
        finally:
            stop.set()
            producer.join()
        path = self.server.media.directory / filename
        with av.open(str(path)) as container:
            self.assertEqual(len(container.streams.video), 1)
            self.assertEqual(len(container.streams.audio), 1)
            video = list(container.decode(video=0))
        with av.open(str(path)) as container:
            audio = list(container.decode(audio=0))
        self.assertGreaterEqual(len(video), 10)
        self.assertEqual((video[0].width, video[0].height), (1280, 720))
        self.assertEqual(sum(frame.samples for frame in audio), len(video) * 1600)
        self.assertTrue(any(frame.to_ndarray().max() > 1000 for frame in audio))
        self.assertFalse(errors)

    def test_short_recording_never_reports_empty_file_saved(self):
        self.video()
        self.audio()
        self.server.media.start()
        info = self.server.media.stop()
        self.assertEqual(info['state'], 'error')
        self.assertFalse((self.server.media.directory / info['filename']).exists())

    def test_stop_flushes_playout_tail(self):
        self.video()
        self.audio()
        self.server.media.start()
        time.sleep(.23)
        info = self.server.media.stop()
        self.assertEqual(info['state'], 'saved', info)
        self.assertEqual(info['seconds'], .2)

    def test_recording_stops_and_finalizes_after_lost_video(self):
        self.video()
        self.audio()
        self.server.media.start()
        time.sleep(2.6)
        info = self.server.media.info()['recording']
        self.assertEqual(info['state'], 'saved', info)
        self.assertIsNone(self.server.media.recorder)
        self.assertIn(b'idx1', (self.server.media.directory / info['filename']).read_bytes())


if __name__ == '__main__':
    unittest.main()

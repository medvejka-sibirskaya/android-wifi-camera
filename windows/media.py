"""Local PCM timeline and explicit MJPEG/PCM AVI recording (no extra codec).
AVI layout follows Microsoft's AVI RIFF File Reference. No media is written
unless the user starts a recording. Buffers are bounded and expire in RAM.
"""
from collections import deque
import io
import secrets
import struct
import threading
import time
from pathlib import Path

RATE = 16000
FPS = 10
WIDTH, HEIGHT = 1280, 720
MAX_AUDIO = 6400  # 200 ms, signed little-endian PCM16 mono.


def chunk(tag, data):
    return tag + struct.pack('<I', len(data)) + data + b'\0' * (len(data) % 2)


def listing(kind, data):
    return chunk(b'LIST', kind + data)


class AviWriter:
    """Seekable AVI 1.0, interleaved MJPEG and 16 kHz PCM, bounded to 1 GiB."""
    def __init__(self, path):
        self.file = open(path, 'xb')
        self.index = []
        self.frames = 0
        self.samples = 0
        self.max_frame = 0
        self.file.write(b'RIFF\0\0\0\0AVI ' + self.header())
        self.movi = self.file.tell()
        self.file.write(b'LIST\0\0\0\0movi')

    def header(self):
        avih = struct.pack('<14I', 1000000 // FPS, self.max_frame * FPS + RATE * 2,
                           0, 0x110, self.frames, 0, 2, self.max_frame,
                           WIDTH, HEIGHT, 0, 0, 0, 0)
        def stream(kind, handler, scale, rate, length, size, rect):
            return struct.pack('<4s4sIHH8I4h', kind, handler, 0, 0, 0,
                               0, scale, rate, 0, length, size, 0xFFFFFFFF,
                               0 if kind == b'vids' else 2, *rect)
        video = chunk(b'strh', stream(b'vids', b'MJPG', 1, FPS, self.frames,
                                     self.max_frame, (0, 0, WIDTH, HEIGHT)))
        video += chunk(b'strf', struct.pack('<IiiHH4sIiiII', 40, WIDTH, HEIGHT,
                                             1, 24, b'MJPG', WIDTH * HEIGHT * 3, 0, 0, 0, 0))
        audio = chunk(b'strh', stream(b'auds', b'\0' * 4, 2, RATE * 2,
                                     self.samples, RATE // FPS * 2, (0, 0, 0, 0)))
        audio += chunk(b'strf', struct.pack('<HHIIHH', 1, 1, RATE, RATE * 2, 2, 16))
        return listing(b'hdrl', chunk(b'avih', avih) + listing(b'strl', video) + listing(b'strl', audio))

    def write(self, jpeg, pcm):
        if self.file.tell() + len(jpeg) + len(pcm) + len(self.index) * 16 > 1024 ** 3:
            raise OSError('Достигнут предел записи 1 ГиБ. Начни новый файл.')
        for tag, data in ((b'00dc', jpeg), (b'01wb', pcm)):
            offset = self.file.tell() - (self.movi + 8)
            self.index.append(struct.pack('<4sIII', tag, 0x10, offset, len(data)))
            self.file.write(chunk(tag, data))
        self.frames += 1
        self.samples += len(pcm) // 2
        self.max_frame = max(self.max_frame, len(jpeg))
        # Bound lost data on abnormal process termination; normal stop patches headers.
        if self.frames % 10 == 0:
            self.file.flush()

    def close(self):
        if self.file.closed:
            return
        try:
            end_movi = self.file.tell()
            self.file.write(chunk(b'idx1', b''.join(self.index)))
            end = self.file.tell()
            self.file.seek(4)
            self.file.write(struct.pack('<I', end - 8))
            self.file.seek(12)
            self.file.write(self.header())
            self.file.seek(self.movi + 4)
            self.file.write(struct.pack('<I', end_movi - self.movi - 8))
        finally:
            self.file.close()


class MediaHub:
    def __init__(self, receiver, directory):
        self.receiver = receiver
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.audio = deque(maxlen=30)
        self.video = deque(maxlen=8)
        self.sequence = 0
        self.audio_updated = 0.0
        self.session = None
        self.offset = 0.0
        self.recorder = None
        self.last_result = {'state': 'idle', 'filename': '', 'error': ''}

    def video_frame(self, data, session, timestamp, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if session != self.session:
                self.audio.clear()
                self.video.clear()
                self.audio_updated = 0.0
                self.session = session
                self.offset = now - timestamp
            self.video.append((self.media_time(timestamp, now), data))

    def media_time(self, timestamp, now):
        return min(now + 0.2, max(now - 1.0, timestamp + self.offset))

    def audio_frame(self, data, session, timestamp):
        now = time.monotonic()
        with self.lock:
            if session != self.session or not self.video or now - self.video[-1][0] > 2:
                return False
            start = self.media_time(timestamp, now)
            # No overlap/replay in the PCM timeline.
            if self.audio and start <= self.audio[-1][1]:
                return False
            self.sequence += 1
            self.audio.append((self.sequence, start, data))
            self.audio_updated = now
            return True

    def pcm_window(self, start, duration):
        result = bytearray(round(duration * RATE) * 2)
        with self.lock:
            packets = list(self.audio)
        for _, at, data in packets:
            first = round((at - start) * RATE)
            left, right = max(0, first), min(len(result) // 2, first + len(data) // 2)
            if left < right:
                source = (left - first) * 2
                result[left * 2:right * 2] = data[source:source + (right - left) * 2]
        return bytes(result)

    def playback(self, after):
        with self.lock:
            packets = [(seq, data) for seq, at, data in self.audio
                       if seq > after and time.monotonic() - at < 0.5]
            return self.sequence, b''.join(data for _, data in packets[-4:])

    def info(self):
        with self.lock:
            return {'audio': {'state': 'receiving' if time.monotonic() - self.audio_updated < 1 else 'off',
                              'sample_rate': RATE},
                    'recording': self.recorder.info() if self.recorder else dict(self.last_result),
                    'recording_dir': str(self.directory)}

    def start(self):
        with self.lock:
            if self.recorder:
                raise ValueError('Запись уже идёт')
            if not self.video or time.monotonic() - self.video[-1][0] > 2:
                raise ValueError('Для записи подключи телефон')
            if time.monotonic() - self.audio_updated > 1:
                raise ValueError('Включи микрофон на телефоне и дождись звука')
            self.directory.mkdir(parents=True, exist_ok=True)
            name = time.strftime('camera-%Y%m%d-%H%M%S-') + secrets.token_hex(3) + '.avi'
            self.recorder = Recording(self, self.directory / name)
            self.recorder.thread.start()
            return self.recorder.info()

    def stop(self):
        with self.lock:
            recorder = self.recorder
        if recorder:
            recorder.stop_at = time.monotonic()
            recorder.stop.set()
            recorder.thread.join(timeout=10)
            if recorder.thread.is_alive():
                raise OSError('Запись ещё завершается. Подожди.')
        with self.lock:
            return dict(self.last_result)

    def close(self):
        self.stop()
        with self.lock:
            self.audio.clear()
            self.video.clear()


class Recording:
    def __init__(self, hub, path):
        self.hub = hub
        self.path = path
        self.stop = threading.Event()
        self.stop_at = float('inf')
        self.started = time.monotonic()
        self.frames = 0
        self.error = ''
        self.state = 'recording'
        self.thread = threading.Thread(target=self.run, daemon=True, name='recording')

    def info(self):
        return {'state': self.state, 'filename': self.path.name,
                'seconds': round(self.frames / FPS, 1), 'error': self.error}

    def run(self):
        from PIL import Image, ImageOps
        writer = None
        last_jpeg = last_source = None
        tick = 0
        try:
            writer = AviWriter(self.path)
            while True:
                at = self.started + tick / FPS
                # Small shared playout buffer lets video and audio arrive independently.
                if self.stop.is_set() and at + 1 / FPS > self.stop_at:
                    break
                if self.stop.wait(max(0, at + 0.35 - time.monotonic())):
                    if at + 1 / FPS > self.stop_at:
                        break
                    time.sleep(max(0, at + 0.35 - time.monotonic()))
                with self.hub.lock:
                    source = next((data for when, data in reversed(self.hub.video) if when <= at + 0.05), None)
                    fresh = bool(self.hub.video and time.monotonic() - self.hub.video[-1][0] < 2)
                if not fresh:
                    break
                if source is None:
                    source = last_source
                if source is not last_source or last_jpeg is None:
                    if source is None:
                        tick += 1
                        continue
                    with Image.open(io.BytesIO(source)) as image:
                        if image.width * image.height > 4096 * 4096:
                            raise ValueError('Слишком большой кадр')
                        contained = ImageOps.contain(image.convert('RGB'), (WIDTH, HEIGHT))
                    canvas = Image.new('RGB', (WIDTH, HEIGHT))
                    canvas.paste(contained, ((WIDTH - contained.width) // 2, (HEIGHT - contained.height) // 2))
                    buffer = io.BytesIO()
                    canvas.save(buffer, 'JPEG', quality=85)
                    last_jpeg, last_source = buffer.getvalue(), source
                writer.write(last_jpeg, self.hub.pcm_window(at, 1 / FPS))
                self.frames += 1
                tick += 1
                if time.monotonic() - (at + 0.35) > 2:
                    raise OSError('Компьютер не успевает записывать видео. Запись остановлена.')
        except Exception as error:
            self.error = str(error) if isinstance(error, (OSError, ValueError)) else type(error).__name__
        finally:
            if writer:
                try:
                    writer.close()
                except OSError as error:
                    self.error = str(error)
            if self.frames == 0:
                self.error = self.error or 'Запись слишком короткая: нет кадров'
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.state = 'error' if self.error else 'saved'
            with self.hub.lock:
                self.hub.last_result = self.info()
                if self.hub.recorder is self:
                    self.hub.recorder = None

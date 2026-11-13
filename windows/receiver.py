"""Local JPEG/PCM receiver with explicit AVI recording, Python 3.11+."""
import argparse
import hmac
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import math
from media import MediaHub, MAX_AUDIO
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote_plus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_FRAME = 4 * 1024 * 1024
AUTH_ATTEMPTS = 5
AUTH_COOLDOWN = 60

def new_code():
    return f'{secrets.randbelow(10000):04d}'

PAGE = b'''<!doctype html><meta charset="utf-8"><title>Wi-Fi Camera</title>
<style>html,body{margin:0;background:black;width:100%;height:100%;overflow:hidden}img{width:100%;height:100%;object-fit:contain}p{color:white;position:absolute;top:0}</style>
<img id="v"><p id="s">Waiting for phone</p><script>
const v=document.getElementById('v'),s=document.getElementById('s');
let old; function clearFrame(){v.removeAttribute('src');if(old)URL.revokeObjectURL(old);old=undefined;s.hidden=false}
async function poll(){try{let r=await fetch('/frame',{cache:'no-store'});
if(r.status===200){let u=URL.createObjectURL(await r.blob());v.src=u;if(old)URL.revokeObjectURL(old);old=u;s.hidden=true}
else{clearFrame()}}catch(e){clearFrame()}
setTimeout(poll,80)}poll();</script>'''
PAGE = PAGE.replace(b'Waiting for phone', 'Ожидание подключения телефона'.encode('utf-8'))
APP_PAGE = Path(__file__).with_name('app.html').read_bytes()

@lru_cache(maxsize=1)
def hardware_addresses():
    if os.name != 'nt':
        return []
    # Read physical IPv4 interfaces through Windows IP Helper: no shell startup.
    import ctypes as c
    from ctypes import wintypes as w
    class Address(c.Structure):
        _fields_ = [('pointer', c.c_void_p), ('length', c.c_int)]
    class Unicast(c.Structure):
        pass
    Unicast._fields_ = [('length', w.ULONG), ('flags', w.DWORD),
                        ('next', c.POINTER(Unicast)), ('address', Address)]
    class Adapter(c.Structure):
        pass
    Adapter._fields_ = [
        ('length', w.ULONG), ('index', w.DWORD), ('next', c.POINTER(Adapter)),
        ('name', c.c_char_p), ('unicast', c.POINTER(Unicast)),
        ('anycast', c.c_void_p), ('multicast', c.c_void_p), ('dns', c.c_void_p),
        ('suffix', w.LPWSTR), ('description', w.LPWSTR), ('friendly', w.LPWSTR),
        ('physical', w.BYTE * 8), ('physical_length', w.DWORD),
        ('flags', w.DWORD), ('mtu', w.DWORD), ('kind', w.DWORD), ('status', c.c_int)]
    try:
        api = c.WinDLL('iphlpapi.dll').GetAdaptersAddresses
        api.argtypes = [w.ULONG, w.ULONG, c.c_void_p, c.POINTER(Adapter), c.POINTER(w.ULONG)]
        api.restype = w.ULONG
        size = w.ULONG(15000)
        for _ in range(2):
            buffer = c.create_string_buffer(size.value)
            head = c.cast(buffer, c.POINTER(Adapter))
            code = api(socket.AF_INET, 14, None, head, c.byref(size))
            if code != 111:
                break
        if code != 0:
            return []
        values = []
        while head:
            adapter = head.contents
            description = ((adapter.description or '') + ' ' + (adapter.friendly or '')).lower()
            virtual = any(term in description for term in (
                'virtual', 'hyper-v', 'wsl', 'docker', 'vmware', 'vpn', 'tailscale', 'wireguard', 'loopback'))
            if adapter.status == 1 and adapter.kind in (6, 71) and not virtual:
                current = adapter.unicast
                while current:
                    address = current.contents.address
                    if address.pointer and address.length >= 8:
                        raw = c.string_at(address.pointer, address.length)
                        if int.from_bytes(raw[:2], 'little') == socket.AF_INET:
                            values.append(socket.inet_ntoa(raw[4:8]))
                    current = current.contents.next
            head = adapter.next
        return values
    except (OSError, ValueError, AttributeError):
        return []

def local_addresses(port):
    primary = None
    try:
        # UDP connect selects a route only; no packet is sent to this test address.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(('192.0.2.1', 9))
            primary = route.getsockname()[0]
    except OSError:
        pass
    try:
        candidates = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        candidates = []
    addresses = []
    for value in candidates:
        ip = ipaddress.ip_address(value)
        if ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local:
            addresses.append(value if port == 8765 else f'{value}:{port}')
    addresses = sorted(set(addresses))
    preferred = [value if port == 8765 else f'{value}:{port}' for value in hardware_addresses()]
    if not preferred and primary:
        preferred = [primary if port == 8765 else f'{primary}:{port}']
    addresses.sort(key=lambda value: (value not in preferred, value))
    return addresses

def open_desktop(port):
    profile = Path(__file__).with_name('work') / 'desktop-profile'
    profile.mkdir(parents=True, exist_ok=True)
    for base in (os.environ.get('ProgramFiles(x86)', ''), os.environ.get('ProgramFiles', '')):
        browser = Path(base) / 'Microsoft/Edge/Application/msedge.exe'
        if browser.is_file():
            subprocess.Popen([str(browser), f'--app=http://127.0.0.1:{port}/app',
                              '--window-size=980,760', f'--user-data-dir={profile}',
                              '--no-first-run', '--no-default-browser-check', '--disable-background-networking'])
            return
    import webbrowser
    webbrowser.open(f'http://127.0.0.1:{port}/app')

class Receiver(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, token, phone=None, data_dir=None):
        if len(token) != 4 or not token.isascii() or not token.isdigit():
            raise ValueError('Connection code must contain exactly four ASCII digits')
        super().__init__(address, Handler)
        self.token = token
        self.phone = phone
        self.frame = None
        self.updated = 0.0
        self.lock = threading.Lock()
        self.auth_failures = 0
        self.auth_blocked_until = 0.0
        self.phone_name = ''
        self.virtual_camera = None
        self.capture_dir = Path(data_dir or Path(__file__).with_name('user-data')) / 'captures'
        self.media = MediaHub(self, self.capture_dir.parent / 'recordings')
        self.addresses = local_addresses(self.server_port)
        self.slots = threading.BoundedSemaphore(8)

    def server_close(self):
        if hasattr(self, 'media'):
            self.media.close()
        super().server_close()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

class Handler(BaseHTTPRequestHandler):
    def reject_upload(self, status):
        # Drain a bounded fixed-length upload before closing, so Windows clients
        # receive the HTTP error instead of a TCP reset while still sending JPEG.
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = 0
        if 0 < size <= MAX_FRAME:
            self.rfile.read(size)
        self.reply(status)

    def is_local(self):
        return (ipaddress.ip_address(self.client_address[0]).is_loopback and
                self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',
                                             f'localhost:{self.server.server_port}'))

    def reply_json(self, status, value):
        self.reply(status, json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *args):
        pass  # Never log credentials or frame data.

    def reply(self, status, data=b'', kind='text/plain'):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        if status == 204:
            from urllib.parse import quote
            self.send_header('X-Receiver-Name', quote(socket.gethostname(), safe=''))
        if status == 429:
            self.send_header('Retry-After', str(AUTH_COOLDOWN))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Video can only be read on this computer, never over the LAN.
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            return self.reply(403)
        if self.headers.get('Host') not in (
            '127.0.0.1:%d' % self.server.server_port,
            'localhost:%d' % self.server.server_port,
        ):
            return self.reply(403)
        if self.path == '/':
            return self.reply(200, PAGE, 'text/html; charset=utf-8')
        if self.path == '/app':
            return self.reply(200, APP_PAGE, 'text/html; charset=utf-8')
        if self.path == '/api/status':
            with self.server.lock:
                state = ('waiting' if self.server.frame is None else
                         'connected' if time.monotonic() - self.server.updated < 2 else 'lost')
                value = {'state': state, 'phone_name': self.server.phone_name,
                         'virtual_camera': self.server.virtual_camera.info if self.server.virtual_camera else {'state': 'disabled'},
                         'code': self.server.token, 'addresses': self.server.addresses,
                         'capture_dir': str(self.server.capture_dir), 'version': '0.4.0'}
            value.update(self.server.media.info())
            return self.reply_json(200, value)
        if self.path == '/api/audio':
            try:
                after = max(0, int(self.headers.get('X-Audio-After', '0')))
            except ValueError:
                return self.reply(400)
            sequence, pcm = self.server.media.playback(after)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(pcm)))
            self.send_header('X-Audio-Sequence', str(sequence))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(pcm)
            return
        if self.path == '/frame':
            with self.server.lock:
                frame = self.server.frame if time.monotonic() - self.server.updated < 2 else None
            return self.reply(200, frame, 'image/jpeg') if frame else self.reply(204)
        self.reply(404)

    def do_POST(self):
        if self.path in ('/api/record/start', '/api/record/stop'):
            action = 'record-start' if self.path.endswith('/start') else 'record-stop'
            if not self.is_local() or self.headers.get('X-Camera-Action') != action:
                return self.reply(403)
            try:
                result = self.server.media.start() if action == 'record-start' else self.server.media.stop()
            except ValueError as error:
                return self.reply_json(409, {'error': str(error)})
            except OSError as error:
                return self.reply_json(500, {'error': str(error)})
            return self.reply_json(200, result)
        if self.path == '/api/shutdown':
            if not self.is_local() or self.headers.get('X-Camera-Action') != 'shutdown':
                return self.reply(403)
            self.reply_json(200, {'stopped': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path == '/api/snapshot':
            if not self.is_local() or self.headers.get('X-Camera-Action') != 'snapshot':
                return self.reply(403)
            with self.server.lock:
                frame = self.server.frame if time.monotonic() - self.server.updated < 2 else None
            if frame is None:
                return self.reply_json(409, {'error': 'Нет свежего кадра для снимка'})
            name = time.strftime('camera-%Y%m%d-%H%M%S-') + secrets.token_hex(3) + '.jpg'
            try:
                self.server.capture_dir.mkdir(parents=True, exist_ok=True)
                (self.server.capture_dir / name).write_bytes(frame)
            except OSError:
                return self.reply_json(500, {'error': 'Не удалось сохранить снимок'})
            return self.reply_json(200, {'filename': name})
        if self.path not in ('/upload', '/audio'):
            return self.reply(404)
        if self.server.phone and self.client_address[0] != self.server.phone:
            return self.reject_upload(403)
        supplied = self.headers.get('Authorization', '').encode('utf-8')
        expected = ('Bearer ' + self.server.token).encode('ascii')
        with self.server.lock:
            now = time.monotonic()
            if now < self.server.auth_blocked_until:
                auth_status = 429
            elif not hmac.compare_digest(supplied, expected):
                self.server.auth_failures += 1
                auth_status = 401
                if self.server.auth_failures >= AUTH_ATTEMPTS:
                    self.server.auth_failures = 0
                    self.server.auth_blocked_until = now + AUTH_COOLDOWN
                    auth_status = 429
            else:
                auth_status = 204
        if auth_status != 204:
            return self.reject_upload(auth_status)
        if self.headers.get('Transfer-Encoding'):
            return self.reject_upload(400)
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.reply(400)
        audio = self.path == '/audio'
        if not (2 if audio else 4) <= size <= (MAX_AUDIO if audio else MAX_FRAME):
            return self.reply(413)
        if self.headers.get('Content-Type') != ('audio/pcm;rate=16000;channels=1;bits=16' if audio else 'image/jpeg'):
            return self.reject_upload(415)
        session = self.headers.get('X-Media-Session', 'legacy')
        if not 1 <= len(session) <= 64:
            return self.reject_upload(400)
        try:
            media_time = float(self.headers.get('X-Media-Time', str(time.monotonic() * 1000))) / 1000
            if not math.isfinite(media_time) or media_time < 0:
                raise ValueError()
        except ValueError:
            return self.reject_upload(400)
        data = self.rfile.read(size)
        if audio:
            if len(data) != size or size % 2:
                return self.reply(400)
            with self.server.lock:
                paired = self.server.phone == self.client_address[0] and time.monotonic() - self.server.updated < 2
            if not paired:
                return self.reply(409)
            accepted = self.server.media.audio_frame(data, session, media_time)
            return self.reply(204 if accepted else 409)
        if len(data) != size or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            return self.reply(400)
        with self.server.lock:
            # Bind to the first sender only after a valid authenticated frame.
            if self.server.phone and self.server.phone != self.client_address[0]:
                sender_allowed = False
            else:
                sender_allowed = True
                self.server.phone = self.client_address[0]
                self.server.auth_failures = 0
                self.server.phone_name = unquote_plus(self.headers.get('X-Phone-Name', 'Телефон'))[:100]
                self.server.frame = data
                self.server.updated = time.monotonic()
        if sender_allowed:
            self.server.media.video_frame(data, session, media_time)
        self.reply(204 if sender_allowed else 403)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='0.0.0.0', help='IPv4 interface to listen on')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--phone', help='Optional allowed phone IPv4 address')
    parser.add_argument('--desktop', action='store_true', help='Open the local desktop control window')
    parser.add_argument('--no-camera', action='store_true', help='Run preview only, without the Windows camera output')
    args = parser.parse_args()
    if args.phone:
        ipaddress.IPv4Address(args.phone)
    token = new_code()
    with Receiver((args.bind, args.port), token, args.phone) as server:
        if not args.no_camera and os.name == 'nt':
            from native_camera import NativeCamera
            server.virtual_camera = NativeCamera(server)
            server.virtual_camera.start()
        print('Phone: http://<PC Wi-Fi IPv4>:%d' % args.port)
        print('Код подключения:', token)
        print('OBS Browser URL: http://127.0.0.1:%d/' % args.port)
        print('Trusted private Wi-Fi only. HTTP is NOT encrypted. Ctrl+C stops receiver.')
        if args.desktop:
            open_desktop(args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server.virtual_camera:
                server.virtual_camera.close()

if __name__ == '__main__':
    main()

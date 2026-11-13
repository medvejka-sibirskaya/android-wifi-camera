"""Feed the MIT-licensed Unity Capture DirectShow filter without OBS or Unity.

Wire format: schellingb/UnityCapture Source/shared.inl, commit
3ed54c325e0ad71afcf4f246c07e5e17b3d7f2d2. No camera frames are saved.
"""
import ctypes as c
from ctypes import wintypes as w
import io
import os
import struct
import threading
import time

DEVICE_NAME = 'Wi-Fi Camera'
WIDTH, HEIGHT = 1280, 720


def registered():
    if os.name != 'nt':
        return False
    import winreg
    path = r'SOFTWARE\Classes\CLSID\{5C2CD55C-92AD-4999-8666-912BD3E70010}\InprocServer32'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                           winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            dll = winreg.QueryValueEx(key, None)[0]
            return os.path.isfile(dll)
    except OSError:
        return False


class SharedSender:
    """Bounded Win32 IPC; the capture filter owns the shared mapping."""
    def __init__(self):
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateEventW': ([c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            'OpenMutexW': ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            'OpenEventW': ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            'OpenFileMappingW': ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            'MapViewOfFile': ([w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.c_size_t], c.c_void_p),
            'UnmapViewOfFile': ([c.c_void_p], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'SetEvent': ([w.HANDLE], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'ReleaseMutex': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.mutex = self.sent = self.mapping = self.view = None
        # Must exist before the receiving filter opens its shared memory.
        self.want = self.k.CreateEventW(None, False, False, 'UnityCapture_Want')
        if not self.want:
            raise c.WinError(c.get_last_error())

    def _disconnect(self):
        if self.view:
            self.k.UnmapViewOfFile(self.view)
            self.view = None
        for name in ('mapping', 'sent', 'mutex'):
            handle = getattr(self, name)
            if handle:
                self.k.CloseHandle(handle)
                setattr(self, name, None)

    def connect(self):
        if self.view:
            return True
        self.mutex = self.k.OpenMutexW(0x100001, False, 'UnityCapture_Mutx')
        self.sent = self.k.OpenEventW(2, False, 'UnityCapture_Sent')
        self.mapping = self.k.OpenFileMappingW(6, False, 'UnityCapture_Data')
        if self.mutex and self.sent and self.mapping:
            # Map exactly the fixed 720p header+frame, not an unbounded region.
            self.view = self.k.MapViewOfFile(self.mapping, 6, 0, 0, 32 + WIDTH * HEIGHT * 4)
        if not self.view:
            self._disconnect()
            return False
        return True

    def send(self, rgba):
        if len(rgba) != WIDTH * HEIGHT * 4:
            raise ValueError('Wrong RGBA frame size')
        if not self.connect():
            return False
        result = self.k.WaitForSingleObject(self.mutex, 200)
        if result not in (0, 0x80):
            return False
        try:
            capacity = c.c_uint32.from_address(self.view).value
            if capacity < len(rgba):
                return False
            header = struct.pack('<7i', WIDTH, HEIGHT, WIDTH, 0, 1, 0, 1000)
            c.memmove(self.view + 4, header, len(header))
            c.memmove(self.view + 32, rgba, len(rgba))
        finally:
            self.k.ReleaseMutex(self.mutex)
        self.k.SetEvent(self.sent)
        self.k.WaitForSingleObject(self.want, 0)
        return True

    def close(self):
        self._disconnect()
        if self.want:
            self.k.CloseHandle(self.want)
            self.want = None


def prepare_frame(jpeg):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(jpeg)) as source:
        if source.width * source.height > 4096 * 4096:
            raise ValueError('Image dimensions exceed camera limit')
        image = ImageOps.contain(source.convert('RGB'), (WIDTH, HEIGHT))
    canvas = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 255))
    canvas.paste(image, ((WIDTH-image.width)//2, (HEIGHT-image.height)//2))
    # Unity's texture convention is bottom-up, like DirectShow RGB DIBs.
    return canvas.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()


class NativeCamera:
    def __init__(self, receiver):
        self.receiver = receiver
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='native-camera')
        self.info = {'device': DEVICE_NAME, 'state': 'starting'}

    def start(self):
        self.thread.start()

    def run(self):
        sender = None
        try:
            if not registered():
                self.info = {'device': DEVICE_NAME, 'state': 'not_installed'}
                return
            from PIL import Image, ImageDraw
            blank = Image.new('RGBA', (WIDTH, HEIGHT), (11, 37, 45, 255))
            ImageDraw.Draw(blank).text((40, 40), 'Wi-Fi Camera - waiting for phone', fill='white')
            blank = blank.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
            sender = SharedSender()
            previous, pixels = None, blank
            while not self.stop.is_set():
                with self.receiver.lock:
                    frame = self.receiver.frame if time.monotonic() - self.receiver.updated < 2 else None
                if frame is not previous:
                    try:
                        pixels = prepare_frame(frame) if frame else blank
                    except (OSError, ValueError):
                        pixels = blank
                    previous = frame
                delivered = sender.send(pixels)
                self.info = {'device': DEVICE_NAME, 'state': 'sending' if delivered else 'ready'}
                self.stop.wait(0.1)
        except Exception as error:
            self.info = {'device': DEVICE_NAME, 'state': 'error', 'error': type(error).__name__}
        finally:
            if sender:
                sender.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)

"""Open an existing local receiver, or start it with its desktop window."""
import sys
import os
import subprocess
from pathlib import Path
import urllib.error
import urllib.request
from receiver import main, open_desktop

if __name__ == '__main__':
    runtime = Path(__file__).parent / 'work/runtime/Scripts/pythonw.exe'
    if runtime.is_file() and Path(sys.prefix).resolve() != runtime.parents[1].resolve():
        subprocess.Popen([str(runtime), '-B', str(Path(__file__).resolve())],
                         cwd=Path(__file__).parent, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
        sys.exit(0)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=2) as reply:
            available = reply.status == 200
    except (OSError, urllib.error.URLError):
        available = False
    if available:
        open_desktop(8765)
    else:
        sys.argv = [sys.argv[0], '--desktop']
        main()

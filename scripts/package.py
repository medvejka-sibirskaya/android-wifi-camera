"""Create distributable archives without local toolchains, caches or signing keys."""
import hashlib
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs'
OUT.mkdir(exist_ok=True)
for source, destination in [('QUICKSTART.md', 'БЫСТРЫЙ-СТАРТ.md'), ('VERIFICATION.md', 'BUILD-STATUS.md')]:
    if (ROOT / source).is_file():
        shutil.copyfile(ROOT / source, OUT / destination)

def archive(target, entries):
    with ZipFile(target, 'w', ZIP_DEFLATED) as result:
        for source, destination in entries:
            result.write(source, destination)
    with ZipFile(target) as result:
        assert result.testzip() is None

receiver_files = [(p, p.name) for p in (ROOT / 'windows').iterdir() if p.is_file() and p.suffix in ('.py', '.js', '.cmd', '.html', '.ps1', '.txt')]
receiver_files.extend((p, 'camera-component/' + p.name) for p in (ROOT / 'windows/camera-component').iterdir() if p.is_file())
receiver_files.append((OUT / 'БЫСТРЫЙ-СТАРТ.md', 'README.md'))
archive(OUT / 'WifiCamera-Windows.zip', receiver_files)

excluded = {'work', 'outputs', 'build', 'user-data', '.gradle', '.git', '.idea', '__pycache__', '.kotlin'}
source_files = []
for folder in ('android', 'windows', 'scripts', 'docs'):
    for item in (ROOT / folder).rglob('*'):
        relative = item.relative_to(ROOT)
        if not item.is_file() or any(part in excluded for part in relative.parts):
            continue
        if item.name == 'local.properties' or item.suffix.lower() in ('.jks', '.keystore', '.pyc'):
            continue
        source_files.append((item, str(relative).replace('\\', '/')))
for name in ('README.md', '.gitignore', 'build.cmd'):
    source_files.append((ROOT / name, name))
source_files.append((OUT / 'БЫСТРЫЙ-СТАРТ.md', 'QUICKSTART.md'))
source_files.append((OUT / 'BUILD-STATUS.md', 'VERIFICATION.md'))
archive(OUT / 'WifiCamera-Source.zip', sorted(source_files, key=lambda row: row[1]))

for filename in ('WifiCamera-Windows.zip', 'WifiCamera-Source.zip'):
    with ZipFile(OUT / filename) as result:
        for name in result.namelist():
            assert not name.startswith(('/', '\\')) and '..' not in Path(name).parts
            assert not any(part in excluded for part in Path(name).parts)
            assert not name.endswith(('.jks', '.keystore', '.pyc', 'local.properties'))

artifacts = [OUT / name for name in (
    'WifiCamera-debug.apk', 'WifiCamera-Windows.zip', 'WifiCamera-Source.zip',
    'БЫСТРЫЙ-СТАРТ.md', 'BUILD-STATUS.md', 'android-lint.html',
) if (OUT / name).is_file()]
(OUT / 'SHA256SUMS.txt').write_text(''.join(
    hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name + '\n' for file in artifacts
), encoding='utf-8')
for item in artifacts:
    print(item.name, item.stat().st_size)
print('Archives verified; no local configuration, toolchains or keys included.')

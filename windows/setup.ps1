$ErrorActionPreference = 'Stop'
if ([System.IO.Path]::GetPathRoot($PSScriptRoot) -notin @('D:\','F:\')) {
    throw 'Unpack this project on D: or F: before setup.'
}
$work = Join-Path $PSScriptRoot 'work'
New-Item -ItemType Directory -Force (Join-Path $work 'tmp') | Out-Null
$env:TEMP = Join-Path $work 'tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
& py -3 -c "import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8, 'Python 3.11+ x64 is required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ x64 is required.' }
$runtime = Join-Path $work 'runtime'
if (!(Test-Path -LiteralPath (Join-Path $runtime 'Scripts\python.exe'))) {
    & py -3 -m venv $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ x64 is required.' }
}
& (Join-Path $runtime 'Scripts\python.exe') -m pip install --cache-dir (Join-Path $work 'pip-cache') -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the image decoder.' }
$installer = Join-Path $PSScriptRoot 'install-camera.ps1'
$process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$installer`"" -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Camera installation failed. Check for another Unity Capture installation.' }
Write-Output 'Setup complete. Start start.cmd, connect the phone, select Wi-Fi Camera in your conferencing app.'

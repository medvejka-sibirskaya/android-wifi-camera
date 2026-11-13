param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$component = Join-Path $PSScriptRoot 'camera-component'
$entries = @(
    @{File='UnityCaptureFilter64.dll'; Hash='72812F5363D8ECB45632253F8C8C888844B1B62E27616F3C8CC21064CCDE25E5'; Id='{5C2CD55C-92AD-4999-8666-912BD3E70010}'; View=[Microsoft.Win32.RegistryView]::Registry64; Exe='System32\regsvr32.exe'},
    @{File='UnityCaptureFilter32.dll'; Hash='AA3EBDF03DEA7F3AAB3DD7B724751F49ED71672256B57C6A19AA6809CABF30BA'; Id='{5C2CD55C-92AD-4999-8666-912BD3E70020}'; View=[Microsoft.Win32.RegistryView]::Registry32; Exe='SysWOW64\regsvr32.exe'}
)
# Validate both files and existing ownership before changing either registration.
foreach ($entry in $entries) {
    $dll = Join-Path $component $entry.File
    if ((Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash -ne $entry.Hash) { throw "Component hash mismatch: $dll" }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,$entry.View)
    try {
        $key = $base.OpenSubKey("SOFTWARE\Classes\CLSID\$($entry.Id)\InprocServer32")
        if ($key) {
            try { $existing = $key.GetValue('') } finally { $key.Dispose() }
            if ([System.IO.Path]::GetFullPath($existing) -ne [System.IO.Path]::GetFullPath($dll)) {
                throw "Another Unity Capture installation owns this camera: $existing. No changes made."
            }
        }
    } finally { $base.Dispose() }
}
foreach ($entry in $entries) {
    $dll = Join-Path $component $entry.File
    $exe = Join-Path $env:SystemRoot $entry.Exe
    if ($Uninstall) { & $exe /s /u $dll }
    else { & $exe /s $dll '/i:UnityCaptureName=Wi-Fi Camera' }
    if ($LASTEXITCODE -ne 0) { throw "Camera registration failed ($LASTEXITCODE). Run this installer as administrator." }
}
Write-Output $(if ($Uninstall) {'Wi-Fi Camera removed.'} else {'Wi-Fi Camera installed (32-bit and 64-bit). Restart camera applications to refresh their device lists.'})

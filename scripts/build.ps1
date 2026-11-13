param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$work = Join-Path $project 'work'
$tools = Join-Path $work 'toolchain'
$jdk = Get-ChildItem -LiteralPath $tools -Directory -Filter 'jdk-17*' | Select-Object -First 1
if (-not $jdk) { throw 'Portable JDK 17 missing in work/toolchain. See README.' }
$env:JAVA_HOME = $jdk.FullName
$env:ANDROID_HOME = Join-Path $tools 'android-sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:GRADLE_USER_HOME = Join-Path $work 'gradle-cache'
$env:ANDROID_USER_HOME = Join-Path $work 'android-user'
$env:TEMP = Join-Path $work 'tmp'
$env:TMP = $env:TEMP
$javaHomeForTask = Join-Path $work 'java-home'
@($env:TEMP, $env:ANDROID_USER_HOME, $javaHomeForTask, (Join-Path $project 'outputs')) | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}
$env:JAVA_TOOL_OPTIONS = '-Duser.home="' + $javaHomeForTask + '" -Djava.io.tmpdir="' + $env:TEMP + '"'
$env:PATH = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:PATH"
$env:PYTHONDONTWRITEBYTECODE = '1'
$sdkPath = $env:ANDROID_HOME.Replace('\', '/')
Set-Content -LiteralPath (Join-Path $project 'android/local.properties') -Value "sdk.dir=$sdkPath" -Encoding ascii
Push-Location $project
try {
    if (-not $SkipTests) {
        & py -3 -m unittest discover -s windows -v
        if ($LASTEXITCODE -ne 0) { throw 'Receiver tests failed' }
    }
    Push-Location (Join-Path $project 'android')
    try {
        if (-not (Test-Path -LiteralPath './gradlew.bat')) {
            & "$tools\gradle-8.9\bin\gradle.bat" wrapper --gradle-version 8.9 --distribution-type bin --gradle-distribution-sha256-sum d725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab --no-daemon
            if ($LASTEXITCODE -ne 0) { throw 'Wrapper generation failed' }
        }
        & ./gradlew.bat :app:assembleDebug :app:lintDebug --no-daemon --console=plain
        if ($LASTEXITCODE -ne 0) { throw 'Android build/lint failed' }
        Copy-Item -LiteralPath './app/build/outputs/apk/debug/app-debug.apk' -Destination (Join-Path $project 'outputs/WifiCamera-debug.apk') -Force
        Copy-Item -LiteralPath './app/build/reports/lint-results-debug.html' -Destination (Join-Path $project 'outputs/android-lint.html') -Force
        Write-Output ('APK: ' + (Join-Path $project 'outputs/WifiCamera-debug.apk'))
    } finally { Pop-Location }
} finally { Pop-Location }

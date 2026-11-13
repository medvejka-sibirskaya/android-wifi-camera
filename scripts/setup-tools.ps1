param([switch]$AcceptAndroidLicense)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not $AcceptAndroidLicense) {
    throw 'Read https://developer.android.com/studio/terms and rerun with -AcceptAndroidLicense to install Android SDK.'
}
$project = Split-Path $PSScriptRoot -Parent
$work = Join-Path $project 'work'
$tools = Join-Path $work 'toolchain'
$downloads = Join-Path $tools 'downloads'
New-Item -ItemType Directory -Path $downloads, "$work/tmp", "$work/android-user", "$work/java-home", "$work/logs" -Force | Out-Null
function Get-VerifiedArchive($name, $url, $algorithm, $expected) {
    $file = Join-Path $downloads $name
    if (-not (Test-Path -LiteralPath $file)) {
        Write-Output "Downloading $name"
        Invoke-WebRequest -Uri $url -OutFile $file
    }
    if ((Get-FileHash -LiteralPath $file -Algorithm $algorithm).Hash.ToLowerInvariant() -ne $expected) {
        throw "Checksum mismatch: $file. Do not extract this file."
    }
    Write-Output "Verified $name"
}
Get-VerifiedArchive 'jdk17.zip' 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_x64_windows_hotspot_17.0.21_9.zip' 'SHA256' '98518ecf554465057ee5509462631deeee374bc368c12a411d225cd29381f84b'
Get-VerifiedArchive 'gradle-8.9-bin.zip' 'https://services.gradle.org/distributions/gradle-8.9-bin.zip' 'SHA256' 'd725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab'
# Google's official repository2-1.xml publishes this archive's SHA-1 checksum.
Get-VerifiedArchive 'android-command-tools-19.zip' 'https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip' 'SHA1' '54a582f3bf73e04253602f2d1c80bd5868aac115'
if (-not (Test-Path -LiteralPath "$tools/jdk-17.0.21+9/bin/java.exe")) {
    Expand-Archive -LiteralPath "$downloads/jdk17.zip" -DestinationPath $tools -Force
}
if (-not (Test-Path -LiteralPath "$tools/gradle-8.9/bin/gradle.bat")) {
    Expand-Archive -LiteralPath "$downloads/gradle-8.9-bin.zip" -DestinationPath $tools -Force
}
if (-not (Test-Path -LiteralPath "$tools/android-sdk/cmdline-tools/19.0/bin/sdkmanager.bat")) {
    Expand-Archive -LiteralPath "$downloads/android-command-tools-19.zip" -DestinationPath "$tools/command-tools-extract" -Force
    New-Item -ItemType Directory -Path "$tools/android-sdk/cmdline-tools/19.0" -Force | Out-Null
    Get-ChildItem -LiteralPath "$tools/command-tools-extract/cmdline-tools" | Copy-Item -Destination "$tools/android-sdk/cmdline-tools/19.0" -Recurse -Force
}
$env:JAVA_HOME = Join-Path $tools 'jdk-17.0.21+9'
$env:ANDROID_HOME = Join-Path $tools 'android-sdk'
$env:ANDROID_USER_HOME = Join-Path $work 'android-user'
$env:TEMP = Join-Path $work 'tmp'
$env:TMP = $env:TEMP
$env:JAVA_TOOL_OPTIONS = '-Duser.home="' + "$work/java-home" + '" -Djava.io.tmpdir="' + $env:TEMP + '"'
1..100 | ForEach-Object { 'y' } | & "$tools/android-sdk/cmdline-tools/19.0/bin/sdkmanager.bat" "--sdk_root=$env:ANDROID_HOME" 'platforms;android-35' 'build-tools;34.0.0' 'platform-tools'
if ($LASTEXITCODE -ne 0) { throw 'Android SDK installation failed' }
foreach ($required in @('platforms/android-35/android.jar', 'build-tools/34.0.0/aapt2.exe', 'platform-tools/adb.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $env:ANDROID_HOME $required))) { throw "SDK file missing: $required" }
}
Write-Output "Portable Android toolchain ready: $tools"
Write-Output 'Run build.cmd to build the APK. Tools remain installed for future builds.'

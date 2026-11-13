# Unity Capture filter

Upstream: https://github.com/schellingb/UnityCapture

Pinned commit: 3ed54c325e0ad71afcf4f246c07e5e17b3d7f2d2

Unmodified upstream binaries from Install/:

- UnityCaptureFilter32.dll SHA256 AA3EBDF03DEA7F3AAB3DD7B724751F49ED71672256B57C6A19AA6809CABF30BA
- UnityCaptureFilter64.dll SHA256 72812F5363D8ECB45632253F8C8C888844B1B62E27616F3C8CC21064CCDE25E5

The DirectShow filter is MIT licensed; see LICENSE.txt. This product uses only
the filter, not Unity or the Unity plugin. Custom display name registration is
an upstream-supported feature. This is a user-mode COM filter, not a kernel driver.
Install once using install-camera.ps1; unregister before moving its DLLs.
An existing installation at another path is never overwritten automatically.

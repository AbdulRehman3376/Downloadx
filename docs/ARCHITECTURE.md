# Architecture

Page video -> content.js (button) -> background.js (service worker)
  -> chrome.runtime.sendNativeMessage -> downloadx_host.bat -> downloadx_host.py
  -> file saved in Downloads.

- Fixed extension ID comes from the key in manifest.json, so the native host manifest hard-codes allowed_origins.
- Registration: HKCU\Software\Google\Chrome\NativeMessagingHosts\com.downloadx.host -> %LOCALAPPDATA%\DownloadX\com.downloadx.host.json
- Logs: %LOCALAPPDATA%\DownloadX\host.log

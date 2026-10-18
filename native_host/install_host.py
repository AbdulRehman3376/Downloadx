"""Registers the DownloadX native host for the current Windows user (no admin needed).

Usage:  py install_host.py            (install)
        py install_host.py --uninstall
"""
import json
import os
import sys
from pathlib import Path

HOST_NAME = "com.downloadx.host"
EXTENSION_ID = "nobiidalhdhibgabpdlodcfcefojejnc"  # fixed by the "key" field in extension/manifest.json
REG_PATH = "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME


def main():
    if sys.platform != "win32":
        print("This installer is for Windows only.")
        return 1
    import winreg

    if "--uninstall" in sys.argv:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_PATH)
            print("Registry key removed.")
        except FileNotFoundError:
            print("Nothing to remove.")
        return 0

    here = Path(__file__).resolve().parent
    launcher = here / "downloadx_host.bat"
    if not launcher.exists():
        print("downloadx_host.bat not found next to this script.")
        return 1

    manifest = {
        "name": HOST_NAME,
        "description": "DownloadX native helper",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": ["chrome-extension://" + EXTENSION_ID + "/"],
    }
    out_dir = here.parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / (HOST_NAME + ".json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))

    print("Installed.")
    print("  Manifest :", manifest_path)
    print("  Launcher :", launcher)
    print("  Extension:", EXTENSION_ID)
    print("Now reload the extension in chrome://extensions and click Check helper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
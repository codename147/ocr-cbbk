# -*- coding: utf-8 -*-
"""Tro giup cai dat: kiem tra Tesseract, tao loi tat ngoai Desktop."""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

def shortcut():
    app = os.path.join(core.APP_DIR, "app.py")
    pyw = os.path.join(core.APP_DIR, ".venv", "Scripts", "pythonw.exe")
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Tra cuu ho so.lnk'));"
        f"$s.TargetPath='{pyw}';"
        f"$s.Arguments='\"{app}\"';"
        f"$s.WorkingDirectory='{core.APP_DIR}';"
        "$s.Description='Tra cuu ho so';"
        "$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    print("Da tao loi tat 'Tra cuu ho so' ngoai Desktop." if r.returncode == 0 else "Khong tao duoc loi tat.")

def check():
    cfg = core.load_config()
    import fitz  # noqa
    ok, msg = core.tesseract_info(cfg)
    print("Tesseract:", "OK -" if ok else "CHUA CO -", msg.encode("ascii", "replace").decode())
    sys.exit(0 if ok else 2)

if __name__ == "__main__":
    {"shortcut": shortcut, "check": check}[sys.argv[1]]()

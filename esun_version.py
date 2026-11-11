# -*- coding: utf-8 -*-
"""
esun_version.py — 程式碼版本指紋與存檔（D-Plan v4.3：code_version 須對應當天實際執行的程式）
  · 指紋 = 所有比賽程式（esun_*.py、dplan_validator.py）與設定檔（esun_sector.json、schema）內容的 sha256 前 12 碼
  · 改任何程式或參數，指紋就會變
  · 每個新指紋第一次出現時，把那一版程式打包成 esun_code_archive/code_<指紋>.zip
    晉級或進前 30 名要交程式碼時，用報告裡的 code_version 找對應的 zip
"""
import os, glob, hashlib, zipfile

ARCHIVE_DIR = "./esun_code_archive"
PATTERNS = ["esun_*.py", "dplan_validator.py", "esun_sector.json", "esun_override.json", "D-Plan_schema_v4_3.json", "esun_run.bat",
            "esun_prompts/*"]


def code_files():
    fs = set()
    for pat in PATTERNS:
        fs.update(f for f in glob.glob(pat) if os.path.isfile(f))
    return sorted(fs)


def fingerprint():
    h = hashlib.sha256()
    for f in code_files():
        h.update(f.encode("utf-8"))
        with open(f, "rb") as fh:
            h.update(fh.read().replace(b"\r\n", b"\n"))       # 忽略 Windows 換行差異
    return h.hexdigest()[:12]


def archive():
    """回傳 code_version 字串，並確保該版本已打包存檔。"""
    fp = fingerprint()
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    zp = os.path.join(ARCHIVE_DIR, f"code_{fp}.zip")
    if not os.path.exists(zp):
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in code_files():
                z.write(f)
    return f"sha256:{fp}"


if __name__ == "__main__":
    print("目前版本：", archive())
    print("存檔：", sorted(os.listdir(ARCHIVE_DIR)))
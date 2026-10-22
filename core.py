# -*- coding: utf-8 -*-
"""
Lõi phần mềm Tra cứu hồ sơ: lập chỉ mục (PDF có chữ, PDF scan qua OCR,
Word, Excel, text, ảnh) và tìm kiếm có dấu / không dấu.

NGUYÊN TẮC: chỉ ĐỌC các thư mục nguồn. Mọi dữ liệu (chỉ mục, ảnh tạm OCR,
cấu hình) đều nằm trong thư mục `data` cạnh phần mềm.
"""
import os
import re
import sys
import json
import time
import queue
import shutil
import sqlite3
import zipfile
import threading
import traceback
import subprocess
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

try:
    import pymupdf as fitz
except ImportError:  # bản PyMuPDF cũ
    import fitz

APP_NAME = "Tra cứu hồ sơ"
APP_VERSION = "1.0.2"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "index.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
TMP_DIR = os.path.join(DATA_DIR, "tmp")
TESSDATA_DIR = os.path.join(APP_DIR, "tessdata")
IS_WIN = os.name == "nt"

SUPPORTED = {
    ".pdf": "pdf",
    ".docx": "docx", ".docm": "docx",
    ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".txt": "txt", ".csv": "txt", ".md": "txt", ".log": "txt",
    ".png": "img", ".jpg": "img", ".jpeg": "img",
    ".tif": "img", ".tiff": "img", ".bmp": "img",
}

DEFAULT_CONFIG = {
    "sources": [],
    "ocr_enabled": True,
    "tesseract_path": "",
    "ocr_lang": "vie",
    "ocr_model": "fast",      # fast = vie_fast (nhanh gấp ~2), best = vie (chính xác hơn chút)
    "ocr_scale": 2.0,         # phóng ảnh gấp mấy lần độ phân giải ảnh scan gốc (2.0 là tối ưu khi đo thử)
    "ocr_dpi": 220,           # chỉ dùng cho trang không có ảnh nhúng (chữ vẽ thành nét)
    "ocr_psm": 3,
    "ocr_max_px": 3600,       # cạnh dài tối đa của ảnh đưa vào OCR
    "skip": [],               # danh sách file (đường dẫn tương đối) không lập chỉ mục
    "cfg_ver": 2,
    "workers": max(1, (os.cpu_count() or 2) - 1),
    "min_text_chars": 30,
    "ocr_vector": True,
    "vector_min_glyphs": 25,
    "viewer": "auto",
    "max_pages": 3000,
}


# --------------------------------------------------------------------------
# Chuẩn hoá tiếng Việt (bỏ dấu) — giữ nguyên độ dài chuỗi để tô sáng đúng vị trí
# --------------------------------------------------------------------------
class _FoldMap(dict):
    def __missing__(self, code):
        c = chr(code)
        if c in "đĐ":
            m = "d"
        else:
            base = "".join(ch for ch in unicodedata.normalize("NFD", c)
                           if unicodedata.category(ch) != "Mn")
            m = base.lower() if base else c
            if len(m) != 1:
                m = base[:1] if len(base) == 1 else c.lower()
                if len(m) != 1:
                    m = c
        self[code] = m
        return m


_FOLD = _FoldMap()


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\ufeff\ufffd\ue000-\uf8ff]")
_COMB = re.compile(r"[\u0300-\u036f]")


def clean_display(s):
    """Làm sạch chuỗi để HIỂN THỊ/XUẤT: NFC, bỏ ký tự điều khiển/vùng riêng (ra ô vuông), gộp khoảng trắng."""
    s = unicodedata.normalize("NFC", s or "")
    s = _CTRL.sub("", s)
    s = s.replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def fold(s):
    """Bỏ dấu + chữ thường; len(fold(s)) == len(s) với s đã NFC."""
    return (s or "").translate(_FOLD)


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def words(s):
    return _WORD_RE.findall(fold(s))


# --------------------------------------------------------------------------
# Cấu hình
# --------------------------------------------------------------------------
def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)


def load_config():
    ensure_dirs()
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        saved = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
        if saved.get("cfg_ver", 1) < 2:
            # bản cũ lưu DPI 300 / 6000px: chậm gấp ~3 lần mà đọc không tốt hơn -> áp mặc định mới
            for k in ("ocr_model", "ocr_scale", "ocr_dpi", "ocr_max_px"):
                cfg[k] = DEFAULT_CONFIG[k]
            cfg["cfg_ver"] = 2
    return cfg


def save_config(cfg):
    ensure_dirs()
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _norm_path(p):
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/")


def is_inside(child, parent):
    c, p = _norm_path(child), _norm_path(parent)
    return c == p or c.startswith(p + os.sep)


def check_source_allowed(path):
    """Trả về chuỗi lỗi nếu thư mục nguồn không hợp lệ, None nếu được."""
    if not os.path.isdir(path):
        return "Thư mục không tồn tại."
    if is_inside(APP_DIR, path):
        return ("Phần mềm đang nằm bên trong thư mục này. Hãy đặt phần mềm ở "
                "chỗ khác để không ghi bất kỳ dữ liệu nào vào thư mục hồ sơ.")
    if is_inside(path, DATA_DIR):
        return "Không thể chọn thư mục dữ liệu của phần mềm."
    return None


# --------------------------------------------------------------------------
# Cơ sở dữ liệu
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    source TEXT,
    rel TEXT,
    rel_fold TEXT,
    name TEXT,
    ext TEXT,
    size INTEGER,
    mtime REAL,
    pages INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    ocr_pages INTEGER DEFAULT 0,
    pending_ocr INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    error TEXT,
    updated REAL
);
CREATE TABLE IF NOT EXISTS pages(
    id INTEGER PRIMARY KEY,
    file_id INTEGER,
    page INTEGER,
    label TEXT,
    kind TEXT,
    text TEXT,
    w REAL, h REAL,
    UNIQUE(file_id, page)
);
CREATE TABLE IF NOT EXISTS lines(
    file_id INTEGER,
    page INTEGER,
    line INTEGER,
    text TEXT,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL
);
CREATE INDEX IF NOT EXISTS ix_lines ON lines(file_id, page);
CREATE TABLE IF NOT EXISTS corrections(
    file_id INTEGER,
    page INTEGER,
    line INTEGER,
    orig TEXT,
    fixed TEXT,
    status TEXT,
    updated REAL,
    PRIMARY KEY(file_id, page, line)
);
CREATE INDEX IF NOT EXISTS ix_pages_file ON pages(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(norm, tokenize='unicode61');
"""


INDEX_VER = 3   # 3: sửa chữ TCVN3 + OCR bổ sung cho bản vẽ vector


def connect(path=DB_PATH):
    ensure_dirs()
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(files)")}
    if "ver" not in cols:      # phiên bản cách lập chỉ mục; < INDEX_VER -> kiểm tra lại trang PDF
        con.execute("ALTER TABLE files ADD COLUMN ver INTEGER DEFAULT 0")
        con.commit()
    return con


def delete_file_data(con, file_id):
    con.execute("DELETE FROM pages_fts WHERE rowid IN (SELECT id FROM pages WHERE file_id=?)", (file_id,))
    con.execute("DELETE FROM pages WHERE file_id=?", (file_id,))
    con.execute("DELETE FROM lines WHERE file_id=?", (file_id,))


def remove_file(con, file_id):
    delete_file_data(con, file_id)
    con.execute("DELETE FROM files WHERE id=?", (file_id,))


def reset_file(con, file_id):
    delete_file_data(con, file_id)
    con.execute("UPDATE files SET done=0, ocr_pages=0, pending_ocr=0, status='new', error=NULL WHERE id=?",
                (file_id,))


def remove_source(con, source):
    ids = [r[0] for r in con.execute("SELECT id FROM files WHERE source=?", (source,))]
    for i in ids:
        remove_file(con, i)
    con.commit()


def stats(con):
    r = con.execute("SELECT COUNT(*), COALESCE(SUM(pages),0), COALESCE(SUM(done),0), "
                    "COALESCE(SUM(ocr_pages),0), COALESCE(SUM(pending_ocr),0) FROM files").fetchone()
    return {"files": r[0], "pages": r[1], "done": r[2], "ocr": r[3], "pending_ocr": r[4]}


# --------------------------------------------------------------------------
# OCR (Tesseract)
# --------------------------------------------------------------------------
def find_tesseract(cfg):
    cands = [cfg.get("tesseract_path") or "",
             os.path.join(APP_DIR, "Tesseract-OCR", "tesseract.exe"),
             shutil.which("tesseract") or ""]
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cands.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _popen_kwargs():
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return kw


def _ascii_work_dir():
    """Tesseract trên Windows hay lỗi với đường dẫn có dấu tiếng Việt.
    Nếu thư mục phần mềm có ký tự ngoài ASCII thì dùng một thư mục ASCII thay thế."""
    if DATA_DIR.isascii():
        return None
    for base in (os.environ.get("ProgramData"), os.environ.get("PUBLIC"), "C:\\TraCuuHoSo_tmp"):
        if base and base.isascii():
            d = os.path.join(base, "TraCuuHoSo")
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError:
                continue
    return None


def ocr_tmp_dir():
    w = _ascii_work_dir()
    d = os.path.join(w, "tmp") if w else TMP_DIR
    os.makedirs(d, exist_ok=True)
    return d


_TD_CACHE = {}


def effective_lang(cfg):
    """vie -> vie_fast nếu chọn chế độ Nhanh và có sẵn dữ liệu kèm phần mềm."""
    lang = cfg.get("ocr_lang", "vie") or "vie"
    if cfg.get("ocr_model", "fast") == "fast":
        parts = [("vie_fast" if p == "vie" else p) for p in lang.split("+") if p]
        if parts and all(os.path.isfile(os.path.join(TESSDATA_DIR, p + ".traineddata")) for p in parts):
            return "+".join(parts)
    return lang


def tessdata_dir_for(lang):
    langs = [l for l in lang.split("+") if l]
    if not langs or not all(os.path.isfile(os.path.join(TESSDATA_DIR, l + ".traineddata")) for l in langs):
        return None
    if lang in _TD_CACHE:
        return _TD_CACHE[lang]
    td = TESSDATA_DIR
    w = _ascii_work_dir()
    if w:
        td = os.path.join(w, "tessdata")
        os.makedirs(td, exist_ok=True)
        for l in langs:
            src, dst = os.path.join(TESSDATA_DIR, l + ".traineddata"), os.path.join(td, l + ".traineddata")
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
    _TD_CACHE[lang] = td
    return td


def tesseract_info(cfg):
    """(ok, mô tả) để hiện trong phần Cài đặt."""
    exe = find_tesseract(cfg)
    if not exe:
        return False, "Chưa tìm thấy Tesseract. Hãy cài Tesseract-OCR hoặc chọn đường dẫn tesseract.exe."
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=20, **_popen_kwargs())
        ver = (r.stdout or r.stderr).decode("utf-8", "replace").splitlines()[0]
    except Exception as e:
        return False, f"Không chạy được {exe}: {e}"
    lang = effective_lang(cfg)
    td = tessdata_dir_for(lang)
    if td:
        return True, f"{ver}  ·  ngôn ngữ '{lang}' dùng dữ liệu kèm phần mềm"
    try:
        r = subprocess.run([exe, "--list-langs"], capture_output=True, timeout=20, **_popen_kwargs())
        have = set((r.stdout or b"").decode("utf-8", "replace").split())
    except Exception:
        have = set()
    missing = [l for l in lang.split("+") if l and l not in have]
    if missing:
        return False, f"{ver}  ·  THIẾU dữ liệu ngôn ngữ: {', '.join(missing)}"
    return True, f"{ver}  ·  ngôn ngữ '{lang}'"


def run_ocr(exe, img_bytes, lang, psm, dpi, ext="pgm", extra=None, words=False, both=False):
    """OCR một ảnh (PGM/PNG) -> danh sách dòng [(text, x0, y0, x1, y1)] theo pixel."""
    fd_name = os.path.join(ocr_tmp_dir(), f"ocr_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.{ext}")
    with open(fd_name, "wb") as f:
        f.write(img_bytes)
    try:
        cmd = [exe, fd_name, "stdout", "-l", lang, "--psm", str(psm), "--dpi", str(int(dpi))]
        td = tessdata_dir_for(lang)
        if td:
            cmd += ["--tessdata-dir", td]
        cmd += ["-c", "tessedit_create_tsv=1"]
        for kv in (extra or []):
            cmd += ["-c", kv]
        env = dict(os.environ, OMP_THREAD_LIMIT="1", OMP_NUM_THREADS="1")
        r = subprocess.run(cmd, capture_output=True, timeout=300, env=env, **_popen_kwargs())
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:])
        return parse_tsv(r.stdout.decode("utf-8", "replace"), words, both)
    finally:
        try:
            os.remove(fd_name)
        except OSError:
            pass


def run_ocr_timed(*a, **k):
    t = time.time()
    res = run_ocr(*a, **k)
    return res, time.time() - t


def parse_tsv(tsv, words=False, both=False):
    lines = {}
    order = []
    out_w = []
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or c[0] != "5":
            continue
        txt = c[11].strip()
        if not txt:
            continue
        try:
            conf = float(c[10])
        except ValueError:
            conf = 0
        if conf < 0:
            continue
        key = (c[1], c[2], c[3], c[4])
        l, t, w, h = int(c[6]), int(c[7]), int(c[8]), int(c[9])
        if words or both:
            out_w.append((txt, l, t, l + w, t + h))
            if words:
                continue
        if key not in lines:
            lines[key] = [[], l, t, l + w, t + h]
            order.append(key)
        e = lines[key]
        e[0].append(txt)
        e[1], e[2] = min(e[1], l), min(e[2], t)
        e[3], e[4] = max(e[3], l + w), max(e[4], t + h)
    if words:
        return out_w
    out = []
    for k in order:
        ws, x0, y0, x1, y1 = lines[k]
        out.append((" ".join(ws), x0, y0, x1, y1))
    if both:
        return out, out_w
    return out


# --------------------------------------------------------------------------
# Trích xuất
# --------------------------------------------------------------------------
# ---- chữ tiếng Việt bảng mã cũ TCVN3 (.VnTime, VHAVAN, vnsimli…): PDF xuất từ CAD thường có chữ kiểu "thÐp r©u treo lång"
_TCVN3 = {
    0xA1: "Ă", 0xA2: "Â", 0xA3: "Ê", 0xA4: "Ô", 0xA5: "Ơ", 0xA6: "Ư", 0xA7: "Đ",
    0xA8: "ă", 0xA9: "â", 0xAA: "ê", 0xAB: "ô", 0xAC: "ơ", 0xAD: "ư", 0xAE: "đ",
    0xB5: "à", 0xB6: "ả", 0xB7: "ã", 0xB8: "á", 0xB9: "ạ",
    0xBB: "ằ", 0xBC: "ẳ", 0xBD: "ẵ", 0xBE: "ắ", 0xC6: "ặ",
    0xC7: "ầ", 0xC8: "ẩ", 0xC9: "ẫ", 0xCA: "ấ", 0xCB: "ậ",
    0xCC: "è", 0xCE: "ẻ", 0xCF: "ẽ", 0xD0: "é", 0xD1: "ẹ",
    0xD2: "ề", 0xD3: "ể", 0xD4: "ễ", 0xD5: "ế", 0xD6: "ệ",
    0xD7: "ì", 0xD8: "ỉ", 0xDC: "ĩ", 0xDD: "í", 0xDE: "ị",
    0xDF: "ò", 0xE1: "ỏ", 0xE2: "õ", 0xE3: "ó", 0xE4: "ọ",
    0xE5: "ồ", 0xE6: "ổ", 0xE7: "ỗ", 0xE8: "ố", 0xE9: "ộ",
    0xEA: "ờ", 0xEB: "ở", 0xEC: "ỡ", 0xED: "ớ", 0xEE: "ợ",
    0xEF: "ù", 0xF1: "ủ", 0xF2: "ũ", 0xF3: "ú", 0xF4: "ụ",
    0xF5: "ừ", 0xF6: "ử", 0xF7: "ữ", 0xF8: "ứ", 0xF9: "ự",
    0xFA: "ỳ", 0xFB: "ỷ", 0xFC: "ỹ", 0xFD: "ý", 0xFE: "ỵ",
}
_VI_UNI = set("ĂÂÊÔƠƯĐăâêôơưđàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")
_LATIN1_VI = set(range(0xC0, 0xC4)) | {0xC8, 0xC9, 0xCA, 0xCC, 0xCD, 0xD2, 0xD3, 0xD4, 0xD5, 0xD9, 0xDA, 0xDD} | \
    set(range(0xE0, 0xE4)) | {0xE8, 0xE9, 0xEA, 0xEC, 0xED, 0xF2, 0xF3, 0xF4, 0xF5, 0xF9, 0xFA, 0xFD}
_TCVN_FONT = re.compile(r"(^|[+\\/])(\.?vn|vh|vnsim|vnavan|vntime)|\.vn", re.I)


def font_is_tcvn(font):
    return bool(font) and bool(_TCVN_FONT.search(font))


def decode_tcvn3(s, font_tcvn=False):
    """Chuỗi bị đọc theo cp1252 -> tiếng Việt. Không đụng vào chuỗi đã là Unicode tiếng Việt (có ký tự > U+00FF)."""
    if not s or any(ch in _VI_UNI and ord(ch) > 0xFF for ch in s):
        return s
    bs = []
    for ch in s:
        try:
            bs.append(ch.encode("cp1252")[0])
        except UnicodeEncodeError:
            bs.append(None)
    high = [b for b in bs if b is not None and b >= 0xA1]
    if not high:
        return s
    if not (font_tcvn or any(b not in _LATIN1_VI for b in high)):
        return s
    return "".join(_TCVN3.get(b, ch) if (b is not None and b >= 0xA1) else ch for ch, b in zip(s, bs))


def text_garbage_ratio(lines):
    """Tỷ lệ ký tự điều khiển / vùng riêng / (cid:…) — dấu hiệu lớp chữ của PDF không dùng được."""
    tot = bad = 0
    for l in lines:
        t = l[0]
        tot += len(t)
        bad += 5 * t.count("(cid:")
        for ch in t:
            o = ord(ch)
            if o == 0xFFFD or (o < 32 and ch not in "\t") or 0xE000 <= o <= 0xF8FF:
                bad += 1
    return bad / tot if tot else 0.0


def vector_text_glyphs(page, limit=200000):
    """Đếm các nét vẽ nhỏ giống chữ bị chuyển thành hình (SHX/“text as geometry” khi in CAD ra PDF)."""
    try:
        dr = page.get_cdrawings()
    except Exception:
        return 0
    if len(dr) > limit:
        return 0
    n = 0
    try:
        for d in dr:
            r = d["rect"]
            w, h = r[2] - r[0], r[3] - r[1]
            if 0 < w < 14 and 0 < h < 14 and len(d.get("items", ())) >= 2:
                n += 1
    except Exception:
        return 0
    return n


def merge_ocr_lines(base, ocr):
    """Giữ các dòng của lớp chữ; thêm dòng OCR chưa có (khác nội dung và không đè lên dòng chữ sẵn có)."""
    if not base:
        return list(ocr)
    seen = {fold(b[0]) for b in base}
    rects = [fitz.Rect(b[1], b[2], b[3], b[4]) for b in base]
    out = list(base)
    for o in ocr:
        if fold(o[0]) in seen:
            continue
        r = fitz.Rect(o[1], o[2], o[3], o[4])
        a = max(r.get_area(), 1e-6)
        if any((r & q).get_area() > 0.4 * a for q in rects if not (r & q).is_empty):
            continue
        out.append(o)
    out.sort(key=lambda l: (round(l[2] / 6.0), l[1]))
    return out


def pdf_text_lines(page):
    """Dòng chữ của trang PDF (toạ độ đã xoay theo hướng hiển thị)."""
    out = []
    try:
        d = page.get_text("dict", sort=True)
    except TypeError:
        d = page.get_text("dict")
    rm = page.rotation_matrix
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            t = "".join(s.get("text", "") for s in spans).strip()
            if not t:
                continue
            if any(font_is_tcvn(s.get("font", "")) for s in spans) or any(0x80 <= ord(c) <= 0xFF for c in t):
                t = decode_tcvn3(t, any(font_is_tcvn(s.get("font", "")) for s in spans))
            r = fitz.Rect(ln["bbox"]) * rm
            r.normalize()
            out.append((nfc(t), r.x0, r.y0, r.x1, r.y1))
    return out


def native_px_per_pt(page):
    """Độ phân giải (điểm ảnh / pt) của ảnh scan phủ gần hết trang; None nếu không có."""
    try:
        area = page.rect.width * page.rect.height
        best = None
        for im in page.get_image_info():
            bb = fitz.Rect(im["bbox"])
            if bb.width <= 0 or bb.height <= 0 or bb.width * bb.height < 0.5 * area:
                continue
            v = max(im["width"] / bb.width, im["height"] / bb.height)
            best = v if best is None else max(best, v)
        return best
    except Exception:
        return None


def render_for_ocr(page, dpi, max_px, scale=2.0):
    """Ảnh xám (PGM) để OCR.

    Trang scan: phóng `scale` lần so với ảnh gốc. Phóng quá lớn (vd. 300 DPI từ ảnh gốc 100 DPI)
    làm Tesseract chậm gấp ~3 lần mà đọc còn kém hơn. Trang không có ảnh nhúng: dùng `dpi`."""
    rect = page.rect
    nat = native_px_per_pt(page)
    zoom = nat * scale if nat else dpi / 72.0
    longest = max(rect.width, rect.height) * zoom
    if longest > max_px:
        zoom = max_px / max(rect.width, rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    return pix.tobytes("pnm"), zoom


def docx_paragraphs(path):
    from xml.etree import ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path, "r") as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(ns + "p"):
        parts = []
        for node in p.iter():
            if node.tag == ns + "t" and node.text:
                parts.append(node.text)
            elif node.tag == ns + "tab":
                parts.append("\t")
        t = "".join(parts).strip()
        if t:
            paras.append(nfc(t))
    return paras


def xlsx_sheets(path):
    import openpyxl  # tuỳ chọn
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if v is None or v == "":
                        continue
                    rows.append((cell.row, f"[{cell.coordinate}] {nfc(str(v))}"))
            yield ws.title, rows
    finally:
        wb.close()


def read_text_file(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            return nfc(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return nfc(raw.decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# Lập chỉ mục
# --------------------------------------------------------------------------
def write_page(con, file_id, page, kind, lines, w=0, h=0, label=None):
    row = con.execute("SELECT id FROM pages WHERE file_id=? AND page=?", (file_id, page)).fetchone()
    text = "\n".join(l[0] for l in lines)
    if row:
        pid = row[0]
        con.execute("DELETE FROM pages_fts WHERE rowid=?", (pid,))
        con.execute("DELETE FROM lines WHERE file_id=? AND page=?", (file_id, page))
        con.execute("UPDATE pages SET kind=?, text=?, w=?, h=?, label=? WHERE id=?",
                    (kind, text, w, h, label, pid))
    else:
        cur = con.execute("INSERT INTO pages(file_id,page,label,kind,text,w,h) VALUES(?,?,?,?,?,?,?)",
                          (file_id, page, label, kind, text, w, h))
        pid = cur.lastrowid
    if kind != "none":
        con.execute("INSERT INTO pages_fts(rowid, norm) VALUES(?,?)", (pid, fold(text)))
        con.executemany("INSERT INTO lines(file_id,page,line,text,x0,y0,x1,y1) VALUES(?,?,?,?,?,?,?,?)",
                        [(file_id, page, i + 1, l[0], l[1], l[2], l[3], l[4]) for i, l in enumerate(lines)])


def refresh_file_counts(con, file_id):
    r = con.execute("SELECT SUM(kind!='none'), SUM(kind IN ('ocr','mix')), SUM(kind='none') FROM pages WHERE file_id=?",
                    (file_id,)).fetchone()
    con.execute("UPDATE files SET done=?, ocr_pages=?, pending_ocr=?, updated=? WHERE id=?",
                (r[0] or 0, r[1] or 0, r[2] or 0, time.time(), file_id))


class Indexer(threading.Thread):
    """Chạy nền. Gửi sự kiện vào `events` (queue) dạng (loại, dữ liệu)."""

    def __init__(self, cfg, events, only_ids=None, db_path=DB_PATH):
        super().__init__(daemon=True)
        self.cfg = dict(cfg)
        self.events = events
        self.only_ids = set(only_ids) if only_ids else None
        self.db_path = db_path
        self.stop_flag = threading.Event()
        self.tess = find_tesseract(self.cfg) if self.cfg.get("ocr_enabled") else None
        self.ocr_on = bool(self.tess)
        self.pages_processed = 0
        self.ocr_time = 0.0
        self.ocr_n = 0
        self.t0 = time.time()

    def emit(self, kind, data=None):
        self.events.put((kind, data))

    def stop(self):
        self.stop_flag.set()

    # ---- quét thư mục ----
    def scan(self, con):
        seen = set()
        n = 0
        for src in self.cfg.get("sources", []):
            if not os.path.isdir(src):
                self.emit("log", f"Không truy cập được thư mục nguồn: {src}")
                continue
            for root, dirs, files in os.walk(src):
                if self.stop_flag.is_set():
                    return
                dirs[:] = [d for d in dirs if not d.startswith(".")
                           and not is_inside(os.path.join(root, d), DATA_DIR)]
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in SUPPORTED or fn.startswith("~$"):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    seen.add(p)
                    rel = os.path.relpath(p, src)
                    row = con.execute("SELECT id,size,mtime FROM files WHERE path=?", (p,)).fetchone()
                    if row is None:
                        pages = self.count_pages(p, ext)
                        con.execute("INSERT INTO files(path,source,rel,rel_fold,name,ext,size,mtime,pages,status) "
                                    "VALUES(?,?,?,?,?,?,?,?,?,'new')",
                                    (p, src, rel, fold(nfc(rel)), fn, ext, st.st_size, st.st_mtime, pages))
                    elif row[1] != st.st_size or abs((row[2] or 0) - st.st_mtime) > 1:
                        reset_file(con, row[0])
                        con.execute("UPDATE files SET size=?, mtime=?, pages=? WHERE id=?",
                                    (st.st_size, st.st_mtime, self.count_pages(p, ext), row[0]))
                        self.emit("log", f"File đã thay đổi, sẽ lập lại: {rel}")
                    n += 1
                    if n % 20 == 0:
                        self.emit("scan", n)
                        con.commit()
        # file đã bị xoá khỏi ổ đĩa
        srcs = self.cfg.get("sources", [])
        for fid, path, source in con.execute("SELECT id,path,source FROM files").fetchall():
            if source not in srcs or (path not in seen and os.path.isdir(source)):
                remove_file(con, fid)
        con.commit()
        self.emit("scan", n)

    @staticmethod
    def count_pages(path, ext):
        if SUPPORTED.get(ext) == "pdf":
            try:
                with fitz.open(path) as d:
                    return d.page_count
            except Exception:
                return 0
        return 1 if SUPPORTED.get(ext) in ("img", "txt", "docx") else 0

    # ---- vòng chính ----
    def run(self):
        con = None
        try:
            con = connect(self.db_path)
            if self.cfg.get("ocr_enabled") and not self.tess:
                self.emit("log", "CHÚ Ý: chưa tìm thấy Tesseract — các trang scan sẽ được để lại, "
                                 "chạy OCR sau khi cài Tesseract.")
            self.emit("phase", "Đang quét thư mục…")
            self.scan(con)
            if self.stop_flag.is_set():
                return
            q = ("SELECT id,path,rel,ext,pages,done,status FROM files WHERE status!='done' "
                 f"OR (ext='.pdf' AND IFNULL(ver,0)<{INDEX_VER} AND done>0)")
            if self.ocr_on:
                q += " OR pending_ocr>0"
            todo = con.execute(q + " ORDER BY rel").fetchall()
            if self.only_ids is not None:
                todo = [t for t in todo if t[0] in self.only_ids]
            skip = set(self.cfg.get("skip", []))
            if skip:
                n0 = len(todo)
                todo = [t for t in todo if t[2] not in skip]
                if n0 != len(todo):
                    self.emit("log", f"Bỏ qua {n0 - len(todo)} file theo lựa chọn của bạn.")
            remaining = sum(max(0, (t[4] or 0) - (t[5] or 0)) or (t[4] or 0) for t in todo)
            self.emit("plan", {"files": len(todo), "pages": remaining})
            self.t0 = time.time()
            with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.get("workers", 1)))) as pool:
                for i, t in enumerate(todo):
                    if self.stop_flag.is_set():
                        break
                    self.index_file(con, pool, t, i, len(todo))
            self.emit("phase", "Đã dừng." if self.stop_flag.is_set() else "Hoàn tất.")
        except Exception:
            self.emit("error", traceback.format_exc())
        finally:
            if con:
                try:
                    con.commit()
                    con.close()
                except Exception:
                    pass
            self.emit("finished", None)

    def _progress(self, fid, rel, page, total):
        self.pages_processed += 1
        el = time.time() - self.t0
        self.emit("progress", {"file_id": fid, "rel": rel, "page": page, "total": total,
                               "processed": self.pages_processed, "elapsed": el,
                               "avg_ocr": (self.ocr_time / self.ocr_n) if self.ocr_n else 0})

    def index_file(self, con, pool, t, idx, nfiles):
        fid, path, rel, ext, pages, done, status = t
        kind = SUPPORTED.get(ext)
        self.emit("file_start", {"file_id": fid, "rel": rel, "index": idx, "count": nfiles})
        con.execute("UPDATE files SET status='indexing', error=NULL WHERE id=?", (fid,))
        con.commit()
        try:
            if kind == "pdf":
                self.index_pdf(con, pool, fid, path, rel)
            elif kind == "img":
                self.index_image(con, fid, path, rel)
            elif kind == "docx":
                paras = docx_paragraphs(path)
                write_page(con, fid, 1, "doc", [(p, 0, 0, 0, 0) for p in paras], label="Word")
                self._progress(fid, rel, 1, 1)
            elif kind == "xlsx":
                n = 0
                for n, (title, rows) in enumerate(xlsx_sheets(path), 1):
                    write_page(con, fid, n, "sheet", [(txt, r, 0, 0, 0) for r, txt in rows], label=title)
                con.execute("UPDATE files SET pages=? WHERE id=?", (n, fid))
                self._progress(fid, rel, n, n)
            elif kind == "txt":
                txt = read_text_file(path)
                ls = [(l.rstrip("\r"), 0, 0, 0, 0) for l in txt.split("\n")]
                write_page(con, fid, 1, "txt", ls, label="Text")
                self._progress(fid, rel, 1, 1)
            refresh_file_counts(con, fid)
            r = con.execute("SELECT pages, done, pending_ocr FROM files WHERE id=?", (fid,)).fetchone()
            if self.stop_flag.is_set() and (r[1] or 0) < (r[0] or 0) and kind == "pdf":
                st = "paused"
            elif r[2]:
                st = "need_ocr"
            else:
                st = "done"
            con.execute("UPDATE files SET status=? WHERE id=?", (st, fid))
            if kind == "pdf" and not self.stop_flag.is_set():
                con.execute("UPDATE files SET ver=? WHERE id=?", (INDEX_VER, fid))
        except Exception as e:
            con.execute("UPDATE files SET status='error', error=? WHERE id=?", (str(e)[:500], fid))
            self.emit("log", f"Lỗi {rel}: {e}")
        con.commit()
        self.emit("file_done", {"file_id": fid})

    def index_pdf(self, con, pool, fid, path, rel):
        doc = fitz.open(path)
        try:
            if doc.needs_pass:
                raise RuntimeError("PDF có mật khẩu")
            n = doc.page_count
            con.execute("UPDATE files SET pages=? WHERE id=?", (n, fid))
            have = dict(con.execute("SELECT page, kind FROM pages WHERE file_id=?", (fid,)).fetchall())
            ver = con.execute("SELECT IFNULL(ver,0) FROM files WHERE id=?", (fid,)).fetchone()[0]
            recheck = ver < INDEX_VER        # chỉ mục cũ: xem lại các trang "chữ" (TCVN3, chữ vẽ bằng nét)
            need = [p for p in range(1, n + 1)
                    if p not in have or (have[p] == "none" and self.ocr_on) or (recheck and have[p] == "text")]
            vec_on = bool(self.cfg.get("ocr_vector", True)) and self.ocr_on
            vec_min = int(self.cfg.get("vector_min_glyphs", 25))
            min_chars = int(self.cfg.get("min_text_chars", 30))
            dpi = int(self.cfg.get("ocr_dpi", 220))
            max_px = int(self.cfg.get("ocr_max_px", 3600))
            scale = float(self.cfg.get("ocr_scale", 2.0))
            lang = effective_lang(self.cfg)
            psm = int(self.cfg.get("ocr_psm", 3))
            workers = max(1, int(self.cfg.get("workers", 1)))
            inflight = deque()
            last_commit = time.time()

            def drain_one():
                pno, fut, zoom, w, h, base = inflight.popleft()
                try:
                    res, dt = fut.result()
                    self.ocr_time += dt
                    self.ocr_n += 1
                    ls = [(nfc(tx), x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom) for tx, x0, y0, x1, y1 in res]
                    if base:
                        write_page(con, fid, pno, "mix", merge_ocr_lines(base, ls), w, h)
                    else:
                        write_page(con, fid, pno, "ocr", ls, w, h)
                except Exception as e:
                    self.emit("log", f"OCR lỗi {rel} trang {pno}: {e}")
                    write_page(con, fid, pno, "text" if base else "none", base or [], w, h)
                self._progress(fid, rel, pno, n)

            for pno in need:
                if self.stop_flag.is_set():
                    break
                page = doc[pno - 1]
                w, h = page.rect.width, page.rect.height
                ls = pdf_text_lines(page)
                nchar = sum(len(l[0]) for l in ls)
                bad = text_garbage_ratio(ls) > 0.15          # lớp chữ hỏng (font không có bảng mã)
                base = [] if bad else ls
                vec = False
                if vec_on and not bad and nchar >= min_chars:
                    vec = vector_text_glyphs(page) >= vec_min   # bản vẽ CAD: chữ bị vẽ thành nét
                if nchar >= min_chars and not bad and not vec:
                    write_page(con, fid, pno, "text", ls, w, h)
                    self._progress(fid, rel, pno, n)
                elif self.ocr_on:
                    img, zoom = render_for_ocr(page, dpi, max_px, scale)
                    fut = pool.submit(run_ocr_timed, self.tess, img, lang, psm, zoom * 72)
                    inflight.append((pno, fut, zoom, w, h, base))
                elif base and nchar >= min_chars:
                    write_page(con, fid, pno, "text", base, w, h)
                    self._progress(fid, rel, pno, n)
                    while len(inflight) >= workers * 2:
                        drain_one()
                else:
                    write_page(con, fid, pno, "none", [], w, h)
                    self._progress(fid, rel, pno, n)
                if time.time() - last_commit > 3:
                    refresh_file_counts(con, fid)
                    con.commit()
                    last_commit = time.time()
            while inflight:
                drain_one()
        finally:
            doc.close()

    def index_image(self, con, fid, path, rel):
        if not self.ocr_on:
            write_page(con, fid, 1, "none", [])
            self._progress(fid, rel, 1, 1)
            return
        with fitz.open(path) as d:
            page = d[0]
            img, zoom = render_for_ocr(page, int(self.cfg.get("ocr_dpi", 220)),
                                       int(self.cfg.get("ocr_max_px", 3600)),
                                       float(self.cfg.get("ocr_scale", 2.0)))
            res = run_ocr(self.tess, img, effective_lang(self.cfg),
                          int(self.cfg.get("ocr_psm", 3)), zoom * 72)
            ls = [(nfc(tx), x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom) for tx, x0, y0, x1, y1 in res]
            write_page(con, fid, 1, "ocr", ls, page.rect.width, page.rect.height)
        self._progress(fid, rel, 1, 1)


# --------------------------------------------------------------------------
# Tìm kiếm
# --------------------------------------------------------------------------
@dataclass
class Term:
    raw: str
    tokens: list
    prefix: bool = False
    exclude: bool = False


@dataclass
class Hit:
    file_id: int
    path: str
    rel: str
    name: str
    ext: str
    page: int
    label: str
    kind: str
    line: int          # 0 = không xác định được dòng
    text: str
    spans: list = field(default_factory=list)
    bbox: tuple = None
    score: float = 0.0
    nterms: int = 0


def parse_query(q, whole_phrase=False):
    q = nfc(q.strip())
    terms = []
    if not q:
        return terms
    if whole_phrase:
        raw = q.strip('"')
        pref = raw.endswith("*")
        raw = raw.rstrip("*")
        toks = words(raw)
        if toks:
            terms.append(Term(raw, toks, pref))
        return terms
    for m in re.finditer(r'(-?)"([^"]+)"|(-?)(\S+)', q):
        neg = (m.group(1) or m.group(3)) == "-"
        raw = m.group(2) if m.group(2) is not None else m.group(4)
        pref = raw.endswith("*")
        raw = raw.rstrip("*")
        toks = words(raw)
        if toks:
            terms.append(Term(raw, toks, pref, neg))
    return terms


def fts_expression(terms):
    pos = [t for t in terms if not t.exclude]
    neg = [t for t in terms if t.exclude]
    if not pos:
        return None

    def one(t):
        return '"' + " ".join(t.tokens) + '"' + ("*" if t.prefix else "")
    expr = " AND ".join(one(t) for t in pos)
    for t in neg:
        expr = f"({expr}) NOT {one(t)}"
    return expr


def term_regex(t):
    body = r"[\W_]+".join(re.escape(x) for x in t.tokens)
    tail = "" if t.prefix else r"(?![^\W_])"
    return re.compile(r"(?<![^\W_])" + body + tail, re.UNICODE)


def find_spans(text, term, rx, accent):
    """Danh sách (start, end) của term trong text."""
    if accent:
        low = text.lower()
        needle = nfc(term.raw).lower()
        out, i = [], low.find(needle)
        while i >= 0 and needle:
            out.append((i, i + len(needle)))
            i = low.find(needle, i + len(needle))
        return out
    return [m.span() for m in rx.finditer(fold(text))]


def search(con, query, accent=False, whole_phrase=False, file_filter="", sort="rank",
           max_pages=3000, max_lines_per_page=8):
    terms = parse_query(query, whole_phrase)
    expr = fts_expression(terms)
    if not expr:
        return [], terms, 0
    pos = [t for t in terms if not t.exclude]
    rxs = [term_regex(t) for t in pos]
    sql = ("SELECT p.id, p.file_id, p.page, p.label, p.kind, f.path, f.rel, f.name, f.ext, "
           "bm25(pages_fts) AS sc FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid "
           "JOIN files f ON f.id = p.file_id WHERE pages_fts MATCH ?")
    args = [expr]
    for part in words(file_filter):
        sql += " AND f.rel_fold LIKE ?"
        args.append(f"%{part}%")
    sql += " ORDER BY sc" if sort == "rank" else " ORDER BY f.rel, p.page"
    sql += " LIMIT ?"
    args.append(int(max_pages))
    rows = con.execute(sql, args).fetchall()
    hits = []
    for pid, fid, page, label, kind, path, rel, name, ext, sc in rows:
        lines = con.execute("SELECT line, text, x0, y0, x1, y1 FROM lines WHERE file_id=? AND page=? ORDER BY line",
                            (fid, page)).fetchall()
        scored = []
        for ln, txt, x0, y0, x1, y1 in lines:
            spans, cnt = [], 0
            for t, rx in zip(pos, rxs):
                s = find_spans(txt, t, rx, accent)
                if s:
                    cnt += 1
                    spans.extend(s)
            if cnt:
                scored.append((cnt, ln, txt, sorted(spans), (x0, y0, x1, y1)))
        if accent:
            # yêu cầu mọi cụm từ có mặt đúng dấu trong trang
            page_txt = "\n".join(l[1] for l in lines)
            if not all(find_spans(page_txt, t, None, True) for t in pos):
                continue
        if scored:
            best = max(s[0] for s in scored)
            chosen = [s for s in scored if s[0] == best][:max_lines_per_page]
            for cnt, ln, txt, spans, bb in chosen:
                hits.append(Hit(fid, path, rel, name, ext, page, label or "", kind, ln, txt, spans,
                                bb, sc, cnt))
        else:
            # các từ nằm trên nhiều dòng khác nhau
            first = lines[0][1] if lines else ""
            hits.append(Hit(fid, path, rel, name, ext, page, label or "", kind, 0,
                            "(các từ khoá nằm rải rác trong trang) " + first[:120], [], None, sc, 0))
    return hits, terms, len(rows)


def page_lines(con, file_id, page):
    return con.execute("SELECT line, text, x0, y0, x1, y1 FROM lines WHERE file_id=? AND page=? ORDER BY line",
                       (file_id, page)).fetchall()


def page_kind(con, file_id, page):
    r = con.execute("SELECT kind, label FROM pages WHERE file_id=? AND page=?", (file_id, page)).fetchone()
    return r if r else (None, None)


# --------------------------------------------------------------------------
# Mở file bằng phần mềm ngoài
# --------------------------------------------------------------------------
def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def _pf(*parts):
    out = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            out.append(os.path.join(base, *parts))
    return out


def find_viewers():
    if not IS_WIN:
        return {}
    v = {
        "sumatra": _first_existing(_pf("SumatraPDF", "SumatraPDF.exe")),
        "foxit": _first_existing(_pf("Foxit Software", "Foxit PDF Reader", "FoxitPDFReader.exe")
                                 + _pf("Foxit Software", "Foxit Reader", "FoxitReader.exe")
                                 + _pf("Foxit Software", "Foxit PDF Editor", "FoxitPDFEditor.exe")),
        "acrobat": _first_existing(_pf("Adobe", "Acrobat DC", "Acrobat", "Acrobat.exe")
                                   + _pf("Adobe", "Acrobat Reader DC", "Reader", "AcroRd32.exe")
                                   + _pf("Adobe", "Acrobat Reader", "Reader", "AcroRd32.exe")),
        "edge": _first_existing(_pf("Microsoft", "Edge", "Application", "msedge.exe")),
        "chrome": _first_existing(_pf("Google", "Chrome", "Application", "chrome.exe")),
    }
    return {k: p for k, p in v.items() if p}


VIEWER_NAMES = {
    "auto": "Tự động",
    "sumatra": "SumatraPDF",
    "foxit": "Foxit Reader",
    "acrobat": "Adobe Acrobat / Reader",
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "default": "Mặc định của Windows (không nhảy trang)",
}


def open_external(path, page=1, viewer="auto"):
    ext = os.path.splitext(path)[1].lower()
    if not IS_WIN:
        subprocess.Popen(["xdg-open", path])
        return "xdg-open"
    if ext != ".pdf" or viewer == "default":
        os.startfile(path)
        return "default"
    vs = find_viewers()
    order = [viewer] if viewer in vs else []
    if viewer == "auto" or not order:
        order = [k for k in ("sumatra", "foxit", "acrobat", "edge", "chrome") if k in vs]
    for k in order:
        exe = vs[k]
        if k == "sumatra":
            subprocess.Popen([exe, "-reuse-instance", "-page", str(page), path])
        elif k in ("foxit", "acrobat"):
            subprocess.Popen([exe, "/A", f"page={page}", path])
        else:
            from pathlib import Path
            subprocess.Popen([exe, Path(path).as_uri() + f"#page={page}"])
        return k
    os.startfile(path)
    return "default"


def open_folder(path):
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])

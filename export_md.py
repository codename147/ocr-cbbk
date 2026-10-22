# -*- coding: utf-8 -*-
"""
Xuất chỉ mục OCR sang Markdown NHẸ để đưa cho AI (Claude) tóm tắt.

Đọc thẳng từ chỉ mục (không OCR lại, không mở file PDF gốc ngoài việc trích trang).
 - Trang chữ (thuyết minh, bảng tính, hồ sơ mời thầu…): giữ nội dung, bỏ dòng rác OCR,
   bỏ khung tên/đầu trang lặp, gộp các ô cùng hàng của bảng thành "a | b | c".
 - Trang bản vẽ (OCR phần lớn ra rác): chỉ giữ danh mục — tiêu đề, lý trình, ghi chú đọc được;
   các trang liền nhau giống nhau được gộp thành một dải "tr.100–240".
Số liệu quan trọng trong bảng/bản vẽ cần AI đối chiếu lại bằng ảnh trang: các trang đó được đánh dấu.
"""
import os
import re
import time
import difflib
from collections import Counter

import core
import thkl as T
from core import fold, nfc

MODES = {
    "raw": "Toàn bộ OCR (nguyên văn, không lọc)",
    "rawclean": "Toàn bộ OCR đã làm sạch (sửa dấu từ khóa, bỏ rác, bảng THKL chuẩn)",
    "full": "Đầy đủ (chỉ bỏ khung tên lặp)",
    "gon": "Gọn (khuyên dùng)",
    "ratgon": "Rất gọn (chỉ phần chữ, bản vẽ chỉ còn danh mục)",
}

# ---------------------------------------------------------------- nhận dạng token
_SYL = re.compile(r"^(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|b|c|d|g|h|k|l|m|n|p|r|s|t|v|x)?"
                  r"[aeiouy]{1,3}(?:ng|nh|ch|c|m|n|p|t)?$")
_BAD_NUC = ("ii", "yy", "ee", "aaa", "uuu", "ooo", "eue", "aea", "uyu")
_ACRO = {"btct", "bt", "tcvn", "qcvn", "astm", "aashto", "iso", "jscee", "jsce", "en", "bs", "din", "mcn", "ln",
         "cdm", "spt", "pda", "sps", "xlpe", "pvc", "hdpe", "bim", "its", "etc", "epc", "ubnd", "tcxdvn", "tcxd",
         "cvn", "vbom", "cbbk", "km", "cd", "cr", "tv", "ic", "bep", "uv", "led", "qtkt", "hse", "wbs", "pccc",
         "dn", "ds", "tcn", "ttcn", "mc", "mcdd", "hsmt", "hsdt", "tkbvtc", "bvtc", "dtcb", "cdc", "wd", "cbd"}
_NUM = re.compile(r"^[+\-±]?\d[\d.,]*%?$")
_CODE = re.compile(r"^[A-Za-z]{1,6}[\d][A-Za-z0-9.\-/+]*$|^[\d]+[A-Za-z]{1,3}$|^\d+[xX×]\d+.*$")
_TOK = re.compile(r"[^\W_]+(?:[.,\-/+][^\W_]+)*", re.UNICODE)
_OCRD = {"D": "0", "O": "0", "o": "0", "U": "0", "u": "0", "I": "1", "l": "1", "S": "5", "B": "8"}
_KM = re.compile(r"\bK\s?M\s*([0-9DOoUuIlSB]{1,3})\s*\+\s*([0-9DOoUuIlSB]{2,3})(?:\s*[.,]\s*([0-9DOoUuIlSB]{1,2}))?",
                 re.I)


def _syllable(tok):
    t = fold(tok)
    if not t.isalpha() or not t.isascii() or len(t) > 7:
        return False
    if any(b in t for b in _BAD_NUC):
        return False
    return bool(_SYL.match(t))


def classify_line(text):
    """-> (loại, từ_hợp_lệ, tổng_từ). loại: text | num | junk"""
    toks = _TOK.findall(text)
    if not toks:
        return "junk", 0, 0
    words = [t for t in toks if t.isalpha() and len(t) >= 2]
    valid = 0
    for w in words:
        lw = fold(w)
        if _syllable(w) or lw in _ACRO:
            valid += 1
    nums = [t for t in toks if _NUM.match(t) or _CODE.match(t)]
    letters = sum(c.isalpha() for c in text)
    alnum = sum(c.isalnum() for c in text)
    if alnum < max(2, 0.45 * len(text.replace(" ", ""))):
        return "junk", valid, len(words)
    if words and valid >= 2 and valid >= 0.6 * len(words):
        return "text", valid, len(words)
    if len(words) == 1 and valid == 1 and len(words[0]) >= 4 and len(toks) <= 3:
        return "text", valid, 1
    if nums and len(nums) >= 0.7 * len(toks) and (len(nums) >= 2 or sum(c.isdigit() for c in text) >= 3):
        return "num", valid, len(words)
    if words and valid >= 1 and valid >= 0.5 * len(words) and letters >= 4 and len(toks) >= 2:
        return "text", valid, len(words)
    return "junk", valid, len(words)


def extract_km(texts):
    out = []
    for t in texts:
        for m in _KM.finditer(t):
            a, b, c = m.group(1), m.group(2), m.group(3)
            fix = lambda s: "".join(_OCRD.get(ch, ch) for ch in s)
            try:
                km = int(fix(a))
                mm = int(fix(b))
            except ValueError:
                continue
            if km > 200 or mm > 999:
                continue
            out.append((km * 1000 + mm, f"Km{km}+{mm:03d}"))
    return out


def _key(text):
    return re.sub(r"[^a-z]", "", fold(text))


# ---------------------------------------------------------------- dựng markdown
_TITLE_KW = ("trac ngang", "trac doc", "binh do", "mat cat", "mat bang", "chi tiet", "tong hop khoi luong",
             "khoi luong", "so do", "bo tri", "tuong chan", "nut giao", "ket cau", "dien hinh", "coc khoan",
             "ban mat cau", "goi cau", "khe co gian", "lan can", "thoat nuoc", "dia chat", "lo khoan", "mo cau",
             "tru cau", "dam", "cong hop", "cong tron", "ham chui", "ham so", "cau ", "duong dan", "mong",
             "bang ", "ghi chu", "thuyet minh", "tim tuyen", "phoi canh", "vi tri", "ho khoan")


def guess_book_type(rel):
    """'draw' (quyển bản vẽ) hay 'text' (thuyết minh, bảng tính, hồ sơ mời thầu…) — đoán từ tên file."""
    f = fold(nfc(rel))
    if re.search(r"thuyet minh|bang tinh|ban tinh|ho so moi thau|bieu ket qua|bao cao|bien ban|phu luc|ke hoach|"
                 r"tong hop chi tieu", f):
        return "text"
    if re.search(r"ban ve|quyen ii\.", f):
        return "draw"
    return "text"


# ---------------------------------------------------------------- số liệu nghi sai + sửa tay
_CONF = "OoIlSBDUZ"
_AMBIG = re.compile(r"(?<![^\W\d_])(?=[\d" + _CONF + r".,+]*\d)(?=[\d.,+]*[" + _CONF + r"])[\d" + _CONF + r"][\d" + _CONF + r".,+]*(?![^\W\d_])")
_BADNUM = re.compile(r"\d[.,]\d+[.,]\d+[.,]\d+|\d[.,]{2,}\d|\d{5,}[.,]\d{4,}|(?<!\d)[.,]\d+[.,]\d+(?!\d)")
_UNITNUM = re.compile(r"\d\s*(?:m2|m3|m²|m³|kg|tấn|tan|MPa|cm|mm|%|km|kN|T)\b", re.I)
REASONS = {"conf": "chữ lẫn trong số (O/I/l/S/B/D…)", "bad": "số sai dạng (dấu . , thừa/lạ)",
           "km": "lý trình có chữ", "tab": "bảng số liệu (OCR số kém tin cậy)"}


def load_fixes(con, file_id):
    """{(page,line): (status, fixed, orig)}"""
    try:
        rows = con.execute("SELECT page,line,status,fixed,orig FROM corrections WHERE file_id=?", (file_id,)).fetchall()
    except Exception:
        return {}
    return {(p, l): (s, f, o) for p, l, s, f, o in rows}


def save_fix(con, file_id, page, line, orig, fixed, status):
    con.execute("INSERT OR REPLACE INTO corrections(file_id,page,line,orig,fixed,status,updated) VALUES(?,?,?,?,?,?,?)",
                (file_id, page, line, orig, fixed, status, time.time()))
    con.commit()


def clear_fix(con, file_id, page, line):
    con.execute("DELETE FROM corrections WHERE file_id=? AND page=? AND line=?", (file_id, page, line))
    con.commit()


_HEADNUM = re.compile(r"^\s*[\[(]?[A-Za-zIVX]{1,4}\.[\d.IlO]*\.?\s")


def line_doubts(text):
    """Các lý do một dòng OCR có thể sai số. -> list mã lý do."""
    out = []
    if not any(c.isdigit() for c in text) or "...." in text:
        return out
    body = _HEADNUM.sub("", text, count=1) if _HEADNUM.match(text) else text
    for m in _AMBIG.finditer(body):
        tok = m.group(0)
        nd = sum(ch.isdigit() for ch in tok)
        if 3 <= len(tok) <= 14 and (nd >= 2 or re.search(r"\d[.,]|[.,]\d", tok)) and nd < len(tok):
            out.append("conf")
            break
    if _BADNUM.search(body):
        out.append("bad")
    for m in _KM.finditer(text):
        if any(ch in _CONF for ch in (m.group(1) + m.group(2))):
            out.append("km")
            break
    return out


def _load_pages(con, file_id, fixes=None):
    pages = {}
    fixes = fixes or {}
    for pid, page, label, kind, w, h in con.execute(
            "SELECT id,page,label,kind,w,h FROM pages WHERE file_id=? ORDER BY page", (file_id,)):
        pages[page] = {"label": label, "kind": kind, "w": w or 0, "h": h or 0, "lines": []}
    for ln, page, text, x0, y0, x1, y1 in con.execute(
            "SELECT line,page,text,x0,y0,x1,y1 FROM lines WHERE file_id=? ORDER BY page,line", (file_id,)):
        if page in pages:
            fx = fixes.get((page, ln))
            if fx and fx[0] == "fixed" and fx[1]:
                text = fx[1]
            pages[page]["lines"].append((ln, nfc(text), x0, y0, x1, y1))
    return pages


def _boilerplate(pages):
    """Dòng lặp ở >= 40% số trang có nội dung -> khung tên / đầu trang chân trang."""
    content = [p for p in pages.values() if p["lines"]]
    n = len(content)
    if n < 6:
        return set(), []
    thr = max(4, int(0.4 * n))
    cnt, sample = Counter(), {}
    for p in content:
        seen = set()
        for _, t, *_ in p["lines"]:
            k = _key(t)
            if len(k) >= 8 and k not in seen:
                seen.add(k)
                cnt[k] += 1
                sample.setdefault(k, t)
    keys = {k for k, c in cnt.items() if c >= thr}
    ordered = sorted(keys, key=lambda k: -cnt[k])
    return keys, [sample[k] for k in ordered[:10]]


def _merge_rows(items):
    """Gộp các đoạn chữ cùng một hàng (theo toạ độ y) thành 'a | b | c'. items: (ln,text,x0,y0,x1,y1)."""
    rows = []
    for it in sorted(items, key=lambda r: ((r[3] + r[5]) / 2, r[2])):
        _, _, x0, y0, x1, y1 = it
        h = max(1.0, y1 - y0)
        if rows:
            ry0, ry1 = rows[-1]["y0"], rows[-1]["y1"]
            ov = min(y1, ry1) - max(y0, ry0)
            if ov >= 0.5 * min(h, max(1.0, ry1 - ry0)):
                rows[-1]["cells"].append(it)
                rows[-1]["y0"], rows[-1]["y1"] = min(ry0, y0), max(ry1, y1)
                continue
        rows.append({"cells": [it], "y0": y0, "y1": y1})
    out = []
    for r in rows:
        cells = sorted(r["cells"], key=lambda c: c[2])
        out.append((" | ".join(c[1] for c in cells), len(cells)))
    return out


def pick_title(texts):
    """Tiêu đề bản vẽ: cụm bắt đầu từ từ khoá (TRẮC NGANG…, BÌNH ĐỒ…) trong dòng có nhiều từ hợp lệ nhất."""
    best, best_s = "", 0
    for t in texts:
        t = " ".join(t.split())
        if not (6 <= len(t) <= 160):
            continue
        ft = fold(t).lower()
        hits = [(ft.find(k), k) for k in _TITLE_KW if len(k) >= 6 and k in ft]
        if not hits:
            continue
        c, valid, nw = classify_line(t)
        if valid < 2:
            continue
        pos = min(h[0] for h in hits)
        cand = t[pos:pos + 80].strip(" |-:.,")
        sc = valid + 3 * len(hits)
        if sc > best_s:
            best, best_s = cand, sc
    return best


# ---------------------------------------------------------------- tiêu đề chuẩn của trang bản vẽ cầu
_CANON = [
    ("tonghopkhoiluong", "Tổng hợp khối lượng"),
    ("lancankhecogian", "Lan can, khe co giãn, thoát nước và hoàn thiện"),
    ("binhdocau", "Bình đồ cầu"),
    ("botrichungcau", "Bố trí chung cầu"),
    ("matcatngangcau", "Mặt cắt ngang cầu"),
    ("sieuamcockhoan", "Siêu âm cọc khoan nhồi"),
    ("cockhoannhoi", "Cọc khoan nhồi"),
    ("ketcauphanduoi", "Kết cấu phần dưới"),
    ("ketcauphantren", "Kết cấu phần trên"),
    ("ketcaumatduong", "Kết cấu mặt đường trên mố"),
    ("tracdoccau", "Trắc dọc cầu"),
    ("diachatthamkhao", "Địa chất tham khảo"),
    ("lokhoan", "Lỗ khoan"),
    ("tracngangcau", "Trắc ngang cầu"),
    ("trucau", "Trụ cầu"),
    ("mocau", "Mố cầu"),
    ("banmatcau", "Bản mặt cầu"),
    ("ghichu", "Ghi chú"),
    ("ketcau", "Kết cấu"),
]
_SUFFIX = re.compile(r"\(\s*(\d)\s*/\s*(\d)\s*\)")


def _tri(k):
    return {k[i:i + 3] for i in range(len(k) - 2)}


def canon_title(raw_lines, page_h, fallback=""):
    """Tiêu đề chuẩn theo khung tên cuối trang (sửa chữ OCR hỏng). raw_lines: (ln,text,x0,y0,x1,y1)."""
    allc = T._compact(" ".join(r[1] for r in raw_lines))
    if "mucluc" in allc and ("tenbanve" in allc or "kyhieu" in allc):
        return "Mục lục bản vẽ"
    top = T._compact(" ".join(r[1] for r in raw_lines if page_h and r[3] < 0.12 * page_h))
    if "uybannhandan" in top and "binhdocau" not in allc and len(raw_lines) <= 45 and "hangmuccongviec" not in allc:
        return "Bìa quyển"
    def search(lines):
        comps = [(T._compact(r[1]), r[1]) for r in lines]
        for key, title in _CANON:
            for comp, orig in comps:
                hit = key in comp
                if not hit and len(key) >= 9 and len(comp) >= len(key) - 2:
                    tk = _tri(key)
                    hit = len(tk & _tri(comp)) >= 0.78 * len(tk)
                if hit:
                    m = _SUFFIX.search(orig)
                    return title + (f" ({m.group(1)}/{m.group(2)})" if m else "")
        return ""
    low = [r for r in raw_lines if page_h and r[3] >= 0.78 * page_h]
    return search(low) or search(raw_lines) or fallback


def _seg_title(sg):
    if sg.get("front"):
        return "Mở đầu (bìa, mục lục)"
    if sg.get("tail"):
        return "Các trang tiếp theo"
    if sg.get("label"):
        return sg["label"]
    return f"Cầu {sg['n']} · {sg['km'] or '(chưa đọc được lý trình)'}"


_SEP = re.compile(r"^\W*([IVX]{1,3}\s*\.\s*\d{1,2})\s*[.:\-]?\s*(\S.*)$")
_KMTXT = re.compile(r"K\s?M\s*(\d{1,3})\s*\+\s*(\d{3}(?:\s*[.,]\s*\d{1,3})?)", re.I)


def _seg_from_separators(infos, npages):
    """Quyển có trang ngăn kiểu 'I.7. CẦU KM4+282.000': mỗi trang ngăn mở đầu một cầu."""
    seps = sorted(p for p, i in infos.items() if i.get("sep"))
    thkl_n = sum(1 for i in infos.values() if i.get("thkl") is not None)
    if len(seps) < 3 or len(seps) < 0.6 * max(1, thkl_n):
        return []
    nums = [infos[p]["sep"][0].replace(" ", "") for p in seps if infos[p]["sep"][0]]
    fix_ii = nums and sum(n.startswith("I.") for n in nums) >= 0.6 * len(nums)   # OCR hay đọc "I." thành "II."
    segs = []
    first = seps[0]
    first_page = min(infos) if infos else 1
    if first > first_page:
        segs.append({"a": first_page, "b": first - 1, "thkl": None, "front": True, "n": None, "km": ""})
    last = max(max(infos), npages or 0)
    for i, p in enumerate(seps):
        b = seps[i + 1] - 1 if i + 1 < len(seps) else last
        num, rest = infos[p]["sep"]
        rest = " ".join(rest.split())
        if fix_ii and num.replace(" ", "").startswith("II."):
            num = num.replace(" ", "")[1:]
            rest = rest
        m = _KMTXT.search(rest)
        km = ""
        if m:
            km = f"Km{int(m.group(1))}+" + re.sub(r"\s+", "", m.group(2)).replace(",", ".")
        thk = next((q for q in range(p, b + 1) if q in infos and infos[q].get("thkl") is not None), None)
        segs.append({"a": p, "b": b, "thkl": thk, "n": num.replace(" ", "") or "·", "km": km,
                     "label": (f"{num.replace(' ', '')} · {rest}" if num else rest)})
    return segs


def _make_segments(infos, npages):
    """Chia quyển bản vẽ thành các đoạn theo cầu: mỗi cầu kết thúc bằng 1 trang bảng tổng hợp khối lượng."""
    by_sep = _seg_from_separators(infos, npages)
    if by_sep:
        return by_sep
    thkl_p = sorted(p for p, i in infos.items() if i.get("thkl") is not None)
    if not thkl_p:
        return []
    nbase = lambda c: len({k.split(".")[0] for k in c})
    segs, prev = [], 0
    for t in thkl_p:
        segs.append({"a": prev + 1, "b": t, "thkl": t})
        prev = t
    last = max(max(infos), npages or 0)
    if last > prev and any(p in infos and infos[p]["kept"] for p in range(prev + 1, last + 1)):
        segs.append({"a": prev + 1, "b": last, "thkl": None, "tail": True})
    f = segs[0]
    sb = next((p for p in range(f["a"], f["b"] + 1) if p in infos and nbase(infos[p]["cands"]) == 1), None)
    if sb and sb > f["a"] and sb - f["a"] <= 8:
        segs.insert(0, {"a": f["a"], "b": sb - 1, "thkl": None, "front": True})
        f["a"] = sb
    n = 0
    for sg in segs:
        sg.setdefault("km", "")
        if sg.get("front") or sg.get("tail"):
            sg["n"] = None
            continue
        cnt = Counter()
        for p in range(sg["a"], sg["b"] + 1):
            if p in infos and nbase(infos[p]["cands"]) <= 2:
                cnt.update(infos[p]["cands"])
        base = Counter()
        for k, c in cnt.items():
            base[k.split(".")[0]] += c
        if base:
            top = base.most_common(1)[0][0]
            fulls = [(("." in k), c, len(k), k) for k, c in cnt.items() if k.split(".")[0] == top]
            sg["km"] = max(fulls)[3]
        n += 1
        sg["n"] = n
    return segs


def _fmt_val(r):
    v = r["val"]
    if v is None:
        return "?"
    if r["note"] and "đã tự chèn" not in r["note"]:
        return "~" + v if not v.endswith("??") else v
    if r["note"]:
        return "~" + v
    return v


def _serious(r):
    n = r["note"]
    return r["val"] is None or "≠" in n or "phải =" in n or "cụt" in n or "nghi" in n or "lạ" in n


def thkl_block(pno, res, sg, med_rows):
    """Bảng THKL của 1 cầu -> (markdown, số_dòng_nghi_sai, đọc_thiếu)."""
    items = res["items"]
    n = len(items)
    serious = [r for r in items if _serious(r)]
    weak = bool(med_rows) and n < 0.75 * med_rows
    ttl = "Bảng tổng hợp khối lượng"
    if sg and (sg.get("n") or sg.get("label")):
        ttl = f"{_seg_title(sg)} — {ttl}"
    flag_txt = ""
    if weak:
        flag_txt = "  ⚠ đọc thiếu dòng: xem ảnh trang"
    elif len(serious) >= 6:
        flag_txt = f"  ⚠ {len(serious)} số nghi sai: xem ảnh trang"
    out = [f"### {ttl} (tr.{pno})" + flag_txt]
    out.append(f"> Tách từ chữ OCR ({n} dòng; thường ~{med_rows or '70-90'}). `~`=số nghi sai hoặc đã tự chèn dấu thập phân "
               f"(bảng ghi 2 số lẻ), `?`=không đọc được, ✓=cốt thép cộng khớp. Khối trái rồi khối phải. "
               f"**Mọi số `~`/`?` phải đối chiếu ảnh trang.**")
    if weak:
        out.append(f"> ⚠ Chỉ đọc được {n} dòng — thiếu nhiều dòng (dấu thẩm tra đè hoặc OCR kém).")
    out.append("")
    out.append("| Mục | Hạng mục | ĐV | KL | Ghi chú |")
    out.append("|---|---|---|---|---|")
    last_sec = None
    for r in items:
        sec = r["section"] if r["section"] != last_sec else ""
        last_sec = r["section"]
        note = r["note"] + ("; " if r["note"] and r.get("ok") else "") + ("✓" if r.get("ok") else "")
        name = " ".join(r["name"].split()).replace("|", "/")
        out.append(f"| {sec} | {name} | {r['unit']} | {_fmt_val(r)} | {note} |")
    if res["unparsed"]:
        out.append("")
        out.append("Dòng có số nhưng chưa tách được (xem ảnh trang): " +
                   " ‖ ".join(" ".join(u.split())[:70] for u in res["unparsed"][:8]))
    return "\n".join(out) + "\n", len(serious), weak


def build_book(con, file_id, mode="gon", pages_filter=None, merge_rows=True, book_type="auto"):
    """-> dict(blocks, catalog, stats). book_type: auto | text | draw."""
    f = con.execute("SELECT rel, pages, ext FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        raise ValueError("Không có file trong chỉ mục")
    rel, npages, ext = f
    btype = guess_book_type(rel) if book_type == "auto" else book_type
    fixes = load_fixes(con, file_id)
    pages = _load_pages(con, file_id, fixes)
    doubts = []
    raw_bytes = sum(len(t.encode("utf-8")) for p in pages.values() for _, t, *_ in p["lines"])
    boiler, boiler_samples = _boilerplate(pages)

    # lý trình xuất hiện ở >= 30% số trang là lý trình của cả quyển (khung tên) -> bỏ khỏi từng trang
    km_pages = Counter()
    for p in pages.values():
        for v in {v for v, _ in extract_km([t for _, t, *_ in p["lines"]])}:
            km_pages[v] += 1
    n_content = max(1, sum(1 for p in pages.values() if p["lines"]))
    km_const = {v for v, c in km_pages.items() if n_content >= 6 and c >= 0.3 * n_content}

    infos, blank, unread = {}, [], []
    for pno in sorted(pages):
        p = pages[pno]
        kind = "ocr" if p["kind"] == "mix" else p["kind"]
        if kind == "none":
            unread.append(pno)
            continue
        raw_lines = [r for r in p["lines"] if r[1].strip()]
        cands, thkl_res = Counter(), None
        if btype == "draw":
            cands = T.bridge_candidates([r[1] for r in raw_lines])
            if mode != "full" and kind == "ocr" and raw_lines:
                ok_t, _n = T.is_thkl_page([r[1] for r in raw_lines])
                if ok_t:
                    srt = sorted(raw_lines, key=lambda r: (r[3], r[2]))
                    thkl_res = T.parse_page([(r[1], r[2], r[4]) for r in srt])
                    T.check_steel(thkl_res["items"])
                    T.check_overlay(thkl_res["items"])
        sep = None
        if btype == "draw" and 1 <= len(raw_lines) <= 3:
            for r in raw_lines:
                m_sep = _SEP.match(r[1].strip())
                if m_sep and "C" in m_sep.group(2).upper()[:3]:
                    sep = (m_sep.group(1), m_sep.group(2))
                    break
                if re.match(r"^C[ẦẤA]U\s*K\s?M\s*\d", r[1].strip().upper()):
                    sep = ("", r[1].strip())
                    break
        kmv = sorted({kv for kv in extract_km([r[1] for r in raw_lines]) if kv[0] not in km_const})
        kept, cnt = [], Counter()
        prose = 0
        for r in raw_lines:
            k = _key(r[1])
            if k in boiler and len(k) >= 8:
                continue
            if mode == "full" or kind in ("doc", "sheet", "txt"):
                c, valid = "text", 9
            else:
                c, valid, _ = classify_line(r[1])
            cnt[c] += 1
            if c == "text" and valid >= 4:
                prose += 1
            if c != "junk":
                kept.append((r, c))
        n_all = max(1, sum(cnt.values()))
        text_chars = sum(len(r[1]) for r, c in kept if c == "text")
        ratio = cnt["text"] / n_all
        if kind in ("doc", "sheet", "txt") or mode == "full" or thkl_res is not None:
            is_draw = False
        elif btype == "draw":
            big = max(p["w"], p["h"]) >= 1000
            is_draw = kind == "ocr" and (big or not (prose >= 20 and ratio >= 0.7)) or \
                (kind != "ocr" and not (prose >= 6 and text_chars >= 600))
        else:
            is_draw = kind == "ocr" and prose < 3 and text_chars < 350 and ratio < 0.30
        if not is_draw and kind == "ocr" and thkl_res is None:
            for r, c in kept:
                if (pno, r[0]) in fixes:
                    continue
                why = line_doubts(r[1])
                if why:
                    doubts.append({"page": pno, "line": r[0], "text": r[1], "why": why})
        title = pick_title([r[1] for r, c in kept if c == "text"])
        if btype == "draw" and kind == "ocr":
            title = canon_title(raw_lines, p["h"], title)
        infos[pno] = {"kind": kind, "kept": kept, "km": kmv, "draw": is_draw, "label": p["label"],
                      "n_num": cnt["num"], "title": ("Trang ngăn" if sep else title), "cands": cands, "thkl": thkl_res,
                      "sep": sep}
        if not kept and not kmv:
            blank.append(pno)

    blocks, cat_rows = [], []
    segs = _make_segments(infos, npages) if btype == "draw" else []
    seg_of = {p: sg for sg in segs for p in range(sg["a"], sg["b"] + 1)}
    nrows = sorted(len(i["thkl"]["items"]) for i in infos.values() if i.get("thkl") is not None)
    med_rows = nrows[len(nrows) // 2] if nrows else 0
    thkl_weak, thkl_serious = [], 0
    for pno in sorted(infos):
        info = infos[pno]
        if pno in blank or (pages_filter is not None and pno not in pages_filter):
            continue
        kept, km = info["kept"], info["km"]
        if info.get("thkl") is not None:
            md, nser, weak = thkl_block(pno, info["thkl"], seg_of.get(pno), med_rows)
            blocks.append((pno, md))
            need = bool(nser or weak)
            thkl_serious += nser
            if weak:
                thkl_weak.append(pno)
            if need and fixes.get((pno, 0), ("",))[0] != "skip":
                doubts.append({"page": pno, "line": 0, "text": f"bảng khối lượng: {nser} dòng nghi sai"
                               + (", đọc thiếu dòng" if weak else ""), "why": ["tab"]})
            cat_rows.append((pno, "thkl", "Bảng tổng hợp khối lượng", "", need))
            continue
        km_txt = ""
        if km:
            km_txt = km[0][1] if km[0][1] == km[-1][1] else f"{km[0][1]} → {km[-1][1]}"
        if info["draw"]:
            cat_rows.append((pno, "draw", info["title"], km_txt, False))
            if pages_filter is not None:     # xuất theo kết quả tìm: ghi 1 dòng cho trang bản vẽ
                blocks.append((pno, f"### Trang {pno} [bản vẽ] {info['title'] or '(không đọc được tiêu đề)'}"
                                    + (f" — {km_txt}" if km_txt else "") + "\n"))
            continue
        if info["kind"] == "sheet":
            rows = _merge_rows([(r[0], r[1], r[2], r[2], r[2] + 1, r[2] + 1) for r, _ in kept]) if merge_rows else \
                [(r[1], 1) for r, _ in kept]
            body = "\n".join(t for t, _ in rows)
            blocks.append((pno, f"### Sheet {info['label'] or pno}\n{body}\n"))
            cat_rows.append((pno, "text", f"Sheet {info['label'] or pno}", "", False))
            continue
        items = [r for r, c in kept if c == "text" or (c == "num" and mode != "ratgon")]
        has_bbox = info["kind"] in ("ocr", "text")
        rows, multi = None, 0
        if merge_rows and has_bbox and items:
            rows = _merge_rows(items)
            multi = sum(1 for _, n in rows if n >= 2)
        is_table = bool(rows) and multi >= 4
        lines = [t for t, _ in rows] if is_table else [r[1] for r in items]
        body = "\n".join(l.strip() for l in lines if l.strip())
        if len(body) < 12 and not km_txt:
            continue
        need_img = is_table and info["n_num"] >= 8 and info["kind"] == "ocr"
        if need_img:
            if fixes.get((pno, 0), ("",))[0] == "skip":
                need_img = False
            else:
                doubts.append({"page": pno, "line": 0, "text": f"{info['n_num']} dòng số trong bảng", "why": ["tab"]})
        flag = "  ⚠ bảng số liệu: đối chiếu ảnh trang" if need_img else ""
        blocks.append((pno, f"### Trang {pno}" + (f" — Lý trình {km_txt}" if km_txt else "") + f"{flag}\n{body}\n"))
        title = info["title"] or (lines[0][:90] if lines else "")
        cat_rows.append((pno, "text", title, km_txt, need_img))

    stats = {"rel": rel, "pages": npages, "raw_bytes": raw_bytes, "btype": btype, "blank": blank, "unread": unread,
             "draw_pages": sum(1 for r in cat_rows if r[1] == "draw"),
             "text_pages": sum(1 for r in cat_rows if r[1] == "text"),
             "boiler": boiler_samples, "flag_pages": [r[0] for r in cat_rows if r[4]],
             "doubts": sorted(doubts, key=lambda d: (d["page"], d["line"])),
             "resolved": sorted([(p, l, v[2], v[1]) for (p, l), v in fixes.items() if v[0] == "fixed"]),
             "skipped": sum(1 for v in fixes.values() if v[0] == "skip")}
    stats["segments"] = segs
    stats["thkl_pages"] = sum(1 for i in infos.values() if i.get("thkl") is not None)
    stats["thkl_weak"] = thkl_weak
    stats["thkl_serious"] = thkl_serious
    if pages_filter is not None:
        stats["blocks_only"] = True
    return {"blocks": [b for b in blocks if not (b[1].startswith("### Trang") and "[bản vẽ]" in b[1].split("\n")[0]) or pages_filter is not None],
            "catalog": cat_rows, "stats": stats}


def _runs(ints):
    out, s, p = [], None, None
    for i in ints:
        if s is None:
            s = p = i
        elif i == p + 1:
            p = i
        else:
            out.append((s, p))
            s = p = i
    if s is not None:
        out.append((s, p))
    return out


def _fmt_runs(ints, limit=40):
    r = _runs(sorted(ints))
    txt = ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in r[:limit])
    return txt + (" …" if len(r) > limit else "")


def catalog_md(cat_rows):
    """Danh mục trang: các trang bản vẽ liền nhau cùng tiêu đề gộp thành một dải."""
    lines, i, n = [], 0, len(cat_rows)
    while i < n:
        pno, kind, title, km, img = cat_rows[i]
        if kind == "draw":
            j, kms, cnt = i, [km] if km else [], 1
            cur_title = title
            while j + 1 < n and cat_rows[j + 1][1] == "draw" and cat_rows[j + 1][0] == cat_rows[j][0] + 1:
                t2 = cat_rows[j + 1][2]
                if cur_title and t2 and difflib.SequenceMatcher(None, _key(cur_title), _key(t2)).ratio() < 0.6:
                    break
                if not cur_title:
                    cur_title = t2
                j += 1
                cnt += 1
                if cat_rows[j][3]:
                    kms.append(cat_rows[j][3])
            span = f"tr.{pno}" if j == i else f"tr.{pno}–{cat_rows[j][0]} ({cnt} trang)"
            kmtxt = ""
            vals = [k.split(" → ") for k in kms]
            flat = [x for v in vals for x in v]
            if flat:
                kmtxt = f" · {flat[0]}" + (f" → {flat[-1]}" if flat[-1] != flat[0] else "")
            lines.append(f"- {span} [bản vẽ] {cur_title[:90] or '(chưa đọc được tiêu đề)'}{kmtxt}")
            i = j + 1
        else:
            lines.append(f"- tr.{pno} {title[:70]}" + (f" · {km}" if km else "") + (" ⚠" if img else ""))
            i += 1
    return "\n".join(lines)


def _base_title(t):
    return _SUFFIX.sub("", t or "").strip()


def catalog_seg_md(cat_rows, segs):
    """Danh mục theo từng cầu; các trang liền nhau cùng loại bản vẽ gộp thành 1 dải."""
    by = {r[0]: r for r in cat_rows}
    out = []
    for sg in segs:
        pages = [p for p in range(sg["a"], sg["b"] + 1) if p in by]
        if not pages:
            continue
        head = f"### {_seg_title(sg)} · tr.{sg['a']}–{sg['b']}"
        out.append(head)
        i = 0
        while i < len(pages):
            pno, kind, title, km, img = by[pages[i]]
            j = i
            while (j + 1 < len(pages) and pages[j + 1] == pages[j] + 1 and by[pages[j + 1]][1] == kind
                   and kind == "draw" and _base_title(by[pages[j + 1]][2]) == _base_title(title)):
                j += 1
            span = f"tr.{pno}" if j == i else f"tr.{pno}–{pages[j]} ({j - i + 1} trang)"
            if kind == "thkl":
                out.append(f"- {span} **Bảng tổng hợp khối lượng** → mục “Bảng tổng hợp khối lượng” bên dưới" + (" ⚠" if img else ""))
            elif kind == "draw":
                out.append(f"- {span} [bản vẽ] {_base_title(title)[:90] or '(chưa đọc được tiêu đề)'}")
            else:
                out.append(f"- {span} {title[:70]}" + (" ⚠" if img else ""))
            i = j + 1
        out.append("")
    return "\n".join(out).rstrip("\n")


def safe_name(rel):
    base = os.path.splitext(os.path.basename(rel))[0]
    s = fold(nfc(base))
    s = re.sub(r"[^a-z0-9.+\-]+", "_", s, flags=re.I).strip("_")
    return (s[:90] or "file")


def doubt_report_md(st, limit=120):
    """Mục báo cáo đặt ở ĐẦU file: các số liệu OCR có thể sai + các chỗ đã nhập tay."""
    d = st.get("doubts") or []
    res = st.get("resolved") or []
    if not d and not res:
        return ""
    out = ["## ⚠ BÁO CÁO SỐ LIỆU CHƯA CHẮC CHẮN (đọc trước khi dùng số)", ""]
    if d:
        cnt = Counter(w for x in d for w in x["why"])
        out.append(f"Có **{len(d)}** chỗ OCR có thể sai (" + "; ".join(f"{REASONS[k]}: {v}" for k, v in cnt.items()) +
                   "). Chưa kiểm tra bằng mắt — KHÔNG dùng các số này làm khối lượng chính thức khi chưa đối chiếu ảnh trang.")
        out.append("")
        for x in d[:limit]:
            pos = f"tr.{x['page']}" + (f" dòng {x['line']}" if x["line"] else " (cả bảng)")
            txt = " ".join(x["text"].split())
            out.append(f"- {pos} [{', '.join(REASONS[w].split(' (')[0] for w in x['why'])}]: `{txt[:110]}`")
        if len(d) > limit:
            out.append(f"- … và {len(d) - limit} chỗ nữa (xem đầy đủ trong phần mềm: Chỉ mục → Kiểm tra số liệu)")
        out.append("")
    if res:
        out.append(f"**Đã nhập tay/xác nhận bởi người dùng ({len(res)}):** (văn bản bên dưới đã dùng giá trị mới)")
        for p, l, o, f in res[:60]:
            out.append(f"- tr.{p} dòng {l}: `{' '.join((o or '').split())[:70]}` → `{' '.join((f or '').split())[:70]}`")
        out.append("")
    if st.get("skipped"):
        out.append(f"Người dùng đã chọn bỏ qua (chấp nhận nguyên văn OCR): {st['skipped']} chỗ, không liệt kê lại.")
        out.append("")
    return "\n".join(out) + "\n"


def compose_parts(book, rel, mode, split_kb, ts):
    """Ghép tiêu đề + mục lục + các khối, tách thành nhiều phần theo dung lượng."""
    st = book["stats"]
    name = os.path.splitext(os.path.basename(rel))[0]
    head = [f"# {name}", ""]
    kind_txt = (("quyển bản vẽ (danh mục theo cầu + bảng tổng hợp khối lượng)" if st.get("thkl_pages")
                 else "quyển bản vẽ (chỉ có danh mục)") if st.get("btype") == "draw" else "quyển văn bản")
    head.append(f"> Nguồn: `{rel}` · {st['pages']} trang PDF · {kind_txt} · xuất {ts} · chế độ: {MODES.get(mode, mode)}")
    head.append(f"> Văn bản do OCR tự động — có thể sai dấu/số. Trang đánh dấu ⚠ là bảng số liệu: cần đối chiếu ảnh trang gốc.")
    if st["boiler"]:
        head.append("> Khung tên/đầu trang lặp ở nhiều trang (đã lược bỏ): " +
                    " / ".join(b[:70] for b in st["boiler"][:6]))
    if st["unread"]:
        head.append(f"> Trang chưa đọc được (OCR lỗi/chưa chạy): {_fmt_runs(st['unread'])}")
    if st["blank"]:
        head.append(f"> Trang trống/không có chữ đọc được: {_fmt_runs(st['blank'])}")
    if st.get("thkl_pages"):
        nb = sum(1 for sg in st.get("segments", []) if sg.get("n"))
        head.append(f"> Nhận dạng {nb} cầu, {st['thkl_pages']} bảng tổng hợp khối lượng"
                    + (f" (trang đọc thiếu: {_fmt_runs(st['thkl_weak'])})" if st.get("thkl_weak") else "")
                    + f"; {st['thkl_serious']} dòng khối lượng nghi sai/không đọc được — đối chiếu ảnh trang trước khi dùng số.")
    head.append("")
    report = doubt_report_md(st)
    cat = ""
    if book.get("catalog_text"):
        title_c = (("Danh mục bản vẽ theo cầu (số liệu hình học phải xem ảnh trang)" if st.get("segments")
                    else "Danh mục bản vẽ (số liệu phải xem ảnh trang)") if st.get("btype") == "draw" else "Mục lục trang")
        cat = f"## {title_c}\n" + book["catalog_text"] + "\n\n"
    body_blocks = book["blocks"]
    # bản vẽ: gom thành nhóm "## Danh mục bản vẽ" để dễ đọc
    parts, cur, cur_bytes = [], [], 0
    limit = max(50, int(split_kb)) * 1024 if split_kb else 0
    for pno, md in body_blocks:
        b = len(md.encode("utf-8"))
        if limit and cur and cur_bytes + b > limit:
            parts.append(cur)
            cur, cur_bytes = [], 0
        cur.append((pno, md))
        cur_bytes += b
    if cur or not parts:
        parts.append(cur)
    out = []
    for i, blk in enumerate(parts, 1):
        h = list(head)
        if len(parts) > 1:
            h[0] = f"# {name} (phần {i}/{len(parts)}: trang {blk[0][0]}–{blk[-1][0]})" if blk else h[0]
        text = "\n".join(h) + "\n"
        if i == 1:
            text += report + cat
        if blk:
            text += ("## Bảng tổng hợp khối lượng và trang chữ\n\n" if st.get("thkl_pages") else "## Trang chữ\n\n") \
                if st.get("btype") == "draw" else "## Nội dung\n\n"
        text += "".join(md if md.startswith("###") else md for _, md in blk)
        out.append(text)
    return out


PROMPT_TEMPLATE = """# PROMPT – Tóm tắt file OCR sang .md gọn (cầu đường)

> Cách dùng: đính kèm file(s) `.md` xuất từ phần mềm Tra cứu hồ sơ (và PDF các trang ⚠ nếu có), dán khối dưới đây, sửa 3 dòng trong [ ].

---

Tôi đính kèm văn bản OCR của một quyển hồ sơ (đã lọc rác và bỏ khung tên lặp). Hãy tóm tắt thành **một file Markdown gọn** để lưu trong Project và dùng lại cho bóc tách khối lượng / viết thuyết minh biện pháp thi công.

**Thông tin:**
- Tên quyển: [{book}]
- Loại: [thuyết minh / bản vẽ cầu / bản vẽ tuyến / bảng tính / hồ sơ mời thầu]
- Tên file .md đầu ra: [{out}]

**1. Nguồn và độ tin cậy**
- Văn bản là OCR tự động: dấu tiếng Việt và chữ số có thể sai. KHÔNG tự sửa số; số nghi ngờ ghi "~" hoặc "(?)".
- Trang có ⚠ là bảng số liệu: mọi số trong bảng đó phải đối chiếu với ảnh trang PDF tôi đính kèm (hoặc yêu cầu tôi đính kèm). Không có ảnh thì ghi "[chưa đối chiếu ảnh]".
- Mục "[bản vẽ]" chỉ là danh mục (tiêu đề + lý trình). Muốn lấy số liệu của bản vẽ phải xem ảnh trang đó — hãy liệt kê cho tôi các trang cần gửi ảnh.

**2. Giữ**
- Theo mục lục quyển: tên hạng mục + lý trình + số trang PDF.
- Thông số kỹ thuật, tiêu chuẩn áp dụng (TCVN/QCVN/ASTM…), vật liệu, biện pháp thi công, trình tự, yêu cầu nghiệm thu, các con số có đơn vị.
- Bảng khối lượng: chép ĐỦ mọi dòng có số ≠ 0, đúng đơn vị (bảng Markdown).

**3. Bỏ**
- Phần lặp (khung tên, câu mở đầu giống nhau giữa các mục) → ghi 1 lần ở "Thông số chung".
- Nội dung chung chung không có số liệu/yêu cầu cụ thể.

**4. Định dạng**
- `# Tên quyển` → khối thông tin chung → `## 0. Thông số chung` → mỗi mục `### <STT theo mục lục> – <Tên> (trang PDF a–b)`.
- Cuối file: bảng TỔNG HỢP + dòng tổng + các điểm cần đối chiếu/mâu thuẫn giữa các trang (KHÔNG tự sửa).
- Thứ tự ưu tiên khi các văn bản mâu thuẫn: luật → TCVN → bản vẽ được phát hành → thẩm tra → bản vẽ sửa đổi → bản vẽ có dấu → văn bản không có dấu.
- Không bịa số; chỗ không đọc được ghi "[không đọc rõ]".

**5. Hoàn tất**
- Kiểm tra đủ mục như mục lục; cộng lại các tổng bằng code.
- Lưu .md vào Project cùng chỗ các Quyển đã trích; báo ngắn: số mục, các điểm cần đối chiếu, các trang cần gửi ảnh.
"""


def export_files(con, file_ids, out_dir, mode="gon", split_kb=300, merge_rows=True, book_type="auto",
                 progress=None, stop=None):
    """Ghi mỗi quyển ra 1 (hoặc vài) file .md. Trả về danh sách kết quả."""
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%d/%m/%Y %H:%M")
    results = []
    for i, fid in enumerate(file_ids):
        if stop and stop():
            break
        rel = con.execute("SELECT rel FROM files WHERE id=?", (fid,)).fetchone()[0]
        if progress:
            progress(i, len(file_ids), rel)
        book = build_book(con, fid, mode, merge_rows=merge_rows, book_type=book_type)
        book["catalog_text"] = (catalog_seg_md(book["catalog"], book["stats"]["segments"])
                                if book["stats"].get("segments") else catalog_md(book["catalog"]))
        parts = compose_parts(book, rel, mode, split_kb, ts)
        base = safe_name(rel)
        paths = []
        for k, txt in enumerate(parts, 1):
            fn = f"{base}.md" if len(parts) == 1 else f"{base}_p{k}.md"
            p = os.path.join(out_dir, fn)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
            paths.append(p)
        st = book["stats"]
        results.append({"rel": rel, "paths": paths, "raw": st["raw_bytes"], "out": sum(len(t.encode("utf-8")) for t in parts),
                        "pages": st["pages"], "draw": st["draw_pages"], "text": st["text_pages"], "btype": st["btype"],
                        "flag": len(st["flag_pages"]), "unread": len(st["unread"])})
    # tổng quan + prompt
    if results:
        idx = ["# Danh sách file đã xuất", "", f"Xuất lúc {ts} · chế độ: {MODES.get(mode, mode)}", "",
               "| File .md | Quyển gốc | Loại | Số trang | Trang chữ | Trang bản vẽ | Trang ⚠ | KB |", "|---|---|---|---|---|---|---|---|"]
        for r in results:
            idx.append(f"| {', '.join(os.path.basename(p) for p in r['paths'])} | {os.path.basename(r['rel'])} | {'bản vẽ' if r['btype'] == 'draw' else 'văn bản'} | {r['pages']} | "
                       f"{r['text']} | {r['draw']} | {r['flag']} | {r['out'] // 1024} |")
        with open(os.path.join(out_dir, "_DANH_SACH.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(idx) + "\n")
        first = results[0]
        with open(os.path.join(out_dir, "_PROMPT_tom_tat.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(PROMPT_TEMPLATE.format(book=os.path.splitext(os.path.basename(first["rel"]))[0],
                                           out=os.path.basename(first["paths"][0])))
    return results


def export_hits(con, hits, out_path, mode="gon", query=""):
    """Xuất các trang có kết quả tìm kiếm thành 1 file .md (có ghi nguồn file + trang)."""
    by = {}
    for h in hits:
        by.setdefault(h.file_id, set()).add(h.page)
    ts = time.strftime("%d/%m/%Y %H:%M")
    out = [f"# Kết quả tìm: {query}", "", f"> Xuất {ts} · {sum(len(v) for v in by.values())} trang từ {len(by)} file · "
           "văn bản OCR có thể sai dấu/số; trang ⚠ cần đối chiếu ảnh.", ""]
    for fid, pgs in by.items():
        book = build_book(con, fid, mode, pages_filter=pgs)
        rel = book["stats"]["rel"]
        out.append(f"## {os.path.basename(rel)}")
        out.append(f"> `{rel}`")
        out.append("")
        for pno, md in book["blocks"]:
            out.append(md.replace("### Trang", "### Trang", 1))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return out_path


def export_pdf_pages(page_map, out_path, max_pages=300):
    """page_map: {đường_dẫn_pdf: [số trang 1..]} -> 1 file PDF chứa các trang đó (chỉ ĐỌC file gốc)."""
    fitz = core.fitz
    total = sum(len(v) for v in page_map.values())
    if total > max_pages:
        raise ValueError(f"Có {total} trang, vượt giới hạn {max_pages}. Hãy lọc kết quả hẹp hơn.")
    out = fitz.open()
    n = 0
    for path, pages in page_map.items():
        if not path.lower().endswith(".pdf"):
            continue
        with fitz.open(path) as src:
            for p in sorted(set(pages)):
                if 1 <= p <= src.page_count:
                    out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
                    n += 1
    if not n:
        raise ValueError("Không có trang PDF nào để trích.")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    out.save(out_path, garbage=4, deflate=True)
    out.close()
    return n


# ---------------------------------------------------------------- 1.0.2: làm sạch bản xuất toàn bộ OCR
# Sai dấu hay gặp của Tesseract ở từ khóa xây dựng (chỉ áp cho chữ IN HOA / cụm đủ ngữ cảnh -> ít sửa nhầm)
_VN_WORDS = {
    "KÉT": "KẾT", "CÓT": "CỐT", "OÓT": "CỐT", "CÔT": "CỐT", "CÒT": "CỐT", "ĐÁT": "ĐẤT", "ĐẮT": "ĐẤT",
    "DÀM": "DẦM", "ĐÀM": "DẦM", "GÓI": "GỐI", "CÀU": "CẦU", "CÂU": "CẦU", "CÁU": "CẦU", "CÃU": "CẦU",
    "TÁN": "TẤN", "NHÒI": "NHỒI", "NHÔI": "NHỒI", "NHỎI": "NHỒI", "PHÀN": "PHẦN", "ĐẸM": "ĐỆM",
}
_VN_PHRASES = [
    (r"\bB[ẰÀ]NG\s+T[ỔỎÔ]NG\s+H[ỢƠ]P", "BẢNG TỔNG HỢP"),
    (r"\bTÁM\s+(?=ĐỆM|ĐẸM|BẢN|LÓT|BTXM|THÉP|CHẮN)", "TẤM "),
    (r"\bB[ẼÉỆÈẺ]\s*TÔNG", "BÊ TÔNG"),
    (r"\bV[ẤĂÂẨÁ]N\s+KHUÔN", "VÁN KHUÔN"), (r"\bM[ÓÒÕ]\s+C[ẦÀÂÃẢÁ]U", "MỐ CẦU"),
    (r"\bKH[ÓÔỐÕ]I\s+LƯỢNG", "KHỐI LƯỢNG"), (r"\bT[ÔỎỐÓ]NG\s+HỢP", "TỔNG HỢP"),
    (r"\bB[ÓÔỐ]\s+TR[ÍÌ]", "BỐ TRÍ"), (r"\bBÌNH\s+Đ[ÓÔỎ]", "BÌNH ĐỒ"),
    (r"\b[ÓÔ]NG\s+(?=THOÁT|THÉP|PVC|UPVC|NÓI|NỐI)", "ỐNG "), (r"\bN[ÓÔ]I\s+M[ÈÉ]M", "NỐI MỀM"),
    (r"\bM[ẶẠ]T\s+C[ÁẤẢ]T", "MẶT CẮT"), (r"\bĐ[ÁẮẤ]P\s+(?=ĐẤT|ĐÁT|ĐẮT|VẬT|K9)", "ĐẮP "),
    (r"\bCÁP\s+PH[ÓÔỐ]I", "CẤP PHỐI"),
]

_VN_PHRASES = [(re.compile(p), r) for p, r in _VN_PHRASES]
_VN_PRE = [(re.compile(p), r) for p, r in (
    (r"\bĐÁ\s+D[ÀẦÃẢ]M\b", "ĐÁ DĂM"), (r"\bCAP\s+PHOI\b", "CẤP PHỐI"), (r"\bD[ẠẬ]Y\s+(?=\d)", "DÀY "),
    (r"\bNH[ỰỤ]A\s+Đ[ƯỨ][ỚỜ]NG\b", "NHỰA ĐƯỜNG"),
)]
_TOKRX = re.compile(r"[^\W\d_]+", re.UNICODE)
_CAD_HDR = re.compile(r"DWG|DNG\b|TRABTC|THU?[CỤ]\s*HEN|KM[0O]{2,}|CAO\s*BANG|BAC\s*KAN|2028-11", re.I)
_BOXCH = set("¡§¦|┤│├┼─━╌ÏỊỈỈịìĩïĨÌỈ¬ˆ^~=_<>{}[]\\")


def fix_vn(line, counter=None):
    """Sửa dấu từ khóa xây dựng (chỉ chữ IN HOA). Trả dòng đã sửa; counter đếm số lần sửa."""
    def tok(m):
        w = m.group(0)
        if w.isupper() and w in _VN_WORDS and _VN_WORDS[w] != w:
            if counter is not None:
                counter[w + "→" + _VN_WORDS[w]] += 1
            return _VN_WORDS[w]
        return w
    out = line
    for rx, rep in _VN_PRE:
        out = rx.sub(rep, out)
    out = _TOKRX.sub(tok, out)
    for rx, rep in _VN_PHRASES:
        n = rx.sub(rep, out)
        if n != out:
            if counter is not None:
                counter["cụm"] += 1
            out = n
    return out


def _is_cad_hdr(s):
    """Dòng tên file CAD in ở lề/đầu trang (đọc ra rác): chứa DWG/TRABTC/THUC HEN… và gần hết là chữ in hoa/ký hiệu."""
    return len(s) > 25 and (bool(re.search(r"DWG\b", s)) or len(_CAD_HDR.findall(s)) >= 2)


def is_noise_line(s):
    """Dòng rác OCR (nét bản vẽ, chữ dọc, mộc đè…)."""
    s = s.strip()
    if not s:
        return True
    n = len(s)
    alnum = sum(ch.isalnum() for ch in s)
    digit = sum(ch.isdigit() for ch in s)
    if n <= 2 and not digit:
        return True
    if alnum / n < 0.45:
        return True
    if sum(ch in _BOXCH for ch in s) >= 3 and alnum / n < 0.7:
        return True
    toks = s.split()
    if len(toks) >= 3 and not digit and all(len(t) <= 2 for t in toks):
        return True
    return False


def _thkl_md_by_page(con, file_id):
    """{trang: markdown bảng THKL chuẩn} lấy từ bộ dựng quyển bản vẽ."""
    out = {}
    try:
        book = build_book(con, file_id, "gon", book_type="draw")
    except Exception:
        return out
    for pno, md in book["blocks"]:
        if "Bảng tổng hợp khối lượng (tr." in md.split("\n", 1)[0]:
            out[pno] = md
    return out


def _tidy_thkl(md, fixc):
    """Dòng 'chưa tách được' của bảng THKL: bỏ tiêu đề mục không có số, sửa dấu phần còn lại."""
    out = []
    for ln in md.split("\n"):
        if ln.startswith("Dòng có số nhưng chưa tách được"):
            head, _, rest = ln.partition(": ")
            segs = [x.strip() for x in rest.split("‖")]
            segs = [fix_vn(x, fixc) for x in segs if re.search(r"\d+[.,]\d{2}\b|\b\d{3,}\b", x)
                    and not _is_cad_hdr(x)]
            if not segs:
                continue
            ln = head + ": " + " ‖ ".join(segs)
        out.append(ln)
    return "\n".join(out)


def export_raw_book(con, file_id, split_kb=800, include_fixes=True, clean=False):
    """Trả về (rel, [nội dung .md ...], số trang, số dòng, thống kê). clean=False: nguyên văn, KHÔNG lọc."""
    rel, path = con.execute("SELECT rel, path FROM files WHERE id=?", (file_id,)).fetchone()
    fixes = load_fixes(con, file_id) if include_fixes else {}
    pages = _load_pages(con, file_id, fixes)
    ts = time.strftime("%d/%m/%Y %H:%M")
    thkl_md = _thkl_md_by_page(con, file_id) if clean else {}
    fixc = Counter()
    st = {"dropped": 0, "thkl": len(thkl_md), "junk_pages": 0}
    blocks, nl = [], 0
    for pno in sorted(pages):
        pg = pages[pno]
        kind = {"text": "chữ", "ocr": "OCR", "mix": "chữ+OCR", "none": "chưa đọc"}.get(pg["kind"], pg["kind"])
        lab = f" ({pg['label']})" if pg["label"] and str(pg["label"]) != str(pno) else ""
        extra = " · THKL" if pno in thkl_md else ""
        b = [f"## Trang {pno}{lab} · {kind}{extra}", ""]
        raw_rows = [core.clean_display(t) for _ln, t, *_ in pg["lines"]]
        raw_rows = [r for r in raw_rows if r]
        if clean:
            if pno in thkl_md:
                b.append(_tidy_thkl(thkl_md[pno], fixc).replace("### ", "#### ", 1).rstrip())
                keep = []                       # phần còn lại của trang THKL = tiêu đề/khung tên: giữ dòng sạch
                for i, r in enumerate(raw_rows):
                    if is_noise_line(r) or _is_cad_hdr(r):
                        st["dropped"] += 1
                    elif len(r) > 3 and not T.split_line(r)[0]:
                        keep.append(fix_vn(r, fixc))
                    else:
                        st["dropped"] += 1       # dòng bảng: đã có trong bảng chuẩn ở trên
                rows = keep
            else:
                rows = []
                for i, r in enumerate(raw_rows):
                    if is_noise_line(r) or _is_cad_hdr(r):
                        st["dropped"] += 1
                    else:
                        rows.append(fix_vn(r, fixc))
                if raw_rows and len(rows) < 0.4 * len(raw_rows) and len(raw_rows) >= 15:
                    st["junk_pages"] += 1
                    b.append(f"_(trang bản vẽ: chỉ {len(rows)}/{len(raw_rows)} dòng đọc được, phần còn lại là rác OCR — "
                             "xem ảnh trang để lấy số liệu)_")
        else:
            rows = raw_rows
        if not rows and pno not in thkl_md:
            b.append("_(không có chữ)_" if pg["kind"] != "none" else "_(trang chưa được đọc/OCR)_")
        else:
            if rows:
                if pno in thkl_md:
                    b.append("")
                b.extend(rows)
            nl += len(rows)
        b.append("")
        blocks.append((pno, "\n".join(b)))
    head = [f"# {os.path.splitext(os.path.basename(rel))[0]}", "",
            f"> Nguồn: `{rel}` · xuất {ts} · phần mềm Tra cứu hồ sơ {core.APP_VERSION}"]
    if clean:
        head += ["> Bản ĐÃ LÀM SẠCH: bỏ dòng rác OCR, bỏ dòng tên file CAD đầu trang, sửa dấu từ khóa xây dựng "
                 f"({sum(fixc.values())} chỗ), bảng THKL ({len(thkl_md)} bảng) thay bằng bảng chuẩn.",
                 "> Số `~`/`?`/`⟨alt⟩` trong bảng THKL là nghi sai: BẮT BUỘC đối chiếu ảnh trang. Trang bản vẽ: hình học mố/trụ phải xem ảnh."]
    else:
        head += ["> Toàn bộ chữ đọc được, nguyên văn, KHÔNG lọc. Nguồn mỗi trang: chữ (có sẵn trong PDF) / OCR (nhận dạng từ ảnh).",
                 "> OCR có thể sai dấu/số; chỗ nghi ngờ phải đối chiếu ảnh trang."]
    head.append("")
    parts, cur, cur_sz, first, cur_pno = [], [], 0, None, 0
    lim = max(100, split_kb) * 1024
    for pno, txt in blocks:
        sz = len(txt.encode("utf-8"))
        if cur and cur_sz + sz > lim:
            parts.append((first, cur_pno, "\n".join(cur)))
            cur, cur_sz, first = [], 0, None
        if first is None:
            first = pno
        cur.append(txt)
        cur_sz += sz
        cur_pno = pno
    if cur:
        parts.append((first, cur_pno, "\n".join(cur)))
    out = []
    for k, (a, b, body) in enumerate(parts, 1):
        h = list(head)
        if len(parts) > 1:
            h.insert(3, f"> Phần {k}/{len(parts)} · trang {a}–{b}")
        out.append("\n".join(h) + "\n" + body)
    st["fix"] = sum(fixc.values())
    st["fix_top"] = fixc.most_common(8)
    return rel, out, len(pages), nl, st


def export_raw_files(con, file_ids, out_dir, split_kb=800, progress=None, stop=None, clean=False):
    """Ghi toàn bộ OCR mỗi file ra .md (UTF-8 có BOM để Notepad/Excel hiện đúng tiếng Việt)."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for i, fid in enumerate(file_ids):
        if stop and stop():
            break
        rel = con.execute("SELECT rel FROM files WHERE id=?", (fid,)).fetchone()[0]
        if progress:
            progress(i, len(file_ids), rel)
        rel, parts, npg, nl, st = export_raw_book(con, fid, split_kb, clean=clean)
        base = safe_name(rel) + ("_OCR_sach" if clean else "_OCR")
        paths = []
        for k, txt in enumerate(parts, 1):
            fn = f"{base}.md" if len(parts) == 1 else f"{base}_p{k}.md"
            p = os.path.join(out_dir, fn)
            with open(p, "w", encoding="utf-8-sig", newline="\r\n") as f:
                f.write(txt)
            paths.append(p)
        results.append({"rel": rel, "paths": paths, "raw": sum(len(t.encode("utf-8")) for t in parts),
                        "out": sum(len(t.encode("utf-8")) for t in parts), "pages": npg, "lines": nl,
                        "draw": 0, "text": npg, "btype": "rawclean" if clean else "raw", "flag": 0, "unread": 0, "stat": st})
    return results

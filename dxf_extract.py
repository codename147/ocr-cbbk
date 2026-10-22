# -*- coding: utf-8 -*-
"""Trích dữ liệu bản vẽ DXF (AutoCAD) ra Markdown. Chỉ ĐỌC file; dùng ezdxf.

Điểm chính:
 - Giải mã chữ TCVN3 (.VnTime, VHAVAN, vnsimli...) thường gặp trong bản vẽ cũ.
 - Gom nội dung theo "vùng nhìn" (các cụm hình + chữ gần nhau).
 - Bảng thống kê thép từ thuộc tính block + kiểm tra số học (L x SL, khối lượng).
 - Bảng ghép từ chữ rời, ghi chú, cao độ, kích thước, đối chiếu ký hiệu thép với bảng.
 - Có giới hạn số thực thể + báo tiến độ để không treo giao diện với file nặng.
"""
import os
import re
import math
import time
from collections import Counter, defaultdict

# ------------------------------------------------------------------ TCVN3 -> Unicode
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


# byte Latin-1 mà CŨNG là chữ tiếng Việt hợp lệ (à á â ã è é ê ì í ò ó ô õ ù ú ý và hoa) -> không chứng minh được TCVN3
_LATIN1_VI = set(range(0xC0, 0xC4)) | {0xC8, 0xC9, 0xCA, 0xCC, 0xCD, 0xD2, 0xD3, 0xD4, 0xD5, 0xD9, 0xDA, 0xDD} | \
    set(range(0xE0, 0xE4)) | {0xE8, 0xE9, 0xEA, 0xEC, 0xED, 0xF2, 0xF3, 0xF4, 0xF5, 0xF9, 0xFA, 0xFD}
_TCVN_FONT = re.compile(r"(^|[\\/])(\.?vn|vh|vnsim|vnavan|vntime)|\.vn", re.I)


def font_is_tcvn(font):
    return bool(font) and bool(_TCVN_FONT.search(font))


def decode_tcvn3(s, font_tcvn=False):
    """Chữ bị đọc theo cp1252 -> tiếng Việt. Giữ nguyên chuỗi đã là Unicode tiếng Việt (có ký tự > U+00FF)."""
    if not s or any(ch in _VI_UNI and ord(ch) > 0xFF for ch in s):
        return s
    bytes_ = []
    for ch in s:
        try:
            bytes_.append(ch.encode("cp1252")[0])
        except UnicodeEncodeError:
            bytes_.append(None)
    high = [b for b in bytes_ if b is not None and b >= 0xA1]
    if not high:
        return s
    proof = any(b not in _LATIN1_VI for b in high)       # có ký tự chắc chắn của TCVN3
    if not (proof or font_tcvn):
        return s
    out = []
    for ch, b in zip(s, bytes_):
        out.append(_TCVN3.get(b, ch) if (b is not None and b >= 0xA1) else ch)
    return "".join(out)


_CAPS_STYLES = ("vnavanth", "vnavh", "vh")


def clean_text(s, style="", font_tcvn=False):
    s = decode_tcvn3(s or "", font_tcvn)
    s = re.sub(r"%%[uUoO]", "", s)
    s = re.sub(r"%%[cC]|`", "Ø", s)
    s = re.sub(r"%%[pP]", "±", s)
    s = re.sub(r"%%[dD]", "°", s)
    s = s.replace("%%%", "%")
    s = re.sub(r"\\[Pp]", "\n", s)
    s = " ".join(s.split())
    if any(k in (style or "").lower() for k in _CAPS_STYLES):
        s = s.upper()
    return s


# ------------------------------------------------------------------ thu thập
class Item:
    __slots__ = ("kind", "x", "y", "h", "text", "layer", "extra")

    def __init__(self, kind, x, y, h, text, layer, extra=None):
        self.kind, self.x, self.y, self.h, self.text, self.layer, self.extra = kind, x, y, h, text, layer, extra


def _mt(e):
    from ezdxf.tools.text import plain_mtext
    try:
        return plain_mtext(e.text)
    except Exception:
        return e.dxf.get("text", "")


def collect(doc, limit_entities=2_000_000, progress=None):
    """Duyệt model space một lượt: chữ, kích thước, block + thuộc tính, hình học tóm tắt."""
    ms = doc.modelspace()
    tcvn_style = {}
    for st in doc.styles:
        tcvn_style[st.dxf.name.lower()] = font_is_tcvn(st.dxf.get("font", ""))
    items, geo_pts = [], []
    stat = Counter()
    geo = defaultdict(lambda: {"n": 0, "len": 0.0})
    circles = []
    n = 0
    for e in ms:
        n += 1
        if n > limit_entities:
            stat["__truncated__"] = 1
            break
        if progress and n % 20000 == 0:
            progress(n)
        t = e.dxftype()
        stat[t] += 1
        ly = e.dxf.layer
        try:
            if t == "TEXT":
                items.append(Item("T", e.dxf.insert.x, e.dxf.insert.y, e.dxf.height,
                                  clean_text(e.dxf.text, e.dxf.get("style", ""),
                                             tcvn_style.get(e.dxf.get("style", "").lower(), False)), ly))
            elif t == "MTEXT":
                items.append(Item("T", e.dxf.insert.x, e.dxf.insert.y, e.dxf.char_height,
                                  clean_text(_mt(e), e.dxf.get("style", ""),
                                             tcvn_style.get(e.dxf.get("style", "").lower(), False)), ly))
            elif t == "DIMENSION":
                try:
                    m = float(e.get_measurement())
                except Exception:
                    m = None
                if not isinstance(m, float):
                    m = None
                items.append(Item("D", e.dxf.defpoint.x, e.dxf.defpoint.y, 0, clean_text(e.dxf.get("text", "")), ly, m))
            elif t == "INSERT":
                at = {a.dxf.tag: clean_text(a.dxf.text) for a in e.attribs}
                items.append(Item("I", e.dxf.insert.x, e.dxf.insert.y, 0, e.dxf.name, ly, at))
            elif t == "CIRCLE":
                c = e.dxf.center
                circles.append((c.x, c.y, e.dxf.radius, ly))
                geo_pts.append((c.x, c.y))
                g = geo[ly]
                g["n"] += 1
            elif t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                geo_pts.append((a.x, a.y))
                geo_pts.append((b.x, b.y))
                g = geo[ly]
                g["n"] += 1
                g["len"] += math.hypot(b.x - a.x, b.y - a.y)
            elif t in ("LWPOLYLINE",):
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if pts:
                    geo_pts.extend(pts[:: max(1, len(pts) // 8)])
                    g = geo[ly]
                    g["n"] += 1
                    L = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
                    if e.closed and len(pts) > 1:
                        L += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                    g["len"] += L
            elif t in ("ARC", "ELLIPSE", "SPLINE", "HATCH", "LEADER"):
                geo[ly]["n"] += 1
        except Exception:
            stat["__err__"] += 1
    return items, geo_pts, circles, stat, geo


# ------------------------------------------------------------------ gom vùng
def clusters(points, items, cell=2500.0):
    """Gom các điểm (hình + chữ) thành vùng bằng lưới + liên thông 8 hướng. O(n)."""
    cells = {}
    allpts = list(points) + [(i.x, i.y) for i in items if i.kind in ("T", "D", "I")]
    for x, y in allpts:
        cells[(int(x // cell), int(y // cell))] = 0
    parent = {c: c for c in cells}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c
    for (cx, cy) in list(cells):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nb = (cx + dx, cy + dy)
                if nb in parent and nb != (cx, cy):
                    a, b = find((cx, cy)), find(nb)
                    if a != b:
                        parent[a] = b
    return lambda x, y: find((int(x // cell), int(y // cell))) if (int(x // cell), int(y // cell)) in parent else None


# ------------------------------------------------------------------ phân tích
_LEVEL = re.compile(r"^([+\-]?\d+(?:[.,]\d+)?)\s*\(\s*M\s*\)$", re.I)
_BAR = re.compile(r"\b(\d+)\s*Ø\s*(\d+)\b")
_SPACING = re.compile(r"Ø\s*(\d+)\s*[aA]\s*(\d+)")
_UNIT_W = 0.00617   # kg/m cho 1 mm2 -> d^2 * 0.00617


def para_blocks(texts):
    """Gộp các dòng chữ liền nhau (cùng layer, cùng lề trái, cách dòng nhỏ) thành đoạn. O(n log n)."""
    cols = defaultdict(list)
    for t in texts:
        hh = max(t.h, 1.0)
        # xếp theo "cột lề trái": làm tròn x theo 3 lần cao chữ
        cols[(t.layer, round(t.x / (3.0 * hh)))].append(t)
    blocks = []
    for lst in cols.values():
        lst.sort(key=lambda i: -i.y)
        cur = [lst[0]]
        for b in lst[1:]:
            prev = cur[-1]
            hh = max(prev.h, b.h, 1.0)
            if 0 < prev.y - b.y <= 4.2 * hh and abs(b.x - cur[0].x) <= 3.0 * hh:
                cur.append(b)
            else:
                blocks.append(cur)
                cur = [b]
        blocks.append(cur)
    return blocks


def rows_from_text(texts, tol_ratio=0.6):
    """Chữ rời -> hàng (theo y) -> ô (theo x). Trả về list[(y, [(x,text)])]."""
    rows = []
    for t in sorted(texts, key=lambda i: (-i.y, i.x)):
        tol = max(t.h, 100.0) * tol_ratio
        if rows and abs(rows[-1][0] - t.y) <= tol:
            rows[-1][1].append((t.x, t.text))
        else:
            rows.append([t.y, [(t.x, t.text)]])
    return rows


def fmt_num(v, nd=1):
    s = f"{v:,.{nd}f}".replace(",", " ")
    return s.rstrip("0").rstrip(".") if "." in s else s


def analyse_tables(items):
    """Bảng thống kê thép từ thuộc tính block (TK_*). Kiểm tra: LA = L*SL*SCK/1000, KL = LA*d²*0.00617."""
    rows = []
    for it in items:
        if it.kind != "I" or not it.extra:
            continue
        a = it.extra
        if {"SH", "DK", "L", "SL"} <= set(a):
            def f(k):
                try:
                    return float(str(a.get(k, "")).replace(",", "."))
                except ValueError:
                    return None
            rows.append({"x": it.x, "y": it.y, "block": it.text, "SH": a.get("SH"), "DK": f("DK"), "L": f("L"),
                         "SL": f("SL"), "SCK": f("SCK") or 1.0, "LA": f("LA"), "KL": f("KL")})
    rows.sort(key=lambda r: (-r["y"], r["x"]))
    return rows


def md_escape(s):
    return str(s).replace("|", "\\|")


def dxf_to_md(path, progress=None, max_entities=2_000_000):
    import ezdxf
    t0 = time.time()
    doc = ezdxf.readfile(path)
    t_load = time.time() - t0
    t1 = time.time()
    items, pts, circles, stat, geo = collect(doc, max_entities, progress)
    t_col = time.time() - t1
    units = {0: "không rõ", 1: "inch", 2: "feet", 4: "mm", 5: "cm", 6: "m"}.get(doc.header.get("$INSUNITS", 0), "?")
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0}.get(units, 1.0)
    name = os.path.basename(path)
    out = []
    W = out.append
    W(f"# {name}")
    W("")
    W(f"> Nguồn: `{name}` · DXF {doc.acad_release} · đơn vị bản vẽ: **{units}** · "
      f"{sum(v for k, v in stat.items() if not k.startswith('__')):,} thực thể trong Model · đọc {t_load:.1f}s + trích {t_col:.1f}s".replace(",", "."))
    W("> Dữ liệu lấy trực tiếp từ file CAD (không OCR): chữ số là số thật trong bản vẽ. "
      "Phần đánh dấu [suy ra] là do phần mềm tính/đối chiếu, cần kiểm tra.")
    if stat.get("__truncated__"):
        W(f"> ⚠ File quá lớn: chỉ đọc {max_entities:,} thực thể đầu.".replace(",", "."))
    if doc.layout_names() and len(doc.layout_names()) > 1:
        W(f"> Layout (tờ in): {', '.join(n for n in doc.layout_names() if n != 'Model')}")
    W("")

    texts = [i for i in items if i.kind == "T" and i.text]
    dims = [i for i in items if i.kind == "D"]
    inserts = [i for i in items if i.kind == "I"]
    # loại các thực thể quá xa vùng bản vẽ (khung tên xref, rác ở xa)
    xs = sorted(i.x for i in texts + dims)
    ys = sorted(i.y for i in texts + dims)
    if xs:
        mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
        span = 400000.0 * scale
        far = [i for i in items if abs(i.x - mx) > span or abs(i.y - my) > span]
        far_ids = {id(i) for i in far}
        items = [i for i in items if id(i) not in far_ids]
        texts = [i for i in texts if id(i) not in far_ids]
        dims = [i for i in dims if id(i) not in far_ids]
        inserts = [i for i in inserts if id(i) not in far_ids]
        pts = [p for p in pts if abs(p[0] - mx) <= span and abs(p[1] - my) <= span]
        circles = [c for c in circles if abs(c[0] - mx) <= span and abs(c[1] - my) <= span]
        if far:
            W(f"> Bỏ qua {len(far)} đối tượng nằm rất xa vùng bản vẽ (thường là khung tên/xref).")
            W("")

    # ---- tiêu đề, mã bản vẽ
    titles = [t for t in texts if t.layer.lower().endswith("text") and len(t.text) > 3 and t.h >= max(
        (x.h for x in texts), default=0) * 0.9]
    if titles:
        W("## 1. Nhận dạng bản vẽ")
        for t in sorted(titles, key=lambda i: (-i.y, i.x))[:10]:
            W(f"- {md_escape(t.text)}")
        W("")

    # ---- ghi chú (đoạn văn)
    blocks = para_blocks(texts)
    paras = [b for b in blocks if len(b) >= 3 and sum(len(i.text) for i in b) >= 60]
    if paras:
        W("## 2. Ghi chú / đoạn văn trên bản vẽ")
        for b in sorted(paras, key=lambda b: (-(b[0].y), b[0].x)):
            W(f"**Vùng x≈{b[0].x:.0f}, y≈{b[0].y:.0f}**")
            for i in b:
                W(f"- {md_escape(i.text)}")
            W("")

    # ---- bảng thép từ block
    tab = analyse_tables(inserts)
    if tab:
        W("## 3. Bảng thống kê cốt thép (thuộc tính block)")
        W("| Số hiệu | Ø (mm) | Dài 1 thanh (mm) | SL/1 cấu kiện | Số CK | Tổng dài (m) | Khối lượng (kg) | Kiểm tra [suy ra] |")
        W("|---|---|---|---|---|---|---|---|")
        tla = tkl = 0.0
        for r in tab:
            chk = []
            if r["L"] and r["SL"] and r["LA"] is not None:
                calc = r["L"] * r["SL"] * r["SCK"] / 1000.0
                if abs(calc - r["LA"]) > max(0.6, 0.002 * calc):
                    chk.append(f"⚠ tổng dài tính lại {calc:.1f} ≠ {r['LA']}")
            if r["DK"] and r["LA"] is not None and r["KL"] is not None:
                w = r["LA"] * r["DK"] ** 2 * _UNIT_W
                if abs(w - r["KL"]) > max(1.5, 0.01 * w):
                    chk.append(f"⚠ khối lượng tính lại {w:.1f} ≠ {r['KL']}")
            tla += r["LA"] or 0
            tkl += r["KL"] or 0
            W(f"| {r['SH']} | {fmt_num(r['DK'] or 0)} | {fmt_num(r['L'] or 0, 0)} | {fmt_num(r['SL'] or 0, 0)} | "
              f"{fmt_num(r['SCK'], 0)} | {fmt_num(r['LA'] or 0)} | {fmt_num(r['KL'] or 0)} | {'; '.join(chk) or 'khớp'} |")
        W(f"| **Tổng** | | | | | **{fmt_num(tla)}** | **{fmt_num(tkl)}** | [suy ra] cộng các dòng |")
        W("")
        # đối chiếu với ký hiệu thép ghi trên hình
        callouts = Counter()
        spacing = Counter()
        for t in texts:
            for m in _SPACING.finditer(t.text):
                spacing[int(m.group(1))] += 1
            for m in _BAR.finditer(t.text):
                callouts[(int(m.group(1)), int(m.group(2)))] += 1
        if callouts:
            W("**Đối chiếu ký hiệu `nØd` ghi trên hình với bảng** [suy ra]:")
            for r in tab:
                if r["DK"] and r["SL"]:
                    hit = [(n, d, c) for (n, d), c in callouts.items() if d == int(r["DK"]) and (
                        n == int(r["SL"]) or n * c == int(r["SL"]) or n * 2 == int(r["SL"]))]
                    sp = [(d, c) for (d, c) in spacing.items() if d == int(r["DK"])]
                    if not hit and sp:
                        W(f"- {r['SH']} (Ø{fmt_num(r['DK'])}×{fmt_num(r['SL'], 0)}): có ký hiệu bước " +
                          ", ".join(f"Ø{d}a… ×{c} lần" for d, c in sp) + " (thép đai/xoắn, không đếm thanh)")
                    elif hit:
                        W(f"- {r['SH']} (Ø{fmt_num(r['DK'])}×{fmt_num(r['SL'], 0)}): có ký hiệu " +
                          ", ".join(f"{n}Ø{d} ×{c} lần" for n, d, c in hit))
                    else:
                        W(f"- {r['SH']} (Ø{fmt_num(r['DK'])}×{fmt_num(r['SL'], 0)}): ⚠ không thấy ký hiệu tương ứng trên hình")
            W("")

    # ---- vùng nhìn: gom theo cụm hình + chữ
    para_ids = {id(i) for b in paras for i in b}
    simple = [t for t in texts if id(t) not in para_ids]
    cid = clusters(pts, texts + dims + inserts, 1800.0)
    groups = defaultdict(list)
    for t in simple:
        groups[cid(t.x, t.y)].append(t)
    ins_by = defaultdict(list)
    for r in tab:
        ins_by[cid(r["x"], r["y"])].append(r)
    _TITLE = re.compile(r"^(MẶT CẮT|CHI TIẾT|BẢNG|GHI CHÚ|THỐNG KÊ|MẶT BẰNG|MẶT ĐỨNG|BÌNH ĐỒ|TRẮC|SƠ ĐỒ|TỔNG HỢP|KẾT CẤU|CTN|NÚT)", re.I)
    W("## 4. Nội dung theo vùng nhìn (nhãn, ký hiệu, bảng chữ)")
    W("*Mỗi vùng là một cụm hình + chữ nằm gần nhau; tiêu đề vùng lấy từ dòng chữ giống tiêu đề.*")
    W("")
    ordered = sorted(groups.items(), key=lambda kv: (-max(t.y for t in kv[1]) // 7000, min(t.x for t in kv[1])))
    for key, ts in ordered:
        if len(ts) < 2 and not ins_by.get(key):
            continue
        rows = rows_from_text(ts)
        multi = sum(1 for r in rows if len(r[1]) >= 2)
        ttl = next((t.text for t in sorted(ts, key=lambda i: (-i.y, i.x)) if _TITLE.match(t.text)), "")
        x0, x1 = min(t.x for t in ts), max(t.x for t in ts)
        y0, y1 = min(t.y for t in ts), max(t.y for t in ts)
        W(f"**{md_escape(ttl) or 'Vùng không có tiêu đề'}** — x {x0:.0f}…{x1:.0f}, y {y1:.0f}…{y0:.0f}"
          + (" · dạng bảng" if multi >= 3 else "") + (f" · có {len(ins_by[key])} dòng bảng thép (mục 3)" if ins_by.get(key) else ""))
        for y, cells in rows[:80]:
            cells.sort()
            ln = " | ".join(md_escape(c[1]) for c in cells)
            if ln != ttl:
                W(f"- {ln}")
        W("")
    # ---- cao độ
    lv = []
    for t in texts:
        m = _LEVEL.match(t.text.replace(" ", ""))
        if m:
            lv.append((float(m.group(1).replace(",", ".")), t.y, t.x))
    if len(lv) >= 3:
        lv.sort(key=lambda z: -z[0])
        # hồi quy y = a*elev + b
        n = len(lv)
        sx = sum(e for e, _, _ in lv)
        sy = sum(y for _, y, _ in lv)
        sxx = sum(e * e for e, _, _ in lv)
        sxy = sum(e * y for e, y, _ in lv)
        den = n * sxx - sx * sx
        if den:
            a = (n * sxy - sx * sy) / den
            b = (sy - a * sx) / n
            W("## 5. Cao độ ghi trên hình dọc")
            W("| Cao độ (m) | y trên bản vẽ |")
            W("|---|---|")
            for e, y, x in lv:
                W(f"| {e:g} | {y:.0f} |")
            W("")
            W(f"[suy ra] Quan hệ y ≈ {a:.1f}×cao độ + {b:.0f} → hệ số {abs(a):.0f} đơn vị bản vẽ/m "
              f"({'đúng tỷ lệ 1:1 theo mm' if abs(abs(a) - 1000) < 20 else 'KHÔNG phải tỷ lệ 1:1 — kiểm tra tỷ lệ'}). "
              "Có thể dùng để tính chiều dài đoạn thẳng đứng từ toạ độ.")
            W("")

    # ---- kích thước
    if dims:
        W("## 6. Kích thước (DIMENSION)")
        by = Counter()
        over = []
        for d in dims:
            if d.extra is not None:
                by[round(d.extra, 1)] += 1
            if d.text and d.text != "<>":
                over.append(d)
        W("Giá trị đo thật (mm) → số lần xuất hiện: " + ", ".join(
            f"{fmt_num(v)}×{c}" for v, c in sorted(by.items(), key=lambda z: -z[0])[:60]))
        W("")
        if over:
            W("Kích thước bị **ghi đè chữ** (giá trị hiển thị khác giá trị đo — thường là đoạn cắt ngắn, không đúng tỷ lệ):")
            for d in sorted(over, key=lambda i: (-i.y, i.x))[:40]:
                W(f"- đo {fmt_num(d.extra or 0)} nhưng hiển thị `{md_escape(d.text)}` (x≈{d.x:.0f}, y≈{d.y:.0f}) [suy ra: kiểm tra]")
            W("")

    # ---- block + hình học tóm tắt
    W("## 7. Thống kê block và hình học")
    bc = Counter(i.text for i in inserts if not i.text.startswith("*"))
    if bc:
        W("Block (số lần chèn): " + ", ".join(f"{k}×{v}" for k, v in bc.most_common(25)))
        W("")
    rc = Counter((round(c[2] * 2, 1), c[3]) for c in circles)
    if rc:
        W("Vòng tròn theo đường kính (mm) & layer: " + ", ".join(
            f"Ø{fmt_num(d)} [{ly}]×{n}" for (d, ly), n in rc.most_common(20)))
        W("")
    W("| Layer | Số thực thể | Tổng chiều dài đoạn thẳng/polyline (m) |")
    W("|---|---|---|")
    for ly, g in sorted(geo.items(), key=lambda z: -z[1]["n"])[:20]:
        W(f"| {md_escape(ly)} | {g['n']} | {fmt_num(g['len'] * scale / 1000.0, 1)} |")
    W("")
    W("*Tổng chiều dài theo layer là độ dài hình vẽ, KHÔNG phải khối lượng thi công; chỉ dùng để đối chiếu.*")
    return "\n".join(out) + "\n", {"load": t_load, "collect": t_col, "entities": sum(stat.values())}

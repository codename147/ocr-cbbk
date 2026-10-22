# -*- coding: utf-8 -*-
"""OCR lại trang "BẢNG TỔNG HỢP KHỐI LƯỢNG" (THKL) cho chính xác hơn OCR cả trang.

Vì sao: OCR cả trang trộn hai nửa bảng (trái/phải) trên cùng một dòng, đường kẻ ô + dấu mộc làm sai chữ/số.
Cách làm (chỉ ĐỌC file PDF gốc, chỉ ghi vào chỉ mục):
  1. Vẽ trang, bỏ mực đỏ/xanh của dấu mộc (lấy kênh sáng nhất của RGB).
  2. Nắn thẳng trang scan bị nghiêng.
  3. Xoá đường kẻ ngang/dọc (đoạn thẳng dài) để chữ không dính khung.
  4. Tách đôi khối trái/phải theo dải trắng giữa hai khối; OCR từng khối (psm 6).
  5. OCR lần 2 CHỈ cột số (chỉ cho phép chữ số, dấu chấm, phẩy) -> số đọc chuẩn hơn; thay vào dòng tương ứng.
Mỗi dòng bảng thành 1 dòng "tên đơn_vị giá_trị"; toạ độ x giữ đúng vị trí để thkl.py phân cột trái/phải.
"""
import re

import core
import thkl as T

fitz = core.fitz
Z = 3.0
_WL = ["tessedit_char_whitelist=0123456789.,"]
_NUMTOK = re.compile(r"\d[\d.,]*")


def _np():
    try:
        import numpy as np
        return np
    except ImportError:
        raise RuntimeError("Thiếu thư viện numpy. Hãy chạy lại CAI_DAT.bat (đã thêm numpy vào requirements.txt).")


def _shear_y(a, t, fill, np):
    H, W = a.shape
    sh = np.round((np.arange(W) - W / 2.0) * t).astype(np.int32)
    rows = np.arange(H, dtype=np.int32)[:, None] + sh[None, :]
    ok = (rows >= 0) & (rows < H)
    rows = np.clip(rows, 0, H - 1)
    out = a[rows, np.arange(W)[None, :]]
    out[~ok] = fill
    return out


def deskew(g, np):
    """Nắn trang scan nghiêng (±1.7°). Trả (ảnh, độ nghiêng)."""
    small = (g[::4, ::4] < 185).astype(np.float32)

    def score(t):
        s = _shear_y(small, t, 0, np).sum(axis=1)
        return float((s * s).sum())
    best = max(np.linspace(-0.03, 0.03, 25), key=score)
    best = max(np.linspace(best - 0.003, best + 0.003, 13), key=score)
    if abs(best) < 0.0006:
        return g, 0.0
    out = _shear_y(g, best, 255, np)
    out = _shear_y(np.ascontiguousarray(out.T), -best, 255, np).T
    return np.ascontiguousarray(out), float(best)


def _runmask(dk, L, axis, np):
    a = dk if axis == 1 else dk.T
    cs = np.zeros((a.shape[0], a.shape[1] + 1), np.int32)
    np.cumsum(a, axis=1, dtype=np.int32, out=cs[:, 1:])
    win = cs[:, L:] - cs[:, :-L]
    r, c = np.nonzero(win >= L - 8)
    mark = np.zeros((a.shape[0], a.shape[1] + 1), np.int32)
    np.add.at(mark, (r, c), 1)
    np.add.at(mark, (r, np.minimum(c + L, a.shape[1])), -1)
    m = np.cumsum(mark, axis=1)[:, :a.shape[1]] > 0
    return m if axis == 1 else m.T


def render_clean(page, np, zoom=Z):
    """Ảnh xám đã bỏ mực màu, nắn thẳng, xoá đường kẻ. Trả (ảnh, độ nghiêng)."""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    g = a.max(axis=2)                      # mực đỏ/xanh (dấu mộc) -> sáng, chữ đen vẫn tối
    g, t = deskew(g, np)
    dk = g < 185
    ln = _runmask(dk, 70, 1, np) | _runmask(dk, 70, 0, np)
    k = ln.copy()
    for s in (1, 2, 3):
        k[:, s:] |= ln[:, :-s]
        k[:, :-s] |= ln[:, s:]
        k[s:, :] |= ln[:-s, :]
        k[:-s, :] |= ln[s:, :]
    g = g.copy()
    g[k] = 255
    return g, t


def _pgm(arr):
    h, w = arr.shape
    return b"P5\n%d %d\n255\n" % (w, h) + arr.tobytes()


def find_split(g, np):
    """Vị trí x giữa hai khối bảng (dải trắng rộng nhất quanh giữa trang). Bỏ qua vết đường kẻ dọc mảnh còn sót."""
    from numpy.lib.stride_tricks import sliding_window_view as swv
    H, W = g.shape
    prof = (g[int(H * .10):int(H * .80)] < 150).sum(axis=0).astype(np.int32)
    w = 21                                                    # mở (erode rồi dilate) -> bỏ gai hẹp < 21 điểm ảnh
    pad = np.pad(prof, w // 2, mode="edge")
    er = swv(pad, w).min(axis=1)
    pad = np.pad(er, w // 2, mode="edge")
    op = swv(pad, w).max(axis=1)
    lo, hi = int(W * .36), int(W * .64)
    seg = op[lo:hi]
    thr = int(seg.min()) + 3
    best, i = (0, 0, 0), 0
    while i < len(seg):
        if seg[i] <= thr:
            j = i
            while j < len(seg) and seg[j] <= thr:
                j += 1
            if j - i > best[0]:
                best = (j - i, i, j)
            i = j
        else:
            i += 1
    return lo + (best[1] + best[2]) // 2


def _ymatch(a, b):
    """a, b: (y0,y1). Tỉ lệ chồng lấn theo chiều cao nhỏ hơn."""
    ov = min(a[1], b[1]) - max(a[0], b[0])
    return ov / max(1.0, min(a[1] - a[0], b[1] - b[0]))


def _replace_val(text, new):
    m = T._ITEM.search(text)
    if not m:
        return text
    return text[:m.start("val")] + new + text[m.end("val"):]


def _num_from(text):
    toks = _NUMTOK.findall(text)
    for t in reversed(toks):
        t = t.replace(",", ".").strip(".")
        if re.fullmatch(r"\d+(\.\d{1,3})?", t) and len(t) <= 9:
            return t
    return None


def _upscale(c, k, np):
    """Phóng nguyên số k lần (lặp điểm ảnh rồi làm mịn nhẹ) — không cần Pillow."""
    big = np.repeat(np.repeat(c, k, axis=0), k, axis=1).astype(np.float32)
    # làm mịn 3 điểm theo 2 chiều để nét chữ liền (gần giống nội suy)
    for ax in (0, 1):
        p = np.pad(big, [(1, 1) if i == ax else (0, 0) for i in range(2)], mode="edge")
        sl = [slice(None), slice(None)]
        big = (np.take(p, range(0, big.shape[ax]), axis=ax) + 2 * np.take(p, range(1, big.shape[ax] + 1), axis=ax)
               + np.take(p, range(2, big.shape[ax] + 2), axis=ax)) / 4.0
    return np.clip(big, 0, 255).astype(np.uint8)


_PIPE = re.compile(r"(?:(?<=\s)|^)[|¦ỊỈ¡!\[\]]+(?=\s|$)")


def _unpipe(t):
    """Bỏ các ký tự '|' lẻ do vết đường kẻ dọc còn sót."""
    t = _PIPE.sub(" ", t)
    t = re.sub(r"[|¦]+\s*$", "", t)
    return " ".join(t.split())


def _digits(v):
    return re.sub(r"\D", "", v or "")


def _stack_read(exe, crop, rows_idx, rows, xl, xr, K, lang, np, yb=None):
    """Cắt riêng cột số của từng dòng, phóng K lần, xếp chồng thành 1 ảnh, OCR chỉ chữ số. -> {chỉ_số_dòng: số}"""
    pieces, pos, y = [], [], 0
    for i in rows_idx:
        r = rows[i]
        if yb and i in yb:
            y0, y1 = max(0, int(yb[i][0]) - 2), min(crop.shape[0], int(yb[i][1]) + 2)
        else:
            y0, y1 = max(0, int(r[2]) - 4), min(crop.shape[0], int(r[4]) + 4)
        c = _upscale(crop[y0:y1, xl:xr], K, np)
        c = np.pad(c, ((30, 30), (30, 30)), constant_values=255)
        pieces.append(c)
        pos.append((i, y + 30, y + c.shape[0] - 30))
        y += c.shape[0]
    stack = np.ascontiguousarray(np.concatenate(pieces, axis=0))
    res2 = [q for q in core.run_ocr(exe, _pgm(stack), lang, 6, Z * 72, extra=_WL) if re.search(r"\d", q[0])]
    out = {}
    byorder = len(res2) == len(pos)
    for k, (i, ya, yb) in enumerate(pos):
        if byorder:
            best = res2[k]
        else:
            best, bs = None, 0.0
            for q in res2:
                sc = _ymatch((ya, yb), (q[2], q[4]))
                if sc > bs:
                    best, bs = q, sc
            if bs < 0.7:
                best = None
        if best is not None:
            v = _num_from(best[0])
            if v:
                out[i] = v
    return out


def ocr_block(exe, crop, lang, np):
    """OCR 1 khối bảng. Trả danh sách (text, x0, y0, x1, y1) theo pixel của crop."""
    res, words = core.run_ocr(exe, _pgm(crop), lang, 6, Z * 72, both=True)
    rows = [(_unpipe(r[0]),) + tuple(r[1:]) for r in res if _unpipe(r[0])]
    items = [i for i, r in enumerate(rows) if T.split_line(r[0])[0]]
    if len(items) < 5:
        return rows
    # biên cột số: lấy từ vị trí các từ số cuối dòng của lần đọc 1
    xs0, xs1, yb = [], [], {}
    for i in items:
        r = rows[i]
        cand = [w for w in words if r[2] - 3 <= w[2] and w[4] <= r[4] + 3 and re.fullmatch(r"\d[\d.,]*", w[0])]
        if cand:
            w = max(cand, key=lambda q: q[1])
            xs0.append(w[1])
            xs1.append(w[3])
            yb[i] = (w[2], w[4])
    if len(xs0) < 5:
        return rows
    xs0.sort()
    xs1.sort()
    xl = max(0, int(xs0[0]) - 16)
    xr = min(crop.shape[1], int(xs1[-1]) + 16)
    if xr - xl < 60:
        return rows
    ra = _stack_read(exe, crop, items, rows, xl, xr, 3, lang, np, yb)
    rb = _stack_read(exe, crop, items, rows, xl, xr, 4, lang, np, yb)
    out = list(rows)
    for i in items:
        r = rows[i]
        m = T._ITEM.search(r[0])
        old = m.group("val").replace(",", ".") if m else ""
        a, b = ra.get(i), rb.get(i)
        alt = None
        if a and b:
            if a == b:
                pick = a
            elif _digits(old) and _digits(old) in (_digits(a), _digits(b)):
                pick = a if _digits(a) == _digits(old) else b
            else:
                pick, alt = a, b
        else:
            pick = a or b
        if pick and re.fullmatch(r"\d+\.\d{2}", old) and _digits(pick) != _digits(old):
            alt = alt or old                    # lần 1 và lần 2 đọc khác nhau -> ghi lại để cảnh báo
        if pick:
            txt = _replace_val(r[0], pick)
            if alt and alt != pick:
                txt += f" ⟨alt={alt}⟩"
            out[i] = (txt, r[1], r[2], r[3], r[4])
    return out


def thkl_pages(con, file_id):
    """Danh sách trang nhiều khả năng là THKL (theo chữ đã có trong chỉ mục)."""
    pages = []
    cur = {}
    for page, text in con.execute("SELECT page, text FROM lines WHERE file_id=? ORDER BY page, line", (file_id,)):
        cur.setdefault(page, []).append(text)
    for p, ls in cur.items():
        ok, n = T.is_thkl_page(ls)
        c = T._compact(" ".join(ls))
        if ok or n >= 10 or ("tonghopkhoiluongtoancau" in c and n >= 4):
            pages.append(p)
    return sorted(pages)


def reocr_page(doc, pno, old_lines, exe, lang, np):
    """-> (dòng mới cho trang, thông tin). old_lines: [(text,x0,y0,x1,y1)] theo pt (từ chỉ mục cũ)."""
    page = doc[pno - 1]
    w, h = page.rect.width, page.rect.height
    g, skew = render_clean(page, np)
    H, W = g.shape
    ytop = int(0.075 * H)
    ybot = int(0.855 * H)
    foot = [l[2] for l in old_lines if re.search(r"uybannhandan|duanthanhphan|liendanhgoithau", T._compact(l[0]))
            and l[2] > 0.6 * h]
    if foot:
        ybot = int(min(foot) * Z) - 6
        ybot = max(int(0.6 * H), min(ybot, int(0.9 * H)))
    xs = find_split(g, np)
    out_rows, n_blocks = [], []
    for x0, x1 in ((int(0.12 * W), xs), (xs, W)):   # bỏ lề trái (chữ dọc đường dẫn file CAD)
        crop = np.ascontiguousarray(g[ytop:ybot, x0:x1])
        rows = ocr_block(exe, crop, lang, np)
        for text, a, b, c, d in rows:
            out_rows.append((core.nfc(text), (x0 + a) / Z, (ytop + b) / Z, (x0 + c) / Z, (ytop + d) / Z))
        n_blocks.append(len(rows))
    keep_top = [l for l in old_lines if l[4] <= ytop / Z]
    keep_bot = [l for l in old_lines if l[2] >= ybot / Z]
    return keep_top + out_rows + keep_bot, {"skew": skew, "rows": n_blocks, "w": w, "h": h}


def reocr_file(con, file_id, path, cfg, exe, stop=None, progress=None, log=None, only_pages=None):
    """OCR lại các trang THKL của 1 file PDF. Chỉ đọc PDF; ghi vào chỉ mục. Trả (số trang xử lý, [trang])."""
    np = _np()
    pages = only_pages or thkl_pages(con, file_id)
    if not pages:
        return 0, []
    lang = "vie" if core.tessdata_dir_for("vie") else core.effective_lang(cfg)
    doc = fitz.open(path)
    done = []
    try:
        old = {}
        for pno in pages:
            old[pno] = list(con.execute("SELECT text,x0,y0,x1,y1 FROM lines WHERE file_id=? AND page=? ORDER BY line",
                                        (file_id, pno)))
        # PyMuPDF không an toàn đa luồng nên xử lý tuần tự từng trang (~20 giây/trang)
        for i, pno in enumerate(pages):
            if stop and stop():
                break
            if progress:
                progress(i, len(pages), pno)
            try:
                lines, info = reocr_page(doc, pno, old[pno], exe, lang, np)
                w, h = info["w"], info["h"]
                core.write_page(con, file_id, pno, "ocr", lines, w, h)
                con.commit()
                done.append(pno)
                if log:
                    log(f"Trang {pno}: nắn nghiêng {info['skew'] * 57.3:+.2f}°, {info['rows'][0]}+{info['rows'][1]} dòng")
            except Exception as e:
                if log:
                    log(f"Trang {pno}: lỗi {e}")
        core.refresh_file_counts(con, file_id)
        con.commit()
    finally:
        doc.close()
    return len(done), done

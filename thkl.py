# -*- coding: utf-8 -*-
"""
Nhận dạng và tách BẢNG TỔNG HỢP KHỐI LƯỢNG (THKL) trong quyển bản vẽ cầu từ chữ OCR đã có trong chỉ mục.

Bảng THKL toàn cầu in 2 khối cạnh nhau (khối phải là phần tiếp theo của khối trái); OCR đọc mỗi dòng gồm cả
hai khối. Module này:
  - nhận trang THKL (tiêu đề/đầu bảng + nhiều cặp "đơn vị + số"),
  - tách từng dòng thành các mục (hạng mục, đơn vị, khối lượng), gán về khối trái/phải, theo dõi mục lớn,
  - sửa lỗi OCR có quy luật (mất dấu thập phân: THKL luôn ghi 2 số lẻ) và ĐÁNH DẤU ⚠ chỗ chưa chắc,
  - kiểm tra cộng: cốt thép tổng = D≤10 + 10<D≤18 + D>18,
  - liệt kê các dòng có số mà không tách được (không để mất số liệu âm thầm).
KHÔNG tự bịa số: chỗ không đọc được ghi rõ là "không đọc được".
"""
import re
from collections import Counter

from core import fold

# ---------------------------------------------------------------- đơn vị
# (tên, regex) — OCR hay đọc sai dấu/chữ nên regex khá rộng. Đơn vị "m" đơn lẻ chỉ nhận khi giá trị là SỐ.
_UNIT_PATTERNS = [
    ("m3", r"M\s?[3³]|M[ãÃ]|MS|M\s3"),
    ("m2", r"M\s?[2²]|M[ỊIlêÊ]"),
    ("tấn", r"T[ẤÁÂẨÀAẦÃ]N|TẤN"),
    ("cái", r"C[ÁẢAÃẠ]I|CẢI|CÁL|CẢL|CAI"),
    ("bộ", r"B[ỘỌO]"),
    ("cọc", r"C[ỌOQ]C"),
    ("md", r"MD|M\.D"),
    ("kg", r"KG"),
    ("dầm", r"D[ẦÀAÂ]M|ĐẦM|Đ[ÀA]M"),
    ("mặt cắt", r"M[ẶẠAÀ]T\s*C[ẮẤÁAÃ]T"),
    ("m", r"M"),
]
_UNIT_RX = "|".join(f"(?P<u{i}>{p})" for i, (_, p) in enumerate(_UNIT_PATTERNS))
_NUMVAL = r"[0-9][0-9.,]*|[0Oo][.,]?\d+"
_GARB = r"[A-Za-zÀ-ỹ]{1,2}\.?|[~_—-]{1,3}"
_ITEM = re.compile(
    r"(?P<name>.*?)(?<![A-Za-zÀ-ỹ0-9])(?P<unit>" + _UNIT_RX + r")\.?\s+(?P<val>" + _NUMVAL + r"|" + _GARB + r")"
    r"(?=\s|$|[|)\]'\"])", re.UNICODE)

_JUNK = r"\s\-_~—¡|.\[\]()':;,!`“”’‘*<>=+"
_BULLET = re.compile(r"^[" + _JUNK + r"]+")
_TRAIL = re.compile(r"[" + _JUNK + r"]+$")

# ---------------------------------------------------------------- mục lớn (nhận bằng chữ không dấu, bỏ khoảng trắng)
# (khoá, tên hiển thị). Khoá dài hơn thắng.
_SECTIONS = [
    ("ketcauphantren", "I. Kết cấu phần trên"),
    ("ketcauphanduoi", "II. Kết cấu phần dưới"),
    ("ketcaudam", "I.1 Kết cấu dầm"),
    ("goicau", "I.2 Gối cầu"),
    ("goichau", "I.2.1 Gối chậu"),
    ("goicaosu", "I.2.2 Gối cao su"),
    ("banmatcau", "I.3 Bản mặt cầu"),
    ("tamlotvankhuon", "I.4 Tấm lót ván khuôn bản mặt cầu"),
    ("damngang", "I.5 Dầm ngang"),
    ("neodamngang", "I.6 Neo dầm ngang"),
    ("lopphumatcau", "I.7 Lớp phủ mặt cầu"),
    ("khecogian", "I.8 Khe co giãn"),
    ("golancan", "I.9 Gờ lan can"),
    ("thoatnuocmatcau", "I.10 Thoát nước mặt cầu"),
    ("mocau", "II.1 Mố cầu"),
    ("trucau", "II.2 Trụ cầu"),
    ("trutrencan", "II.2.1 Trụ trên cạn"),
    ("banquado", "II.3 Bản quá độ"),
    ("giacodaumo", "III Gia cố đầu mố"),
    ("thinghiemthucoc", "IV Thí nghiệm thử cọc"),
    ("photrothicong", "V Phụ trợ thi công"),
    ("tuongchan", "VI Tường chắn"),
]
_HEAD_TAIL = ("lientucnhiet", "")  # phần đuôi của tiêu đề (bỏ khi cắt tiêu đề dính vào tên mục)

# tên mục chuẩn (khoá: chuỗi không dấu, không khoảng trắng; chọn khoá DÀI nhất khớp) -> tên đẹp
_NAMES = [
    ("dambtctdul", "Dầm BTCT DƯL I"),
    ("damsupert", "Dầm Super T"),
    ("goididong", "Gối di động"),
    ("tambandemgoicau", "Tấm bản đệm gối cầu"),
    ("tambandem", "Tấm bản đệm gối cầu"),
    ("goicaosu", "Gối cao su"),
    ("lopphongnuoc", "Lớp phòng nước"),
    ("khecogiandangrangluoc", "Khe co giãn dạng răng lược (100mm)"),
    ("betongkhongconguot", "Bê tông không co ngót cốt liệu nhỏ 40MPa"),
    ("betongkhongcongot", "Bê tông không co ngót cốt liệu nhỏ 40MPa"),
    ("betongnhuarong", "Bê tông nhựa rỗng 12.5 dày 4cm"),
    ("betongnhuachat", "Bê tông nhựa chặt 16 dày 4cm"),
    ("tuoinhuadinhbam", "Tưới nhựa dính bám 0.5kg/m2"),
    ("ongthoatnuocxoanoc", "Ống thoát nước xoắn ốc"),
    ("ongthoatnuocd150", "Ống thoát nước D150 (gang xám đúc)"),
    ("napchanrac", "Nắp chắn rác"),
    ("ongupvc", "Ống uPVC D150"),
    ("thanhdinhvi", "Thanh định vị"),
    ("ongnoimem", "Ống nối mềm"),
    ("luoithepmakem", "Lưới thép mạ kẽm"),
    ("dauthunuoc", "Đầu thu nước"),
    ("ongpvcd125", "Ống PVC D125 trong gờ lan can"),
    ("vankhuongochan", "Ván khuôn gờ chắn"),
    ("tamdemdanhoi10mm", "Tấm đệm đàn hồi 10mm (bản liên tục nhiệt)"),
    ("tamdemdanhoi20mm", "Tấm đệm đàn hồi 20mm"),
    ("bitumchenkhe", "Bitum chèn khe"),
    ("bitumnhua", "Bitum nhựa"),
    ("thanhneomakem", "Thanh neo mạ kẽm D32"),
    ("ongthep", "Ống thép"),
    ("betongdemc10", "Bê tông đệm C10"),
    ("dadamdem", "Đá dăm đệm"),
    ("dahocxay", "Đá hộc xây vữa XM M100"),
    ("dapdattunon", "Đắp đất tứ nón K95"),
    ("betongchankhay", "Bê tông chân khay C20"),
    ("daodatchankhay", "Đào đất chân khay"),
    ("dapdatchankhay", "Đắp đất chân khay"),
    ("dapvatlieudanghat", "Đắp vật liệu dạng hạt (sau mố cầu) K98"),
    ("vankhuontam", "Ván khuôn tấm BTXM"),
    ("ketcaumatduong", "Kết cấu mặt đường trên mố + 10m sau mố"),
    ("khoanmun", "Khoan mùn kiểm tra cọc"),
]

_DAYS = re.compile(r"(?:^|[\s|])(?:theo\s*van\s*ban|ngay\.|chu\s*tri|ky\s*ten|tham\s*tra|truong\s*dai\s*hoc|"
                   r"dai\s*hoc\s*giao|cong\s*ty\s*tnhh)", re.I)


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", fold(s).lower())


# ---------------------------------------------------------------- giá trị
_COUNT_UNITS = {"cái", "bộ", "cọc", "dầm", "mặt cắt"}


def norm_value(val, unit):
    """-> (số_chuỗi | None, ghi_chú). THKL luôn ghi 2 số lẻ cho m, m2, m3, tấn, md; số nguyên cho cái/cọc/dầm."""
    v = (val or "").strip().strip("'\"")
    if not v or not re.search(r"\d", v):
        return None, "không đọc được"
    if re.fullmatch(r"[0Oo][.,]?\d+", v):
        v = v.replace("O", "0").replace("o", "0")
    v = v.replace(",", ".")
    if v.endswith(".") and unit not in _COUNT_UNITS and "." not in v.rstrip("."):
        return v.rstrip(".") + ".??", "bị cụt (thiếu số lẻ)"
    v = v.rstrip(".")
    note = ""
    if v.count(".") > 1:
        parts = v.split(".")
        v = "".join(parts[:-1]) + "." + parts[-1]
        note = "dấu thập phân lạ"
    if unit in _COUNT_UNITS:
        if re.fullmatch(r"\d{2,}00", v) and "." not in v:
            return str(int(v[:-2])), "đã bỏ .00"
        if re.fullmatch(r"\d+(\.0+)?", v):
            return str(int(float(v))), note
        if re.fullmatch(r"\d+\.\d+", v):
            return v, note
        return v, "nghi sai"
    if "." in v:
        a, b = v.split(".")
        if len(b) == 2:
            return v, note
        if len(b) == 1:
            return v + "0", note or "chỉ đọc được 1 số lẻ"
        if len(b) > 2:
            return v, "nhiều hơn 2 số lẻ (nghi sai)"
        return v + "00", note or "thiếu số lẻ"
    if re.fullmatch(r"0+", v):
        return "0.00", ""
    if len(v) >= 4 or (len(v) == 3 and unit in ("tấn", "m2", "m3")):
        return v[:-2] + "." + v[-2:], "mất dấu thập phân (đã tự chèn)"
    return v, "không có số lẻ (nghi mất .00 hoặc dấu chấm)"


def _unit_name(m):
    for i, (name, _) in enumerate(_UNIT_PATTERNS):
        if m.group(f"u{i}") is not None:
            return name
    return ""


def _clean(s):
    s = _BULLET.sub("", s)
    s = _TRAIL.sub("", s)
    return " ".join(s.split())


def _strip_heading(name):
    """Cắt tiêu đề mục dính ở đầu tên (vd 'GỐI CHẬU BÊ TÔNG ĐỆM C10'). -> (tên_còn_lại, mục | None)."""
    toks = name.split()
    for key, title in sorted(_SECTIONS, key=lambda s: -len(s[0])):
        if key in ("khecogian", "goicaosu", "goicau"):   # trùng với tên mục thật ("Khe co giãn dạng răng lược…")
            continue
        acc = ""
        for i, t in enumerate(toks):
            acc += _compact(t)
            if acc == key and i + 1 < len(toks):
                rest = toks[i + 1:]
                # bỏ đuôi tiêu đề kiểu "LIÊN TỤC NHIỆT"
                while rest and _compact(rest[0]) in ("lien", "tuc", "nhiet") and len(rest) > 1:
                    rest = rest[1:]
                return " ".join(rest), title
            if len(acc) > len(key):
                break
    return name, None


def _find_section(text):
    k = _compact(text)
    if len(k) < 5:
        return None
    best = None
    for key, title in _SECTIONS:
        if key in k and (best is None or len(key) > len(best[0])):
            best = (key, title)
    return best[1] if best else None


# ---------------------------------------------------------------- tên chuẩn
_STEEL = re.compile(r"cot\s*thep|cotthep", re.I)


def canonical_name(name, unit):
    """-> (tên đẹp, khoá). Sửa chữ OCR cho các mục chuẩn; không nhận ra thì giữ nguyên chữ OCR."""
    f = " ".join(fold(name).lower().split())
    k = _compact(name)
    # --- dầm
    if "super" in f or "supert" in k:
        if re.search(r"khong\s*c[ao]t\s*kh", f):
            return "Dầm Super T L=38.2m (không cắt khấc)", "dam_st_0"
        if re.search(r"c[ao]t\s*kh\w*\s*2", f):
            return "Dầm Super T L=38.2m (cắt khấc 2 đầu)", "dam_st_2"
        if re.search(r"c[ao]t\s*kh\w*\s*1", f):
            return "Dầm Super T L=38.2m (cắt khấc 1 đầu)", "dam_st_1"
        return "Dầm Super T L=38.2m", "dam_st"
    if "btct" in k and ("dul" in k or "dam" in f):
        return "Dầm BTCT DƯL I L=33m", "dam_i33"
    # --- gối
    if "goi" in f and ("di dong" in f or "didong" in k or "dong" in f) and unit == "cái":
        if "da huong" in f or "dahuong" in k:
            return "Gối di động đa hướng 200T (gối chậu)", "goi_da"
        if "don huong" in f or "donhuong" in k:
            return "Gối di động đơn hướng 200T (gối chậu)", "goi_don"
        return "Gối di động 200T (gối chậu)", "goi_dd"
    if "goi cao su" in f or "goicaosu" in k or ("cao su" in f and unit == "cái") or ("goi cao" in f and unit == "cái"):
        return "Gối cao su (400x300x78 / 400x300x57)", "goi_cs"
    # --- cốt thép
    if ("thep" in f or "tthep" in k or "cothep" in k) and unit == "tấn" and "luoi" not in f and "ong thep" not in f \
            and "tam" not in f:
        if re.search(r"d\s*[<≤]\s*=?\s*10|d\s*<\s*10", f) and not re.search(r"10\s*<", f):
            return "Cốt thép D≤10", "ct_d10"
        if re.search(r"10\s*[<≤]\s*=?\s*d|10\s*<\s*d", f):
            return "Cốt thép 10<D≤18", "ct_1018"
        if re.search(r"d\s*[>≥]\s*=?\s*18|d>1|d\s*>\s*18", f):
            return "Cốt thép D>18", "ct_d18"
        return "Cốt thép tròn các loại", "ct"
    if "loai" in f and unit == "tấn":
        return "Cốt thép tròn các loại", "ct"
    # --- cọc khoan nhồi, thử cọc
    if unit == "m" and ("coc" in f or "cqc" in f or "khoan" in f):
        m = re.search(r"d\s*(1000|1200|1500)", f)
        return (f"Cọc khoan nhồi D{m.group(1)}" if m else "Cọc khoan nhồi"), "ckn" + (m.group(1) if m else "")
    if "sieu" in f or "siêu" in name.lower():
        m = re.search(r"d\s*(1000|1200|1500)", f)
        return (f"Siêu âm cọc khoan nhồi D{m.group(1)}" if m else "Siêu âm cọc khoan nhồi"), "sieuam" + (m.group(1) if m else "")
    if "pda" in f or "thu dong" in f:
        m = re.search(r"d\s*(1000|1200|1500)", f)
        return (f"Thử động PDA cọc khoan nhồi D{m.group(1)}" if m else "Thử động PDA cọc khoan nhồi"), "pda" + (m.group(1) if m else "")
    # --- bê tông kết cấu
    if unit == "m3" and ("tong" in f or "tông" in name.lower()) and "nhua" not in f and "khong co" not in f \
            and "chan khay" not in f:
        mb = re.search(r"c\s?(10|20|25|30|35|40)\b", f)
        grade = f"C{mb.group(1)}" if mb else ""
        if "mu tru" in f:
            return f"Bê tông {grade} mũ trụ", "bt_mutru"
        if "tru" in f and ("mong" in f or "than" in f):
            return f"Bê tông {grade} móng, thân trụ", "bt_tru"
        if "mo" in f and ("mong" in f or "than" in f):
            return f"Bê tông {grade} móng, thân mố", "bt_mo"
        if "dem" in f:
            return f"Bê tông đệm {grade}", "bt_dem" + grade.lower()
        if "dam" in f:
            return f"Bê tông dầm {grade}", "bt_dam"
        return f"Bê tông {grade}".strip(), "bt" + grade.lower()
    for key, nice in _NAMES:
        if key in k:
            return nice, key
    if "van khuon" in f or "khuon" in f:
        if "ban mat cau" in f or "banmatcau" in k:
            return "Ván khuôn bản mặt cầu", "vk_bmc"
        if "go chan" in f or "gochan" in k:
            return "Ván khuôn gờ chắn", "vk_go"
        return "Ván khuôn", "vk"
    if "dao dat" in f and "khay" not in f:
        return "Đào đất", "dao"
    if "dap dat" in f and "k95" in f and "non" not in f and "khay" not in f:
        return "Đắp đất K95", "dapk95"
    return name, k


# ---------------------------------------------------------------- tách một dòng
_ALT = re.compile(r"\s*⟨alt=([0-9.]+)⟩")


def split_line(text):
    """-> (items, frags). items: [{name, unit, raw, alt?}]; frags: [(vị_trí 'pre'|'post', chữ, chỉ_số_mục)]."""
    items, frags = [], []
    am = _ALT.search(text)
    alt = am.group(1) if am else None
    text = _ALT.sub("", text)
    pos, t = 0, text
    last_end = 0
    while pos < len(t):
        m = _ITEM.search(t, pos)
        if not m:
            break
        unit = _unit_name(m)
        raw = m.group("val")
        # đơn vị "m" trơn chỉ hợp lệ khi giá trị là số
        if unit == "m" and not re.match(r"\d", raw):
            pos = m.end("unit")
            continue
        # rác ngắn làm giá trị: chỉ nhận khi đứng cuối dòng hoặc trước dấu '|'
        if not re.match(r"[\dOo~_—-]", raw):
            rest = t[m.end():].strip()
            if rest and not rest.startswith("|") and len(rest) > 6 and unit != "tấn":
                # giá trị rác nhưng phía sau còn chữ: coi như chữ thường, bỏ qua đơn vị này
                pos = m.end("unit")
                continue
        raw_name = m.group("name")
        cells = raw_name.split("|")
        name = _clean(cells[-1])
        for c in cells[:-1]:
            c2 = _clean(c)
            if c2:
                frags.append(("pre", c2, len(items)))
        name2, sec = _strip_heading(name)
        if sec:
            frags.append(("pre", name[:len(name) - len(name2)].strip(), len(items)))
            name = name2
        items.append({"name": name, "unit": unit, "raw": raw})
        pos = last_end = m.end()
    tail = _clean(t[last_end:]) if last_end < len(t) else ""
    if tail:
        frags.append(("post", tail, len(items)))
    if alt and items:
        items[-1]["alt"] = alt
    return items, frags


# ---------------------------------------------------------------- nhận trang THKL
def thkl_score(lines):
    """-> số mục tách được trên trang (để đoán trang THKL)."""
    n = 0
    for t in lines:
        its, _ = split_line(t)
        n += sum(1 for i in its if i["unit"] and (re.match(r"\d", i["raw"]) or i["raw"][:1] in "0Oo"))
    return n


def is_thkl_page(lines):
    allc = _compact(" ".join(lines))
    has_title = "tonghopkhoiluong" in allc
    has_hdr = "hangmuccongviec" in allc and "donvi" in allc
    n = thkl_score(lines)
    return (n >= 12 and (has_title or has_hdr)) or n >= 35, n


_BRIDGE = re.compile(r"C[ẦẤÂAÃ]U\s*K\s?M\s*(\d{1,3})\s*\+\s*(\d{3})(?:\s*[.,]\s*(\d{1,3}))?", re.I)


def bridge_candidates(lines):
    """Đếm các chuỗi 'Cầu Km…' trong các dòng -> Counter({'Km4+282.000': n})."""
    cnt = Counter()
    for t in lines:
        for m in _BRIDGE.finditer(t):
            km = f"Km{int(m.group(1))}+{m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
            cnt[km] += 1
    return cnt


def bridge_name(lines):
    cnt = bridge_candidates(lines)
    if not cnt:
        return ""
    base = Counter()
    for km, c in cnt.items():
        base[km.split(".")[0]] += c
    top = base.most_common(1)[0][0]
    cands = [km for km in cnt if km.split(".")[0] == top]
    return max(cands, key=lambda k: (cnt[k], len(k)))


# ---------------------------------------------------------------- mục lớn suy từ tên dòng (ưu tiên hơn tiêu đề theo dõi)
_ROW_SECTION = [
    (r"mu tru", "II.2 Trụ cầu"),
    (r"than tru|mong, than tru|mong,than tru|mongthantru", "II.2 Trụ cầu"),
    (r"than mo|mong, than mo", "II.1 Mố cầu"),
    (r"bet?ong dam|dam ngang", "I.5 Dầm ngang"),
    (r"pvc d125|go chan|go lan can", "I.9 Gờ lan can"),
    (r"xoan oc|nap chan rac|upvc|dinh vi|noi mem|luoi thep|dau thu|gang xam", "I.10 Thoát nước mặt cầu"),
    (r"khe co gian dang|khong co ngot", "I.8 Khe co giãn"),
    (r"nhua rong|nhua chat 16|phong nuoc|dinh bam tieu chuan", "I.7 Lớp phủ mặt cầu"),
    (r"chan khay|da hoc|tu non|vat lieu dang hat|da dam dem|tam btxm|mat duong tren mo", "III Gia cố đầu mố"),
    (r"pda|sieu am|khoan mun", "IV Thí nghiệm thử cọc"),
    (r"super t|btct dul|dam btct", "I.1 Kết cấu dầm"),
    (r"goi di dong|tam ban dem goi", "I.2.1 Gối chậu"),
    (r"goi cao su", "I.2.2 Gối cao su"),
    (r"dem dan hoi 10|ban lien tuc nhiet", "I.3 Bản mặt cầu"),
    (r"dem dan hoi 20|bitum nhua|thanh neo|ong thep", "I.6 Neo dầm ngang"),
    (r"bitum chen khe", "II.3 Bản quá độ"),
]


def _row_section(name):
    f = " ".join(fold(name).lower().split())
    for rx, title in _ROW_SECTION:
        if re.search(rx, f):
            return title
    return None


# ---------------------------------------------------------------- phân tích cả trang
def parse_page(rows):
    """rows: list[(text, x0, x1)] theo thứ tự y. -> dict(items=[...], unparsed=[...])
    Mỗi mục: col (L|R), section, name, unit, val, note."""
    cut = len(rows)
    for i, (t, _, _) in enumerate(rows):
        ct = _compact(t)
        if "uybannhandan" in ct or "duanthanhphan" in ct:
            cut = i
            break
    rows = rows[:cut]
    body = [(a, b) for (t, a, b) in rows if split_line(t)[0] and (b - a) >= 25]
    if not body:
        return {"items": [], "unparsed": []}
    xs = sorted(a for a, _ in body)
    xmin = xs[len(xs) // 12]            # bỏ vài dòng lệch trái bất thường
    xmax = max(b for _, b in body)
    xmid = (xmin + xmax) / 2.0
    span = max(1.0, xmax - xmin)

    items_out, unparsed = [], []
    sec = {"L": "", "R": "I.10 Thoát nước mặt cầu"}
    for text, x0, x1 in rows:
        ct = _compact(text)
        if "hangmuccongviec" in ct or "tonghopkhoiluong" in ct:
            continue
        if x1 - x0 < 25 or x1 < xmin - 10:     # chữ dọc ở lề (đường dẫn file CAD…) — không phải dòng bảng
            continue
        items, frags = split_line(text)
        n = len(items)
        wide = (x1 - x0) > 0.62 * span
        cols = []
        if n >= 2:
            cols = ["L"] * (n - 1) + ["R"]
        elif n == 1:
            has_pre = any(f[0] == "pre" and not re.fullmatch(r"[IVXl1-9.\s]{1,8}", f[1]) for f in frags)
            has_post = any(f[0] == "post" and len(re.sub(r"[^A-Za-zÀ-ỹ0-9]", "", f[1])) >= 2 for f in frags)
            if wide:
                cols = ["L"] if has_post else ["R"]
            elif x1 < xmid + 0.04 * span and not has_pre:
                cols = ["L"]
            elif x0 > xmid - 0.04 * span:
                cols = ["R"]
            elif has_pre and not has_post:
                cols = ["R"]
            elif has_post and not has_pre:
                cols = ["L"]
            else:
                cols = ["L" if (x0 + x1) / 2 < xmid else "R"]
        # tiêu đề mục: đứng trước mục k -> khối trái (nếu mục k thuộc khối phải); đứng sau mục cuối -> khối phải
        for kind, ftxt, k in frags:
            s = _find_section(ftxt)
            if not s:
                continue
            if n == 0:
                sec["L" if (x0 + x1) / 2 < xmid else "R"] = s
            elif kind == "pre":
                sec["L" if cols[min(k, n - 1)] == "R" else cols[min(k, n - 1)]] = s
            else:
                sec["R"] = s
        for it, c in zip(items, cols):
            name, key = canonical_name(it["name"], it["unit"])
            val, note = norm_value(it["raw"], it["unit"])
            if it.get("alt"):
                note = (note + "; " if note else "") + f"2 lần OCR khác nhau (nghi sai): {it['alt']}"
            s_row = _row_section(it["name"]) or sec[c]
            sec[c] = s_row
            items_out.append({"col": c, "section": s_row, "name": name, "ocr_name": it["name"], "key": key,
                              "unit": it["unit"], "val": val, "note": note, "line": text})
        if n == 0 and re.search(r"\d{2,}", text) and not _DAYS.search(fold(text)) and len(text) > 10:
            unparsed.append(text)
    items_out.sort(key=lambda r: 0 if r["col"] == "L" else 1)   # sort ổn định: giữ thứ tự y trong mỗi khối
    return {"items": items_out, "unparsed": unparsed}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def check_steel(items):
    """Cốt thép tổng = tổng các nhóm D. Ghi chú vào items (in-place), trả về số chỗ lệch."""
    bad = 0
    n = len(items)
    for i, r in enumerate(items):
        if r["key"] != "ct":
            continue
        kids = []
        for j in range(i + 1, min(n, i + 6)):
            if items[j]["key"] in ("ct_d10", "ct_1018", "ct_d18") and items[j]["col"] == r["col"]:
                kids.append(items[j])
            elif items[j]["key"] == "ct":
                break
        vals = [_f(k["val"]) for k in kids]
        if len(kids) >= 2 and _f(r["val"]) is not None and all(v is not None for v in vals):
            tot, s = _f(r["val"]), sum(vals)
            if abs(tot - s) <= max(0.03, 0.003 * abs(tot)):
                r["ok"] = True
            else:
                r["note"] = (r["note"] + "; " if r["note"] else "") + f"3 nhóm cộng = {s:.2f} ≠ {tot:.2f}"
                bad += 1
    return bad


def check_overlay(items):
    """Lớp phủ mặt cầu: BTN rỗng = BTN chặt = phòng nước; tưới dính bám = 2 × BTN rỗng. Ghi chú nếu lệch."""
    def pick(keys):
        for r in items:
            if r["key"] in keys and r["val"] not in (None,):
                try:
                    return r, float(r["val"])
                except ValueError:
                    return None
        return None
    rong = pick({"betongnhuarong"})
    chat = pick({"betongnhuachat"})
    tuoi = pick({"tuoinhuadinhbam"})
    phong = pick({"lopphongnuoc"})
    bad = 0
    if rong and tuoi and rong[1] > 0 and abs(tuoi[1] - 2 * rong[1]) > 0.05:
        tuoi[0]["note"] = (tuoi[0]["note"] + "; " if tuoi[0]["note"] else "") + f"phải = 2×BTN rỗng = {2 * rong[1]:.2f}"
        bad += 1
    if rong and chat and abs(chat[1] - rong[1]) > 0.05:
        chat[0]["note"] = (chat[0]["note"] + "; " if chat[0]["note"] else "") + f"phải = BTN rỗng = {rong[1]:.2f}"
        bad += 1
    if rong and phong and abs(phong[1] - rong[1]) > 0.05:
        phong[0]["note"] = (phong[0]["note"] + "; " if phong[0]["note"] else "") + f"phải = BTN rỗng = {rong[1]:.2f}"
        bad += 1
    return bad

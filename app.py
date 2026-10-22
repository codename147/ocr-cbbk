# -*- coding: utf-8 -*-
"""
TRA CỨU HỒ SƠ — giao diện chính (Tkinter).
Chạy:  python app.py
"""
import os
import sys
import csv
import time
import queue
import base64
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import threading
import core
import export_ui
from core import fitz
from collections import deque

if core.IS_WIN:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ---------------- màu sắc ----------------
C_BG = "#f4f5f7"
C_PANEL = "#ffffff"
C_HEAD = "#1d3557"
C_HEAD_FG = "#ffffff"
C_ACCENT = "#e76f51"
C_MUTED = "#6b7280"
C_HL = "#ffe066"
C_CUR = "#dbeafe"
C_LINE = "#e5e7eb"

STATUS_VN = {
    "new": "Chưa lập",
    "indexing": "Đang xử lý",
    "done": "Xong",
    "paused": "Tạm dừng",
    "need_ocr": "Còn trang chưa OCR",
    "error": "Lỗi",
    "skipped": "Bỏ qua (không lập chỉ mục)",
}
KIND_VN = {"text": "chữ", "ocr": "OCR", "mix": "chữ+OCR", "doc": "Word", "sheet": "Excel", "txt": "text", "none": "—"}

QUALITY_CHOICES = {
    "fast": "Nhanh (khuyên dùng)",
    "best": "Chính xác (chậm gấp ~2 lần)",
}

PSM_CHOICES = [
    ("3", "3 – Tự động (văn bản thường)"),
    ("4", "4 – Một cột, nhiều cỡ chữ"),
    ("6", "6 – Một khối chữ đồng nhất"),
    ("11", "11 – Chữ rời rạc (bản vẽ)"),
    ("12", "12 – Chữ rời rạc + nhận hướng"),
]


def fmt_dur(sec):
    sec = int(max(0, sec))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h} giờ {m:02d} phút"
    if m:
        return f"{m} phút"
    return f"{sec} giây"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = core.load_config()
        self._clean_tmp()
        self.con = core.connect()
        self.events = queue.Queue()
        self.indexer = None
        self.plan_pages = 0
        self._rate_hist = deque()
        self.hits = []
        self.terms = []
        self.doc_cache = {}
        self.cur = None          # dict: file_id, path, ext, page, pages, line
        self.zoom = None         # None = vừa khung
        self._img = None
        self._last_list_refresh = 0

        self.title(f"{core.APP_NAME} {core.APP_VERSION}")
        self.geometry("1440x880")
        self.minsize(1000, 640)
        self.configure(bg=C_BG)
        self._fonts()
        self._style()
        self._build_header()
        self._build_body()
        self._build_status()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<Control-f>", lambda e: (self.ent_q.focus_set(), self.ent_q.select_range(0, "end")))
        self.after(200, self.poll)
        self.refresh_stats()
        self.refresh_files()
        if not self.cfg.get("sources"):
            self.nb.select(self.tab_index)
            self.after(400, lambda: messagebox.showinfo(
                core.APP_NAME, "Hãy thêm thư mục hồ sơ cần tra cứu rồi bấm “Cập nhật chỉ mục”."))
        elif core.stats(self.con)["done"] == 0:
            self.nb.select(self.tab_index)
        self.ent_q.focus_set()

    # ------------------------------------------------------------ giao diện
    def _fonts(self):
        import tkinter.font as tkfont
        have = set(tkfont.families(self))
        fam = next((f for f in ("Segoe UI", "Arial", "Tahoma", "DejaVu Sans") if f in have), "TkDefaultFont")
        self.f_base = (fam, 10)
        self.f_bold = (fam, 10, "bold")
        self.f_small = (fam, 9)
        self.f_title = (fam, 15, "bold")
        self.f_search = (fam, 14)
        mono = next((f for f in ("Consolas", "Cascadia Mono", "Courier New", "DejaVu Sans Mono") if f in have), "TkFixedFont")
        self.f_mono = (mono, 10)
        self.option_add("*Font", self.f_base)

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=C_BG, font=self.f_base)
        st.configure("TFrame", background=C_BG)
        st.configure("Panel.TFrame", background=C_PANEL)
        st.configure("Head.TFrame", background=C_HEAD)
        st.configure("Head.TLabel", background=C_HEAD, foreground=C_HEAD_FG, font=self.f_title)
        st.configure("HeadSub.TLabel", background=C_HEAD, foreground="#c9d6e8", font=self.f_small)
        st.configure("Head.TCheckbutton", background=C_HEAD, foreground=C_HEAD_FG)
        st.map("Head.TCheckbutton", background=[("active", C_HEAD)], foreground=[("active", "#ffffff")])
        st.configure("TLabel", background=C_BG)
        st.configure("Panel.TLabel", background=C_PANEL)
        st.configure("Muted.TLabel", background=C_BG, foreground=C_MUTED, font=self.f_small)
        st.configure("PanelMuted.TLabel", background=C_PANEL, foreground=C_MUTED, font=self.f_small)
        st.configure("Section.TLabel", background=C_PANEL, font=self.f_bold)
        st.configure("Accent.TButton", background=C_ACCENT, foreground="#ffffff", font=self.f_bold,
                     padding=(16, 6), borderwidth=0)
        st.map("Accent.TButton", background=[("active", "#d65a3c"), ("disabled", "#e5b3a6")])
        st.configure("TButton", padding=(10, 4))
        st.configure("Tool.TButton", padding=(6, 2))
        st.configure("Treeview", rowheight=26, fieldbackground=C_PANEL, background=C_PANEL)
        st.configure("Treeview.Heading", font=self.f_bold, background="#e9edf3")
        st.map("Treeview", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])
        st.configure("TNotebook", background=C_BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(18, 6), font=self.f_bold)
        st.configure("TLabelframe", background=C_PANEL)
        st.configure("TLabelframe.Label", background=C_PANEL, font=self.f_bold)
        st.configure("Panel.TCheckbutton", background=C_PANEL)
        st.configure("Horizontal.TProgressbar", thickness=14, background="#2a9d8f")

    def _build_header(self):
        h = ttk.Frame(self, style="Head.TFrame", padding=(16, 10))
        h.pack(fill="x")
        left = ttk.Frame(h, style="Head.TFrame")
        left.pack(side="left", padx=(0, 18))
        ttk.Label(left, text="TRA CỨU HỒ SƠ", style="Head.TLabel").pack(anchor="w")
        ttk.Label(left, text="PDF · PDF scan (OCR) · Word · Excel", style="HeadSub.TLabel").pack(anchor="w")

        mid = ttk.Frame(h, style="Head.TFrame")
        mid.pack(side="left", fill="x", expand=True)
        row1 = ttk.Frame(mid, style="Head.TFrame")
        row1.pack(fill="x")
        self.var_q = tk.StringVar()
        self.ent_q = tk.Entry(row1, textvariable=self.var_q, font=self.f_search, relief="flat",
                              bg="#ffffff", highlightthickness=2, highlightcolor=C_ACCENT,
                              highlightbackground="#ffffff")
        self.ent_q.pack(side="left", fill="x", expand=True, ipady=5)
        self.ent_q.bind("<Return>", lambda e: self.do_search())
        ttk.Button(row1, text="Tìm", style="Accent.TButton", command=self.do_search).pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(mid, style="Head.TFrame")
        row2.pack(fill="x", pady=(6, 0))
        self.var_accent = tk.BooleanVar(value=False)
        self.var_phrase = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Phân biệt dấu", variable=self.var_accent, style="Head.TCheckbutton",
                        command=self.do_search_if_any).pack(side="left")
        ttk.Checkbutton(row2, text="Cả cụm từ liền nhau", variable=self.var_phrase, style="Head.TCheckbutton",
                        command=self.do_search_if_any).pack(side="left", padx=(12, 0))
        tk.Label(row2, text="Lọc theo tên file/thư mục:", bg=C_HEAD, fg=C_HEAD_FG).pack(side="left", padx=(20, 4))
        self.var_filter = tk.StringVar()
        ef = tk.Entry(row2, textvariable=self.var_filter, width=22, relief="flat")
        ef.pack(side="left", ipady=2)
        ef.bind("<Return>", lambda e: self.do_search())
        tk.Label(row2, text="Sắp xếp:", bg=C_HEAD, fg=C_HEAD_FG).pack(side="left", padx=(16, 4))
        self.var_sort = tk.StringVar(value="Liên quan nhất")
        cb = ttk.Combobox(row2, textvariable=self.var_sort, state="readonly", width=16,
                          values=["Liên quan nhất", "Theo file & trang"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.do_search_if_any())

    def _build_body(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self.tab_search = ttk.Frame(self.nb)
        self.tab_index = ttk.Frame(self.nb, padding=10)
        self.tab_settings = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_search, text="  Tìm kiếm  ")
        self.nb.add(self.tab_index, text="  Chỉ mục  ")
        self.nb.add(self.tab_settings, text="  Cài đặt  ")
        self._build_search_tab()
        self._build_index_tab()
        self._build_settings_tab()

    # ---------------- tab Tìm kiếm ----------------
    def _build_search_tab(self):
        pw = ttk.PanedWindow(self.tab_search, orient="horizontal")
        pw.pack(fill="both", expand=True)

        left = ttk.PanedWindow(pw, orient="vertical")
        pw.add(left, weight=5)

        # kết quả
        fr = ttk.Frame(left, style="Panel.TFrame", padding=(8, 6))
        left.add(fr, weight=3)
        top = ttk.Frame(fr, style="Panel.TFrame")
        top.pack(fill="x")
        self.lbl_count = ttk.Label(top, text='Nhập từ khoá rồi nhấn Enter.   Mẹo: "cụm từ"  ·  -từ_loại_trừ  ·  từ* (khớp đầu từ)',
                                   style="Section.TLabel")
        self.lbl_count.pack(side="left")
        ttk.Button(top, text="Xuất CSV", style="Tool.TButton", command=self.export_csv).pack(side="right")
        ttk.Button(top, text="Trích PDF", style="Tool.TButton",
                   command=lambda: export_ui.export_hits_pdf(self)).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Xuất .md", style="Tool.TButton",
                   command=lambda: export_ui.export_hits_md(self)).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Thu gọn", style="Tool.TButton",
                   command=lambda: self._expand_all(False)).pack(side="right", padx=4)
        ttk.Button(top, text="Mở rộng", style="Tool.TButton",
                   command=lambda: self._expand_all(True)).pack(side="right")

        tf = ttk.Frame(fr, style="Panel.TFrame")
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.tree = ttk.Treeview(tf, columns=("page", "line", "kind", "text"), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="File")
        self.tree.heading("page", text="Trang")
        self.tree.heading("line", text="Dòng")
        self.tree.heading("kind", text="Nguồn")
        self.tree.heading("text", text="Nội dung")
        self.tree.column("#0", width=330, stretch=False)
        self.tree.column("page", width=70, anchor="center", stretch=False)
        self.tree.column("line", width=55, anchor="center", stretch=False)
        self.tree.column("kind", width=68, anchor="center", stretch=False)
        self.tree.column("text", width=500)
        self.tree.tag_configure("file", font=self.f_bold, background="#f1f5f9")
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_current())
        self.tree.bind("<Return>", lambda e: self.open_current())
        self.tree.bind("<Button-3>", self.on_tree_menu)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Mở file tại trang này", command=self.open_current)
        self.menu.add_command(label="Mở thư mục chứa file", command=self.open_current_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Sao chép đường dẫn file", command=self.copy_path)
        self.menu.add_command(label="Sao chép dòng kết quả", command=self.copy_line)

        # nội dung trang
        fc = ttk.Frame(left, style="Panel.TFrame", padding=(8, 6))
        left.add(fc, weight=2)
        self.lbl_ctx = ttk.Label(fc, text="Nội dung trang", style="Section.TLabel")
        self.lbl_ctx.pack(anchor="w")
        cf = ttk.Frame(fc, style="Panel.TFrame")
        cf.pack(fill="both", expand=True, pady=(4, 0))
        self.txt = tk.Text(cf, wrap="word", font=self.f_mono, relief="flat", bg="#fbfbfc",
                           padx=6, pady=4, state="disabled", cursor="arrow")
        sv = ttk.Scrollbar(cf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sv.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sv.pack(side="right", fill="y")
        self.txt.tag_configure("ln", foreground="#9ca3af")
        self.txt.tag_configure("hl", background=C_HL)
        self.txt.tag_configure("cur", background=C_CUR)
        self.txt.tag_raise("hl")

        # xem trước
        right = ttk.Frame(pw, style="Panel.TFrame", padding=(8, 6))
        pw.add(right, weight=4)
        bar = ttk.Frame(right, style="Panel.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="◀", width=3, style="Tool.TButton", command=lambda: self.goto_page(-1)).pack(side="left")
        self.var_page = tk.StringVar()
        ep = ttk.Entry(bar, textvariable=self.var_page, width=6, justify="center")
        ep.pack(side="left", padx=2)
        ep.bind("<Return>", lambda e: self.goto_page(0, absolute=True))
        self.lbl_pages = ttk.Label(bar, text="/ –", style="Panel.TLabel")
        self.lbl_pages.pack(side="left")
        ttk.Button(bar, text="▶", width=3, style="Tool.TButton", command=lambda: self.goto_page(1)).pack(side="left", padx=(2, 10))
        ttk.Button(bar, text="−", width=3, style="Tool.TButton", command=lambda: self.zoom_by(1 / 1.25)).pack(side="left")
        ttk.Button(bar, text="+", width=3, style="Tool.TButton", command=lambda: self.zoom_by(1.25)).pack(side="left", padx=2)
        ttk.Button(bar, text="Vừa khung", style="Tool.TButton", command=self.zoom_fit).pack(side="left")
        ttk.Button(bar, text="Mở thư mục", style="Tool.TButton", command=self.open_current_folder).pack(side="right")
        ttk.Button(bar, text="Mở file tại trang này", style="Tool.TButton",
                   command=self.open_current).pack(side="right", padx=4)
        self.lbl_file = ttk.Label(right, text="", style="PanelMuted.TLabel")
        self.lbl_file.pack(fill="x", pady=(4, 2))
        cv = ttk.Frame(right, style="Panel.TFrame")
        cv.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(cv, bg="#525659", highlightthickness=0)
        cvs = ttk.Scrollbar(cv, orient="vertical", command=self.canvas.yview)
        chs = ttk.Scrollbar(cv, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=cvs.set, xscrollcommand=chs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        cvs.grid(row=0, column=1, sticky="ns")
        chs.grid(row=1, column=0, sticky="ew")
        cv.rowconfigure(0, weight=1)
        cv.columnconfigure(0, weight=1)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self._resize_job = None
        self._welcome()

    def _welcome(self):
        self.canvas.delete("all")
        self.canvas.create_text(20, 20, anchor="nw", fill="#e5e7eb", font=self.f_base,
                                text="Chọn một kết quả để xem trước trang.\n\n"
                                     "• Nháy đúp: mở file đúng trang bằng phần mềm đọc PDF\n"
                                     "• Kéo chuột để di chuyển, Ctrl + lăn chuột để phóng to")

    # ---------------- tab Chỉ mục ----------------
    def _build_index_tab(self):
        t = self.tab_index
        src = ttk.LabelFrame(t, text=" Thư mục hồ sơ (chỉ đọc, phần mềm không ghi gì vào đây) ", padding=8)
        src.pack(fill="x")
        self.lst_src = tk.Listbox(src, height=3, relief="flat", bg="#fbfbfc", activestyle="none")
        self.lst_src.pack(side="left", fill="x", expand=True)
        b = ttk.Frame(src, style="Panel.TFrame")
        b.pack(side="left", padx=(8, 0))
        ttk.Button(b, text="Thêm thư mục…", command=self.add_source).pack(fill="x")
        ttk.Button(b, text="Bỏ thư mục", command=self.remove_source).pack(fill="x", pady=(4, 0))
        self._fill_sources()

        act = ttk.Frame(t)
        act.pack(fill="x", pady=(10, 4))
        self.btn_run = ttk.Button(act, text="▶  Cập nhật chỉ mục", style="Accent.TButton",
                                  command=lambda: self.start_index(None))
        self.btn_run.pack(side="left")
        self.btn_sel = ttk.Button(act, text="Chỉ xử lý file đã chọn", command=self.index_selected)
        self.btn_sel.pack(side="left", padx=6)
        self.btn_redo = ttk.Button(act, text="Lập lại từ đầu file đã chọn", command=self.redo_selected)
        self.btn_redo.pack(side="left")
        self.btn_stop = ttk.Button(act, text="■  Dừng", command=self.stop_index, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Label(act, text="Có thể dừng bất cứ lúc nào, lần sau sẽ chạy tiếp từ trang đang dở.",
                  style="Muted.TLabel").pack(side="left", padx=10)

        act2 = ttk.Frame(t)
        act2.pack(fill="x", pady=(0, 4))
        ttk.Button(act2, text="Bỏ qua file đã chọn", command=self.skip_selected).pack(side="left")
        ttk.Button(act2, text="Bao gồm lại", command=self.unskip_selected).pack(side="left", padx=6)
        ttk.Button(act2, text="Kiểm tra số liệu OCR…", command=self.doubt_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(act2, text="Xuất Markdown cho AI…", command=self.export_md_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(act2, text="Xuất TOÀN BỘ OCR ra .md…", command=self.export_raw_dialog).pack(side="left", padx=(6, 0))
        self.btn_reocr = ttk.Button(act2, text="OCR lại bảng khối lượng…", command=self.reocr_dialog)
        self.btn_reocr.pack(side="left", padx=(6, 0))
        ttk.Label(act2, text="Chọn nhiều file bằng Ctrl/Shift. File bị bỏ qua sẽ không OCR nữa (phần đã đọc vẫn tìm được). "
                             "Dùng để loại các quyển không cần tra cứu cho nhanh.", style="Muted.TLabel").pack(side="left", padx=10)

        pg = ttk.Frame(t)
        pg.pack(fill="x", pady=(4, 6))
        self.pbar = ttk.Progressbar(pg, mode="determinate")
        self.pbar.pack(fill="x")
        self.lbl_prog = ttk.Label(pg, text="Chưa chạy.", style="Muted.TLabel")
        self.lbl_prog.pack(anchor="w", pady=(3, 0))

        pw = ttk.PanedWindow(t, orient="vertical")
        pw.pack(fill="both", expand=True)
        ff = ttk.Frame(pw, style="Panel.TFrame", padding=4)
        pw.add(ff, weight=4)
        cols = ("pages", "done", "ocr", "status")
        self.ftree = ttk.Treeview(ff, columns=cols, show="tree headings", selectmode="extended")
        self.ftree.heading("#0", text="File")
        self.ftree.heading("pages", text="Số trang")
        self.ftree.heading("done", text="Đã xử lý")
        self.ftree.heading("ocr", text="Trang OCR")
        self.ftree.heading("status", text="Trạng thái")
        self.ftree.column("#0", width=640)
        for c, w in (("pages", 80), ("done", 90), ("ocr", 90), ("status", 260)):
            self.ftree.column(c, width=w, anchor="center", stretch=(c == "status"))
        self.ftree.tag_configure("done", foreground="#15803d")
        self.ftree.tag_configure("error", foreground="#b91c1c")
        self.ftree.tag_configure("skipped", foreground="#9ca3af")
        self.ftree.tag_configure("indexing", foreground="#1d4ed8", font=self.f_bold)
        fs = ttk.Scrollbar(ff, orient="vertical", command=self.ftree.yview)
        self.ftree.configure(yscrollcommand=fs.set)
        self.ftree.pack(side="left", fill="both", expand=True)
        fs.pack(side="right", fill="y")

        lf = ttk.Frame(pw, style="Panel.TFrame", padding=4)
        pw.add(lf, weight=1)
        self.log = tk.Text(lf, height=6, font=self.f_small, relief="flat", bg="#fbfbfc", state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ls.set)
        self.log.pack(side="left", fill="both", expand=True)
        ls.pack(side="right", fill="y")

    # ---------------- tab Cài đặt ----------------
    def _build_settings_tab(self):
        t = self.tab_settings
        ocr = ttk.LabelFrame(t, text=" Nhận dạng chữ (OCR) cho PDF scan ", padding=10)
        ocr.pack(fill="x")
        self.var_ocr = tk.BooleanVar(value=self.cfg.get("ocr_enabled", True))
        ttk.Checkbutton(ocr, text="Bật OCR (cần Tesseract-OCR)", variable=self.var_ocr,
                        style="Panel.TCheckbutton").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(ocr, text="Đường dẫn tesseract.exe:", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.var_tess = tk.StringVar(value=self.cfg.get("tesseract_path", ""))
        ttk.Entry(ocr, textvariable=self.var_tess, width=70).grid(row=1, column=1, sticky="we", padx=6)
        bb = ttk.Frame(ocr, style="Panel.TFrame")
        bb.grid(row=1, column=2, sticky="w")
        ttk.Button(bb, text="Chọn…", command=self.pick_tess).pack(side="left")
        ttk.Button(bb, text="Kiểm tra", command=self.check_tess).pack(side="left", padx=4)
        self.lbl_tess = ttk.Label(ocr, text="", style="PanelMuted.TLabel")
        self.lbl_tess.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Label(ocr, text="(Để trống = tự tìm)", style="PanelMuted.TLabel").grid(row=2, column=0, sticky="w")

        ttk.Label(ocr, text="Ngôn ngữ:", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.var_lang = tk.StringVar(value=self.cfg.get("ocr_lang", "vie"))
        ttk.Entry(ocr, textvariable=self.var_lang, width=14).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text="Chất lượng OCR:", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        self.var_model = tk.StringVar(value=QUALITY_CHOICES.get(self.cfg.get("ocr_model", "fast"), QUALITY_CHOICES["fast"]))
        ttk.Combobox(ocr, textvariable=self.var_model, values=list(QUALITY_CHOICES.values()), state="readonly",
                     width=34).grid(row=4, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text="Nhanh: đo thử đọc được số từ tương đương, nhanh gấp ~2 lần.",
                  style="PanelMuted.TLabel").grid(row=4, column=2, sticky="w")
        ttk.Label(ocr, text="Kiểu bố cục trang:", style="Panel.TLabel").grid(row=5, column=0, sticky="w", pady=4)
        cur_psm = str(self.cfg.get("ocr_psm", 3))
        self.var_psm = tk.StringVar(value=next((l for v, l in PSM_CHOICES if v == cur_psm), PSM_CHOICES[0][1]))
        ttk.Combobox(ocr, textvariable=self.var_psm, values=[l for _, l in PSM_CHOICES], state="readonly",
                     width=34).grid(row=5, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text="Số luồng OCR song song:", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=4)
        self.var_workers = tk.StringVar(value=str(self.cfg.get("workers", 2)))
        ttk.Spinbox(ocr, from_=1, to=max(2, os.cpu_count() or 2), textvariable=self.var_workers, width=8).grid(
            row=6, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text=f"Máy có {os.cpu_count()} luồng CPU. Nên để bằng số luồng trừ 1.",
                  style="PanelMuted.TLabel").grid(row=6, column=2, sticky="w")
        ttk.Label(ocr, text="Trang có ít hơn … ký tự thì OCR:", style="Panel.TLabel").grid(row=7, column=0, sticky="w", pady=4)
        self.var_minc = tk.StringVar(value=str(self.cfg.get("min_text_chars", 30)))
        ttk.Spinbox(ocr, from_=0, to=1000, increment=10, textvariable=self.var_minc, width=8).grid(
            row=7, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text="Phóng ảnh khi OCR (lần):", style="Panel.TLabel").grid(row=8, column=0, sticky="w", pady=4)
        self.var_scale = tk.StringVar(value=str(self.cfg.get("ocr_scale", 2.0)))
        ttk.Spinbox(ocr, from_=1.0, to=3.0, increment=0.5, textvariable=self.var_scale, width=8).grid(
            row=8, column=1, sticky="w", padx=6)
        ttk.Label(ocr, text="So với độ phân giải ảnh scan gốc. 2.0 là tối ưu; 3.0 chậm hơn mà không đọc tốt hơn.",
                  style="PanelMuted.TLabel").grid(row=8, column=2, sticky="w")
        ttk.Label(ocr, text="Cạnh dài tối đa của ảnh (px):", style="Panel.TLabel").grid(row=9, column=0, sticky="w", pady=4)
        self.var_maxpx = tk.StringVar(value=str(self.cfg.get("ocr_max_px", 3600)))
        ttk.Spinbox(ocr, from_=2000, to=6000, increment=200, textvariable=self.var_maxpx, width=8).grid(
            row=9, column=1, sticky="w", padx=6)
        ocr.columnconfigure(1, weight=1)

        vw = ttk.LabelFrame(t, text=" Mở file PDF ", padding=10)
        vw.pack(fill="x", pady=10)
        found = core.find_viewers()
        names = [core.VIEWER_NAMES[k] for k in ("auto", "sumatra", "foxit", "acrobat", "edge", "chrome", "default")]
        self.var_viewer = tk.StringVar(value=core.VIEWER_NAMES.get(self.cfg.get("viewer", "auto"), "Tự động"))
        ttk.Label(vw, text="Phần mềm mở PDF:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(vw, textvariable=self.var_viewer, values=names, state="readonly", width=40).grid(
            row=0, column=1, sticky="w", padx=6)
        fl = ", ".join(core.VIEWER_NAMES[k] for k in found) or "không thấy phần mềm nào hỗ trợ nhảy trang"
        ttk.Label(vw, text=f"Tìm thấy trên máy: {fl}", style="PanelMuted.TLabel").grid(
            row=1, column=1, sticky="w", padx=6, pady=(4, 0))

        dt = ttk.LabelFrame(t, text=" Dữ liệu ", padding=10)
        dt.pack(fill="x")
        ttk.Label(dt, text=f"Chỉ mục và cấu hình lưu tại:  {core.DATA_DIR}", style="Panel.TLabel").pack(anchor="w")
        ttk.Label(dt, text="Phần mềm chỉ mở file hồ sơ ở chế độ đọc. Xoá thư mục data = xoá toàn bộ chỉ mục.",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(2, 6))
        ttk.Button(dt, text="Mở thư mục dữ liệu", command=lambda: core.open_folder(core.DB_PATH)).pack(anchor="w")

        ttk.Button(t, text="Lưu cài đặt", style="Accent.TButton", command=self.save_settings).pack(anchor="w", pady=12)
        self.after(300, self.check_tess)

    def _build_status(self):
        sb = ttk.Frame(self, padding=(12, 4))
        sb.pack(fill="x", side="bottom")
        self.lbl_stats = ttk.Label(sb, text="", style="Muted.TLabel")
        self.lbl_stats.pack(side="left")
        self.lbl_run = ttk.Label(sb, text="", style="Muted.TLabel")
        self.lbl_run.pack(side="right")

    # ------------------------------------------------------------ tiện ích
    def _clean_tmp(self):
        try:
            for f in os.listdir(core.TMP_DIR):
                try:
                    os.remove(os.path.join(core.TMP_DIR, f))
                except OSError:
                    pass
        except OSError:
            pass

    def log_msg(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  ") + s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_stats(self):
        s = core.stats(self.con)
        txt = (f"Đã lập chỉ mục {s['done']:,} / {s['pages']:,} trang  ·  {s['files']} file  ·  "
               f"{s['ocr']:,} trang OCR")
        if s["pending_ocr"]:
            txt += f"  ·  {s['pending_ocr']:,} trang scan chưa OCR"
        self.lbl_stats.configure(text=txt.replace(",", "."))

    def refresh_files(self):
        sel = set(self.ftree.selection())
        self.ftree.delete(*self.ftree.get_children())
        rows = self.con.execute("SELECT id, rel, pages, done, ocr_pages, status, error, pending_ocr "
                                "FROM files ORDER BY rel").fetchall()
        skip = set(self.cfg.get("skip", []))
        for fid, rel, pages, done, ocr, status, err, pend in rows:
            if rel in skip and status != "done":
                status = "skipped"
            st = STATUS_VN.get(status, status)
            if status == "error" and err:
                st = f"Lỗi: {err[:60]}"
            elif status == "need_ocr":
                st = f"Còn {pend} trang chưa OCR"
            iid = f"F{fid}"
            self.ftree.insert("", "end", iid=iid, text=rel, values=(pages, done, ocr, st),
                              tags=(status,))
            if iid in sel:
                self.ftree.selection_add(iid)
        self._last_list_refresh = time.time()

    def _fill_sources(self):
        self.lst_src.delete(0, "end")
        for s in self.cfg.get("sources", []):
            self.lst_src.insert("end", s)

    # ------------------------------------------------------------ nguồn
    def add_source(self):
        p = filedialog.askdirectory(title="Chọn thư mục hồ sơ")
        if not p:
            return
        p = os.path.normpath(p)
        err = core.check_source_allowed(p)
        if err:
            messagebox.showerror(core.APP_NAME, err)
            return
        srcs = self.cfg.setdefault("sources", [])
        if any(core.is_inside(p, s) for s in srcs):
            messagebox.showinfo(core.APP_NAME, "Thư mục này đã nằm trong danh sách.")
            return
        srcs.append(p)
        core.save_config(self.cfg)
        self._fill_sources()
        if messagebox.askyesno(core.APP_NAME, "Đã thêm. Bắt đầu lập chỉ mục ngay?"):
            self.start_index(None)

    def remove_source(self):
        i = self.lst_src.curselection()
        if not i:
            return
        s = self.lst_src.get(i[0])
        if self.indexer:
            messagebox.showwarning(core.APP_NAME, "Hãy dừng lập chỉ mục trước.")
            return
        if not messagebox.askyesno(core.APP_NAME, f"Bỏ thư mục và xoá chỉ mục của nó?\n\n{s}\n\n"
                                                  "(File gốc không bị ảnh hưởng)"):
            return
        self.cfg["sources"] = [x for x in self.cfg.get("sources", []) if x != s]
        core.save_config(self.cfg)
        core.remove_source(self.con, s)
        self._fill_sources()
        self.refresh_files()
        self.refresh_stats()

    # ------------------------------------------------------------ lập chỉ mục
    def start_index(self, only_ids):
        if self.indexer:
            return
        if not self.cfg.get("sources"):
            messagebox.showinfo(core.APP_NAME, "Chưa có thư mục hồ sơ nào.")
            return
        for s in self.cfg["sources"]:
            err = core.check_source_allowed(s) if os.path.isdir(s) else None
            if err:
                messagebox.showerror(core.APP_NAME, f"{s}\n\n{err}")
                return
        if self.cfg.get("ocr_enabled") and not core.find_tesseract(self.cfg):
            if not messagebox.askyesno(core.APP_NAME,
                                       "Chưa tìm thấy Tesseract-OCR nên các trang scan sẽ chưa đọc được.\n"
                                       "Vẫn lập chỉ mục các file có chữ?"):
                return
        self.indexer = core.Indexer(self.cfg, self.events, only_ids)
        self.indexer.start()
        self.btn_run.configure(state="disabled")
        self.btn_sel.configure(state="disabled")
        self.btn_redo.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.pbar.configure(value=0, maximum=1)
        self.log_msg("Bắt đầu lập chỉ mục.")

    def _selected_file_ids(self):
        return [int(i[1:]) for i in self.ftree.selection() if i.startswith("F")]

    def doubt_dialog(self):
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn một hoặc nhiều file trong danh sách (giữ Ctrl để chọn nhiều).")
            return
        export_ui.DoubtDialog(self, ids)

    def export_md_dialog(self):
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn một hoặc nhiều file trong danh sách (giữ Ctrl để chọn nhiều).")
            return
        export_ui.ExportDialog(self, ids)

    def reocr_dialog(self):
        """OCR lại các trang 'BẢNG TỔNG HỢP KHỐI LƯỢNG': tách nửa trái/phải, bỏ đường kẻ + mộc, đọc riêng cột số."""
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn một hoặc nhiều quyển bản vẽ (PDF) trong danh sách.")
            return
        if self.indexer:
            messagebox.showwarning(core.APP_NAME, "Đang lập chỉ mục. Hãy chờ xong hoặc bấm Dừng trước.")
            return
        exe = core.find_tesseract(self.cfg)
        if not exe:
            messagebox.showerror(core.APP_NAME, "Chưa tìm thấy Tesseract-OCR (xem tab Cài đặt).")
            return
        try:
            import reocr
            reocr._np()
        except Exception as e:
            messagebox.showerror(core.APP_NAME, str(e))
            return
        if not messagebox.askyesno(core.APP_NAME,
                                   f"OCR lại các trang bảng khối lượng của {len(ids)} file đã chọn?\n\n"
                                   "Khoảng 20 giây/trang (một quyển ~30 bảng ≈ 10 phút). Chỉ đọc file PDF gốc, "
                                   "chỉ ghi vào chỉ mục. Xong hãy xuất lại .md."):
            return
        self.btn_reocr.configure(state="disabled")
        self._reocr_stop = False
        cfg = dict(self.cfg)

        def work():
            con = core.connect()
            tot = 0
            try:
                for fid in ids:
                    r = con.execute("SELECT path, rel, ext FROM files WHERE id=?", (fid,)).fetchone()
                    if not r or r[2] != ".pdf":
                        continue
                    self.events.put(("log", f"OCR lại bảng khối lượng: {os.path.basename(r[1])}"))
                    n, done = reocr.reocr_file(
                        con, fid, r[0], cfg, exe, stop=lambda: self._reocr_stop,
                        progress=lambda i, n, p: self.events.put(("phase", f"OCR lại bảng khối lượng {i + 1}/{n} (trang {p})")),
                        log=lambda m: self.events.put(("log", m)))
                    tot += n
                    self.events.put(("log", f"  → xong {n} trang bảng khối lượng."))
                self.events.put(("reocr_done", tot))
            except Exception as e:
                self.events.put(("error", f"OCR lại bảng khối lượng: {e}"))
                self.events.put(("reocr_done", tot))
            finally:
                con.close()
        threading.Thread(target=work, daemon=True).start()

    def export_raw_dialog(self):
        ids = self._selected_file_ids()
        if not ids:  # không chọn gì = tất cả file đã có chữ
            ids = [r[0] for r in self.con.execute("SELECT id FROM files WHERE done>0 ORDER BY rel")]
            if not ids:
                messagebox.showinfo(core.APP_NAME, "Chưa có file nào được lập chỉ mục.")
                return
            if not messagebox.askyesno(core.APP_NAME, f"Chưa chọn file nào. Xuất TOÀN BỘ {len(ids)} file đã lập chỉ mục?"):
                return
        export_ui.ExportDialog(self, ids, preset="raw")

    def index_selected(self):
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn một hoặc nhiều file trong danh sách (giữ Ctrl để chọn nhiều).")
            return
        self.start_index(ids)

    def redo_selected(self):
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn file cần lập lại trong danh sách.")
            return
        if not messagebox.askyesno(core.APP_NAME, f"Xoá chỉ mục và xử lý lại {len(ids)} file đã chọn?"):
            return
        for i in ids:
            core.reset_file(self.con, i)
        self.con.commit()
        self.refresh_files()
        self.start_index(ids)

    def _set_skip(self, flag):
        ids = self._selected_file_ids()
        if not ids:
            messagebox.showinfo(core.APP_NAME, "Chọn một hoặc nhiều file trong danh sách trước.")
            return
        rels = [r[0] for i in ids for r in self.con.execute("SELECT rel FROM files WHERE id=?", (i,))]
        skip = set(self.cfg.get("skip", []))
        if flag:
            skip.update(rels)
        else:
            skip.difference_update(rels)
        self.cfg["skip"] = sorted(skip)
        core.save_config(self.cfg)
        self.refresh_files()
        if self.indexer:
            self.log_msg("Thay đổi 'bỏ qua' sẽ có hiệu lực từ lần chạy sau (bấm Dừng rồi Cập nhật chỉ mục).")

    def skip_selected(self):
        self._set_skip(True)

    def unskip_selected(self):
        self._set_skip(False)

    def stop_index(self):
        if self.indexer:
            self.indexer.stop()
            self.btn_stop.configure(state="disabled")
            self.lbl_prog.configure(text="Đang dừng… (chờ các trang đang OCR xong)")

    def poll(self):
        try:
            for _ in range(500):
                kind, d = self.events.get_nowait()
                self.handle_event(kind, d)
        except queue.Empty:
            pass
        if self.indexer and time.time() - self._last_list_refresh > 3:
            self.refresh_files()
            self.refresh_stats()
        self.after(250, self.poll)

    def handle_event(self, kind, d):
        if kind == "phase":
            self.lbl_prog.configure(text=d)
            self.lbl_run.configure(text=d)
            self.log_msg(d)
        elif kind == "scan":
            self.lbl_prog.configure(text=f"Đang quét thư mục… {d} file")
        elif kind == "plan":
            self.plan_pages = max(1, d["pages"])
            self._rate_hist.clear()
            self.pbar.configure(maximum=self.plan_pages, value=0)
            self.log_msg(f"Cần xử lý {d['files']} file, khoảng {d['pages']:,} trang.".replace(",", "."))
        elif kind == "file_start":
            self.cur_file = d["rel"]
        elif kind == "progress":
            p, el = d["processed"], d["elapsed"]
            self.pbar.configure(value=min(p, self.plan_pages))
            avg = p / el if el > 0 else 0
            now = time.time()
            self._rate_hist.append((now, p))
            while len(self._rate_hist) > 2 and now - self._rate_hist[0][0] > 120:
                self._rate_hist.popleft()
            t0, p0 = self._rate_hist[0]
            rate = (p - p0) / (now - t0) if now - t0 >= 15 else avg   # tốc độ 2 phút gần nhất
            eta = (self.plan_pages - p) / rate if rate > 0 else 0
            ocr_txt = f"   ·   OCR {d['avg_ocr']:.1f} giây/trang/luồng" if d.get("avg_ocr") else ""
            msg = (f"{os.path.basename(d['rel'])} — trang {d['page']}/{d['total']}   ·   "
                   f"{p:,}/{self.plan_pages:,} trang   ·   hiện tại {rate * 60:.0f} trang/phút "
                   f"(trung bình {avg * 60:.0f}){ocr_txt}   ·   còn khoảng {fmt_dur(eta)}").replace(",", ".")
            self.lbl_prog.configure(text=msg)
            self.lbl_run.configure(text=f"Đang lập chỉ mục {p * 100 // self.plan_pages}%")
        elif kind == "file_done":
            pass
        elif kind == "reocr_done":
            self.btn_reocr.configure(state="normal")
            self.lbl_prog.configure(text=f"Đã OCR lại {d} trang bảng khối lượng. Hãy xuất lại .md.")
            self.refresh_files()
            self.refresh_stats()
        elif kind == "log":
            self.log_msg(d)
        elif kind == "error":
            self.log_msg("LỖI:\n" + d)
        elif kind == "finished":
            self.indexer = None
            self.btn_run.configure(state="normal")
            self.btn_sel.configure(state="normal")
            self.btn_redo.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.lbl_run.configure(text="")
            self.refresh_files()
            self.refresh_stats()
            self.log_msg("Xong OCR. Số liệu OCR có thể sai (số lẫn chữ, bảng số…): chọn file rồi bấm "
                         "'Kiểm tra số liệu OCR…' để xem, nhập tay hoặc bỏ qua. File .md xuất ra sẽ có mục báo cáo ở đầu.")

    # ------------------------------------------------------------ tìm kiếm
    def do_search_if_any(self):
        if self.var_q.get().strip():
            self.do_search()

    def do_search(self):
        q = self.var_q.get().strip()
        if not q:
            return
        self.nb.select(self.tab_search)
        t0 = time.time()
        try:
            hits, terms, npages = core.search(
                self.con, q, accent=self.var_accent.get(), whole_phrase=self.var_phrase.get(),
                file_filter=self.var_filter.get(),
                sort="rank" if self.var_sort.get().startswith("Liên") else "file",
                max_pages=int(self.cfg.get("max_pages", 3000)))
        except Exception as e:
            messagebox.showerror(core.APP_NAME, f"Không tìm được: {e}")
            return
        self.hits, self.terms = hits, terms
        self.tree.delete(*self.tree.get_children())
        groups = {}
        order = []
        for i, h in enumerate(hits):
            if h.file_id not in groups:
                groups[h.file_id] = []
                order.append(h.file_id)
            groups[h.file_id].append(i)
        expand = len(order) <= 25
        for fid in order:
            idxs = groups[fid]
            h0 = hits[idxs[0]]
            pages = len({hits[i].page for i in idxs})
            folder = os.path.dirname(h0.rel)
            label = f"{h0.name}   ({len(idxs)} kết quả, {pages} trang)"
            self.tree.insert("", "end", iid=f"f{fid}", text=label, open=expand, tags=("file",),
                             values=("", "", "", folder))
            for i in idxs:
                h = hits[i]
                pg = h.label if h.kind == "sheet" else (h.page if h.ext == ".pdf" else "")
                self.tree.insert(f"f{fid}", "end", iid=f"h{i}", text="",
                                 values=(pg, h.line or "–", KIND_VN.get(h.kind, h.kind), self._snippet(h)))
        dt = (time.time() - t0) * 1000
        self.lbl_count.configure(text=f"{len(hits):,} dòng khớp · {npages:,} trang · {len(order)} file   "
                                      f"({dt:.0f} ms)".replace(",", "."))
        kids = self.tree.get_children()
        if kids:
            first = self.tree.get_children(kids[0])
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
                self.tree.yview_moveto(0)
        else:
            self._welcome()
            self._set_ctx([])

    @staticmethod
    def _snippet(h, width=170):
        t = " ".join(core.clean_display(h.text).split())
        if not h.spans:
            return t[:width]
        # tính lại vị trí trên chuỗi đã gộp khoảng trắng: dùng mốc ký tự đầu tiên
        a = h.spans[0][0]
        prefix = " ".join(h.text[:a].split())
        a2 = len(prefix) + (1 if prefix and h.text[:a][-1:].isspace() else 0)
        start = max(0, a2 - 50)
        s = t[start:start + width]
        return ("…" if start else "") + s + ("…" if start + width < len(t) else "")

    def _expand_all(self, flag):
        for k in self.tree.get_children():
            self.tree.item(k, open=flag)

    def on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("f"):
            kids = self.tree.get_children(iid)
            if not kids:
                return
            iid = kids[0]
        h = self.hits[int(iid[1:])]
        pages = None
        if h.ext == ".pdf":
            r = self.con.execute("SELECT pages FROM files WHERE id=?", (h.file_id,)).fetchone()
            pages = r[0] if r else None
        self.cur = {"file_id": h.file_id, "path": h.path, "ext": h.ext, "page": h.page,
                    "pages": pages, "line": h.line, "rel": h.rel, "hit": h}
        self.show_page()

    # ------------------------------------------------------------ hiển thị trang
    def _line_spans(self, text):
        spans = []
        accent = self.var_accent.get()
        for t in self.terms:
            if t.exclude:
                continue
            spans.extend(core.find_spans(text, t, core.term_regex(t), accent))
        return spans

    def _set_ctx(self, lines, cur_line=0, title="Nội dung trang"):
        self.lbl_ctx.configure(text=title)
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        for ln, text, *_ in lines:
            row = int(self.txt.index("end-1c").split(".")[0])
            pre = f"{ln:>4} │ "
            self.txt.insert("end", pre, ("ln",))
            text = core.nfc(text)
            self.txt.insert("end", text + "\n")
            for a, b in self._line_spans(text):
                self.txt.tag_add("hl", f"{row}.{len(pre) + a}", f"{row}.{len(pre) + b}")
            if ln == cur_line:
                self.txt.tag_add("cur", f"{row}.0", f"{row + 1}.0")
                cur_row = row
        self.txt.configure(state="disabled")
        if cur_line:
            try:
                self.txt.see(f"{cur_row}.0")
                self.txt.yview_scroll(-3, "units")
            except Exception:
                pass

    def show_page(self, keep_scroll=False):
        c = self.cur
        if not c:
            return
        lines = core.page_lines(self.con, c["file_id"], c["page"])
        kind, label = core.page_kind(self.con, c["file_id"], c["page"])
        where = f"Sheet {label}" if kind == "sheet" else (f"Trang {c['page']}" if c["ext"] == ".pdf" else "")
        line_txt = f" · dòng {c['line']}" if c.get("line") and c["page"] == c["hit"].page else ""
        self._set_ctx(lines, c["line"] if c["page"] == c["hit"].page else 0,
                      f"{where}{line_txt}  —  {KIND_VN.get(kind, '')}  ·  {os.path.basename(c['path'])}")
        self.lbl_file.configure(text=c["rel"])
        self.var_page.set(str(c["page"]))
        self.lbl_pages.configure(text=f"/ {c['pages'] or '–'}")
        if c["ext"] == ".pdf" or core.SUPPORTED.get(c["ext"]) == "img":
            self.render_preview(lines, keep_scroll)
        else:
            self.canvas.delete("all")
            self.canvas.create_text(20, 20, anchor="nw", fill="#e5e7eb", font=self.f_base,
                                    text="Không có xem trước cho loại file này.\nNháy đúp để mở file.")

    def get_doc(self, path):
        d = self.doc_cache.get(path)
        if d is None:
            if len(self.doc_cache) > 6:
                for k in list(self.doc_cache)[:3]:
                    try:
                        self.doc_cache.pop(k).close()
                    except Exception:
                        pass
            d = fitz.open(path)
            self.doc_cache[path] = d
        return d

    def render_preview(self, lines, keep_scroll=False):
        c = self.cur
        self.canvas.delete("all")
        try:
            doc = self.get_doc(c["path"])
            page = doc[c["page"] - 1]
        except Exception as e:
            self.canvas.create_text(20, 20, anchor="nw", fill="#fca5a5", text=f"Không mở được file:\n{e}")
            return
        cw = max(200, self.canvas.winfo_width() - 20)
        fit = cw / page.rect.width
        z = self.zoom or fit
        z = max(0.1, min(z, 6.0))
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        try:
            img = tk.PhotoImage(data=pix.tobytes("ppm"))
        except tk.TclError:
            img = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")))
        self._img = img
        x0 = 10
        self.canvas.create_image(x0, 10, anchor="nw", image=img)
        cur_y = None
        hit = c["hit"]
        for ln, text, a, b, cc, d in lines:
            if not (cc or d) or (a == 0 and b == 0 and cc == 0):
                continue
            is_cur = (ln == c["line"] and c["page"] == hit.page)
            if self._line_spans(text) or is_cur:
                rx0, ry0, rx1, ry1 = x0 + a * z, 10 + b * z, x0 + cc * z, 10 + d * z
                self.canvas.create_rectangle(rx0 - 2, ry0 - 2, rx1 + 2, ry1 + 2, fill=C_HL, stipple="gray25",
                                             outline="#f59e0b" if not is_cur else "#dc2626",
                                             width=1 if not is_cur else 3)
                if is_cur:
                    cur_y = ry0
        self.canvas.configure(scrollregion=(0, 0, img.width() + 20, img.height() + 20))
        if not keep_scroll:
            self.canvas.xview_moveto(0)
            if cur_y is not None:
                vis = self.canvas.winfo_height()
                self.canvas.yview_moveto(max(0, (cur_y - vis / 3) / (img.height() + 20)))
            else:
                self.canvas.yview_moveto(0)

    def goto_page(self, delta, absolute=False):
        c = self.cur
        if not c or c["ext"] != ".pdf":
            return
        try:
            p = int(self.var_page.get()) if absolute else c["page"] + delta
        except ValueError:
            return
        n = c["pages"] or 1
        p = max(1, min(n, p))
        if p != c["page"]:
            c["page"] = p
            self.show_page()

    def zoom_by(self, f):
        if not self.cur:
            return
        if self.zoom is None:
            r = self.get_doc(self.cur["path"])[self.cur["page"] - 1].rect
            self.zoom = max(200, self.canvas.winfo_width() - 20) / r.width
        self.zoom *= f
        self.show_page(keep_scroll=False)

    def zoom_fit(self):
        self.zoom = None
        self.show_page()

    def on_wheel(self, e):
        if e.state & 0x0004:  # Ctrl
            self.zoom_by(1.15 if e.delta > 0 else 1 / 1.15)
        else:
            self.canvas.yview_scroll(int(-e.delta / 40), "units")

    def on_canvas_resize(self, _e):
        if self.zoom is None and self.cur:
            if self._resize_job:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after(250, lambda: self.show_page(keep_scroll=True))

    # ------------------------------------------------------------ hành động
    def _current_hit(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid.startswith("f"):
            kids = self.tree.get_children(iid)
            iid = kids[0] if kids else None
        return self.hits[int(iid[1:])] if iid else None

    def open_current(self):
        if not self.cur:
            return
        page = self.cur["page"] if self.cur["ext"] == ".pdf" else 1
        try:
            v = core.open_external(self.cur["path"], page, self.cfg.get("viewer", "auto"))
            if v == "default" and self.cur["ext"] == ".pdf":
                self.lbl_run.configure(text=f"Đã mở file — hãy tự chuyển đến trang {page} "
                                            "(cài SumatraPDF để tự nhảy trang)")
        except Exception as e:
            messagebox.showerror(core.APP_NAME, f"Không mở được file: {e}")

    def open_current_folder(self):
        if self.cur:
            core.open_folder(self.cur["path"])

    def copy_path(self):
        h = self._current_hit()
        if h:
            self.clipboard_clear()
            self.clipboard_append(h.path)

    def copy_line(self):
        h = self._current_hit()
        if h:
            self.clipboard_clear()
            self.clipboard_append(f"{h.rel} | trang {h.page} | dòng {h.line} | {h.text}")

    def on_tree_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(e.x_root, e.y_root)

    def export_csv(self):
        if not self.hits:
            messagebox.showinfo(core.APP_NAME, "Chưa có kết quả để xuất.")
            return
        p = filedialog.asksaveasfilename(title="Lưu kết quả", defaultextension=".csv",
                                         filetypes=[("CSV (mở bằng Excel)", "*.csv")],
                                         initialfile="ket_qua_tra_cuu.csv")
        if not p:
            return
        for s in self.cfg.get("sources", []):
            if core.is_inside(p, s):
                messagebox.showerror(core.APP_NAME, "Không lưu vào thư mục hồ sơ. Hãy chọn chỗ khác.")
                return
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Thư mục", "File", "Trang/Sheet", "Dòng", "Nguồn", "Nội dung", "Đường dẫn"])
            for h in self.hits:
                w.writerow([os.path.dirname(h.rel), h.name, h.label if h.kind == "sheet" else h.page,
                            h.line, KIND_VN.get(h.kind, h.kind), h.text, h.path])
        self.lbl_run.configure(text=f"Đã lưu {p}")

    # ------------------------------------------------------------ cài đặt
    def pick_tess(self):
        p = filedialog.askopenfilename(title="Chọn tesseract.exe",
                                       filetypes=[("tesseract", "tesseract.exe"), ("Tất cả", "*.*")])
        if p:
            self.var_tess.set(os.path.normpath(p))
            self.check_tess()

    def _collect_settings(self):
        cfg = dict(self.cfg)
        cfg["ocr_enabled"] = self.var_ocr.get()
        cfg["tesseract_path"] = self.var_tess.get().strip()
        cfg["ocr_lang"] = self.var_lang.get().strip() or "vie"
        for key, var, lo, hi in (("workers", self.var_workers, 1, 64), ("ocr_max_px", self.var_maxpx, 1500, 8000),
                                 ("min_text_chars", self.var_minc, 0, 100000)):
            try:
                cfg[key] = max(lo, min(hi, int(var.get())))
            except ValueError:
                pass
        try:
            cfg["ocr_scale"] = max(1.0, min(3.0, float(self.var_scale.get().replace(",", "."))))
        except ValueError:
            pass
        cfg["ocr_model"] = next((k for k, v in QUALITY_CHOICES.items() if v == self.var_model.get()), "fast")
        cfg["ocr_psm"] = int(self.var_psm.get().split()[0])
        rev = {v: k for k, v in core.VIEWER_NAMES.items()}
        cfg["viewer"] = rev.get(self.var_viewer.get(), "auto")
        return cfg

    def check_tess(self):
        ok, msg = core.tesseract_info(self._collect_settings())
        self.lbl_tess.configure(text=("✔ " if ok else "✖ ") + msg,
                                foreground="#15803d" if ok else "#b91c1c")

    def save_settings(self):
        self.cfg = self._collect_settings()
        core.save_config(self.cfg)
        self.check_tess()
        msg = "Đã lưu cài đặt."
        if self.indexer:
            msg += "\nThay đổi về OCR sẽ áp dụng từ lần lập chỉ mục sau."
        messagebox.showinfo(core.APP_NAME, msg)

    # ------------------------------------------------------------ thoát
    def on_close(self):
        self._reocr_stop = True
        if self.indexer:
            if not messagebox.askyesno(core.APP_NAME, "Đang lập chỉ mục. Dừng và thoát?\n"
                                                      "(Lần sau sẽ chạy tiếp từ chỗ dừng)"):
                return
            self.indexer.stop()
            self.indexer.join(timeout=5)
        for d in self.doc_cache.values():
            try:
                d.close()
            except Exception:
                pass
        try:
            self.con.close()
        except Exception:
            pass
        self.destroy()


def main():
    try:
        app = App()
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            core.ensure_dirs()
            with open(os.path.join(core.DATA_DIR, "loi.txt"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(core.APP_NAME, err[-1500:])
        except Exception:
            print(err)


if __name__ == "__main__":
    main()

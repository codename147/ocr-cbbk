# -*- coding: utf-8 -*-
"""Hộp thoại xuất Markdown cho AI + xuất kết quả tìm kiếm. Chỉ ĐỌC chỉ mục / file gốc."""
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import core
import export_md as E

RAW_ITEM = ("raw", "Toàn bộ OCR: mọi trang, mọi dòng, nguyên văn (không lọc)")
CLEAN_ITEM = ("rawclean", "Toàn bộ OCR đã làm sạch: sửa dấu từ khóa, bỏ rác, bảng THKL chuẩn")
MODE_ITEMS = [("gon", "Gọn (khuyên dùng): bỏ rác OCR, gộp dòng bảng"),
              ("ratgon", "Rất gọn: bỏ cả các dòng chỉ có số"),
              ("full", "Đầy đủ: giữ nguyên chữ OCR"),
              RAW_ITEM, CLEAN_ITEM]
TYPE_ITEMS = [("auto", "Tự đoán theo tên file"), ("text", "Quyển văn bản (thuyết minh, hồ sơ mời thầu…)"),
              ("draw", "Quyển bản vẽ (danh mục + bảng khối lượng)")]


def default_out_dir():
    return os.path.join(core.DATA_DIR, "export_md")


def check_out_dir(cfg, path):
    for s in cfg.get("sources", []):
        if core.is_inside(path, s):
            return "Không xuất vào thư mục hồ sơ gốc. Hãy chọn thư mục khác."
    return None


class ExportDialog(tk.Toplevel):
    def __init__(self, app, file_ids, preset=None):
        super().__init__(app)
        self.app, self.ids = app, file_ids
        self.title("Xuất Markdown cho AI")
        self.transient(app)
        self.resizable(False, False)
        self.q = queue.Queue()
        self.stop_flag = False
        self.worker = None
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Đã chọn {len(file_ids)} file. Văn bản đã OCR sẽ được lọc rác, bỏ phần lặp "
                          "(khung tên, đầu/chân trang) rồi ghi ra .md nhẹ.", wraplength=560).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.v_mode = tk.StringVar(value=(CLEAN_ITEM[1] if preset == "raw" else MODE_ITEMS[0][1]))
        self.v_type = tk.StringVar(value=TYPE_ITEMS[0][1])
        self.v_split = tk.IntVar(value=800 if preset == "raw" else 300)
        self.v_out = tk.StringVar(value=app.cfg.get("export_dir") or default_out_dir())
        r = 1
        for lab, var, items in (("Chế độ", self.v_mode, MODE_ITEMS), ("Loại quyển", self.v_type, TYPE_ITEMS)):
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", pady=(10, 0))
            ttk.Combobox(f, textvariable=var, values=[i[1] for i in items], state="readonly", width=58).grid(
                row=r, column=1, columnspan=2, sticky="we", pady=(10, 0), padx=6)
            r += 1
        ttk.Label(f, text="Cắt file mỗi (KB)").grid(row=r, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(f, from_=100, to=900, increment=50, textvariable=self.v_split, width=8).grid(
            row=r, column=1, sticky="w", pady=(10, 0), padx=6)
        r += 1
        ttk.Label(f, text="Thư mục xuất").grid(row=r, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(f, textvariable=self.v_out, width=52).grid(row=r, column=1, sticky="we", pady=(10, 0), padx=6)
        ttk.Button(f, text="Chọn…", command=self.pick).grid(row=r, column=2, pady=(10, 0))
        r += 1
        self.pb = ttk.Progressbar(f, mode="determinate")
        self.pb.grid(row=r, column=0, columnspan=3, sticky="we", pady=(14, 2))
        r += 1
        self.lbl = ttk.Label(f, text="", wraplength=560)
        self.lbl.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.txt = tk.Text(f, height=9, width=78, state="disabled", wrap="none")
        self.txt.grid(row=r, column=0, columnspan=3, sticky="we", pady=(6, 0))
        r += 1
        bt = ttk.Frame(f)
        bt.grid(row=r, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.b_run = ttk.Button(bt, text="Xuất", command=self.run)
        self.b_run.pack(side="left")
        self.b_open = ttk.Button(bt, text="Mở thư mục", command=self.open_dir, state="disabled")
        self.b_open.pack(side="left", padx=6)
        ttk.Button(bt, text="Đóng", command=self.close).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self.poll)

    def pick(self):
        p = filedialog.askdirectory(title="Chọn thư mục xuất", initialdir=self.v_out.get() or None)
        if p:
            self.v_out.set(p)

    def _val(self, var, items):
        return next((k for k, t in items if t == var.get()), items[0][0])

    def log(self, s):
        self.txt.configure(state="normal")
        self.txt.insert("end", s + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def run(self):
        out = self.v_out.get().strip()
        if not out:
            return
        err = check_out_dir(self.app.cfg, out)
        if err:
            messagebox.showerror(core.APP_NAME, err, parent=self)
            return
        mode, btype = self._val(self.v_mode, MODE_ITEMS), self._val(self.v_type, TYPE_ITEMS)
        split = max(100, int(self.v_split.get() or 300))
        self.app.cfg["export_dir"] = out
        self.b_run.configure(state="disabled")
        self.stop_flag = False
        self.pb.configure(value=0, maximum=max(1, len(self.ids)))

        def work():
            con = core.connect()
            try:
                cb = lambda i, n, rel: self.q.put(("p", i, n, rel))
                if mode in ("raw", "rawclean"):
                    res = E.export_raw_files(con, self.ids, out, split_kb=split, progress=cb,
                                             stop=lambda: self.stop_flag, clean=(mode == "rawclean"))
                else:
                    res = E.export_files(con, self.ids, out, mode=mode, split_kb=split, book_type=btype,
                                         progress=cb, stop=lambda: self.stop_flag)
                self.q.put(("done", res))
            except Exception as e:
                self.q.put(("err", str(e)))
            finally:
                con.close()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "p":
                    self.pb.configure(value=m[1])
                    self.lbl.configure(text=f"Đang xuất {m[1] + 1}/{m[2]}: {os.path.basename(m[3])}")
                elif m[0] == "done":
                    self.pb.configure(value=self.pb["maximum"])
                    tr = to = 0
                    for r in m[1]:
                        tr += r["raw"]
                        to += r["out"]
                        kind = {"draw": "bản vẽ", "raw": "toàn bộ OCR", "rawclean": "OCR đã làm sạch"}.get(r["btype"], "văn bản")
                        extra = f"{r.get('lines', 0)} dòng" if r["btype"] in ("raw", "rawclean") else f"{r['flag']} trang ⚠"
                        if r["btype"] == "rawclean":
                            s2 = r["stat"]
                            extra += f", bỏ {s2['dropped']} dòng rác, sửa dấu {s2['fix']} chỗ, {s2['thkl']} bảng THKL"
                        self.log(f"{os.path.basename(r['rel'])[:48]} [{kind}]: {r['raw'] // 1024} → {r['out'] // 1024} KB"
                                 f" · {r['pages']} trang, {extra}")
                    self.log(f"TỔNG: {tr // 1024} KB chữ OCR → {to // 1024} KB .md")
                    self.lbl.configure(text="Xong. File .md lưu trong thư mục xuất (UTF-8, mở bằng Notepad/VS Code đều đúng tiếng Việt).")
                    self.b_run.configure(state="normal")
                    self.b_open.configure(state="normal")
                elif m[0] == "err":
                    self.lbl.configure(text="Lỗi: " + m[1])
                    self.b_run.configure(state="normal")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self.poll)

    def open_dir(self):
        d = self.v_out.get()
        try:
            if core.IS_WIN:
                os.startfile(d)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", d])
        except Exception:
            pass

    def close(self):
        self.stop_flag = True
        self.destroy()


def export_hits_md(app):
    if not app.hits:
        messagebox.showinfo(core.APP_NAME, "Chưa có kết quả tìm kiếm.")
        return
    p = filedialog.asksaveasfilename(title="Lưu các trang kết quả (.md)", defaultextension=".md",
                                     filetypes=[("Markdown", "*.md")], initialfile="ket_qua_tra_cuu.md",
                                     initialdir=default_out_dir() if os.path.isdir(default_out_dir()) else None)
    if not p:
        return
    err = check_out_dir(app.cfg, p)
    if err:
        messagebox.showerror(core.APP_NAME, err)
        return
    E.export_hits(app.con, app.hits, p, query=app.var_q.get() if hasattr(app, "var_q") else "")
    app.lbl_run.configure(text=f"Đã lưu {p}")


def export_hits_pdf(app):
    if not app.hits:
        messagebox.showinfo(core.APP_NAME, "Chưa có kết quả tìm kiếm.")
        return
    pm = {}
    for h in app.hits:
        if h.ext == ".pdf":
            pm.setdefault(h.path, set()).add(h.page)
    n = sum(len(v) for v in pm.values())
    if not n:
        messagebox.showinfo(core.APP_NAME, "Kết quả không có trang PDF nào.")
        return
    p = filedialog.asksaveasfilename(title=f"Trích {n} trang ra PDF mới", defaultextension=".pdf",
                                     filetypes=[("PDF", "*.pdf")], initialfile="trang_ket_qua.pdf")
    if not p:
        return
    err = check_out_dir(app.cfg, p)
    if err:
        messagebox.showerror(core.APP_NAME, err)
        return
    try:
        k = E.export_pdf_pages({a: sorted(b) for a, b in pm.items()}, p)
        app.lbl_run.configure(text=f"Đã trích {k} trang vào {p}")
    except Exception as e:
        messagebox.showerror(core.APP_NAME, str(e))


class DoubtDialog(tk.Toplevel):
    """Danh sách số liệu OCR nghi sai: xem ảnh, nhập tay giá trị đúng hoặc chọn bỏ qua."""

    def __init__(self, app, file_ids):
        super().__init__(app)
        self.app, self.ids = app, file_ids
        self.title("Kiểm tra số liệu OCR nghi sai")
        self.geometry("1100x720")
        self.transient(app)
        self.items = {}       # iid -> dict
        self.doc = None
        self._img = None
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        self.v_show = tk.StringVar(value="Chưa xử lý")
        ttk.Label(top, text="Hiện:").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.v_show, values=["Chưa xử lý", "Đã nhập tay", "Đã bỏ qua", "Tất cả"],
                          state="readonly", width=12)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        self.lbl = ttk.Label(top, text="Đang phân tích…", style="Muted.TLabel")
        self.lbl.pack(side="left", padx=10)
        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10)
        fr = ttk.Frame(pw)
        cols = ("file", "page", "line", "why", "text", "state")
        self.tv = ttk.Treeview(fr, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("file", "File", 170), ("page", "Trang", 55), ("line", "Dòng", 55), ("why", "Lý do", 190),
                        ("text", "Nội dung OCR", 440), ("state", "Trạng thái", 110)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w" if c in ("file", "why", "text") else "center")
        sb = ttk.Scrollbar(fr, command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<<TreeviewSelect>>", self.on_sel)
        pw.add(fr, weight=3)
        self.cv = tk.Canvas(pw, bg="#444", height=170, highlightthickness=0)
        pw.add(self.cv, weight=2)
        bt = ttk.Frame(self, padding=(10, 8))
        bt.pack(fill="x")
        ttk.Label(bt, text="Giá trị đúng:").pack(side="left")
        self.v_fix = tk.StringVar()
        ent = ttk.Entry(bt, textvariable=self.v_fix, width=30)
        ent.pack(side="left", padx=6, fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.save())
        ttk.Button(bt, text="Lưu sửa", command=self.save).pack(side="left")
        ttk.Button(bt, text="Bỏ qua (giữ nguyên OCR)", command=self.skip).pack(side="left", padx=6)
        ttk.Button(bt, text="Hoàn tác", command=self.undo).pack(side="left")
        ttk.Button(bt, text="Mở file PDF", command=self.open_pdf).pack(side="left", padx=6)
        self.after(50, self.load)

    def load(self):
        self.all = []
        con = self.app.con
        for fid in self.ids:
            rel = con.execute("SELECT rel FROM files WHERE id=?", (fid,)).fetchone()
            path = con.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()
            if not rel:
                continue
            st = E.build_book(con, fid)["stats"]
            fx = E.load_fixes(con, fid)
            for d in st["doubts"]:
                d.update(fid=fid, rel=rel[0], path=path[0], state="todo")
                self.all.append(d)
            for (p, l), (status, fixed, orig) in fx.items():
                if status == "fixed":
                    self.all.append({"fid": fid, "rel": rel[0], "path": path[0], "page": p, "line": l,
                                     "text": fixed, "orig": orig, "why": [], "state": "fixed"})
                else:
                    self.all.append({"fid": fid, "rel": rel[0], "path": path[0], "page": p, "line": l,
                                     "text": orig or "", "why": [], "state": "skip"})
        self.all.sort(key=lambda d: (d["rel"], d["page"], d["line"]))
        self.fill()

    def fill(self):
        want = {"Chưa xử lý": ("todo",), "Đã nhập tay": ("fixed",), "Đã bỏ qua": ("skip",),
                "Tất cả": ("todo", "fixed", "skip")}[self.v_show.get()]
        self.tv.delete(*self.tv.get_children())
        self.items = {}
        VN = {"todo": "chưa xử lý", "fixed": "đã nhập tay", "skip": "đã bỏ qua"}
        for i, d in enumerate(self.all):
            if d["state"] not in want:
                continue
            iid = f"d{i}"
            self.items[iid] = d
            why = "; ".join(E.REASONS[w].split(" (")[0] for w in d["why"]) or "—"
            self.tv.insert("", "end", iid=iid, values=(os.path.basename(d["rel"])[:30], d["page"],
                                                       d["line"] or "cả bảng", why, " ".join(d["text"].split())[:160],
                                                       VN[d["state"]]))
        n_todo = sum(1 for d in self.all if d["state"] == "todo")
        self.lbl.configure(text=f"{n_todo} chỗ chưa xử lý · {sum(1 for d in self.all if d['state'] == 'fixed')} đã nhập tay · "
                                f"{sum(1 for d in self.all if d['state'] == 'skip')} đã bỏ qua")

    def cur(self):
        s = self.tv.selection()
        return (s[0], self.items[s[0]]) if s else (None, None)

    def on_sel(self, _e=None):
        iid, d = self.cur()
        if not d:
            return
        self.v_fix.set(" ".join(d["text"].split()) if d["line"] else "")
        self.show_crop(d)

    def show_crop(self, d):
        self.cv.delete("all")
        try:
            fitz = core.fitz
            if self.doc is None or self.doc[0] != d["path"]:
                self.doc = (d["path"], fitz.open(d["path"]))
            page = self.doc[1][d["page"] - 1]
            r = None
            if d["line"]:
                row = self.app.con.execute("SELECT x0,y0,x1,y1 FROM lines WHERE file_id=? AND page=? AND line=?",
                                           (d["fid"], d["page"], d["line"])).fetchone()
                if row and (row[2] or row[3]):
                    r = fitz.Rect(*row)
            W = max(300, self.cv.winfo_width() - 20)
            if r is not None:
                clip = fitz.Rect(max(0, r.x0 - 60), max(0, r.y0 - 50), min(page.rect.x1, r.x1 + 60),
                                 min(page.rect.y1, r.y1 + 50))
            else:
                clip = fitz.Rect(page.rect.x0, page.rect.y0, page.rect.x1, min(page.rect.y1, page.rect.y0 + 260))
            z = max(0.5, min(4.0, W / max(1.0, clip.width)))
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), clip=clip, alpha=False)
            import base64
            try:
                self._img = tk.PhotoImage(data=pix.tobytes("ppm"))
            except tk.TclError:
                self._img = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")))
            self.cv.create_image(10, 5, anchor="nw", image=self._img)
            if r is not None:
                self.cv.create_rectangle(10 + (r.x0 - clip.x0) * z - 2, 5 + (r.y0 - clip.y0) * z - 2,
                                         10 + (r.x1 - clip.x0) * z + 2, 5 + (r.y1 - clip.y0) * z + 2,
                                         outline="#dc2626", width=2)
        except Exception as e:
            self.cv.create_text(10, 10, anchor="nw", fill="#fca5a5", text=f"Không hiện được ảnh trang: {e}")

    def _next(self, iid):
        kids = self.tv.get_children()
        if iid in kids:
            k = kids.index(iid)
            if k + 1 < len(kids):
                self.tv.selection_set(kids[k + 1])
                self.tv.see(kids[k + 1])

    def _apply(self, d, state, fixed=None):
        con = self.app.con
        if state == "fixed":
            E.save_fix(con, d["fid"], d["page"], d["line"], d.get("orig") or d["text"], fixed, "fixed")
            d["orig"] = d.get("orig") or d["text"]
            d["text"] = fixed
        else:
            E.save_fix(con, d["fid"], d["page"], d["line"], d.get("orig") or d["text"], "", "skip")
        d["state"] = state

    def save(self):
        iid, d = self.cur()
        if not d:
            return
        v = self.v_fix.get().strip()
        if not d["line"]:
            messagebox.showinfo(core.APP_NAME, "Mục này là cả bảng: hãy mở ảnh trang để đối chiếu, "
                                               "rồi bấm 'Bỏ qua' khi đã kiểm tra xong.", parent=self)
            return
        if not v:
            return
        self._apply(d, "fixed", v)
        self.fill()
        self._next(iid)

    def skip(self):
        iid, d = self.cur()
        if d:
            self._apply(d, "skip")
            self.fill()
            self._next(iid)

    def undo(self):
        iid, d = self.cur()
        if not d or d["state"] == "todo":
            return
        E.clear_fix(self.app.con, d["fid"], d["page"], d["line"])
        self.load()

    def open_pdf(self):
        iid, d = self.cur()
        if d:
            try:
                core.open_external(d["path"], d["page"], self.app.cfg.get("viewer", "auto"))
            except Exception as e:
                messagebox.showerror(core.APP_NAME, str(e), parent=self)

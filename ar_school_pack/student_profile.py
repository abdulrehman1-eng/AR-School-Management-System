import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import db
import rbac
import reports
import theme
import results_engine

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

STATUS_COLORS = {
    "PAID": theme.SUCCESS, "ADVANCE": theme.INFO, "PARTIAL": theme.WARNING,
    "OVERDUE": theme.DANGER, "PENDING": theme.TEXT_MUTED
}


class StudentProfileWindow:
    def __init__(self, parent, user_role, current_user):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.student = None

        if not rbac.can(self.user_role, "student.view"):
            messagebox.showerror("Permission Denied",
                                  f"Role '{self.user_role}' cannot view student profiles.", parent=parent)
            return

        self.win = tk.Toplevel(parent)
        self.win.title("Student Profile")
        self.win.geometry("920x750")
        self.win.config(bg=theme.SILVER)
        self.win.transient(parent)

        self._build_ui()
        self.win.after(120, lambda: self.ent_search.focus_set())

    # ------------------------------------------------------------------
    def _build_ui(self):
        header = tk.Frame(self.win, bg=theme.NAVY, padx=20, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="🧑‍🎓  STUDENT PROFILE", font=theme.FONT_H1, bg=theme.NAVY, fg="white").pack(anchor="w")

        search_row = tk.Frame(self.win, bg=theme.SILVER, padx=16, pady=10)
        search_row.pack(fill=tk.X)
        tk.Label(search_row, text="Student ID / Roll No:", font=theme.FONT_BODY_BOLD,
                 bg=theme.SILVER).pack(side=tk.LEFT)
        self.ent_search = tk.Entry(search_row, font=theme.FONT_BODY, width=22)
        self.ent_search.pack(side=tk.LEFT, padx=8, ipady=3)
        self.ent_search.bind("<Return>", lambda e: self.search_student())
        theme.primary_button(search_row, "🔍 Search", self.search_student).pack(side=tk.LEFT)
        self.lbl_status = tk.Label(search_row, text="", font=theme.FONT_SMALL, bg=theme.SILVER, fg=theme.DANGER)
        self.lbl_status.pack(side=tk.LEFT, padx=12)

        overview = tk.Frame(self.win, bg=theme.WHITE, padx=16, pady=10, highlightbackground=theme.SILVER_BORDER,
                             highlightthickness=1)
        overview.pack(fill=tk.X, padx=16)
        self.lbl_overview = tk.Label(overview, text="Search a Student ID above to load their profile.",
                                      font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_MUTED)
        self.lbl_overview.pack(anchor="w")

        self.notebook = ttk.Notebook(self.win)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        self.tab_overview = tk.Frame(self.notebook, bg=theme.WHITE)
        self.tab_attendance = tk.Frame(self.notebook, bg=theme.WHITE)
        self.tab_results = tk.Frame(self.notebook, bg=theme.WHITE)
        self.tab_fees = tk.Frame(self.notebook, bg=theme.WHITE)
        self.tab_personal = tk.Frame(self.notebook, bg=theme.WHITE)

    def _rebuild_tabs(self):
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        for w in self.tab_overview.winfo_children():
            w.destroy()
        for w in self.tab_attendance.winfo_children():
            w.destroy()
        for w in self.tab_results.winfo_children():
            w.destroy()
        for w in self.tab_fees.winfo_children():
            w.destroy()
        for w in self.tab_personal.winfo_children():
            w.destroy()

        self.notebook.add(self.tab_overview, text="Overview")
        self._safe_build_tab(self._build_overview_tab, self.tab_overview)

        if rbac.can(self.user_role, "attendance.view"):
            self.notebook.add(self.tab_attendance, text="Attendance")
            self._safe_build_tab(self._build_attendance_tab, self.tab_attendance)

        if rbac.can(self.user_role, "results.view"):
            self.notebook.add(self.tab_results, text="Results")
            self._safe_build_tab(self._build_results_tab, self.tab_results)

        if rbac.can(self.user_role, "student.fee.view"):
            self.notebook.add(self.tab_fees, text="Fees")
            self._safe_build_tab(self._build_fees_tab, self.tab_fees)

        if rbac.can(self.user_role, "student.edit"):
            self.notebook.add(self.tab_personal, text="Personal")
            self._safe_build_tab(self._build_personal_tab, self.tab_personal)

    @staticmethod
    def _safe_build_tab(builder, tab_frame):
        """Run a tab's build function in isolation. If it raises, show an
        inline error inside that one tab instead of letting the exception
        bubble up — otherwise every tab queued *after* the failing one
        (e.g. Fees, Personal) would silently never get added to the
        notebook, which is exactly how a bug in one tab used to make
        unrelated tabs 'disappear'.
        """
        try:
            builder()
        except Exception as exc:
            for w in tab_frame.winfo_children():
                w.destroy()
            tk.Label(
                tab_frame,
                text=f"⚠ This tab could not be loaded.\n{exc}",
                font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.DANGER,
                justify="left", anchor="w", wraplength=700,
            ).pack(fill=tk.X, padx=16, pady=16)
            print(f"[student_profile] Tab build error ({builder.__name__}): {exc}")

    # ------------------------------------------------------------------
    def search_student(self):
        sid = self.ent_search.get().strip()
        self.lbl_status.config(text="")
        if not sid:
            self.lbl_status.config(text="Enter a Student ID.")
            return
        row = db.run("SELECT student_id, name, father_name, dob, phone, address, class_sec, photo_path, "
                      "prev_education, total_fee, paid_fee, status FROM students WHERE student_id=?",
                      (sid,), fetchone=True)
        if not row:
            self.student = None
            self.lbl_overview.config(text=f"⚠ No student found with ID '{sid}'.", fg=theme.DANGER)
            self.lbl_status.config(text="Not found.")
            for tab_id in self.notebook.tabs():
                self.notebook.forget(tab_id)
            return

        (s_id, name, father_name, dob, phone, address, class_sec, photo_path,
         prev_edu, total_fee, paid_fee, status) = row
        extra_keys = ["gender", "blood_group", "nationality", "religion", "mother_name", "guardian_name",
                      "guardian_cnic", "occupation", "alt_phone", "email", "current_address", "city", "area",
                      "admission_date", "academic_year", "admission_type", "emergency_contact_name",
                      "emergency_contact_phone", "emergency_relationship", "emergency_notes",
                      "admission_fee", "admission_fee_paid"]
        extra = None
        try:
            extra = db.run(
                "SELECT gender, blood_group, nationality, religion, mother_name, guardian_name, guardian_cnic, "
                "occupation, alt_phone, email, current_address, city, area, admission_date, academic_year, "
                "admission_type, emergency_contact_name, emergency_contact_phone, emergency_relationship, "
                "emergency_notes, COALESCE(admission_fee,0), COALESCE(admission_fee_paid,0) "
                "FROM student_admission_extra WHERE student_id=?", (s_id,), fetchone=True,
            )
        except Exception:
            extra = None
        
        if extra:
            extra_dict = dict(zip(extra_keys, extra))
        else:
            extra_dict = {k: (0.0 if k in ("admission_fee", "admission_fee_paid") else "") for k in extra_keys}

        self.student = {
            "student_id": s_id, "name": name, "father_name": father_name or "-", "dob": dob or "-",
            "phone": phone or "-", "address": address or "-", "class_sec": class_sec or "-",
            "photo_path": photo_path, "prev_education": prev_edu or "-",
            "total_fee": total_fee or 0.0, "paid_fee": paid_fee or 0.0, "status": status or "Active",
            **extra_dict,
        }
        self.lbl_overview.config(
            text=f"{name}   |   ID: {s_id}   |   Class: {class_sec or '-'}   |   Status: {status or 'Active'}",
            fg=theme.TEXT_DARK)
        self.lbl_status.config(text="")
        self._rebuild_tabs()

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def _build_overview_tab(self):
        s = self.student
        f = tk.Frame(self.tab_overview, bg=theme.WHITE, padx=16, pady=16)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            tk.Label(f, text="No student loaded.", bg=theme.WHITE).pack()
            return

        # Left panel: student photo (120x140) — never crashes on missing path
        left = tk.Frame(f, bg=theme.WHITE)
        left.pack(side=tk.LEFT, anchor="n", padx=(0, 18))
        photo_box = tk.Frame(left, bg="#cbd5e1", width=120, height=140,
                             highlightbackground=theme.SILVER_BORDER, highlightthickness=1)
        photo_box.pack()
        photo_box.pack_propagate(False)
        lbl_photo = tk.Label(photo_box, text="No\nPhoto", bg="#cbd5e1", fg=theme.TEXT_MUTED,
                             font=theme.FONT_SMALL)
        lbl_photo.pack(expand=True)
        try:
            from student_photos_util import apply_photo_to_label
            apply_photo_to_label(
                lbl_photo,
                s.get("photo_path"),
                size=(120, 140),
                student_id=s.get("student_id"),
                placeholder_text="No\nPhoto",
            )
        except Exception:
            pass

        # Right panel: overview fields
        right = tk.Frame(f, bg=theme.WHITE)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        adm_status = "—"
        try:
            adm = reports.get_admission_fee_status(s["student_id"])
            if adm and (adm["charged"] > 0 or adm["paid"] > 0):
                adm_status = f"Rs. {adm['paid']:,.0f} / {adm['charged']:,.0f} ({adm['status']})"
        except Exception:
            pass

        now = datetime.now()
        curr_cycle = db.run(
            "SELECT amount_due, amount_paid, status FROM fee_cycles "
            "WHERE student_id=? AND billing_month=? AND billing_year=?",
            (s["student_id"], now.month, now.year), fetchone=True
        )
        if curr_cycle:
            due, paid, st = curr_cycle
            bal = max(0.0, (due or 0) - (paid or 0))
            monthly_line = f"Rs. {paid:,.0f} / {due:,.0f} (Balance: Rs. {bal:,.0f}) [{st}]"
        else:
            monthly_line = f"No fee cycle generated for {MONTH_NAMES[now.month-1]} {now.year}"

        rows = [("Student Name", s["name"]), ("Student ID", s["student_id"]), ("Class / Section", s["class_sec"]),
                ("Father / Guardian", s["father_name"]), ("Date of Birth", s["dob"]),
                ("Admission Date", s.get("admission_date") or "-"), ("Status", s["status"]),
                ("Emergency No.", s.get("emergency_contact_phone") or "-"),
                ("Admission Fee (One-Time)", adm_status),
                ("Current Month Fee", monthly_line)]

        for i, (label, val) in enumerate(rows):
            tk.Label(right, text=f"{label}:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).grid(row=i, column=0, sticky="w", pady=4)
            tk.Label(right, text=str(val), font=theme.FONT_BODY, bg=theme.WHITE).grid(
                row=i, column=1, sticky="w", padx=12, pady=4)

    # ------------------------------------------------------------------
    # Fees Tab
    # ------------------------------------------------------------------
    def _build_fees_tab(self):
        s = self.student
        if not s:
            return

        canvas = tk.Canvas(self.tab_fees, bg=theme.WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_fees, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=theme.WHITE, padx=16, pady=12)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        now = datetime.now()
        current_year = now.year
        current_month = now.month

        # Current Month Card
        curr_cycle = db.run(
            "SELECT fee_amount, discount, previous_balance, amount_due, amount_paid, status "
            "FROM fee_cycles WHERE student_id=? AND billing_month=? AND billing_year=?",
            (s["student_id"], current_month, current_year), fetchone=True
        )

        tk.Label(scroll_frame, text=f"📅 Current Month Fee Status ({MONTH_NAMES[current_month-1]} {current_year})",
                 font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK).pack(anchor="w", pady=(0, 6))

        curr_card = tk.Frame(scroll_frame, bg=theme.WHITE, highlightbackground=theme.SILVER_BORDER, highlightthickness=1, padx=12, pady=8)
        curr_card.pack(fill=tk.X, pady=(0, 14))

        if curr_cycle:
            fee_amt, disc, prev_bal, amt_due, amt_paid, status = curr_cycle
            rem_bal = max(0.0, (amt_due or 0) - (amt_paid or 0))
            
            c_rows = [
                ("Fee Amount", f"Rs. {fee_amt:,.2f}"),
                ("Discount", f"Rs. {disc:,.2f}"),
                ("Previous Balance", f"Rs. {prev_bal:,.2f}"),
                ("Total Due", f"Rs. {amt_due:,.2f}"),
                ("Amount Paid", f"Rs. {amt_paid:,.2f}"),
                ("Remaining Balance", f"Rs. {rem_bal:,.2f}"),
                ("Current Status", status)
            ]
            for i, (lbl, val) in enumerate(c_rows):
                r, c = divmod(i, 2)
                tk.Label(curr_card, text=f"{lbl}:", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(row=r, column=c*2, sticky="w", padx=(0, 4), pady=2)
                fg_col = theme.DANGER if lbl == "Remaining Balance" and rem_bal > 0 else (theme.SUCCESS if lbl == "Current Status" and status == "PAID" else theme.TEXT_DARK)
                tk.Label(curr_card, text=val, font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=fg_col).grid(row=r, column=c*2+1, sticky="w", padx=(0, 30), pady=2)
        else:
            tk.Label(curr_card, text=f"No fee cycle generated for {MONTH_NAMES[current_month-1]} {current_year} yet.",
                     font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(anchor="w")

        # --------------------------------------------------------------
        # Fee Cycles Table (Sirf Paid / Pending / Billed Mahine)
        # --------------------------------------------------------------
        tk.Label(scroll_frame, text=f"📊 Active Fee Cycles ({current_year})",
                 font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK).pack(anchor="w", pady=(6, 6))

        cols = ("month", "fee", "due", "paid", "balance", "status")
        tree_12m = ttk.Treeview(scroll_frame, columns=cols, show="headings", height=8)
        
        headers = {"month": "Month", "fee": "Monthly Fee", "due": "Amount Due", "paid": "Paid Amount", "balance": "Balance", "status": "Status"}
        widths = {"month": 110, "fee": 100, "due": 100, "paid": 100, "balance": 100, "status": 110}

        for c in cols:
            tree_12m.heading(c, text=headers[c])
            tree_12m.column(c, width=widths[c], anchor="center")

        for st, color in STATUS_COLORS.items():
            tree_12m.tag_configure(st, foreground=color)

        tree_12m.pack(fill=tk.X, pady=(0, 14))

        # Sirf wahi records uthayega jinki entries fee_cycles db table mein mojud hain
        cycles_12m = db.run(
            "SELECT billing_month, fee_amount, amount_due, amount_paid, status "
            "FROM fee_cycles WHERE student_id=? AND billing_year=? ORDER BY billing_month ASC",
            (s["student_id"], current_year), fetchall=True
        ) or []

        if cycles_12m:
            for m_idx, fee, due, paid, st in cycles_12m:
                m_name = MONTH_NAMES[m_idx - 1] if 1 <= m_idx <= 12 else f"Month {m_idx}"
                bal = max(0.0, (due or 0) - (paid or 0))
                tree_12m.insert("", tk.END, tags=(st,),
                                values=(m_name, f"Rs. {fee:,.0f}", f"Rs. {due:,.0f}", f"Rs. {paid:,.0f}", f"Rs. {bal:,.0f}", st))
        else:
            tree_12m.insert("", tk.END, values=("No records", "—", "—", "—", "—", "NO BILLING HISTORY"))

        # Payment Receipts Logs
        tk.Label(scroll_frame, text="🧾 Payment Receipts History Logs", font=theme.FONT_H2, bg=theme.WHITE).pack(anchor="w", pady=(6, 6))
        tree_hist = ttk.Treeview(scroll_frame, columns=("date", "type", "amount", "method", "by", "desc"), show="headings", height=6)
        for c, w in [("date", 90), ("type", 110), ("amount", 90), ("method", 80), ("by", 90), ("desc", 200)]:
            tree_hist.heading(c, text=c.replace("_", " ").title())
            tree_hist.column(c, width=w, anchor="center" if c != "desc" else "w")
        tree_hist.pack(fill=tk.X, pady=(0, 10))

        history = db.run(
            "SELECT date, source_type, amount, payment_method, recorded_by, description "
            "FROM accounting_revenue WHERE student_id=? AND source_type IN ('Student Fee', 'Admission Fee') ORDER BY id DESC",
            (s["student_id"],), fetchall=True
        ) or []
        for d, src, amt, method, by, desc in history:
            tree_hist.insert("", tk.END, values=(d, src, f"Rs. {amt:,.0f}", method or "-", by or "-", desc or ""))

    # ------------------------------------------------------------------
    # Attendance helpers (personal monthly / yearly)
    # ------------------------------------------------------------------
    @staticmethod
    def _attendance_counts(student_id, year=None, month=None):
        q = "SELECT status, COUNT(*) FROM attendance WHERE student_id=?"
        params = [student_id]
        if year and month:
            q += " AND date LIKE ?"
            params.append(f"{year}-{month:02d}%")
        elif year:
            q += " AND date LIKE ?"
            params.append(f"{year}%")
        q += " GROUP BY status"
        rows = db.run(q, tuple(params), fetchall=True) or []
        counts = {"Present": 0, "Absent": 0, "Leave": 0, "Late": 0}
        for st, c in rows:
            if st in counts:
                counts[st] = int(c or 0)
        total = sum(counts.values())
        present_like = counts["Present"] + counts["Late"]
        rate = (present_like / total * 100.0) if total else 0.0
        counts["Total"] = total
        counts["Rate"] = rate
        return counts

    @staticmethod
    def _attendance_day_rows(student_id, year=None, month=None, limit=200):
        q = (
            "SELECT date, status, COALESCE(method,''), COALESCE(in_time,'') "
            "FROM attendance WHERE student_id=?"
        )
        params = [student_id]
        if year and month:
            q += " AND date LIKE ?"
            params.append(f"{year}-{month:02d}%")
        elif year:
            q += " AND date LIKE ?"
            params.append(f"{year}%")
        q += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        try:
            return db.run(q, tuple(params), fetchall=True) or []
        except Exception:
            q2 = "SELECT date, status, COALESCE(method,''), '' FROM attendance WHERE student_id=?"
            params2 = [student_id]
            if year and month:
                q2 += " AND date LIKE ?"
                params2.append(f"{year}-{month:02d}%")
            elif year:
                q2 += " AND date LIKE ?"
                params2.append(f"{year}%")
            q2 += " ORDER BY date DESC LIMIT ?"
            params2.append(limit)
            return db.run(q2, tuple(params2), fetchall=True) or []

    # ------------------------------------------------------------------
    # Attendance Tab (personal monthly + yearly + Excel/PDF export)
    # ------------------------------------------------------------------
    def _build_attendance_tab(self):
        s = self.student
        if not s:
            return

        canvas = tk.Canvas(self.tab_attendance, bg=theme.WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_attendance, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=theme.WHITE, padx=16, pady=12)
        scroll.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        now = datetime.now()
        year_var = tk.StringVar(value=str(now.year))
        month_var = tk.StringVar(value=str(now.month))
        state = {"month_counts": {}, "year_counts": {}, "day_rows": [], "monthly_rows": []}

        ctrl = tk.Frame(scroll, bg=theme.WHITE)
        ctrl.pack(fill=tk.X, pady=(0, 10))
        tk.Label(ctrl, text="Year:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        years = [str(y) for y in range(now.year, now.year - 6, -1)]
        cmb_year = ttk.Combobox(ctrl, textvariable=year_var, values=years, width=8, state="readonly")
        cmb_year.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(ctrl, text="Month:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        cmb_month = ttk.Combobox(
            ctrl, textvariable=month_var,
            values=[f"{i}" for i in range(1, 13)], width=6, state="readonly",
        )
        cmb_month.pack(side=tk.LEFT, padx=(4, 12))

        ATT_COLORS = {
            "Present": theme.SUCCESS, "Absent": theme.DANGER,
            "Leave": theme.INFO, "Late": theme.WARNING,
        }

        cards_host = tk.Frame(scroll, bg=theme.WHITE)
        cards_host.pack(fill=tk.X, pady=(0, 8))

        export_row = tk.Frame(scroll, bg=theme.WHITE)
        export_row.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            scroll, text="📅 Monthly Breakdown (selected year)",
            font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(8, 4))
        month_tree = ttk.Treeview(
            scroll,
            columns=("month", "present", "absent", "leave", "late", "total", "rate"),
            show="headings", height=8,
        )
        for c, h, w in [
            ("month", "Month", 110), ("present", "Present", 80), ("absent", "Absent", 80),
            ("leave", "Leave", 70), ("late", "Late", 70), ("total", "Total Days", 90),
            ("rate", "Attendance %", 100),
        ]:
            month_tree.heading(c, text=h)
            month_tree.column(c, width=w, anchor="center")
        month_tree.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            scroll, text="📋 Day-wise History (selected month)",
            font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(4, 4))
        day_tree = ttk.Treeview(
            scroll, columns=("date", "status", "method", "time"), show="headings", height=10,
        )
        for c, h, w in [
            ("date", "Date", 110), ("status", "Status", 90),
            ("method", "Method", 110), ("time", "In Time", 90),
        ]:
            day_tree.heading(c, text=h)
            day_tree.column(c, width=w, anchor="center")
        for st, color in ATT_COLORS.items():
            day_tree.tag_configure(st, foreground=color)
        day_tree.pack(fill=tk.X, pady=(0, 8))

        def _stat_row(parent, title, counts):
            box = tk.Frame(parent, bg=theme.WHITE, highlightbackground=theme.SILVER_BORDER,
                           highlightthickness=1, padx=10, pady=8)
            box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            tk.Label(box, text=title, font=theme.FONT_SMALL, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).pack(anchor="w")
            rate_fg = theme.SUCCESS if counts["Rate"] >= 80 else (
                theme.WARNING if counts["Rate"] >= 60 else theme.DANGER
            )
            tk.Label(
                box, text=f"{counts['Rate']:.1f}%",
                font=("Segoe UI", 18, "bold"), bg=theme.WHITE, fg=rate_fg,
            ).pack(anchor="w")
            detail = (
                f"P {counts['Present']}  ·  A {counts['Absent']}  ·  "
                f"L {counts['Leave']}  ·  Late {counts['Late']}  ·  Total {counts['Total']}"
            )
            tk.Label(box, text=detail, font=theme.FONT_SMALL, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).pack(anchor="w", pady=(2, 0))

        def refresh(_ev=None):
            for w in cards_host.winfo_children():
                w.destroy()
            try:
                y = int(year_var.get())
                m = int(month_var.get())
            except ValueError:
                y, m = now.year, now.month

            month_counts = self._attendance_counts(s["student_id"], year=y, month=m)
            year_counts = self._attendance_counts(s["student_id"], year=y)
            state["month_counts"] = month_counts
            state["year_counts"] = year_counts
            _stat_row(cards_host, f"This Month — {MONTH_NAMES[m - 1]} {y}", month_counts)
            _stat_row(cards_host, f"Yearly — {y}", year_counts)

            month_tree.delete(*month_tree.get_children())
            monthly_rows = []
            for mi in range(1, 13):
                c = self._attendance_counts(s["student_id"], year=y, month=mi)
                if c["Total"] == 0 and mi > m and y == now.year:
                    continue
                row = (
                    MONTH_NAMES[mi - 1],
                    c["Present"], c["Absent"], c["Leave"], c["Late"],
                    c["Total"], f"{c['Rate']:.1f}%",
                )
                monthly_rows.append(row)
                month_tree.insert("", tk.END, values=row)
            state["monthly_rows"] = monthly_rows

            day_tree.delete(*day_tree.get_children())
            rows = self._attendance_day_rows(s["student_id"], year=y, month=m, limit=200)
            state["day_rows"] = rows
            if not rows:
                day_tree.insert("", tk.END, values=("No records", "—", "—", "—"))
            else:
                for d, st, method, in_time in rows:
                    tag = st if st in ATT_COLORS else ""
                    day_tree.insert(
                        "", tk.END,
                        values=(d, st, method or "—", (in_time or "").strip() or "—"),
                        tags=(tag,) if tag else (),
                    )

        def do_export_excel():
            try:
                y = int(year_var.get())
                m = int(month_var.get())
            except ValueError:
                y, m = now.year, now.month
            try:
                from smart_attendance import (
                    export_personal_attendance_excel, _ask_save_path,
                )
            except Exception as exc:
                messagebox.showerror("Export", f"Could not load export module:\n{exc}", parent=self.win)
                return
            default = f"Personal_Attendance_{s['student_id']}_{y}-{m:02d}.xlsx"
            path = _ask_save_path(self.win, default, "xlsx")
            if not path:
                return
            try:
                export_personal_attendance_excel(
                    s["student_id"], s["name"], s.get("class_sec") or "",
                    y, m, state["month_counts"], state["year_counts"],
                    state["day_rows"], state["monthly_rows"], path,
                )
                messagebox.showinfo("Export Complete", f"Excel saved:\n{path}", parent=self.win)
            except Exception as exc:
                messagebox.showerror("Export Failed", str(exc), parent=self.win)

        def do_export_pdf():
            try:
                y = int(year_var.get())
                m = int(month_var.get())
            except ValueError:
                y, m = now.year, now.month
            try:
                from smart_attendance import (
                    export_personal_attendance_pdf, _ask_save_path,
                )
            except Exception as exc:
                messagebox.showerror("Export", f"Could not load export module:\n{exc}", parent=self.win)
                return
            default = f"Personal_Attendance_{s['student_id']}_{y}-{m:02d}.pdf"
            path = _ask_save_path(self.win, default, "pdf")
            if not path:
                return
            try:
                export_personal_attendance_pdf(
                    s["student_id"], s["name"], s.get("class_sec") or "",
                    y, m, state["month_counts"], state["year_counts"],
                    state["day_rows"], path,
                )
                messagebox.showinfo("Export Complete", f"PDF saved:\n{path}", parent=self.win)
            except Exception as exc:
                messagebox.showerror("Export Failed", str(exc), parent=self.win)

        theme.primary_button(ctrl, "↻ Refresh", refresh, bg=theme.SLATE).pack(side=tk.LEFT, padx=4)
        theme.primary_button(export_row, "📁 Export Excel", do_export_excel, bg=theme.SUCCESS).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(export_row, "📄 Export PDF", do_export_pdf, bg=theme.SLATE).pack(side=tk.LEFT)
        cmb_year.bind("<<ComboboxSelected>>", refresh)
        cmb_month.bind("<<ComboboxSelected>>", refresh)
        refresh()

    def _build_results_tab(self):
        s = self.student
        f = tk.Frame(self.tab_results, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return

        tk.Label(
            f, text="📊 Examination Results", font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(0, 10))

        exam_types = results_engine.exam_types_for_student(s["student_id"])
        if not exam_types:
            tk.Label(
                f, text="No exam results recorded for this student yet.",
                font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED,
            ).pack(anchor="w")
            return

        split = tk.PanedWindow(
            f, orient=tk.HORIZONTAL, bg=theme.WHITE, sashwidth=6, sashrelief="flat",
        )
        split.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------------------
        # LEFT: Single Exam View — dynamic per-exam-type marks/PASS-FAIL
        # ----------------------------------------------------------------
        left = tk.Frame(split, bg=theme.WHITE, padx=6)
        split.add(left, minsize=340)

        tk.Label(
            left, text="Single Exam View", font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(0, 6))

        exam_row = tk.Frame(left, bg=theme.WHITE)
        exam_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            exam_row, text="Exam:", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        ).pack(side=tk.LEFT)
        cmb_exam = ttk.Combobox(
            exam_row, values=exam_types, state="readonly", width=18, font=theme.FONT_SMALL,
        )
        cmb_exam.pack(side=tk.LEFT, padx=6)
        cmb_exam.set(exam_types[0])

        lbl_summary = tk.Label(
            left, text="", font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.TEXT_DARK,
            justify="left", anchor="w", wraplength=320,
        )
        lbl_summary.pack(fill=tk.X, pady=(2, 6))

        cols = ("subject", "obtained", "total", "percent", "result")
        tree = ttk.Treeview(left, columns=cols, show="headings", height=12)
        headers = {
            "subject": "Subject", "obtained": "Obtained", "total": "Total",
            "percent": "Percent", "result": "Result",
        }
        widths = {"subject": 140, "obtained": 70, "total": 60, "percent": 70, "result": 70}
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=widths[c], anchor="center")
        tree.tag_configure("PASS", foreground=theme.SUCCESS)
        tree.tag_configure("FAIL", foreground=theme.DANGER)
        tree.pack(fill=tk.BOTH, expand=True)

        def refresh_single(_ev=None):
            tree.delete(*tree.get_children())
            exam = cmb_exam.get()
            result = results_engine.compute_result(s["student_id"], exam)
            if not result:
                lbl_summary.config(text=f"No marks recorded for {exam}.", fg=theme.TEXT_MUTED)
                return
            for sub in result["subjects"]:
                tag = "PASS" if sub["pass"] else "FAIL"
                tree.insert(
                    "", tk.END, tags=(tag,),
                    values=(
                        sub["subject"], f"{sub['obtained']:.1f}", f"{sub['total']:.1f}",
                        f"{sub['percent']:.1f}%", tag,
                    ),
                )
            lbl_summary.config(
                text=(
                    f"{exam}:  {result['total_obtained']:.1f} / {result['total_marks']:.0f}  "
                    f"({result['percentage']:.1f}%)  ·  Grade {result['grade']}  ·  "
                    f"{'PASS' if result['passed'] else 'FAIL'}"
                ),
                fg=theme.SUCCESS if result["passed"] else theme.DANGER,
            )

        cmb_exam.bind("<<ComboboxSelected>>", refresh_single)
        refresh_single()

        # ----------------------------------------------------------------
        # RIGHT: Multi-Test Selection — combine tests into one PDF report
        # ----------------------------------------------------------------
        right = tk.Frame(split, bg=theme.WHITE, padx=6)
        split.add(right, minsize=260)

        tk.Label(
            right, text="Multi-Test Selection", font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(0, 6))
        tk.Label(
            right,
            text="Select two or more tests (e.g. Quiz + Midterm)\nto combine into one consolidated report.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        lst = tk.Listbox(
            right, selectmode=tk.EXTENDED, font=theme.FONT_BODY, height=10,
            bg="#f8fafc", relief="solid", bd=1, selectbackground=theme.BRAND_BLUE,
            exportselection=False,
        )
        for et in exam_types:
            lst.insert(tk.END, et)
        lst.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        def on_generate():
            selected = [lst.get(i) for i in lst.curselection()]
            self._generate_combined_results_report(selected)

        theme.primary_button(
            right, "📑 Generate Combined Report", on_generate, bg="#7c3aed",
        ).pack(fill=tk.X)

    def _generate_combined_results_report(self, exam_types):
        """Multi-test 'Generate Combined Report' action — consolidates the
        selected exam types into one result via results_engine and renders
        it to a PDF using the same marksheet layout as the rest of the app.
        """
        s = self.student
        if not s:
            return
        if not exam_types:
            messagebox.showerror(
                "Select Tests", "Select one or more tests from the list first.", parent=self.win,
            )
            return

        combined = results_engine.compute_combined_result(s["student_id"], exam_types)
        if not combined:
            messagebox.showinfo(
                "No Data", "No marks found for the selected test(s).", parent=self.win,
            )
            return

        exam_label = " + ".join(exam_types)
        safe_label = exam_label.replace(" ", "").replace("+", "-")
        default_name = f"Combined_Report_{s['student_id']}_{safe_label}.pdf"
        path = filedialog.asksaveasfilename(
            title="Save Combined Report",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.win,
        )
        if not path:
            return

        try:
            reports.generate_marksheet(
                s["student_id"], s["name"], s.get("class_sec") or "",
                combined, path, exam_label=f"Combined ({exam_label})",
            )
        except Exception as exc:
            messagebox.showerror(
                "Report Error", f"Could not generate combined report:\n{exc}", parent=self.win,
            )
            return

        try:
            db.run(
                "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
                (
                    self.current_user,
                    f"Generated combined results report for {s['student_id']} ({exam_label})",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
                commit=True,
            )
        except Exception:
            pass

        messagebox.showinfo("Report Ready", f"Combined report saved:\n{path}", parent=self.win)

    def _build_personal_tab(self):
        s = self.student
        f = tk.Frame(self.tab_personal, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return
        rows = [("Phone", s["phone"]), ("Address", s["address"]), ("Current Address", s.get("current_address") or "-"),
                ("City", s.get("city") or "-"), ("Gender", s.get("gender") or "-"),
                ("Mother's Name", s.get("mother_name") or "-"), ("Guardian Name", s.get("guardian_name") or "-")]
        for i, (label, val) in enumerate(rows):
            tk.Label(f, text=f"{label}:", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(row=i, column=0, sticky="w", pady=3)
            tk.Label(f, text=str(val), font=theme.FONT_BODY, bg=theme.WHITE).grid(row=i, column=1, sticky="w", padx=12, pady=3)


def launch_student_profile_window(parent, user_role, current_user):
    return StudentProfileWindow(parent, user_role, current_user)

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import db
import rbac
import results_engine
import reports
import theme


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
        self.win.geometry("900x720")
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
        # Tabs are (re)added in search_student() based on live rbac.can()
        # checks for the CURRENT role, never fixed at window-open time.

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
        self._build_overview_tab()

        if rbac.can(self.user_role, "attendance.view"):
            self.notebook.add(self.tab_attendance, text="Attendance")
            self._build_attendance_tab()

        if rbac.can(self.user_role, "results.view"):
            self.notebook.add(self.tab_results, text="Results")
            self._build_results_tab()

        if rbac.can(self.user_role, "student.fee.view"):
            self.notebook.add(self.tab_fees, text="Fees")
            self._build_fees_tab()

        if rbac.can(self.user_role, "student.edit"):
            self.notebook.add(self.tab_personal, text="Personal")
            self._build_personal_tab()

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
            try:
                extra_legacy = db.run(
                    "SELECT gender, blood_group, nationality, religion, mother_name, guardian_name, guardian_cnic, "
                    "occupation, alt_phone, email, current_address, city, area, admission_date, academic_year, "
                    "admission_type, emergency_contact_name, emergency_contact_phone, emergency_relationship, "
                    "emergency_notes FROM student_admission_extra WHERE student_id=?", (s_id,), fetchone=True,
                )
                if extra_legacy:
                    extra = tuple(list(extra_legacy) + [0.0, 0.0])
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
        # Quick fee snapshot for overview (full detail on Fees tab)
        adm_status = "—"
        try:
            adm = reports.get_admission_fee_status(s["student_id"])
            if adm and (adm["charged"] > 0 or adm["paid"] > 0):
                adm_status = f"Rs. {adm['paid']:,.0f} / {adm['charged']:,.0f} ({adm['status']})"
            elif float(s.get("admission_fee") or 0) > 0 or float(s.get("admission_fee_paid") or 0) > 0:
                c = float(s.get("admission_fee") or 0)
                pd = float(s.get("admission_fee_paid") or 0)
                st = "Paid" if pd >= c and c > 0 else ("Partial" if pd > 0 else "Pending")
                adm_status = f"Rs. {pd:,.0f} / {c:,.0f} ({st})"
        except Exception:
            pass
        monthly_bal = float(s.get("total_fee") or 0) - float(s.get("paid_fee") or 0)
        monthly_line = (
            f"Rs. {float(s.get('paid_fee') or 0):,.0f} / {float(s.get('total_fee') or 0):,.0f}"
            f"  (Balance: Rs. {monthly_bal:,.0f})"
        )

        rows = [("Student Name", s["name"]), ("Student ID", s["student_id"]), ("Class / Section", s["class_sec"]),
                ("Father / Guardian", s["father_name"]), ("Date of Birth", s["dob"]),
                ("Admission Date", s.get("admission_date") or "-"), ("Status", s["status"]),
                ("Emergency No.", s.get("emergency_contact_phone") or "-"),
                ("Admission Fee (One-Time)", adm_status),
                ("Monthly Fee", monthly_line)]
        for i, (label, val) in enumerate(rows):
            tk.Label(f, text=f"{label}:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).grid(row=i, column=0, sticky="w", pady=4)
            tk.Label(f, text=str(val), font=theme.FONT_BODY, bg=theme.WHITE).grid(row=i, column=1, sticky="w",
                                                                                    padx=12, pady=4)
        row_n = len(rows)
        if s["photo_path"] and os.path.isfile(s["photo_path"]):
            tk.Label(f, text=f"Photo on file: {s['photo_path']}", font=theme.FONT_SMALL, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).grid(row=row_n, column=0, columnspan=2, sticky="w", pady=(10, 0))
            row_n += 1

        # Reprint / regenerate ID card for this student
        btn_row = tk.Frame(f, bg=theme.WHITE)
        btn_row.grid(row=row_n, column=0, columnspan=2, sticky="w", pady=(16, 4))
        theme.primary_button(
            btn_row, "🪪 Generate / Reprint ID Card", self._generate_id_card, bg=theme.BRAND_BLUE,
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            btn_row, "💾 Save ID Card As…", self._save_id_card_as, bg=theme.SLATE,
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------
    def _monthly_attendance(self, s_id, ym):
        rows = db.run("SELECT date, status FROM attendance WHERE student_id=? AND date LIKE ? ORDER BY date",
                       (s_id, f"{ym}%"), fetchall=True)
        total_days = db.run("SELECT COUNT(DISTINCT date) FROM attendance WHERE date LIKE ?",
                             (f"{ym}%",), fetchone=True)[0]
        present = sum(1 for _, st in rows if st == "Present")
        absent = sum(1 for _, st in rows if st == "Absent")
        leave = sum(1 for _, st in rows if st == "Leave")
        late = sum(1 for _, st in rows if st == "Late")
        pct = (present / total_days * 100) if total_days else 0.0
        return rows, total_days, present, absent, leave, late, pct

    def _build_attendance_tab(self):
        s = self.student
        f = tk.Frame(self.tab_attendance, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return

        ctrl = tk.Frame(f, bg=theme.WHITE)
        ctrl.pack(fill=tk.X)
        tk.Label(ctrl, text="Month:", bg=theme.WHITE, font=theme.FONT_SMALL).pack(side=tk.LEFT)
        cmb_month = ttk.Combobox(ctrl, values=[f"{i:02d}" for i in range(1, 13)], width=5, state="readonly")
        cmb_month.current(datetime.now().month - 1)
        cmb_month.pack(side=tk.LEFT, padx=4)
        ent_year = tk.Entry(ctrl, width=6)
        ent_year.insert(0, str(datetime.now().year))
        ent_year.pack(side=tk.LEFT, padx=4)

        summary_lbl = tk.Label(f, text="", font=theme.FONT_BODY, bg=theme.WHITE, justify="left")
        summary_lbl.pack(anchor="w", pady=(10, 6))

        tree = ttk.Treeview(f, columns=("date", "status"), show="headings", height=10)
        tree.heading("date", text="Date")
        tree.heading("status", text="Status")
        tree.pack(fill=tk.BOTH, expand=True)

        state = {}

        def load():
            ym = f"{ent_year.get().strip()}-{cmb_month.get()}"
            rows, total_days, present, absent, leave, late, pct = self._monthly_attendance(s["student_id"], ym)
            summary_lbl.config(text=(f"{ym} — Working days: {total_days} | Present: {present} | Absent: {absent} | "
                                      f"Leave: {leave} | Late: {late} | Attendance: {pct:.1f}%"))
            tree.delete(*tree.get_children())
            for d, st in rows:
                tree.insert("", tk.END, values=(d, st))
            state["last"] = (ym, total_days, present, absent, leave, late, pct, rows)

        def export():
            if "last" not in state:
                load()
            ym, total_days, present, absent, leave, late, pct, rows = state["last"]
            out_path = os.path.join(os.getcwd(), f"Attendance_Report_{s['student_id']}_{ym}.pdf")
            reports.generate_attendance_report(s["student_id"], s["name"], s["class_sec"], ym, total_days,
                                                present, absent, leave, late, pct, rows, out_path)
            messagebox.showinfo("Report Generated", f"Saved to:\n{out_path}", parent=self.win)

        btnrow = tk.Frame(ctrl, bg=theme.WHITE)
        btnrow.pack(side=tk.LEFT, padx=10)
        theme.primary_button(btnrow, "Load", load).pack(side=tk.LEFT, padx=2)
        theme.primary_button(btnrow, "📄 Export PDF", export, bg=theme.SLATE).pack(side=tk.LEFT, padx=2)
        load()

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def _build_results_tab(self):
        s = self.student
        f = tk.Frame(self.tab_results, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return

        exam_types = results_engine.exam_types_for_student(s["student_id"])
        ctrl = tk.Frame(f, bg=theme.WHITE)
        ctrl.pack(fill=tk.X)
        tk.Label(ctrl, text="Examination:", bg=theme.WHITE, font=theme.FONT_SMALL).pack(side=tk.LEFT)
        cmb_exam = ttk.Combobox(ctrl, values=["All Exams"] + exam_types, state="readonly", width=20)
        cmb_exam.current(0)
        cmb_exam.pack(side=tk.LEFT, padx=6)

        summary_lbl = tk.Label(f, text="", font=theme.FONT_BODY_BOLD, bg=theme.WHITE)
        summary_lbl.pack(anchor="w", pady=(10, 6))

        info_lbl = tk.Label(
            f,
            text=(
                f"ID: {s['student_id']}  ·  {s['name']}  ·  "
                f"Father: {s.get('father_name') or '—'}  ·  Class: {s.get('class_sec') or '—'}  ·  "
                f"Session: {s.get('academic_year') or '—'}"
            ),
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        )
        info_lbl.pack(anchor="w", pady=(0, 6))

        tree = ttk.Treeview(f, columns=("subject", "obtained", "total", "percent", "result"),
                             show="headings", height=10)
        for c, h in [("subject", "Subject"), ("obtained", "Obtained"), ("total", "Total"),
                     ("percent", "Percent"), ("result", "Result")]:
            tree.heading(c, text=h)
            tree.column(c, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True)

        state = {}

        def load():
            exam = None if cmb_exam.get() == "All Exams" else cmb_exam.get()
            result = results_engine.compute_result(s["student_id"], exam)
            tree.delete(*tree.get_children())
            if not result:
                summary_lbl.config(text="No marks recorded yet.", fg=theme.TEXT_MUTED)
                state["last"] = None
                return
            for sub in result["subjects"]:
                tree.insert("", tk.END, values=(sub["subject"], f"{sub['obtained']:.1f}", f"{sub['total']:.1f}",
                                                 f"{sub['percent']:.1f}%", "PASS" if sub["pass"] else "FAIL"))
            summary_lbl.config(
                text=f"Total: {result['total_obtained']:.1f}/{result['total_marks']:.1f}  |  "
                     f"Percentage: {result['percentage']:.2f}%  |  Grade: {result['grade']}  |  "
                     f"{'PASS' if result['passed'] else 'FAIL'}",
                fg=theme.SUCCESS if result["passed"] else theme.DANGER)
            state["last"] = (result, exam)

            try:
                from results_window import class_rank_for_student
                rank_info = class_rank_for_student(s["student_id"], s.get("class_sec") or "", exam)
                if rank_info:
                    rank, size, pct = rank_info
                    summary_lbl.config(
                        text=summary_lbl.cget("text") + f"  |  Class Rank: {rank}/{size}"
                    )
            except Exception:
                pass

        def export():
            if "last" not in state or not state["last"]:
                load()
            if not state.get("last"):
                messagebox.showinfo("No Data", "No marks to export.", parent=self.win)
                return
            result, exam = state["last"]
            out_path = os.path.join(os.getcwd(), f"Marksheet_{s['student_id']}.pdf")
            reports.generate_marksheet(s["student_id"], s["name"], s["class_sec"], result, out_path,
                                        exam_label=exam or "All Exams")
            messagebox.showinfo("Marksheet Generated", f"Saved to:\n{out_path}", parent=self.win)

        def open_full_results():
            try:
                from results_window import launch_results_window
                launch_results_window(self.win, self.user_role, self.current_user)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open Results module:\n{e}", parent=self.win)

        cmb_exam.bind("<<ComboboxSelected>>", lambda e: load())
        theme.primary_button(ctrl, "🧾 Export Marksheet PDF", export, bg=theme.SLATE).pack(side=tk.LEFT, padx=10)
        if rbac.can(self.user_role, "results.marks.edit") or rbac.can(self.user_role, "results.view"):
            theme.primary_button(ctrl, "📊 Open Full Results Module", open_full_results, bg=theme.BRAND_BLUE).pack(
                side=tk.LEFT, padx=4
            )
        load()

    def _build_fees_tab(self):
        s = self.student
        f = tk.Frame(self.tab_fees, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return

        # ---- Monthly Fee (from students.total_fee / paid_fee) ----
        monthly_charged = float(s.get("total_fee") or 0)
        monthly_paid = float(s.get("paid_fee") or 0)
        monthly_balance = monthly_charged - monthly_paid
        if monthly_charged <= 0:
            monthly_status = "—"
        elif monthly_balance <= 0:
            monthly_status = "Paid"
        elif monthly_paid > 0:
            monthly_status = "Partial / Pending"
        else:
            monthly_status = "Pending"

        # ---- One-time Admission Fee (ledger first, then extra columns) ----
        adm = None
        try:
            adm = reports.get_admission_fee_status(s["student_id"])
        except Exception:
            adm = None
        if not adm:
            charged = float(s.get("admission_fee") or 0)
            paid_adm = float(s.get("admission_fee_paid") or 0)
            if charged > 0 or paid_adm > 0:
                if paid_adm >= charged and charged > 0:
                    st = "Paid"
                elif paid_adm > 0:
                    st = "Partial"
                else:
                    st = "Pending"
                adm = {
                    "charged": charged,
                    "paid": paid_adm,
                    "status": st,
                    "pending": max(0.0, charged - paid_adm),
                }

        # Section: Admission Fee (One-Time)
        tk.Label(f, text="Admission Fee (One-Time)", font=theme.FONT_H2, bg=theme.WHITE).pack(
            anchor="w", pady=(0, 4)
        )
        if adm and (adm["charged"] > 0 or adm["paid"] > 0):
            adm_rows = [
                ("Charged", f"Rs. {adm['charged']:,.2f}", theme.TEXT_DARK),
                ("Paid", f"Rs. {adm['paid']:,.2f}", theme.SUCCESS),
                ("Pending", f"Rs. {adm['pending']:,.2f}",
                 theme.DANGER if adm["pending"] > 0 else theme.SUCCESS),
                ("Status", adm["status"], theme.SUCCESS if adm["status"] == "Paid" else theme.DANGER),
            ]
        else:
            adm_rows = [
                ("Charged", "Rs. 0.00", theme.TEXT_MUTED),
                ("Paid", "Rs. 0.00", theme.TEXT_MUTED),
                ("Status", "Not charged", theme.TEXT_MUTED),
            ]
        for label, val, color in adm_rows:
            row = tk.Frame(f, bg=theme.WHITE)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label, font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(side=tk.LEFT)
            tk.Label(row, text=val, font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=color).pack(side=tk.RIGHT)

        # Section: Monthly Fee
        tk.Label(f, text="Monthly Fee", font=theme.FONT_H2, bg=theme.WHITE).pack(
            anchor="w", pady=(14, 4)
        )
        for label, val, color in [
            ("Charged (Monthly)", f"Rs. {monthly_charged:,.2f}", theme.TEXT_DARK),
            ("Paid to Date", f"Rs. {monthly_paid:,.2f}", theme.SUCCESS),
            ("Outstanding Balance", f"Rs. {monthly_balance:,.2f}",
             theme.DANGER if monthly_balance > 0 else theme.SUCCESS),
            ("Status", monthly_status,
             theme.SUCCESS if monthly_status == "Paid" else theme.DANGER),
        ]:
            row = tk.Frame(f, bg=theme.WHITE)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label, font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(side=tk.LEFT)
            tk.Label(row, text=val, font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=color).pack(side=tk.RIGHT)

        # Combined revenue summary for this student
        try:
            fee_rev = db.run(
                "SELECT COALESCE(SUM(amount),0) FROM accounting_revenue "
                "WHERE student_id=? AND source_type='Student Fee'",
                (s["student_id"],), fetchone=True,
            )[0]
            adm_rev = db.run(
                "SELECT COALESCE(SUM(amount),0) FROM accounting_revenue "
                "WHERE student_id=? AND source_type='Admission Fee'",
                (s["student_id"],), fetchone=True,
            )[0]
        except Exception:
            fee_rev, adm_rev = 0.0, 0.0
        tk.Label(f, text="Revenue recorded (Accounting)", font=theme.FONT_H2, bg=theme.WHITE).pack(
            anchor="w", pady=(14, 4)
        )
        for label, val, color in [
            ("Monthly Fee Revenue", f"Rs. {float(fee_rev):,.2f}", theme.TEXT_DARK),
            ("Admission Fee Revenue", f"Rs. {float(adm_rev):,.2f}", theme.TEXT_DARK),
            ("Total Fee Revenue", f"Rs. {float(fee_rev) + float(adm_rev):,.2f}", theme.BRAND_BLUE),
        ]:
            row = tk.Frame(f, bg=theme.WHITE)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label, font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(side=tk.LEFT)
            tk.Label(row, text=val, font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=color).pack(side=tk.RIGHT)

        tk.Label(f, text="Payment History (Monthly + Admission Fee)", font=theme.FONT_H2,
                 bg=theme.WHITE).pack(anchor="w", pady=(14, 6))
        tree = ttk.Treeview(
            f,
            columns=("date", "type", "amount", "method", "recorded_by", "description"),
            show="headings",
            height=10,
        )
        for c, w in [
            ("date", 90), ("type", 110), ("amount", 90),
            ("method", 80), ("recorded_by", 100), ("description", 220),
        ]:
            tree.heading(c, text=c.replace("_", " ").title())
            tree.column(c, width=w, anchor="center" if c != "description" else "w")
        tree.pack(fill=tk.BOTH, expand=True)
        history = db.run(
            "SELECT date, source_type, amount, payment_method, recorded_by, description "
            "FROM accounting_revenue "
            "WHERE student_id=? AND source_type IN ('Student Fee', 'Admission Fee') "
            "ORDER BY id DESC",
            (s["student_id"],),
            fetchall=True,
        )
        for d, src, amt, method, by, desc in history:
            type_label = "Admission Fee" if src == "Admission Fee" else "Monthly Fee"
            tree.insert(
                "", tk.END,
                values=(d, type_label, f"Rs. {amt:,.0f}", method or "-", by or "-", desc or ""),
            )

    # ------------------------------------------------------------------
    # Personal
    # ------------------------------------------------------------------
    def _build_personal_tab(self):
        s = self.student
        f = tk.Frame(self.tab_personal, bg=theme.WHITE, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        if not s:
            return
        rows = [("Phone", s["phone"]), ("Address", s["address"]), ("Current Address", s.get("current_address") or "-"),
                ("City", s.get("city") or "-"), ("Gender", s.get("gender") or "-"),
                ("Blood Group", s.get("blood_group") or "-"), ("Mother's Name", s.get("mother_name") or "-"),
                ("Guardian Name", s.get("guardian_name") or "-"), ("Guardian CNIC", s.get("guardian_cnic") or "-"),
                ("Occupation", s.get("occupation") or "-"), ("Alternate Contact", s.get("alt_phone") or "-"),
                ("Email", s.get("email") or "-"), ("Previous Education", s["prev_education"]),
                ("Emergency Contact", s.get("emergency_contact_name") or "-"),
                ("Emergency Phone", s.get("emergency_contact_phone") or "-"),
                ("Emergency Relationship", s.get("emergency_relationship") or "-"),
                ("Emergency Notes", s.get("emergency_notes") or "-")]
        for i, (label, val) in enumerate(rows):
            r, c = divmod(i, 2)
            tk.Label(f, text=f"{label}:", font=theme.FONT_SMALL, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).grid(row=r, column=c * 2, sticky="w", padx=(0, 4), pady=4)
            tk.Label(f, text=str(val), font=theme.FONT_BODY, bg=theme.WHITE).grid(row=r, column=c * 2 + 1,
                                                                                    sticky="w", padx=(0, 30), pady=4)



    # ------------------------------------------------------------------
    # ID Card regenerate / reprint
    # ------------------------------------------------------------------
    def _id_card_args(self):
        s = self.student
        if not s:
            messagebox.showinfo("No Student", "Search and load a student first.", parent=self.win)
            return None
        return dict(
            student_id=s["student_id"],
            name=s["name"],
            cls=s["class_sec"],
            father_name=s.get("father_name") or "",
            phone=s.get("phone") or "",
            photo_path=s.get("photo_path") or None,
            emergency_phone=s.get("emergency_contact_phone") or "",
            session=s.get("academic_year") or "",
        )

    def _generate_id_card(self):
        args = self._id_card_args()
        if not args:
            return
        out_path = os.path.join(os.getcwd(), f"ID_Card_{args['student_id']}.pdf")
        try:
            reports.generate_id_card(
                args["student_id"], args["name"], args["cls"], out_path,
                father_name=args["father_name"], phone=args["phone"],
                photo_path=args["photo_path"], emergency_phone=args["emergency_phone"],
                session=args["session"],
            )
        except Exception as e:
            messagebox.showerror("ID Card Error", f"Could not generate ID card:\n{e}", parent=self.win)
            return
        # Try to open for print/preview
        opened = False
        try:
            if os.name == "nt":
                os.startfile(out_path)
                opened = True
            else:
                import shutil
                if shutil.which("xdg-open"):
                    os.system(f'xdg-open "{out_path}"')
                    opened = True
                elif shutil.which("open"):
                    os.system(f'open "{out_path}"')
                    opened = True
        except Exception:
            opened = False
        try:
            import app as _app
            _app.log_activity(self.current_user, f"Regenerated ID card for {args['student_id']}")
        except Exception:
            pass
        messagebox.showinfo(
            "ID Card Ready",
            (f"ID Card opened:\n{out_path}" if opened else f"ID Card saved:\n{out_path}"),
            parent=self.win,
        )

    def _save_id_card_as(self):
        args = self._id_card_args()
        if not args:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=f"ID_Card_{args['student_id']}.pdf",
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.win,
        )
        if not path:
            return
        try:
            reports.generate_id_card(
                args["student_id"], args["name"], args["cls"], path,
                father_name=args["father_name"], phone=args["phone"],
                photo_path=args["photo_path"], emergency_phone=args["emergency_phone"],
                session=args["session"],
            )
        except Exception as e:
            messagebox.showerror("ID Card Error", f"Could not generate ID card:\n{e}", parent=self.win)
            return
        try:
            import app as _app
            _app.log_activity(self.current_user, f"Saved ID card for {args['student_id']} to {path}")
        except Exception:
            pass
        messagebox.showinfo("Saved", f"ID Card saved:\n{path}", parent=self.win)


def launch_student_profile_window(parent, user_role, current_user):
    return StudentProfileWindow(parent, user_role, current_user)

"""Teacher & Payroll — the single, authoritative Teacher & Payroll screen.

This module owns Teacher registration, the Teachers directory, daily
teacher attendance, and salary/payslip generation — the same
functionality that used to live inline as a tab in app.py.

Everything that touches the database, RBAC, accounting, or reports is
kept byte-for-byte identical in behaviour to the original app.py code
(same SQL, same tables/columns, same rbac.can(...) permission strings,
same accounting.record_salary_expense(...) / reports.generate_payslip(...)
calls, same audit log messages). Only the presentation layer is new —
this file follows the same "modern popup window" pattern already used by
student_admission.py, student_fee_collection.py, student_profile.py, and
smart_attendance.py, and is opened the same way, via a
launch_teacher_payroll_window(root, user_role, current_user) function.
"""

import os
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import db
import rbac
import theme
import reports
import accounting

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def log_activity(username, action):
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            commit=True,
        )
    except Exception as e:
        print(f"Audit Log Error: {e}")


def generate_next_teacher_id():
    row = db.run("SELECT teacher_id FROM teachers ORDER BY ROWID DESC LIMIT 1", fetchone=True)
    if row and row[0].startswith("TCH-"):
        try:
            new_num = int(row[0].split("-")[-1]) + 1
        except ValueError:
            new_num = 1
    else:
        new_num = 1
    return f"TCH-{new_num:03d}"


def safe_float(raw_text, field_label, default=None):
    """Same friendly-error numeric parser used across the app."""
    text = (raw_text or "").strip()
    if not text:
        if default is not None:
            return default, True
        messagebox.showerror("Invalid Input", f"{field_label} is required.")
        return None, False
    try:
        return float(text), True
    except ValueError:
        messagebox.showerror("Invalid Input", f"{field_label} must be a valid number (e.g. 1500 or 1500.50).")
        return None, False


def launch_teacher_payroll_window(root, user_role, current_user):
    """Open the Teacher & Payroll window.

    Mirrors the permission gate that used to guard the sidebar item:
    visible if the user can view teachers OR mark teacher attendance.
    """
    if not (rbac.can(user_role, "teacher.view") or rbac.can(user_role, "teacher.attendance.mark")):
        messagebox.showerror("Permission Denied", "You are not allowed to access Teachers & Payroll.")
        return None
    return TeacherPayrollWindow(root, user_role, current_user)


class TeacherPayrollWindow:
    NAVY = "#0f172a"
    CARD = "#ffffff"
    BORDER = "#e2e8f0"
    BG = "#f8fafc"
    MUTED = "#64748b"
    BLUE = "#2563eb"
    GREEN = "#16a34a"
    PURPLE = "#7c3aed"
    CYAN = "#0284c7"
    RED = "#dc2626"
    AMBER = "#d97706"

    def __init__(self, root, user_role, current_user):
        self.root = root
        self.user_role = user_role
        self.current_user = current_user

        self.can_add_teacher = rbac.can(user_role, "teacher.add")
        self.can_mark_attendance = rbac.can(user_role, "teacher.attendance.mark")
        self.can_view_salary = rbac.can(user_role, "teacher.salary.view")
        self.can_pay_salary = rbac.can(user_role, "teacher.salary.pay")

        self.win = tk.Toplevel(root)
        self.win.title("Teacher & Payroll — AR School Management System")
        self.win.geometry("1180x720")
        self.win.minsize(1020, 620)
        self.win.config(bg=self.BG)

        self._prev_paid = 0  # unused placeholder kept for symmetry, not needed here

        self._build_header()
        self._build_nav()
        self._build_pages()

        self.show_page("directory")

    # ------------------------------------------------------------
    # Chrome: header + section nav
    # ------------------------------------------------------------
    def _build_header(self):
        header = tk.Frame(self.win, bg=self.NAVY, padx=22, pady=16)
        header.pack(fill=tk.X)
        tk.Label(header, text="👨‍🏫 TEACHER & PAYROLL", font=("Segoe UI", 18, "bold"),
                 bg=self.NAVY, fg="white").pack(anchor="w")
        tk.Label(header, text="Register teachers, mark daily attendance, and process salary payslips",
                 font=("Segoe UI", 9), bg=self.NAVY, fg="#cbd5e1").pack(anchor="w", pady=(3, 0))

    def _build_nav(self):
        nav = tk.Frame(self.win, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        nav.pack(fill=tk.X)
        self.nav_buttons = {}
        items = [("directory", "📋  Directory & Registration"),
                 ("attendance", "🗓️  Daily Attendance"),
                 ("payroll", "💵  Salary & Payslip")]
        for key, label in items:
            btn = tk.Button(nav, text=label, command=lambda k=key: self.show_page(k),
                             bg=self.CARD, fg=self.NAVY, activebackground="#eef2ff",
                             relief="flat", bd=0, padx=16, pady=12, font=("Segoe UI", 9, "bold"),
                             cursor="hand2")
            btn.pack(side=tk.LEFT)
            self.nav_buttons[key] = btn

    def _set_nav_active(self, key):
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.config(bg="#dbeafe", fg=self.BLUE)
            else:
                btn.config(bg=self.CARD, fg=self.NAVY)

    def _build_pages(self):
        self.pages_container = tk.Frame(self.win, bg=self.BG)
        self.pages_container.pack(fill=tk.BOTH, expand=True)
        self.pages = {}

        self.page_directory = tk.Frame(self.pages_container, bg=self.BG)
        self.page_attendance = tk.Frame(self.pages_container, bg=self.BG)
        self.page_payroll = tk.Frame(self.pages_container, bg=self.BG)
        for key, frame in [("directory", self.page_directory),
                            ("attendance", self.page_attendance),
                            ("payroll", self.page_payroll)]:
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            self.pages[key] = frame

        self._build_directory_page()
        self._build_attendance_page()
        self._build_payroll_page()

    def show_page(self, key):
        self.pages[key].tkraise()
        self._set_nav_active(key)
        if key == "directory":
            self.load_teacher_table()
        elif key == "payroll":
            self._refresh_payroll_teacher_picker()

    # ------------------------------------------------------------
    # PAGE 1: Directory & Registration
    # ------------------------------------------------------------
    def _build_directory_page(self):
        outer = self.page_directory

        form_card = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        form_card.place(x=12, y=12, width=380, relheight=1.0, height=-24)

        tk.Label(form_card, text="TEACHER REGISTRATION", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(form_card, text="Add a new teacher profile.", font=("Segoe UI", 8),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=16, pady=(0, 10))

        id_row = tk.Frame(form_card, bg=self.CARD)
        id_row.pack(fill=tk.X, padx=16, pady=(0, 10))
        tk.Label(id_row, text="Teacher ID:", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).pack(side=tk.LEFT)
        self.lbl_tch_id = tk.Label(id_row, text=generate_next_teacher_id(), font=("Segoe UI", 10, "bold"),
                                    bg=self.CARD, fg=self.CYAN)
        self.lbl_tch_id.pack(side=tk.LEFT, padx=(8, 0))

        fields = [("Teacher Name*", "ent_tch_name"), ("Designation", "ent_tch_desig"),
                  ("Phone Number", "ent_tch_phone"), ("Basic Salary (Rs)", "ent_tch_sal"),
                  ("Joining Date (YYYY-MM-DD)", "ent_tch_join")]
        for label, attr in fields:
            tk.Label(form_card, text=label, font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
                anchor="w", padx=16, pady=(4, 2))
            ent = tk.Entry(form_card, font=("Segoe UI", 10), relief="solid", bd=1)
            ent.pack(fill=tk.X, padx=16, ipady=4)
            setattr(self, attr, ent)
        if not self.can_view_salary:
            self.ent_tch_sal.config(show="•")

        btn_row = tk.Frame(form_card, bg=self.CARD)
        btn_row.pack(fill=tk.X, padx=16, pady=14)
        add_btn = tk.Button(btn_row, text="＋ Add Teacher", command=self.save_teacher, bg=self.GREEN, fg="white",
                             relief="flat", padx=10, pady=8, font=("Segoe UI", 9, "bold"), cursor="hand2")
        add_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        add_btn.config(state="normal" if self.can_add_teacher else "disabled")
        tk.Button(btn_row, text="Clear", command=self.clear_teacher_form, bg="#f1f5f9", fg=self.NAVY,
                  relief="flat", padx=10, pady=8, font=("Segoe UI", 9, "bold"), cursor="hand2").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        if not self.can_add_teacher:
            tk.Label(form_card, text="You don't have permission to add teachers.", font=("Segoe UI", 8),
                      bg=self.CARD, fg=self.RED, wraplength=340, justify="left").pack(anchor="w", padx=16, pady=(0, 10))

        # ---------------- Directory table ----------------
        right = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        right.place(x=406, y=12, relwidth=1.0, width=-418, relheight=1.0, height=-24)

        toolbar = tk.Frame(right, bg=self.CARD)
        toolbar.pack(fill=tk.X, padx=16, pady=(14, 6))
        tk.Label(toolbar, text="TEACHERS DIRECTORY", font=("Segoe UI", 10, "bold"), bg=self.CARD, fg=self.NAVY).pack(side=tk.LEFT)
        tk.Button(toolbar, text="↻ Refresh", command=self.load_teacher_table, bg="#f1f5f9", fg=self.NAVY,
                  relief="flat", padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side=tk.RIGHT)

        search_bar = tk.Frame(right, bg=self.CARD)
        search_bar.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(search_bar, text="🔍 Search:", font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED).pack(side=tk.LEFT)
        self.ent_tch_search = tk.Entry(search_bar, font=("Segoe UI", 9), relief="solid", bd=1)
        self.ent_tch_search.pack(side=tk.LEFT, padx=(6, 8), ipady=3, fill=tk.X, expand=True)
        self.ent_tch_search.bind("<KeyRelease>", lambda e: self.load_teacher_table())
        view_profile_btn = tk.Button(search_bar, text="👤 View Profile", command=self.open_teacher_profile,
                                      bg=self.BLUE, fg="white", relief="flat", padx=12, pady=4,
                                      font=("Segoe UI", 8, "bold"), cursor="hand2")
        view_profile_btn.pack(side=tk.LEFT)
        if not rbac.can(self.user_role, "teacher.view"):
            view_profile_btn.config(state="disabled")

        tk.Label(right, text="Search by Teacher ID or Name · click a row to load it into the form / attendance / payroll tabs · select a row and click 'View Profile' for full attendance & salary history.",
                 font=("Segoe UI", 8), bg=self.CARD, fg=self.MUTED, wraplength=680, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        table_frame = tk.Frame(right, bg=self.CARD)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        cols = ("id", "name", "desig", "phone", "salary", "joining") if self.can_view_salary else ("id", "name", "desig", "phone", "joining")
        headers = {"id": "Teacher ID", "name": "Name", "desig": "Designation", "phone": "Phone",
                   "salary": "Basic Salary", "joining": "Joining Date"}
        self.tree_teacher = ttk.Treeview(table_frame, columns=cols, show="headings")
        for col in cols:
            self.tree_teacher.heading(col, text=headers[col])
            self.tree_teacher.column(col, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree_teacher.yview)
        self.tree_teacher.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_teacher.pack(fill=tk.BOTH, expand=True)
        self.tree_teacher.bind("<ButtonRelease-1>", self.fill_teacher_form)
        self.tree_teacher.bind("<Double-1>", lambda e: self.open_teacher_profile())

        self.load_teacher_table()

    def save_teacher(self):
        if not rbac.can(self.user_role, "teacher.add"):
            messagebox.showerror("Permission Denied", "Not allowed to add teachers.")
            return
        t_id = self.lbl_tch_id.cget("text")
        name = self.ent_tch_name.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter Teacher Name!")
            return
        sal, ok = safe_float(self.ent_tch_sal.get(), "Basic Salary", default=0.0)
        if not ok:
            return
        db.run("INSERT INTO teachers (teacher_id, name, designation, phone, basic_salary, joining_date) VALUES (?, ?, ?, ?, ?, ?)",
               (t_id, name, self.ent_tch_desig.get(), self.ent_tch_phone.get(), sal, self.ent_tch_join.get()), commit=True)
        log_activity(self.current_user, f"Added teacher profile {name} ({t_id})")
        messagebox.showinfo("Success", f"Teacher Profile Registered: {t_id}")
        self.clear_teacher_form()
        self.load_teacher_table()

    def load_teacher_table(self):
        self.tree_teacher.delete(*self.tree_teacher.get_children())
        search = self.ent_tch_search.get().strip() if hasattr(self, "ent_tch_search") else ""
        cols_sql = "teacher_id, name, designation, phone, basic_salary, joining_date" if self.can_view_salary else "teacher_id, name, designation, phone, joining_date"
        if search:
            rows = db.run(f"SELECT {cols_sql} FROM teachers WHERE teacher_id LIKE ? OR name LIKE ?",
                           (f"%{search}%", f"%{search}%"), fetchall=True)
        else:
            rows = db.run(f"SELECT {cols_sql} FROM teachers", fetchall=True)
        for r in rows:
            self.tree_teacher.insert("", tk.END, values=r)

    def fill_teacher_form(self, ev):
        selected = self.tree_teacher.focus()
        vals = self.tree_teacher.item(selected, "values")
        if not vals:
            return
        self.lbl_tch_id.config(text=vals[0])
        self.ent_tch_name.delete(0, tk.END); self.ent_tch_name.insert(0, vals[1])
        self.ent_tch_desig.delete(0, tk.END); self.ent_tch_desig.insert(0, vals[2] or "")
        self.ent_tch_phone.delete(0, tk.END); self.ent_tch_phone.insert(0, vals[3] or "")
        if self.can_view_salary and len(vals) > 4:
            self.ent_tch_sal.delete(0, tk.END); self.ent_tch_sal.insert(0, str(vals[4]))
            self.ent_tch_join.delete(0, tk.END); self.ent_tch_join.insert(0, vals[5] or "")
        # Keep the Attendance and Payroll tabs in sync with the row picked here.
        if hasattr(self, "ent_att_tch_id"):
            self._load_teacher_into_attendance_page()
        if hasattr(self, "ent_pay_tch_id"):
            self._load_teacher_into_payroll_page()

    def clear_teacher_form(self):
        self.lbl_tch_id.config(text=generate_next_teacher_id())
        for ent in [self.ent_tch_name, self.ent_tch_desig, self.ent_tch_phone, self.ent_tch_sal, self.ent_tch_join]:
            ent.delete(0, tk.END)

    # ------------------------------------------------------------
    # PAGE 2: Daily Attendance
    # ------------------------------------------------------------
    def _build_attendance_page(self):
        outer = self.page_attendance

        top = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        top.pack(fill=tk.X, padx=12, pady=12)
        tk.Label(top, text="DAILY TEACHER ATTENDANCE", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(12, 8))

        tk.Label(top, text="Teacher ID:", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).grid(
            row=1, column=0, padx=(16, 6), pady=(0, 6), sticky="w")
        self.ent_att_tch_id = tk.Entry(top, font=("Segoe UI", 10), width=14, relief="solid", bd=1)
        self.ent_att_tch_id.grid(row=1, column=1, pady=(0, 6), sticky="w")
        self.ent_att_tch_id.bind("<Return>", lambda e: self.load_attendance_teacher())

        tk.Button(top, text="🔎 Load", command=self.load_attendance_teacher, bg="#64748b",
                  fg="white", relief="flat", padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2").grid(
            row=1, column=2, padx=(6, 16), pady=(0, 6))

        self.lbl_att_tch_name = tk.Label(top, text="Type a Teacher ID (or select one from Directory) and click Load.",
                                          font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED)
        self.lbl_att_tch_name.grid(row=2, column=0, columnspan=6, sticky="w", padx=16, pady=(0, 12))

        tk.Label(top, text="Status:", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).grid(
            row=3, column=0, padx=(16, 6), pady=(0, 12), sticky="w")
        self.combo_tch_att = ttk.Combobox(top, values=["Present", "Absent", "Late", "Leave"], width=12, state="readonly")
        self.combo_tch_att.current(0)
        self.combo_tch_att.grid(row=3, column=1, pady=(0, 12), sticky="w")

        btn_mark = tk.Button(top, text="✓ Mark Today's Attendance", command=self.mark_teacher_attendance,
                              bg=self.CYAN, fg="white", relief="flat", padx=14, pady=6,
                              font=("Segoe UI", 9, "bold"), cursor="hand2")
        btn_mark.grid(row=3, column=2, columnspan=2, padx=16, pady=(0, 12), sticky="w")
        btn_mark.config(state="normal" if self.can_mark_attendance else "disabled")

        if not self.can_mark_attendance:
            tk.Label(top, text="You don't have permission to mark teacher attendance.", font=("Segoe UI", 8),
                      bg=self.CARD, fg=self.RED).grid(row=4, column=0, columnspan=5, sticky="w", padx=16, pady=(0, 10))

        hint = tk.Frame(outer, bg=self.BG)
        hint.pack(fill=tk.X, padx=12)
        tk.Label(hint, text="Tip: type a Teacher ID directly and click 'Load', or pick a row in Directory & Registration — either way works.",
                 font=("Segoe UI", 8), bg=self.BG, fg=self.MUTED).pack(anchor="w", pady=(0, 8))

    def load_attendance_teacher(self):
        """Resolve whatever Teacher ID is typed into the Attendance tab and
        show the matching name/designation, without requiring a Directory
        selection first."""
        t_id = self.ent_att_tch_id.get().strip()
        if not t_id:
            self.lbl_att_tch_name.config(text="Enter a Teacher ID first.", fg=self.RED)
            return
        row = db.run("SELECT name, designation FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not row:
            self.lbl_att_tch_name.config(text=f"Teacher ID '{t_id}' not found.", fg=self.RED)
            return
        name, desig = row
        self.lbl_att_tch_name.config(text=f"✓ {name}  ({desig or 'Teacher'})", fg=self.GREEN)

    def _load_teacher_into_attendance_page(self):
        self.ent_att_tch_id.delete(0, tk.END)
        self.ent_att_tch_id.insert(0, self.lbl_tch_id.cget("text"))
        self.load_attendance_teacher()

    def mark_teacher_attendance(self):
        if not rbac.can(self.user_role, "teacher.attendance.mark"):
            messagebox.showerror("Permission Denied", "Not allowed to mark teacher attendance.")
            return
        t_id = self.ent_att_tch_id.get().strip()
        if not t_id:
            messagebox.showerror("Error", "Enter a Teacher ID (or select one from Directory) first.")
            return
        teacher = db.run("SELECT name FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not teacher:
            messagebox.showerror("Error", f"Teacher ID '{t_id}' not found.")
            return
        status = self.combo_tch_att.get()
        today = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M:%S")
        try:
            db.run("INSERT INTO teacher_attendance (teacher_id, date, status, in_time) VALUES (?, ?, ?, ?)",
                   (t_id, today, status, now_time), commit=True)
            messagebox.showinfo("Success", f"Teacher Attendance ({status}) Marked for {teacher[0]} ({t_id})")
        except Exception:
            messagebox.showwarning("Warning", "Today's Attendance already marked for this teacher!")

    # ------------------------------------------------------------
    # PAGE 3: Salary & Payslip
    # ------------------------------------------------------------
    def _build_payroll_page(self):
        outer = self.page_payroll

        top = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        top.pack(fill=tk.X, padx=12, pady=12)
        tk.Label(top, text="SALARY & PAYSLIP GENERATION", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(12, 8))

        if not self.can_view_salary:
            tk.Label(top, text="You don't have permission to view or process salaries.", font=("Segoe UI", 9),
                      bg=self.CARD, fg=self.RED).grid(row=1, column=0, columnspan=4, sticky="w", padx=16, pady=(0, 14))
            return

        tk.Label(top, text="Teacher ID:", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).grid(
            row=1, column=0, padx=(16, 6), pady=(0, 6), sticky="w")
        self.ent_pay_tch_id = tk.Entry(top, font=("Segoe UI", 10), width=14, relief="solid", bd=1)
        self.ent_pay_tch_id.grid(row=1, column=1, pady=(0, 6), sticky="w")
        self.ent_pay_tch_id.bind("<Return>", lambda e: self.load_payroll_teacher())

        tk.Button(top, text="🔎 Load", command=self.load_payroll_teacher, bg="#64748b", fg="white",
                  relief="flat", padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2").grid(
            row=1, column=2, padx=(6, 16), pady=(0, 6))

        self.lbl_pay_tch_name = tk.Label(top, text="Type a Teacher ID (or select one from Directory) and click Load.",
                                          font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED)
        self.lbl_pay_tch_name.grid(row=2, column=0, columnspan=6, sticky="w", padx=16, pady=(0, 6))

        tk.Label(top, text="Basic Salary (Rs):", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).grid(
            row=3, column=0, padx=(16, 6), pady=(0, 12), sticky="w")
        self.lbl_pay_basic_sal = tk.Label(top, text="—", font=("Segoe UI", 10, "bold"), bg=self.CARD, fg=self.NAVY)
        self.lbl_pay_basic_sal.grid(row=3, column=1, sticky="w", pady=(0, 12))

        btn_pay = tk.Button(top, text="💵 Calculate Salary & Export Payslip PDF",
                             command=self.generate_salary_payslip, bg=self.PURPLE, fg="white",
                             relief="flat", padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        btn_pay.grid(row=4, column=0, columnspan=3, padx=16, pady=(0, 14), sticky="w")
        btn_pay.config(state="normal" if self.can_pay_salary else "disabled")

        if not self.can_pay_salary:
            tk.Label(top, text="You can view salary details but you're not permitted to process payments.",
                      font=("Segoe UI", 8), bg=self.CARD, fg=self.AMBER).grid(
                row=5, column=0, columnspan=4, sticky="w", padx=16, pady=(0, 12))

        # ---------------- Result summary ----------------
        self.payroll_summary_card = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        self.payroll_summary_card.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Label(self.payroll_summary_card, text="LAST CALCULATION", font=("Segoe UI", 9, "bold"),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=16, pady=(9, 2))
        self.lbl_payroll_summary = tk.Label(self.payroll_summary_card,
                                             text="Type/select a teacher and use 'Calculate Salary' to see the breakdown here.",
                                             font=("Segoe UI", 10), bg=self.CARD, fg=self.NAVY, anchor="w", justify="left")
        self.lbl_payroll_summary.pack(fill=tk.X, padx=16, pady=(0, 12))

    def load_payroll_teacher(self):
        """Resolve whatever Teacher ID is typed into the Payroll tab and show
        the matching name/basic salary, without requiring a Directory
        selection first."""
        t_id = self.ent_pay_tch_id.get().strip()
        if not t_id:
            self.lbl_pay_tch_name.config(text="Enter a Teacher ID first.", fg=self.RED)
            self.lbl_pay_basic_sal.config(text="—")
            return
        row = db.run("SELECT name, basic_salary FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not row:
            self.lbl_pay_tch_name.config(text=f"Teacher ID '{t_id}' not found.", fg=self.RED)
            self.lbl_pay_basic_sal.config(text="—")
            return
        name, basic_sal = row
        self.lbl_pay_tch_name.config(text=f"✓ {name}", fg=self.GREEN)
        self.lbl_pay_basic_sal.config(text=f"Rs. {basic_sal:,.2f}")

    def _load_teacher_into_payroll_page(self):
        if not self.can_view_salary:
            return
        self.ent_pay_tch_id.delete(0, tk.END)
        self.ent_pay_tch_id.insert(0, self.lbl_tch_id.cget("text"))
        self.load_payroll_teacher()

    def _refresh_payroll_teacher_picker(self):
        # No-op kept for backward compatibility with show_page("payroll");
        # loading now happens explicitly via the Load button / directory sync.
        pass

    def generate_salary_payslip(self):
        if not rbac.can(self.user_role, "teacher.salary.pay"):
            messagebox.showerror("Permission Denied", "Not allowed to process salaries.")
            return
        t_id = self.ent_pay_tch_id.get().strip()
        if not t_id:
            messagebox.showerror("Error", "Enter a Teacher ID (or select one from Directory) first.")
            return
        row = db.run("SELECT name, basic_salary FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not row:
            messagebox.showerror("Error", f"Teacher ID '{t_id}' not found.")
            return
        name, basic_sal = row
        if basic_sal <= 0:
            messagebox.showerror("Error", "This teacher's Basic Salary is not set (0). Update it from Directory & Registration first.")
            return

        today_month = datetime.now().strftime("%Y-%m")

        # Idempotency guard: a "Salary" expense for this teacher this month
        # already means a payslip was issued — don't silently double-pay.
        already_paid = db.run(
            "SELECT id FROM accounting_expense WHERE category='Salary' AND date LIKE ? AND vendor_or_person LIKE ?",
            (f"{today_month}%", f"%({t_id})%"), fetchone=True,
        )
        if already_paid:
            if not messagebox.askyesno(
                "Salary Already Recorded",
                f"A salary payment for {name} ({t_id}) was already recorded for {today_month}.\n"
                "Generating another payslip will record a SECOND expense entry for this teacher this month.\n"
                "Continue anyway?"):
                return

        absents = db.run("SELECT COUNT(*) FROM teacher_attendance WHERE teacher_id=? AND status='Absent' AND date LIKE ?",
                          (t_id, f"{today_month}%"), fetchone=True)[0]

        per_day = basic_sal / 30.0
        deductions = per_day * absents
        net_sal = basic_sal - deductions

        out_path = os.path.join(os.getcwd(), f"Payslip_{t_id}_{today_month}.pdf")
        reports.generate_payslip(t_id, name, today_month, basic_sal, absents, deductions, net_sal, out_path)

        accounting.record_salary_expense(self.user_role, t_id, name, net_sal, today_month, self.current_user, reference=out_path)

        log_activity(self.current_user, f"Generated salary payslip for teacher {t_id}")

        self.lbl_payroll_summary.config(
            text=(f"{name} ({t_id}) — {today_month}\n"
                  f"Basic Salary: Rs. {basic_sal:,.2f}   |   Absences: {absents}   |   Deductions: Rs. {deductions:,.2f}\n"
                  f"Net Payable: Rs. {net_sal:,.2f}   (recorded as an accounting expense)"))

        messagebox.showinfo("Success", f"Payslip PDF Generated:\n{out_path}\nNet Payable: Rs. {net_sal:.2f}\n(Recorded as an accounting expense.)")

    # ------------------------------------------------------------
    # TEACHER PROFILE — full monthly + yearly attendance & salary history
    # ------------------------------------------------------------
    def open_teacher_profile(self):
        if not rbac.can(self.user_role, "teacher.view"):
            messagebox.showerror("Permission Denied", "You are not allowed to view teacher profiles.")
            return
        selected = self.tree_teacher.focus()
        vals = self.tree_teacher.item(selected, "values") if selected else ()
        if not vals:
            messagebox.showinfo("Select Teacher", "Please select a teacher from the directory first.")
            return
        self._build_teacher_profile_window(vals[0])

    def _build_teacher_profile_window(self, t_id):
        row = db.run("SELECT teacher_id, name, designation, phone, basic_salary, joining_date FROM teachers WHERE teacher_id=?",
                      (t_id,), fetchone=True)
        if not row:
            messagebox.showerror("Error", "Teacher not found.")
            return
        t_id, name, desig, phone, basic_sal, joining = row

        win = tk.Toplevel(self.win)
        win.title(f"Teacher Profile — {name}")
        win.geometry("880x720")
        win.minsize(760, 600)
        win.config(bg=self.BG)

        # ---------------- Header ----------------
        header = tk.Frame(win, bg=self.NAVY, padx=20, pady=16)
        header.pack(fill=tk.X)
        tk.Label(header, text=f"👤 {name}", font=("Segoe UI", 16, "bold"), bg=self.NAVY, fg="white").pack(anchor="w")
        tk.Label(header, text=f"{t_id}  ·  {desig or '—'}  ·  {phone or '—'}",
                 font=("Segoe UI", 9), bg=self.NAVY, fg="#cbd5e1").pack(anchor="w", pady=(2, 0))
        if self.can_view_salary:
            tk.Label(header, text=f"Basic Salary: Rs. {basic_sal:,.0f}   |   Joined: {joining or '—'}",
                     font=("Segoe UI", 9), bg=self.NAVY, fg="#94a3b8").pack(anchor="w", pady=(2, 0))

        # ---------------- Scrollable body ----------------
        body_canvas = tk.Canvas(win, bg=self.BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(win, orient=tk.VERTICAL, command=body_canvas.yview)
        body = tk.Frame(body_canvas, bg=self.BG)
        body.bind("<Configure>", lambda e: body_canvas.configure(scrollregion=body_canvas.bbox("all")))
        body_canvas.create_window((0, 0), window=body, anchor="nw", width=860)
        body_canvas.configure(yscrollcommand=vscroll.set)
        body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ---------------- Monthly attendance summary ----------------
        att_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        att_card.pack(fill=tk.X, padx=16, pady=(16, 8))
        tk.Label(att_card, text="ATTENDANCE — MONTHLY SUMMARY", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

        picker = tk.Frame(att_card, bg=self.CARD)
        picker.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(picker, text="Month:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        combo_month = ttk.Combobox(picker, values=[f"{i:02d}" for i in range(1, 13)], width=5, state="readonly")
        combo_month.current(datetime.now().month - 1)
        combo_month.pack(side=tk.LEFT, padx=(6, 16))
        tk.Label(picker, text="Year:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        ent_year = tk.Entry(picker, width=8)
        ent_year.insert(0, str(datetime.now().year))
        ent_year.pack(side=tk.LEFT, padx=(6, 16))

        month_summary_lbl = tk.Label(att_card, text="", font=("Segoe UI", 10, "bold"), bg=self.CARD, fg=self.NAVY,
                                      justify="left", anchor="w", wraplength=780)
        month_summary_lbl.pack(fill=tk.X, padx=16, pady=(0, 14))

        def compute_month():
            month = combo_month.get()
            year = ent_year.get().strip()
            if not (month and year.isdigit() and len(year) == 4):
                messagebox.showerror("Error", "Enter a valid Month and 4-digit Year.")
                return
            ym = f"{year}-{month}"
            # "Total working days" = distinct calendar days the SCHOOL had
            # any teacher-attendance activity that month (school-wide) —
            # same convention already used for student attendance reports.
            total_working_days = db.run(
                "SELECT COUNT(DISTINCT date) FROM teacher_attendance WHERE date LIKE ?", (f"{ym}%",), fetchone=True)[0]
            day_rows = db.run(
                "SELECT status FROM teacher_attendance WHERE teacher_id=? AND date LIKE ?", (t_id, f"{ym}%"), fetchall=True)
            present = sum(1 for (s,) in day_rows if s == "Present")
            absent = sum(1 for (s,) in day_rows if s == "Absent")
            leave = sum(1 for (s,) in day_rows if s == "Leave")
            late = sum(1 for (s,) in day_rows if s == "Late")
            pct = (present / total_working_days * 100) if total_working_days else 0.0
            month_summary_lbl.config(
                text=(f"{ym} — Working days: {total_working_days}  |  Present: {present}  |  Absent: {absent}  |  "
                      f"Leave: {leave}  |  Late: {late}  |  Attendance: {pct:.1f}%"))

        tk.Button(picker, text="Show Month", command=compute_month, bg=self.CYAN, fg="white", relief="flat",
                  padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side=tk.LEFT)
        compute_month()

        # ---------------- Yearly breakdown ----------------
        year_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        year_card.pack(fill=tk.X, padx=16, pady=8)
        tk.Label(year_card, text="ATTENDANCE — YEARLY BREAKDOWN", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

        year_picker = tk.Frame(year_card, bg=self.CARD)
        year_picker.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(year_picker, text="Year:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        ent_year2 = tk.Entry(year_picker, width=8)
        ent_year2.insert(0, str(datetime.now().year))
        ent_year2.pack(side=tk.LEFT, padx=(6, 16))

        year_table_frame = tk.Frame(year_card, bg=self.CARD)
        year_table_frame.pack(fill=tk.X, padx=16, pady=(0, 8))
        year_cols = ("month", "present", "absent", "leave", "late", "pct")
        tree_year = ttk.Treeview(year_table_frame, columns=year_cols, show="headings", height=6)
        for col, h in [("month", "Month"), ("present", "Present"), ("absent", "Absent"),
                       ("leave", "Leave"), ("late", "Late"), ("pct", "Attendance %")]:
            tree_year.heading(col, text=h)
            tree_year.column(col, anchor="center", width=110)
        tree_year.pack(fill=tk.X)

        year_total_lbl = tk.Label(year_card, text="", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.NAVY, anchor="w")
        year_total_lbl.pack(fill=tk.X, padx=16, pady=(6, 14))

        def compute_year():
            year = ent_year2.get().strip()
            if not (year.isdigit() and len(year) == 4):
                messagebox.showerror("Error", "Enter a valid 4-digit Year.")
                return
            tree_year.delete(*tree_year.get_children())
            total_present = total_absent = total_working = 0
            for m in range(1, 13):
                ym = f"{year}-{m:02d}"
                working = db.run("SELECT COUNT(DISTINCT date) FROM teacher_attendance WHERE date LIKE ?", (f"{ym}%",), fetchone=True)[0]
                rows = db.run("SELECT status FROM teacher_attendance WHERE teacher_id=? AND date LIKE ?", (t_id, f"{ym}%"), fetchall=True)
                if working == 0 and not rows:
                    continue  # skip months with no activity at all
                present = sum(1 for (s,) in rows if s == "Present")
                absent = sum(1 for (s,) in rows if s == "Absent")
                leave = sum(1 for (s,) in rows if s == "Leave")
                late = sum(1 for (s,) in rows if s == "Late")
                pct = (present / working * 100) if working else 0.0
                tree_year.insert("", tk.END, values=(ym, present, absent, leave, late, f"{pct:.1f}%"))
                total_present += present
                total_absent += absent
                total_working += working
            overall_pct = (total_present / total_working * 100) if total_working else 0.0
            year_total_lbl.config(
                text=f"Year {year} total — Present: {total_present}  |  Absent: {total_absent}  |  Overall Attendance: {overall_pct:.1f}%")

        tk.Button(year_picker, text="Show Year", command=compute_year, bg=self.CYAN, fg="white", relief="flat",
                  padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side=tk.LEFT)
        compute_year()

        # ---------------- Salary / payslip history ----------------
        if self.can_view_salary:
            pay_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
            pay_card.pack(fill=tk.X, padx=16, pady=8)
            tk.Label(pay_card, text="SALARY / PAYSLIP HISTORY", font=("Segoe UI", 10, "bold"),
                     bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

            pay_rows = db.run(
                "SELECT date, amount, description FROM accounting_expense WHERE category='Salary' AND vendor_or_person LIKE ? ORDER BY id DESC",
                (f"%({t_id})%",), fetchall=True)

            if pay_rows:
                pay_table_frame = tk.Frame(pay_card, bg=self.CARD)
                pay_table_frame.pack(fill=tk.X, padx=16, pady=(0, 14))
                pay_cols = ("date", "amount", "desc")
                tree_pay = ttk.Treeview(pay_table_frame, columns=pay_cols, show="headings", height=6)
                tree_pay.heading("date", text="Month / Date")
                tree_pay.column("date", anchor="center", width=120)
                tree_pay.heading("amount", text="Net Paid (Rs.)")
                tree_pay.column("amount", anchor="center", width=140)
                tree_pay.heading("desc", text="Description")
                tree_pay.column("desc", anchor="w", width=440)
                tree_pay.pack(fill=tk.X)
                total_paid = 0.0
                for date, amount, desc in pay_rows:
                    tree_pay.insert("", tk.END, values=(date, f"{amount:,.2f}", desc or ""))
                    total_paid += amount
                tk.Label(pay_card, text=f"Total paid to date: Rs. {total_paid:,.2f}", font=("Segoe UI", 9, "bold"),
                         bg=self.CARD, fg=self.GREEN).pack(anchor="w", padx=16, pady=(0, 14))
            else:
                tk.Label(pay_card, text="No salary payments recorded yet for this teacher.", font=("Segoe UI", 9),
                         bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=16, pady=(0, 14))

        tk.Frame(body, bg=self.BG, height=16).pack()
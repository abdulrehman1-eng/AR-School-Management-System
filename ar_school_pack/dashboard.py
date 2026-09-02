"""
dashboard.py — Professional Dashboard page for AR School Management System.

Extracted from app.py so the main window stays lean and this screen can
evolve independently. Public API:

    build_dashboard_into(parent, app) -> DashboardController

`app` is the StudentManagementApp instance (callbacks + role/user access).
"""

from __future__ import annotations

from datetime import datetime

import tkinter as tk
from tkinter import ttk

import db
import rbac
import theme

try:
    import accounting
    HAS_ACCOUNTING = True
except Exception:
    HAS_ACCOUNTING = False

try:
    import matplotlib.pyplot as plt  # noqa: F401
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


class DashboardController:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.user_role = app.user_role
        self.current_user = app.current_user

        self.canvas = tk.Canvas(parent, bg=theme.SILVER, highlightthickness=0)
        vscroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=theme.SILVER)
        self.body.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=vscroll.set)

        def _on_canvas_configure(event):
            self.canvas.itemconfig(self._window, width=event.width)

        self.canvas.bind("<Configure>", _on_canvas_configure)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse-wheel (bound while pointer is over dashboard)
        def _wheel(event):
            if event.num == 4 or event.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                self.canvas.yview_scroll(1, "units")

        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", _wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<Button-4>", _wheel), add="+")
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<Button-4>"), add="+")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<Button-5>", _wheel), add="+")
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<Button-5>"), add="+")

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        for w in self.body.winfo_children():
            w.destroy()

        tk.Label(
            self.body,
            text=f"Welcome back, {self.current_user}",
            font=theme.FONT_H1,
            bg=theme.SILVER,
            fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(0, 2))
        tk.Label(
            self.body,
            text=datetime.now().strftime("%A, %d %B %Y"),
            font=theme.FONT_SMALL,
            bg=theme.SILVER,
            fg=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 14))

        # ----- Stats -----
        try:
            total_students = db.run(
                "SELECT COUNT(*) FROM students WHERE COALESCE(status,'Active')='Active'",
                fetchone=True,
            )[0]
        except Exception:
            total_students = 0
        try:
            archived_students = db.run(
                "SELECT COUNT(*) FROM students WHERE status='Archived'",
                fetchone=True,
            )[0]
        except Exception:
            archived_students = 0
        try:
            total_teachers = db.run("SELECT COUNT(*) FROM teachers", fetchone=True)[0]
        except Exception:
            total_teachers = 0

        today = datetime.now().strftime("%Y-%m-%d")
        try:
            late_today = db.run(
                "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'",
                (today,), fetchone=True,
            )[0]
            present_only = db.run(
                "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present'",
                (today,), fetchone=True,
            )[0]
            present_today = present_only + late_today
            absent_today = db.run(
                "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'",
                (today,), fetchone=True,
            )[0]
            leave_today = db.run(
                "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Leave'",
                (today,), fetchone=True,
            )[0]
        except Exception:
            late_today = present_today = absent_today = leave_today = 0

        try:
            pending_fees = db.run(
                "SELECT COALESCE(SUM(total_fee-paid_fee),0) FROM students "
                "WHERE COALESCE(status,'Active')='Active' AND total_fee > paid_fee",
                fetchone=True,
            )[0]
        except Exception:
            pending_fees = 0

        cards_row = tk.Frame(self.body, bg=theme.SILVER)
        cards_row.pack(fill=tk.X, pady=(0, 12))
        theme.stat_card(
            cards_row, "Active Students", total_students, theme.BRAND_BLUE,
            subtitle=f"{archived_students} archived",
        ).pack(side=tk.LEFT, padx=(0, 12), fill=tk.BOTH, expand=True)
        theme.stat_card(
            cards_row, "Teachers", total_teachers, theme.SLATE,
        ).pack(side=tk.LEFT, padx=12, fill=tk.BOTH, expand=True)
        theme.stat_card(
            cards_row, "Present Today", present_today, theme.SUCCESS,
            subtitle=(f"{late_today} late" if late_today else None),
        ).pack(side=tk.LEFT, padx=12, fill=tk.BOTH, expand=True)
        theme.stat_card(
            cards_row, "Absent Today", absent_today, theme.DANGER,
            subtitle=(f"{leave_today} on leave" if leave_today else None),
        ).pack(side=tk.LEFT, padx=(12, 0), fill=tk.BOTH, expand=True)

        if rbac.can(self.user_role, "student.fee.view"):
            cards_row2 = tk.Frame(self.body, bg=theme.SILVER)
            cards_row2.pack(fill=tk.X, pady=(0, 12))
            theme.stat_card(
                cards_row2, "Pending Fees (Rs.)", f"{pending_fees:,.0f}", theme.WARNING,
            ).pack(side=tk.LEFT, padx=(0, 12), fill=tk.BOTH, expand=True)
            if HAS_ACCOUNTING and rbac.can(self.user_role, "accounting.dashboard"):
                try:
                    totals = accounting.dashboard_totals(self.user_role)
                except Exception:
                    totals = {"month_revenue": 0, "month_expense": 0, "month_net_income": 0}
                theme.stat_card(
                    cards_row2, "This Month Revenue (Rs.)",
                    f"{totals.get('month_revenue', 0):,.0f}", theme.SUCCESS,
                ).pack(side=tk.LEFT, padx=12, fill=tk.BOTH, expand=True)
                theme.stat_card(
                    cards_row2, "This Month Expenses (Rs.)",
                    f"{totals.get('month_expense', 0):,.0f}", theme.DANGER,
                ).pack(side=tk.LEFT, padx=12, fill=tk.BOTH, expand=True)
                month_net = totals.get(
                    "month_net_income",
                    float(totals.get("month_revenue") or 0)
                    - float(totals.get("month_expense") or 0),
                )
                theme.stat_card(
                    cards_row2, "This Month Net Income (Rs.)", f"{month_net:,.0f}",
                    theme.SUCCESS if month_net >= 0 else theme.DANGER,
                ).pack(side=tk.LEFT, padx=(12, 0), fill=tk.BOTH, expand=True)

        # ----- Quick Actions (wrap-friendly) -----
        actions_card, actions_body = theme.section_card(self.body, "Quick Actions")
        actions_card.pack(fill=tk.X, pady=(0, 12))

        can_results = getattr(self.app, "can_results", False)
        can_teachers = getattr(self.app, "can_teachers", False)

        qa = [
            ("➕ Add Student", self.app.open_admission, rbac.can(self.user_role, "student.add")),
            ("🗓️ Attendance", self.app.open_attendance, True),
            ("💰 Fee Management", self.app.open_fee_management, rbac.can(self.user_role, "student.fee.view")),
            ("📁 Fee Export", self.app.open_whatsapp_fee_reminders, rbac.can(self.user_role, "fee.reports.view")),
            ("👨‍🏫 Teachers", self.app.open_teacher_payroll, can_teachers and rbac.can(self.user_role, "teacher.add")),
            ("📝 Enter Marks", lambda: self.app.show_page("results"), can_results and rbac.can(self.user_role, "results.marks.edit")),
            ("💰 Finance", lambda: self.app.show_page("accounting"), rbac.can(self.user_role, "accounting.dashboard")),
        ]

        # Wrap buttons in a flow-like row that uses grid so they don't clip
        btn_host = tk.Frame(actions_body, bg=theme.WHITE)
        btn_host.pack(fill=tk.X, pady=4)
        col = 0
        row = 0
        max_cols = 4
        for label, command, allowed in qa:
            if not allowed:
                continue
            theme.primary_button(btn_host, label, command).grid(
                row=row, column=col, padx=(0, 10), pady=6, sticky="w",
            )
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        # ----- Recent lists -----
        lists_row = tk.Frame(self.body, bg=theme.SILVER)
        lists_row.pack(fill=tk.BOTH, expand=True)

        admissions_card, admissions_body = theme.section_card(lists_row, "Recent Admissions")
        admissions_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))
        try:
            recent_students = db.run(
                "SELECT student_id, name, class_sec FROM students "
                "WHERE COALESCE(status,'Active')='Active' ORDER BY ROWID DESC LIMIT 5",
                fetchall=True,
            ) or []
        except Exception:
            recent_students = []
        if recent_students:
            for s_id, name, cls in recent_students:
                row_f = tk.Frame(admissions_body, bg=theme.WHITE)
                row_f.pack(fill=tk.X, pady=2)
                tk.Label(
                    row_f, text=f"{name}", font=theme.FONT_BODY,
                    bg=theme.WHITE, fg=theme.TEXT_DARK,
                ).pack(side=tk.LEFT)
                tk.Label(
                    row_f, text=f"{s_id} · {cls or '-'}", font=theme.FONT_SMALL,
                    bg=theme.WHITE, fg=theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT)
        else:
            tk.Label(
                admissions_body, text="No students admitted yet.",
                font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
            ).pack(anchor="w", pady=8)

        payments_card, payments_body = theme.section_card(lists_row, "Recent Payments")
        payments_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        if rbac.can(self.user_role, "accounting.revenue.view"):
            try:
                recent_payments = db.run(
                    "SELECT student_id, amount, date FROM accounting_revenue "
                    "ORDER BY id DESC LIMIT 5",
                    fetchall=True,
                ) or []
            except Exception:
                recent_payments = []
            if recent_payments:
                for s_id, amount, date in recent_payments:
                    row_f = tk.Frame(payments_body, bg=theme.WHITE)
                    row_f.pack(fill=tk.X, pady=2)
                    tk.Label(
                        row_f, text=f"{s_id or 'General'}", font=theme.FONT_BODY,
                        bg=theme.WHITE, fg=theme.TEXT_DARK,
                    ).pack(side=tk.LEFT)
                    tk.Label(
                        row_f, text=f"Rs. {amount:,.0f} · {date}",
                        font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
                    ).pack(side=tk.RIGHT)
            else:
                tk.Label(
                    payments_body, text="No payments recorded yet.",
                    font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
                ).pack(anchor="w", pady=8)
        else:
            tk.Label(
                payments_body,
                text="You don't have permission to view finance records.",
                font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
            ).pack(anchor="w", pady=8)

        if HAS_MATPLOTLIB and rbac.can(self.user_role, "accounting.dashboard"):
            if hasattr(self.app, "show_finance_chart"):
                theme.primary_button(
                    self.body, "📊 View Revenue vs Expense Chart",
                    self.app.show_finance_chart, bg=theme.SLATE,
                ).pack(anchor="w", pady=(12, 0))


def build_dashboard_into(parent, app):
    """Build the dashboard into `parent`. Returns DashboardController."""
    return DashboardController(parent, app)

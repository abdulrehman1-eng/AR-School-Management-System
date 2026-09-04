"""
dashboard.py — Modern 2026 professional Dashboard for AR Academy ERP
Preserves all original DB queries, RBAC checks, and business logic.
Theme-driven, grid/frame layout, reusable component helpers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk

import db
import rbac
import theme

try:
    import fee_cycles
    HAS_FEE_CYCLES = True
except Exception:
    HAS_FEE_CYCLES = False

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MATPLOTLIB = True
except Exception:
    HAS_MATPLOTLIB = False

# ---------------------------------------------------------------------------
# Local theme tokens (consume central theme where possible; refine for cards)
# ---------------------------------------------------------------------------
BG_MAIN = getattr(theme, "SILVER", "#f1f5f9")
CARD_BG = getattr(theme, "WHITE", "#ffffff")
TEXT_DARK = getattr(theme, "TEXT_DARK", "#0f172a")
TEXT_MUTED = getattr(theme, "TEXT_MUTED", "#64748b")
BORDER = getattr(theme, "SILVER_BORDER", "#e2e8f0")
PRIMARY = getattr(theme, "BRAND_BLUE", "#0284c7")
SUCCESS = getattr(theme, "SUCCESS", "#16a34a")
WARNING = getattr(theme, "WARNING", "#d97706")
DANGER = getattr(theme, "DANGER", "#dc2626")

KPI_ACCENTS = {
    "students":  {"accent": "#2563eb", "soft": "#eff6ff", "border": "#bfdbfe"},
    "attendance":{"accent": "#16a34a", "soft": "#f0fdf4", "border": "#bbf7d0"},
    "collection":{"accent": "#7c3aed", "soft": "#f5f3ff", "border": "#ddd6fe"},
    "pending":   {"accent": "#ea580c", "soft": "#fff7ed", "border": "#fed7aa"},
}


class DashboardController:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.user_role = app.user_role
        self.current_user = app.current_user

        self.canvas = tk.Canvas(parent, bg=BG_MAIN, highlightthickness=0)
        vscroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG_MAIN)

        self.body.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=vscroll.set)

        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self._window, width=e.width),
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(event):
            if event.num == 4 or getattr(event, "delta", 0) > 0:
                self.canvas.yview_scroll(-2, "units")
            elif event.num == 5 or getattr(event, "delta", 0) < 0:
                self.canvas.yview_scroll(2, "units")

        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", _wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<Button-4>", _wheel), add="+")
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<Button-4>"), add="+")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<Button-5>", _wheel), add="+")
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<Button-5>"), add="+")

        self._chart_canvas = None
        self._trend_range = "weekly"  # weekly | monthly
        self.refresh()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def refresh(self):
        for w in self.body.winfo_children():
            w.destroy()
        self._chart_canvas = None

        # Live stats (only what the dashboard needs)
        total_students = self._safe_count(
            "SELECT COUNT(*) FROM students WHERE COALESCE(status,'Active')='Active'"
        )
        total_teachers = self._safe_count("SELECT COUNT(*) FROM teachers")
        total_classes = self._safe_count(
            "SELECT COUNT(DISTINCT class_sec) FROM students WHERE COALESCE(status,'Active')='Active' AND class_sec IS NOT NULL AND TRIM(class_sec)!=''"
        )

        today = datetime.now().strftime("%Y-%m-%d")
        # Single grouped query instead of 4 separate COUNTs (faster startup)
        present_today = absent_today = leave_today = 0
        try:
            rows = db.run(
                "SELECT status, COUNT(*) FROM attendance WHERE date=? GROUP BY status",
                (today,),
                fetchall=True,
            ) or []
            for status, cnt in rows:
                c = int(cnt or 0)
                if status in ("Present", "Late"):
                    present_today += c
                elif status == "Absent":
                    absent_today = c
                elif status == "Leave":
                    leave_today = c
        except Exception:
            pass
        marked_today = present_today + absent_today + leave_today

        if total_students > 0 and marked_today > 0:
            att_pct = (present_today / total_students) * 100
            att_value = f"{att_pct:.1f}%"
            att_sub = f"{present_today} present · {absent_today} absent"
        elif marked_today == 0:
            att_value = "—"
            att_sub = "Not marked today"
        else:
            att_value = str(present_today)
            att_sub = f"{absent_today} absent"

        # This-month fee collection
        month_prefix = datetime.now().strftime("%Y-%m")
        collected_month = 0.0
        try:
            row = db.run(
                "SELECT COALESCE(SUM(amount),0) FROM accounting_revenue WHERE date LIKE ?",
                (f"{month_prefix}%",),
                fetchone=True,
            )
            if row:
                collected_month = float(row[0] or 0)
        except Exception:
            collected_month = 0.0

        pending_amount, overdue_count = self._pending_fee_stats()

        # ---- 1. Welcome + Date ----
        self._build_welcome()

        # ---- 2. KPI row (4 cards — Teachers NOT here) ----
        cards = tk.Frame(self.body, bg=BG_MAIN)
        cards.pack(fill=tk.X, pady=(0, 16), padx=16)

        self._kpi(
            cards,
            accent_key="students",
            title="TOTAL STUDENTS",
            value=str(total_students),
            subtitle="Active in system",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        self._kpi(
            cards,
            accent_key="attendance",
            title="TODAY'S ATTENDANCE",
            value=att_value,
            subtitle=att_sub,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)

        self._kpi(
            cards,
            accent_key="collection",
            title="FEE COLLECTION",
            value=f"Rs. {collected_month:,.0f}",
            subtitle="This month",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)

        fee_sub = f"{overdue_count} overdue accounts" if overdue_count else "All clear"
        self._kpi(
            cards,
            accent_key="pending",
            title="PENDING FEES",
            value=f"Rs. {pending_amount:,.0f}" if pending_amount else "Rs. 0",
            subtitle=fee_sub,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        # ---- 3. Analytics + Quick Actions ----
        mid = tk.Frame(self.body, bg=BG_MAIN)
        mid.pack(fill=tk.X, pady=(0, 16), padx=16)

        trend_wrap = tk.Frame(mid, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        trend_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self._section_header(
            trend_wrap,
            "Attendance Overview",
            extra_widgets=self._trend_range_buttons,
        )
        trend_body = tk.Frame(trend_wrap, bg=CARD_BG, padx=14, pady=10)
        trend_body.pack(fill=tk.BOTH, expand=True)
        self._build_trend(trend_body, total_students)

        qa_wrap = tk.Frame(mid, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        qa_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self._section_header(qa_wrap, "Quick Actions")
        qa_body = tk.Frame(qa_wrap, bg=CARD_BG, padx=14, pady=12)
        qa_body.pack(fill=tk.BOTH, expand=True)
        self._build_quick_actions(qa_body)

        # ---- 4. Recent Admissions + Action Required ----
        bottom = tk.Frame(self.body, bg=BG_MAIN)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(0, 16), padx=16)

        adm_wrap = tk.Frame(bottom, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        adm_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self._section_header(
            adm_wrap,
            "Recent Admissions",
            action_text="View All →",
            action_cmd=lambda: self.app.show_page("students"),
        )
        adm_body = tk.Frame(adm_wrap, bg=CARD_BG, padx=12, pady=8)
        adm_body.pack(fill=tk.BOTH, expand=True)
        self._build_admissions_table(adm_body)

        action_wrap = tk.Frame(bottom, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        action_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self._section_header(action_wrap, "Action Required")
        action_body = tk.Frame(action_wrap, bg=CARD_BG, padx=12, pady=10)
        action_body.pack(fill=tk.BOTH, expand=True)
        self._build_action_required(
            action_body, total_students, marked_today, overdue_count, pending_amount
        )

        # ---- 5. School Overview (Teachers live here) ----
        self._build_school_overview(total_students, total_teachers, total_classes)

        # Bottom spacer
        tk.Frame(self.body, bg=BG_MAIN, height=12).pack(fill=tk.X)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    def _build_welcome(self):
        top = tk.Frame(self.body, bg=BG_MAIN)
        top.pack(fill=tk.X, pady=(16, 18), padx=16)

        left = tk.Frame(top, bg=BG_MAIN)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        hour = datetime.now().hour
        greet = (
            "Good Morning"
            if hour < 12
            else "Good Afternoon"
            if hour < 17
            else "Good Evening"
        )
        tk.Label(
            left,
            text=f"{greet}, {self.current_user}",
            font=("Segoe UI", 20, "bold"),
            bg=BG_MAIN,
            fg=TEXT_DARK,
        ).pack(anchor="w")
        tk.Label(
            left,
            text="Here's your school's overview for today.",
            font=("Segoe UI", 10),
            bg=BG_MAIN,
            fg=TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # Date pill
        pill = tk.Frame(
            top,
            bg=CARD_BG,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=14,
            pady=8,
        )
        pill.pack(side=tk.RIGHT)
        now = datetime.now()
        tk.Label(
            pill,
            text=now.strftime("%d %B %Y"),
            font=("Segoe UI", 10, "bold"),
            bg=CARD_BG,
            fg=TEXT_DARK,
        ).pack(anchor="e")
        tk.Label(
            pill,
            text=now.strftime("%A"),
            font=("Segoe UI", 9),
            bg=CARD_BG,
            fg=TEXT_MUTED,
        ).pack(anchor="e")

    def _kpi(self, parent, accent_key, title, value, subtitle=None):
        style = KPI_ACCENTS.get(accent_key, KPI_ACCENTS["students"])
        card = tk.Frame(
            parent, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1
        )
        # Top accent bar
        tk.Frame(card, bg=style["accent"], height=3).pack(fill=tk.X)

        inner = tk.Frame(card, bg=CARD_BG, padx=16, pady=14)
        inner.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            inner,
            text=title,
            font=("Segoe UI", 8, "bold"),
            bg=CARD_BG,
            fg=TEXT_MUTED,
        ).pack(anchor="w")
        tk.Label(
            inner,
            text=value,
            font=("Segoe UI", 22, "bold"),
            bg=CARD_BG,
            fg=TEXT_DARK,
        ).pack(anchor="w", pady=(2, 0))
        if subtitle:
            tk.Label(
                inner,
                text=subtitle,
                font=("Segoe UI", 8),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="w", pady=(6, 0))
        return card

    def _section_header(self, parent, title, action_text=None, action_cmd=None, extra_widgets=None):
        hdr = tk.Frame(parent, bg=CARD_BG, padx=14, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr,
            text=title,
            font=("Segoe UI", 12, "bold"),
            bg=CARD_BG,
            fg=TEXT_DARK,
        ).pack(side=tk.LEFT)

        if extra_widgets:
            extra_widgets(hdr)

        if action_text and action_cmd:
            tk.Button(
                hdr,
                text=action_text,
                command=action_cmd,
                bg=CARD_BG,
                fg=PRIMARY,
                font=("Segoe UI", 9, "bold"),
                bd=0,
                cursor="hand2",
                activeforeground="#0369a1",
                activebackground=CARD_BG,
            ).pack(side=tk.RIGHT)

        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)

    def _trend_range_buttons(self, hdr):
        """Weekly / Monthly toggle (Yearly skipped — too heavy for live dashboard)."""
        box = tk.Frame(hdr, bg=CARD_BG)
        box.pack(side=tk.RIGHT, padx=(0, 8))

        def _set(mode):
            if self._trend_range == mode:
                return
            self._trend_range = mode
            self.refresh()

        for label, mode in (("Weekly", "weekly"), ("Monthly", "monthly")):
            active = self._trend_range == mode
            tk.Button(
                box,
                text=label,
                command=lambda m=mode: _set(m),
                bg=PRIMARY if active else CARD_BG,
                fg="white" if active else TEXT_MUTED,
                font=("Segoe UI", 8, "bold"),
                bd=0,
                padx=8,
                pady=2,
                cursor="hand2",
                activebackground=PRIMARY,
                activeforeground="white",
                relief="flat",
            ).pack(side=tk.LEFT, padx=2)

    def _build_trend(self, parent, total_students):
        days, rates, presents, absents, leaves = [], [], [], [], []

        # Build label/date list first (no DB yet)
        date_keys = []
        if self._trend_range == "monthly":
            points, step = 10, 3
            for i in range(points - 1, -1, -1):
                dt = datetime.now() - timedelta(days=i * step)
                date_keys.append(dt.strftime("%Y-%m-%d"))
                days.append(dt.strftime("%d %b"))
        else:
            for i in range(6, -1, -1):
                dt = datetime.now() - timedelta(days=i)
                date_keys.append(dt.strftime("%Y-%m-%d"))
                days.append(dt.strftime("%a"))

        # ONE range query instead of 3 x N separate COUNTs (major startup win)
        by_date = {d: {"P": 0, "A": 0, "L": 0} for d in date_keys}
        if date_keys:
            try:
                rows = db.run(
                    "SELECT date, status, COUNT(*) FROM attendance "
                    "WHERE date >= ? AND date <= ? GROUP BY date, status",
                    (date_keys[0], date_keys[-1]),
                    fetchall=True,
                ) or []
                for d, status, cnt in rows:
                    if d not in by_date:
                        continue
                    c = int(cnt or 0)
                    if status in ("Present", "Late"):
                        by_date[d]["P"] += c
                    elif status == "Absent":
                        by_date[d]["A"] = c
                    elif status == "Leave":
                        by_date[d]["L"] = c
            except Exception:
                pass

        for d in date_keys:
            p = by_date[d]["P"]
            a = by_date[d]["A"]
            l = by_date[d]["L"]
            presents.append(p)
            absents.append(a)
            leaves.append(l)
            rates.append(
                min(100.0, (p / total_students) * 100) if total_students > 0 else 0.0
            )

        if not HAS_MATPLOTLIB:
            for label, r, p, a, l in zip(days, rates, presents, absents, leaves):
                row = tk.Frame(parent, bg=CARD_BG)
                row.pack(fill=tk.X, pady=2)
                tk.Label(
                    row,
                    text=label,
                    font=("Segoe UI", 8, "bold"),
                    bg=CARD_BG,
                    fg=TEXT_MUTED,
                    width=7,
                    anchor="w",
                ).pack(side=tk.LEFT)
                bar_w = max(1, int(r / 2))
                tk.Frame(row, bg=PRIMARY, width=bar_w, height=10).pack(
                    side=tk.LEFT, padx=(4, 6)
                )
                tk.Label(
                    row,
                    text=f"{r:.0f}%  (P{p} A{a} L{l})",
                    font=("Segoe UI", 8),
                    bg=CARD_BG,
                    fg=TEXT_DARK,
                ).pack(side=tk.LEFT)
            return

        try:
            fig = Figure(figsize=(5.4, 2.35), dpi=100)
            fig.patch.set_facecolor(CARD_BG)
            ax = fig.add_subplot(111)
            ax.set_facecolor(CARD_BG)

            x = list(range(len(days)))
            ax.plot(
                x,
                rates,
                color=PRIMARY,
                marker="o",
                linewidth=2,
                markersize=5,
                markerfacecolor="#ffffff",
                markeredgecolor=PRIMARY,
                markeredgewidth=2,
                label="Present %",
            )
            ax.fill_between(x, rates, alpha=0.08, color=PRIMARY)
            ax.set_ylim(0, 105)
            ax.set_yticks([0, 25, 50, 75, 100])
            ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=7)
            ax.set_xticks(x)
            ax.set_xticklabels(days, fontsize=8)
            ax.tick_params(axis="y", labelsize=7, colors="#94a3b8")
            ax.tick_params(axis="x", labelsize=8, colors="#64748b")

            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            ax.spines["left"].set_color(BORDER)
            ax.spines["bottom"].set_color(BORDER)
            ax.grid(axis="y", color="#f1f5f9", linewidth=0.8)
            fig.tight_layout(pad=0.4)

            canvas = FigureCanvasTkAgg(fig, master=parent)
            canvas.draw()
            w = canvas.get_tk_widget()
            w.configure(bg=CARD_BG, highlightthickness=0)
            w.pack(fill=tk.BOTH, expand=True)
            self._chart_canvas = canvas
        except Exception:
            tk.Label(
                parent,
                text="Chart unavailable",
                font=("Segoe UI", 9),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="w", pady=8)

    def _build_quick_actions(self, parent):
        """One-click common tasks. Only show actions the role can perform."""
        actions = []

        if rbac.can(self.user_role, "student.add"):
            actions.append(
                ("＋  Add Student", getattr(self.app, "open_admission", None), SUCCESS)
            )
        actions.append(
            ("✓  Mark Attendance", getattr(self.app, "open_attendance", None), PRIMARY)
        )
        if rbac.can(self.user_role, "student.fee.view"):
            actions.append(
                (
                    "💰  Collect Fee",
                    getattr(self.app, "open_fee_management", None),
                    "#7c3aed",
                )
            )
        if rbac.can(self.user_role, "results.marks.edit") or rbac.can(
            self.user_role, "results.view"
        ):
            actions.append(
                (
                    "📝  Enter Result",
                    lambda: self.app.show_page("results")
                    if "results" in getattr(self.app, "pages", {})
                    else None,
                    "#0ea5e9",
                )
            )
        if rbac.can(self.user_role, "teacher.add") or rbac.can(
            self.user_role, "teacher.view"
        ):
            actions.append(
                (
                    "👨‍🏫  Teachers",
                    getattr(self.app, "open_teacher_payroll", None),
                    "#64748b",
                )
            )

        if not actions:
            tk.Label(
                parent,
                text="No quick actions available for your role.",
                font=("Segoe UI", 9),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="w", pady=12)
            return

        # 2-column grid
        for i, (label, cmd, color) in enumerate(actions):
            row = i // 2
            col = i % 2
            btn = tk.Button(
                parent,
                text=label,
                command=cmd if callable(cmd) else (lambda: None),
                bg=CARD_BG,
                fg=TEXT_DARK,
                font=("Segoe UI", 10, "bold"),
                bd=0,
                padx=12,
                pady=12,
                cursor="hand2",
                activebackground="#f8fafc",
                activeforeground=TEXT_DARK,
                anchor="w",
                highlightbackground=BORDER,
                highlightthickness=1,
            )
            btn.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            # Left accent strip via nested frame is overkill; keep clean.
            parent.grid_columnconfigure(col, weight=1)

        parent.grid_rowconfigure(0, weight=1)
        if len(actions) > 2:
            parent.grid_rowconfigure(1, weight=1)

    def _build_admissions_table(self, parent):
        hdr = tk.Frame(parent, bg=CARD_BG)
        hdr.pack(fill=tk.X, pady=(0, 4))
        cols = [("Student", 18), ("Class", 10), ("ID", 12), ("Status", 8)]
        for text, w in cols:
            tk.Label(
                hdr,
                text=text.upper(),
                font=("Segoe UI", 7, "bold"),
                bg=CARD_BG,
                fg=TEXT_MUTED,
                width=w,
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 4))

        try:
            rows = (
                db.run(
                    "SELECT student_id, name, class_sec, COALESCE(status,'Active') "
                    "FROM students ORDER BY ROWID DESC LIMIT 5",
                    fetchall=True,
                )
                or []
            )
        except Exception:
            rows = []

        if not rows:
            tk.Label(
                parent,
                text="No recent admissions recorded.",
                font=("Segoe UI", 9),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="w", pady=10)
            return

        for i, (s_id, name, cls, status) in enumerate(rows):
            bg = "#f8fafc" if i % 2 else CARD_BG
            row = tk.Frame(parent, bg=bg, padx=2, pady=5)
            row.pack(fill=tk.X, pady=1)

            tk.Label(
                row,
                text=(name or "—")[:20],
                font=("Segoe UI", 9, "bold"),
                bg=bg,
                fg=TEXT_DARK,
                width=18,
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(
                row,
                text=(cls or "—")[:10],
                font=("Segoe UI", 9),
                bg=bg,
                fg=TEXT_MUTED,
                width=10,
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(
                row,
                text=s_id or "—",
                font=("Segoe UI", 8),
                bg=bg,
                fg=TEXT_MUTED,
                width=12,
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 4))

            is_active = (status or "Active") == "Active"
            tk.Label(
                row,
                text=f" {status or 'Active'} ",
                font=("Segoe UI", 7, "bold"),
                bg="#dcfce7" if is_active else "#f1f5f9",
                fg="#15803d" if is_active else "#475569",
            ).pack(side=tk.LEFT)

    def _build_action_required(
        self, parent, total_students, marked_today, overdue_count, pending_amount
    ):
        items = []

        if overdue_count and rbac.can(self.user_role, "student.fee.view"):
            items.append(
                {
                    "dot": "●",
                    "color": DANGER,
                    "title": f"{overdue_count} students have overdue fees",
                    "detail": f"Rs. {pending_amount:,.0f} outstanding",
                    "link": "View Fee Records →",
                    "cmd": getattr(self.app, "open_fee_management", None),
                }
            )

        unmarked = max(0, total_students - marked_today)
        if total_students > 0 and unmarked > 0:
            items.append(
                {
                    "dot": "●",
                    "color": WARNING,
                    "title": f"{unmarked} students attendance pending today",
                    "detail": "Mark remaining students before day ends",
                    "link": "Mark Attendance →",
                    "cmd": getattr(self.app, "open_attendance", None),
                }
            )

        try:
            incomplete = self._safe_count(
                "SELECT COUNT(*) FROM students WHERE COALESCE(status,'Active')='Active' "
                "AND (phone IS NULL OR TRIM(phone)='')"
            )
        except Exception:
            incomplete = 0

        if incomplete and rbac.can(self.user_role, "student.view"):
            items.append(
                {
                    "dot": "●",
                    "color": "#ca8a04",
                    "title": f"{incomplete} profiles missing phone numbers",
                    "detail": "Complete contact details for alerts",
                    "link": "Update Profiles →",
                    "cmd": lambda: self.app.show_page("students"),
                }
            )

        if not items:
            empty = tk.Frame(parent, bg="#f0fdf4", padx=12, pady=14)
            empty.pack(fill=tk.X, pady=4)
            tk.Label(
                empty,
                text="✓  All daily tasks complete. System fully updated.",
                font=("Segoe UI", 10),
                bg="#f0fdf4",
                fg=SUCCESS,
            ).pack(anchor="w")
            return

        for a in items[:4]:
            row = tk.Frame(
                parent,
                bg="#f8fafc",
                highlightbackground=BORDER,
                highlightthickness=1,
                padx=10,
                pady=8,
            )
            row.pack(fill=tk.X, pady=3)

            left = tk.Frame(row, bg="#f8fafc")
            left.pack(side=tk.LEFT, fill=tk.X, expand=True)

            title_row = tk.Frame(left, bg="#f8fafc")
            title_row.pack(anchor="w", fill=tk.X)
            tk.Label(
                title_row,
                text=a["dot"],
                font=("Segoe UI", 9),
                bg="#f8fafc",
                fg=a["color"],
            ).pack(side=tk.LEFT)
            tk.Label(
                title_row,
                text=f"  {a['title']}",
                font=("Segoe UI", 9, "bold"),
                bg="#f8fafc",
                fg=TEXT_DARK,
            ).pack(side=tk.LEFT)

            if a.get("detail"):
                tk.Label(
                    left,
                    text=a["detail"],
                    font=("Segoe UI", 8),
                    bg="#f8fafc",
                    fg=TEXT_MUTED,
                ).pack(anchor="w", padx=(14, 0), pady=(1, 0))

            cmd = a.get("cmd")
            if cmd and callable(cmd):
                tk.Button(
                    left,
                    text=a["link"],
                    command=cmd,
                    bg="#f8fafc",
                    fg=PRIMARY,
                    font=("Segoe UI", 8, "bold"),
                    bd=0,
                    cursor="hand2",
                    activebackground="#f8fafc",
                ).pack(anchor="w", padx=(14, 0), pady=(2, 0))

    def _build_school_overview(self, total_students, total_teachers, total_classes):
        wrap = tk.Frame(
            self.body, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1
        )
        wrap.pack(fill=tk.X, padx=16, pady=(0, 8))

        hdr = tk.Frame(wrap, bg=CARD_BG, padx=14, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr,
            text="School Overview",
            font=("Segoe UI", 12, "bold"),
            bg=CARD_BG,
            fg=TEXT_DARK,
        ).pack(side=tk.LEFT)
        tk.Frame(wrap, bg=BORDER, height=1).pack(fill=tk.X)

        body = tk.Frame(wrap, bg=CARD_BG, padx=16, pady=14)
        body.pack(fill=tk.X)

        # Subjects from multiple possible tables used in the ERP
        subjects = self._count_subjects()

        metrics = [
            ("Students", str(total_students)),
            ("Teachers", str(total_teachers)),
            ("Classes", str(total_classes) if total_classes else "—"),
            ("Subjects", str(subjects) if subjects else "—"),
        ]

        for i, (label, value) in enumerate(metrics):
            cell = tk.Frame(body, bg=CARD_BG)
            cell.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if i == 0 else 8, 0))
            tk.Label(
                cell,
                text=label.upper(),
                font=("Segoe UI", 8, "bold"),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="w")
            tk.Label(
                cell,
                text=value,
                font=("Segoe UI", 18, "bold"),
                bg=CARD_BG,
                fg=TEXT_DARK,
            ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Data helpers (unchanged business logic)
    # ------------------------------------------------------------------
    def _count_subjects(self):
        """Count distinct subjects from wherever the system stores them."""
        queries = [
            "SELECT COUNT(DISTINCT subject_name) FROM timetable WHERE subject_name IS NOT NULL AND TRIM(subject_name)!=''",
            "SELECT COUNT(*) FROM subjects",
            "SELECT COUNT(DISTINCT name) FROM subjects WHERE name IS NOT NULL AND TRIM(name)!=''",
            "SELECT COUNT(DISTINCT subject_name) FROM results WHERE subject_name IS NOT NULL AND TRIM(subject_name)!=''",
            "SELECT COUNT(DISTINCT subject) FROM results WHERE subject IS NOT NULL AND TRIM(subject)!=''",
            "SELECT COUNT(DISTINCT subject_name) FROM student_marks WHERE subject_name IS NOT NULL AND TRIM(subject_name)!=''",
            "SELECT COUNT(DISTINCT subject) FROM student_marks WHERE subject IS NOT NULL AND TRIM(subject)!=''",
            "SELECT COUNT(DISTINCT subject_name) FROM marks WHERE subject_name IS NOT NULL AND TRIM(subject_name)!=''",
            "SELECT COUNT(DISTINCT subject) FROM marks WHERE subject IS NOT NULL AND TRIM(subject)!=''",
        ]
        best = 0
        for q in queries:
            n = self._safe_count(q)
            if n > best:
                best = n
        return best

    def _safe_count(self, query, params=()):
        try:
            row = db.run(query, params, fetchone=True)
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0

    def _pending_fee_stats(self):
        amount, count = 0.0, 0
        if HAS_FEE_CYCLES and rbac.can(self.user_role, "fee.reports.view"):
            try:
                items = fee_cycles.pending_and_overdue_students(self.user_role)
                return sum(float(i.get("balance") or 0) for i in items), len(items)
            except Exception:
                pass
        try:
            row = db.run(
                "SELECT COALESCE(SUM(total_fee - paid_fee), 0), COUNT(*) "
                "FROM students WHERE COALESCE(status,'Active')='Active' AND total_fee > paid_fee",
                fetchone=True,
            )
            if row:
                amount, count = float(row[0] or 0), int(row[1] or 0)
        except Exception:
            amount, count = 0.0, 0
        return amount, count


def build_dashboard_into(parent, app):
    return DashboardController(parent, app)

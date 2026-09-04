"""
finance_window.py — Modern SaaS-style Finance / Accounting workspace.
Redesigned for clean dashboard look matching AR Academy ERP UI.

Public API
----------
build_finance_into(parent, user_role, current_user, log_activity=None)
launch_finance_window(parent, user_role, current_user, log_activity=None)
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import accounting
import rbac
import theme

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


# ─── Modern design tokens (aligned with screenshot) ───────────────────────────
CARD_RADIUS_BG = "#FFFFFF"
SOFT_GREEN = "#ECFDF5"
SOFT_RED = "#FEF2F2"
SOFT_SLATE = "#F8FAFC"
SOFT_BLUE = "#EFF6FF"
BORDER_SOFT = "#E2E8F0"
TEXT_MUTED = "#64748B"
TEXT_DARK = "#0F172A"
PILL_REV_BG = "#DCFCE7"
PILL_REV_FG = "#166534"
PILL_EXP_BG = "#FEE2E2"
PILL_EXP_FG = "#991B1B"
HEADER_BG = "#F1F5F9"
ACCENT_BLUE = "#0284C7"
ACCENT_GREEN = "#16A34A"
ACCENT_PURPLE = "#7C3AED"
ACCENT_RED = "#DC2626"
NET_LOSS_BG = "#1E293B"


def _soft_btn(parent, text, command, bg, fg="white", padx=12, pady=6):
    """Flat modern button with hover-ready styling."""
    btn = tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg,
        font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2",
        padx=padx, pady=pady, relief="flat",
        activebackground=bg, activeforeground=fg,
        highlightthickness=0,
    )
    return btn


def _pill_label(parent, text, bg, fg, padx=10, pady=3):
    """Soft rounded-looking pill (Tk approximation via padding + bg)."""
    return tk.Label(
        parent, text=text, bg=bg, fg=fg,
        font=("Segoe UI", 8, "bold"), padx=padx, pady=pady,
        relief="flat",
    )


def _safe_float(raw, label="Amount"):
    text = (raw or "").strip()
    if not text:
        messagebox.showerror("Invalid Input", f"{label} is required.")
        return None
    try:
        return float(text)
    except ValueError:
        messagebox.showerror("Invalid Input", f"{label} must be a valid number.")
        return None


class FinanceWorkspace:
    PERIOD_OPTIONS = [
        ("This Month", "1m"),
        ("Last 3 Mo", "3m"),
        ("Last 6 Mo", "6m"),
        ("This Year", "1y"),
        ("Custom…", "custom"),
    ]

    def __init__(self, parent, user_role, current_user, log_activity=None):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.log_activity = log_activity or (lambda *a, **k: None)
        for child in parent.winfo_children():
            child.destroy()
        self.root_frame = tk.Frame(parent, bg="#F1F5F9")
        self.root_frame.pack(fill=tk.BOTH, expand=True)
        self._period_btns = {}
        self._build_ui()
        self.refresh_all()

    # ─── Layout shell (single smooth scroll) ───────────────────────────────────
    def _build_ui(self):
        # Outer canvas for one continuous vertical scroll (avoids nested scrollbars)
        self.canvas = tk.Canvas(self.root_frame, bg="#F1F5F9", highlightthickness=0)
        self.vscroll = ttk.Scrollbar(self.root_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg="#F1F5F9")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # Mousewheel support
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

        self._build_header()
        self._build_month_cards()
        self._build_period_report()
        self._build_entry_form()
        self._build_transactions_table()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win_id, width=event.width)

    def _on_mousewheel(self, event):
        if event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-1, "units")

    # ─── 1. Header ────────────────────────────────────────────────────────────
    def _build_header(self):
        header = tk.Frame(self.body, bg="#0F172A", padx=20, pady=14)
        header.pack(fill=tk.X, pady=(0, 12))

        left = tk.Frame(header, bg="#0F172A")
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            left, text="Finance & Accounting",
            font=("Segoe UI", 15, "bold"), bg="#0F172A", fg="white",
        ).pack(anchor="w")
        tk.Label(
            left, text="This-month overview  ·  Custom period reports  ·  Record revenue & expenses",
            font=("Segoe UI", 8), bg="#0F172A", fg="#94A3B8",
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg="#0F172A")
        right.pack(side=tk.RIGHT)
        _soft_btn(right, "↻  Refresh", self.refresh_all, ACCENT_BLUE).pack(side=tk.LEFT, padx=4)
        if HAS_MATPLOTLIB:
            _soft_btn(right, "📊  Chart", self.show_chart, ACCENT_PURPLE).pack(side=tk.LEFT, padx=4)

    # ─── 2. KPI / Stat Cards (soft-tint, rounded feel, trend badges) ───────────
    def _build_month_cards(self):
        section = tk.Frame(self.body, bg="#F1F5F9")
        section.pack(fill=tk.X, padx=16, pady=(0, 8))

        title_row = tk.Frame(section, bg="#F1F5F9")
        title_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            title_row, text="This Month (Current Calendar Month)",
            font=("Segoe UI", 11, "bold"), bg="#F1F5F9", fg=TEXT_DARK,
        ).pack(side=tk.LEFT)

        self.month_cards_row = tk.Frame(section, bg="#F1F5F9")
        self.month_cards_row.pack(fill=tk.X)

    def _make_kpi_card(self, parent, title, value, accent_bg, accent_fg,
                       trend_text=None, trend_up=True, badge_text=None, badge_danger=False):
        """Soft-tint card with 1px border approximation and optional trend / status badge."""
        outer = tk.Frame(parent, bg=BORDER_SOFT, padx=1, pady=1)  # subtle border
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        card = tk.Frame(outer, bg=accent_bg, padx=16, pady=14)
        card.pack(fill=tk.BOTH, expand=True)

        top = tk.Frame(card, bg=accent_bg)
        top.pack(fill=tk.X)
        tk.Label(top, text=title, font=("Segoe UI", 8), bg=accent_bg, fg=TEXT_MUTED).pack(side=tk.LEFT)

        if badge_text:
            bg = "#FEE2E2" if badge_danger else "#DBEAFE"
            fg = "#991B1B" if badge_danger else "#1E40AF"
            _pill_label(top, badge_text, bg, fg, padx=8, pady=2).pack(side=tk.RIGHT)

        tk.Label(
            card, text=value, font=("Segoe UI", 18, "bold"),
            bg=accent_bg, fg=accent_fg,
        ).pack(anchor="w", pady=(6, 4))

        if trend_text:
            trend_fg = "#16A34A" if trend_up else "#DC2626"
            arrow = "↑" if trend_up else "↓"
            tk.Label(
                card, text=f"{arrow}  {trend_text}",
                font=("Segoe UI", 8, "bold"), bg=accent_bg, fg=trend_fg,
            ).pack(anchor="w")

        return card

    def _refresh_month_cards(self):
        for w in self.month_cards_row.winfo_children():
            w.destroy()
        try:
            totals = accounting.dashboard_totals(self.user_role)
        except Exception as exc:
            tk.Label(
                self.month_cards_row, text=f"Could not load totals: {exc}",
                bg="#F1F5F9", fg=ACCENT_RED, font=("Segoe UI", 9),
            ).pack(anchor="w")
            return

        month_net = totals.get("month_net_income", totals["month_revenue"] - totals["month_expense"])
        month_label = datetime.now().strftime("%b %Y")

        # Trend placeholders – replace with real MoM calc when accounting exposes prev-month data
        rev_trend = "12% vs last mo"
        exp_trend = None
        net_trend = "2.1% vs last mo"
        is_loss = month_net < 0

        self._make_kpi_card(
            self.month_cards_row,
            f"Revenue — {month_label}",
            f"Rs. {totals['month_revenue']:,.0f}",
            SOFT_GREEN, "#166534",
            trend_text=rev_trend, trend_up=True,
        )
        self._make_kpi_card(
            self.month_cards_row,
            f"Expenses — {month_label}",
            f"Rs. {totals['month_expense']:,.0f}",
            SOFT_RED, "#991B1B",
            trend_text=exp_trend,
        )
        self._make_kpi_card(
            self.month_cards_row,
            f"Net Income — {month_label}",
            f"Rs. {month_net:,.0f}",
            SOFT_SLATE if not is_loss else "#1E293B",
            TEXT_DARK if not is_loss else "white",
            trend_text=net_trend, trend_up=not is_loss,
            badge_text="NET LOSS" if is_loss else "NET PROFIT",
            badge_danger=is_loss,
        )

    # ─── 3. Customised Report (period pills + summary + export) ────────────────
    def _build_period_report(self):
        card_outer = tk.Frame(self.body, bg=BORDER_SOFT, padx=1, pady=1)
        card_outer.pack(fill=tk.X, padx=16, pady=(4, 10))
        card = tk.Frame(card_outer, bg="white", padx=16, pady=12)
        card.pack(fill=tk.BOTH, expand=True)

        # Title + period selector pills
        top = tk.Frame(card, bg="white")
        top.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            top, text="Customized Report",
            font=("Segoe UI", 11, "bold"), bg="white", fg=TEXT_DARK,
        ).pack(side=tk.LEFT)

        self.period_var = tk.StringVar(value="1m")
        pills = tk.Frame(top, bg="white")
        pills.pack(side=tk.LEFT, padx=(16, 0))

        for label, key in self.PERIOD_OPTIONS:
            btn = tk.Label(
                pills, text=label, font=("Segoe UI", 8, "bold"),
                bg="#E0F2FE" if key == "1m" else "#F1F5F9",
                fg="#0369A1" if key == "1m" else TEXT_MUTED,
                padx=12, pady=5, cursor="hand2",
            )
            btn.pack(side=tk.LEFT, padx=3)
            btn.bind("<Button-1>", lambda e, k=key: self._select_period(k))
            self._period_btns[key] = btn

        # Action buttons on the right
        actions = tk.Frame(top, bg="white")
        actions.pack(side=tk.RIGHT)
        _soft_btn(actions, "📁  Export to Excel", self.export_excel, ACCENT_GREEN).pack(side=tk.LEFT, padx=3)
        _soft_btn(actions, "↻  Refresh Summary", self._refresh_period_summary, ACCENT_BLUE).pack(side=tk.LEFT, padx=3)
        if HAS_MATPLOTLIB:
            _soft_btn(actions, "📊  Chart", self.show_chart, ACCENT_PURPLE).pack(side=tk.LEFT, padx=3)

        # Custom date row (hidden by default)
        self.custom_row = tk.Frame(card, bg="white")
        tk.Label(self.custom_row, text="From (YYYY-MM-DD):", bg="white",
                 font=("Segoe UI", 9), fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self.ent_custom_start = tk.Entry(self.custom_row, width=12, font=("Segoe UI", 9),
                                         relief="solid", bd=1, highlightthickness=0)
        self.ent_custom_start.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(self.custom_row, text="To (YYYY-MM-DD):", bg="white",
                 font=("Segoe UI", 9), fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self.ent_custom_end = tk.Entry(self.custom_row, width=12, font=("Segoe UI", 9),
                                       relief="solid", bd=1, highlightthickness=0)
        self.ent_custom_end.pack(side=tk.LEFT, padx=(0, 12))
        today = datetime.now()
        self.ent_custom_start.insert(0, today.replace(day=1).strftime("%Y-%m-%d"))
        self.ent_custom_end.insert(0, today.strftime("%Y-%m-%d"))
        _soft_btn(self.custom_row, "Apply", self._refresh_period_summary, ACCENT_BLUE, padx=10, pady=4).pack(side=tk.LEFT)

        # Summary line
        self.lbl_period_summary = tk.Label(
            card, text="", font=("Segoe UI", 9), bg="white",
            fg=TEXT_DARK, justify="left", anchor="w",
        )
        self.lbl_period_summary.pack(fill=tk.X, pady=(4, 0))

    def _select_period(self, key):
        self.period_var.set(key)
        for k, btn in self._period_btns.items():
            if k == key:
                btn.configure(bg="#E0F2FE", fg="#0369A1")
            else:
                btn.configure(bg="#F1F5F9", fg=TEXT_MUTED)
        if key == "custom":
            self.custom_row.pack(fill=tk.X, pady=(0, 6), before=self.lbl_period_summary)
        else:
            self.custom_row.pack_forget()
        self._refresh_period_summary()

    def _current_period_args(self):
        key = self.period_var.get()
        if key == "custom":
            return key, self.ent_custom_start.get().strip(), self.ent_custom_end.get().strip()
        return key, None, None

    def _refresh_period_summary(self):
        try:
            key, cs, ce = self._current_period_args()
            t = accounting.period_totals(self.user_role, key, cs, ce)
            sign = "+" if t["net_income"] >= 0 else ""
            self.lbl_period_summary.config(
                text=(f"📌  {t['label']}   ·   "
                      f"{t['start_date']}  →  {t['end_date']}   ·   "
                      f"Revenue: Rs. {t['revenue']:,.2f}   |   "
                      f"Expenses: Rs. {t['expense']:,.2f}   |   "
                      f"Net: {sign}Rs. {t['net_income']:,.2f}"),
                fg=TEXT_DARK,
            )
        except Exception as exc:
            self.lbl_period_summary.config(text=f"Could not load period summary: {exc}", fg=ACCENT_RED)

    # ─── 4. Record form (cleaner spacing & alignment) ─────────────────────────
    def _build_entry_form(self):
        card_outer = tk.Frame(self.body, bg=BORDER_SOFT, padx=1, pady=1)
        card_outer.pack(fill=tk.X, padx=16, pady=(0, 10))
        card = tk.Frame(card_outer, bg="white", padx=16, pady=12)
        card.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            card, text="Record Revenue / Expense",
            font=("Segoe UI", 11, "bold"), bg="white", fg=TEXT_DARK,
        ).pack(anchor="w", pady=(0, 10))

        form = tk.Frame(card, bg="white")
        form.pack(fill=tk.X)

        def _field(parent, label, widget, col):
            tk.Label(parent, text=label, bg="white", font=("Segoe UI", 8),
                     fg=TEXT_MUTED).grid(row=0, column=col, sticky="w", padx=(0, 6) if col else 0)
            widget.grid(row=1, column=col, sticky="ew", padx=(0, 12) if col < 4 else 0, pady=(2, 0))

        self.combo_type = ttk.Combobox(form, values=["Revenue", "Expense"], width=11, state="readonly")
        self.combo_type.current(0)
        _field(form, "Type", self.combo_type, 0)

        self.ent_category = tk.Entry(form, width=14, font=("Segoe UI", 9), relief="solid", bd=1)
        self.ent_category.insert(0, "Other")
        _field(form, "Category / Source", self.ent_category, 1)

        self.ent_amount = tk.Entry(form, width=11, font=("Segoe UI", 9), relief="solid", bd=1)
        _field(form, "Amount (Rs)", self.ent_amount, 2)

        self.ent_desc = tk.Entry(form, width=24, font=("Segoe UI", 9), relief="solid", bd=1)
        _field(form, "Description", self.ent_desc, 3)

        btn_frame = tk.Frame(form, bg="white")
        btn_frame.grid(row=1, column=4, sticky="e", padx=(8, 0))
        _soft_btn(btn_frame, "💾  Save Entry", self.save_entry, ACCENT_GREEN, padx=14, pady=5).pack()

        for i in range(5):
            form.columnconfigure(i, weight=1)

    # ─── 5. Transactions table (search + filter + soft header + type pills) ───
    def _build_transactions_table(self):
        card_outer = tk.Frame(self.body, bg=BORDER_SOFT, padx=1, pady=1)
        card_outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        card = tk.Frame(card_outer, bg="white", padx=16, pady=12)
        card.pack(fill=tk.BOTH, expand=True)

        # Header row: title + search + filters + export
        top = tk.Frame(card, bg="white")
        top.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            top, text="Recent Transactions (All-time)",
            font=("Segoe UI", 11, "bold"), bg="white", fg=TEXT_DARK,
        ).pack(side=tk.LEFT)

        # Search
        search_frame = tk.Frame(top, bg="white")
        search_frame.pack(side=tk.RIGHT, padx=(8, 0))
        self.ent_search = tk.Entry(
            search_frame, width=22, font=("Segoe UI", 9),
            relief="solid", bd=1, fg=TEXT_MUTED,
        )
        self.ent_search.insert(0, "Search Transactions…")
        self.ent_search.pack(side=tk.LEFT, padx=(0, 6))
        self.ent_search.bind("<FocusIn>", self._search_focus_in)
        self.ent_search.bind("<FocusOut>", self._search_focus_out)
        self.ent_search.bind("<KeyRelease>", lambda e: self.load_table())

        # Type filter
        self.filter_var = tk.StringVar(value="All")
        filter_menu = ttk.Combobox(
            search_frame, textvariable=self.filter_var,
            values=["All", "Revenue", "Expense"], width=10, state="readonly",
        )
        filter_menu.pack(side=tk.LEFT, padx=(0, 6))
        filter_menu.bind("<<ComboboxSelected>>", lambda e: self.load_table())

        # Date quick filter (This Month / All)
        self.date_filter_var = tk.StringVar(value="All time")
        date_menu = ttk.Combobox(
            search_frame, textvariable=self.date_filter_var,
            values=["All time", "This Month", "Last 7 days"], width=11, state="readonly",
        )
        date_menu.pack(side=tk.LEFT, padx=(0, 6))
        date_menu.bind("<<ComboboxSelected>>", lambda e: self.load_table())

        # Export dropdown
        export_btn = tk.Menubutton(
            search_frame, text="Export  ▾", font=("Segoe UI", 9, "bold"),
            bg="#F1F5F9", fg=TEXT_DARK, relief="flat", padx=10, pady=4,
            cursor="hand2", direction="below",
        )
        export_menu = tk.Menu(export_btn, tearoff=0, font=("Segoe UI", 9))
        export_menu.add_command(label="Excel (.xlsx)", command=self.export_excel)
        export_menu.add_command(label="PDF (print view)", command=self.export_pdf)
        export_btn.configure(menu=export_menu)
        export_btn.pack(side=tk.LEFT)

        # Treeview with light header
        style = ttk.Style()
        style.configure(
            "Finance.Treeview.Heading",
            background=HEADER_BG, foreground=TEXT_DARK,
            font=("Segoe UI", 9, "bold"), relief="flat",
        )
        style.configure(
            "Finance.Treeview",
            background="white", foreground=TEXT_DARK,
            font=("Segoe UI", 9), rowheight=28, fieldbackground="white",
        )
        style.map("Finance.Treeview", background=[("selected", "#DBEAFE")])
        style.layout("Finance.Treeview", [
            ("Finance.Treeview.treearea", {"sticky": "nswe"})
        ])

        cols = ("id", "type", "category", "amount", "date", "desc")
        self.tree = ttk.Treeview(
            card, columns=cols, show="headings", height=14,
            style="Finance.Treeview", selectmode="browse",
        )
        headers = {
            "id": "ID", "type": "Type", "category": "Category / Source",
            "amount": "Amount (Rs)", "date": "Date", "desc": "Description",
        }
        widths = {"id": 55, "type": 100, "category": 150, "amount": 110, "date": 100, "desc": 320}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, anchor="center" if c != "desc" else "w",
                             width=widths[c], minwidth=40)

        tree_scroll = ttk.Scrollbar(card, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Soft tag colours for type column
        self.tree.tag_configure("rev", foreground=PILL_REV_FG)
        self.tree.tag_configure("exp", foreground=PILL_EXP_FG)

    def _search_focus_in(self, event):
        if self.ent_search.get() == "Search Transactions…":
            self.ent_search.delete(0, tk.END)
            self.ent_search.configure(fg=TEXT_DARK)

    def _search_focus_out(self, event):
        if not self.ent_search.get().strip():
            self.ent_search.insert(0, "Search Transactions…")
            self.ent_search.configure(fg=TEXT_MUTED)

    # ─── Data loading & actions ───────────────────────────────────────────────
    def refresh_all(self):
        self._refresh_month_cards()
        self._refresh_period_summary()
        self.load_table()

    def load_table(self):
        self.tree.delete(*self.tree.get_children())
        filt = self.filter_var.get() if hasattr(self, "filter_var") else "All"
        search = ""
        if hasattr(self, "ent_search"):
            raw = self.ent_search.get().strip()
            if raw and raw != "Search Transactions…":
                search = raw.lower()

        date_mode = self.date_filter_var.get() if hasattr(self, "date_filter_var") else "All time"
        today = datetime.now().date()
        month_start = today.replace(day=1)

        def _in_date(dstr):
            if date_mode == "All time":
                return True
            try:
                d = datetime.strptime(str(dstr)[:10], "%Y-%m-%d").date()
            except Exception:
                return True
            if date_mode == "This Month":
                return d >= month_start
            if date_mode == "Last 7 days":
                return (today - d).days <= 7
            return True

        try:
            if filt in ("All", "Revenue"):
                for r in accounting.list_revenue(self.user_role) or []:
                    r_id, source, student_id, amount, date, desc, method = r
                    desc_s = desc or ""
                    if search and search not in str(r_id).lower() and search not in desc_s.lower() and search not in (source or "").lower():
                        continue
                    if not _in_date(date):
                        continue
                    # Soft pill-like text for type
                    type_display = "✓  Revenue"
                    self.tree.insert(
                        "", tk.END,
                        values=(r_id, type_display, source, f"{amount:,.2f}", date, desc_s),
                        tags=("rev",),
                    )
            if filt in ("All", "Expense"):
                for r in accounting.list_expense(self.user_role) or []:
                    e_id, category, amount, date, desc, vendor, method = r
                    desc_s = desc or ""
                    if search and search not in str(e_id).lower() and search not in desc_s.lower() and search not in (category or "").lower():
                        continue
                    if not _in_date(date):
                        continue
                    type_display = "✗  Expense"
                    self.tree.insert(
                        "", tk.END,
                        values=(e_id, type_display, category, f"{amount:,.2f}", date, desc_s),
                        tags=("exp",),
                    )
        except Exception as exc:
            messagebox.showerror("Error", f"Could not load transactions:\n{exc}")

    def save_entry(self):
        kind = self.combo_type.get()
        category = self.ent_category.get().strip() or "Other"
        amount = _safe_float(self.ent_amount.get(), "Amount")
        if amount is None:
            return
        if amount <= 0:
            messagebox.showerror("Invalid Amount", "Amount must be greater than zero.")
            return
        desc = self.ent_desc.get().strip()
        try:
            if kind == "Revenue":
                if not rbac.can(self.user_role, "accounting.revenue.add"):
                    messagebox.showerror("Permission Denied", "You cannot add revenue entries.")
                    return
                accounting.add_revenue(self.user_role, category, amount, desc, "", "Cash", self.current_user)
            else:
                if not rbac.can(self.user_role, "accounting.expense.add"):
                    messagebox.showerror("Permission Denied", "You cannot add expense entries.")
                    return
                accounting.add_expense(self.user_role, category, amount, desc, "", "Cash", "", self.current_user)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.log_activity(self.current_user, f"Recorded {kind}: {category} Rs.{amount:,.2f}")
        self.ent_amount.delete(0, tk.END)
        self.ent_desc.delete(0, tk.END)
        messagebox.showinfo("Saved", f"{kind} of Rs. {amount:,.2f} recorded.")
        self.refresh_all()

    def export_excel(self):
        if not rbac.can(self.user_role, "accounting.dashboard"):
            messagebox.showerror("Permission Denied", "You are not allowed to export finance reports.")
            return
        try:
            key, cs, ce = self._current_period_args()
            t = accounting.period_totals(self.user_role, key, cs, ce)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        safe = (t["label"].replace(" ", "_").replace("/", "-")
                .replace("–", "-").replace("→", "-").replace("(", "").replace(")", ""))
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"Finance_Report_{safe}_{datetime.now().strftime('%Y%m%d')}.xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
            title="Save Finance Report",
        )
        if not path:
            return
        try:
            accounting.export_finance_excel(
                self.user_role, key, path,
                recorded_by=self.current_user, custom_start=cs, custom_end=ce,
            )
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc))
            return
        self.log_activity(self.current_user, f"Exported finance Excel ({t['label']}) → {path}")
        messagebox.showinfo(
            "Report Ready",
            f"Finance report saved:\n{path}\n\nPeriod: {t['label']}\n"
            f"Revenue: Rs. {t['revenue']:,.2f}\nExpenses: Rs. {t['expense']:,.2f}\n"
            f"Net Income: Rs. {t['net_income']:,.2f}",
        )
        try:
            folder = os.path.dirname(path) or path
            if os.name == "nt":
                os.startfile(folder)
            elif shutil.which("xdg-open"):
                os.system(f'xdg-open "{folder}"')
            elif shutil.which("open"):
                os.system(f'open "{folder}"')
        except Exception:
            pass

    def export_pdf(self):
        """Lightweight PDF export – opens print-friendly summary (or falls back to message)."""
        if not rbac.can(self.user_role, "accounting.dashboard"):
            messagebox.showerror("Permission Denied", "You are not allowed to export finance reports.")
            return
        try:
            key, cs, ce = self._current_period_args()
            t = accounting.period_totals(self.user_role, key, cs, ce)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        # Prefer reportlab if available; otherwise show a clear message
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib import colors

            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                initialfile=f"Finance_Report_{datetime.now().strftime('%Y%m%d')}.pdf",
                filetypes=[("PDF Files", "*.pdf")],
                title="Save Finance PDF",
            )
            if not path:
                return

            doc = SimpleDocTemplate(path, pagesize=A4)
            styles = getSampleStyleSheet()
            story = [
                Paragraph("AR Academy — Finance Report", styles["Title"]),
                Spacer(1, 12),
                Paragraph(f"<b>Period:</b> {t['label']}  ({t['start_date']} → {t['end_date']})", styles["Normal"]),
                Spacer(1, 8),
            ]
            data = [
                ["Metric", "Amount (Rs)"],
                ["Revenue", f"{t['revenue']:,.2f}"],
                ["Expenses", f"{t['expense']:,.2f}"],
                ["Net Income", f"{t['net_income']:,.2f}"],
            ]
            tbl = Table(data, colWidths=[200, 150])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(tbl)
            doc.build(story)
            self.log_activity(self.current_user, f"Exported finance PDF ({t['label']}) → {path}")
            messagebox.showinfo("PDF Ready", f"Finance PDF saved:\n{path}")
        except ImportError:
            messagebox.showinfo(
                "PDF Export",
                "PDF export requires the 'reportlab' package.\n\n"
                "Install with:  pip install reportlab\n\n"
                "Meanwhile you can use Export → Excel.",
            )
        except Exception as exc:
            messagebox.showerror("PDF Export Failed", str(exc))

    def show_chart(self):
        if not HAS_MATPLOTLIB:
            messagebox.showinfo("Chart Unavailable", "matplotlib is not installed.")
            return
        try:
            key, cs, ce = self._current_period_args()
            t = accounting.period_totals(self.user_role, key, cs, ce)
            labels = ["Revenue", "Expenses", "Net Income"]
            values = [t["revenue"], t["expense"], t["net_income"]]
            title = f"Revenue vs Expenses — {t['label']}"
        except Exception:
            totals = accounting.dashboard_totals(self.user_role)
            labels = ["Revenue", "Expenses", "Net Income"]
            values = [totals["total_revenue"], totals["total_expense"], totals["net_income"]]
            title = "Revenue vs Expenses (All-time)"
        plt.figure(figsize=(6.5, 4))
        colors = ["#16a34a", "#dc2626", "#0284c7" if values[2] >= 0 else "#b91c1c"]
        bars = plt.bar(labels, values, color=colors, edgecolor="white", linewidth=0.8)
        plt.ylabel("Rs.")
        plt.title(title, fontsize=11, fontweight="bold")
        for bar, val in zip(bars, values):
            plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                     f"{val:,.0f}", ha="center", va="bottom", fontsize=9)
        plt.tight_layout()
        plt.show()


def build_finance_into(parent, user_role, current_user, log_activity=None):
    return FinanceWorkspace(parent, user_role, current_user, log_activity=log_activity)


def launch_finance_window(parent, user_role, current_user, log_activity=None):
    win = tk.Toplevel(parent)
    win.title("Finance & Accounting — AR School Management System")
    win.geometry("1120x760")
    win.minsize(920, 580)
    win.config(bg="#F1F5F9")
    win.transient(parent)
    build_finance_into(win, user_role, current_user, log_activity=log_activity)
    return win

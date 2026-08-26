"""
finance_window.py — Professional Finance / Accounting workspace.

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


def _fixed_btn(parent, text, command, bg):
    return tk.Button(
        parent, text=text, command=command, bg=bg, fg="white",
        font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2",
        padx=14, pady=7, height=1, relief="flat",
        activebackground=bg, activeforeground="white",
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
        ("This Month (1M)", "1m"),
        ("Last 3 Months", "3m"),
        ("Last 6 Months", "6m"),
        ("This Year", "1y"),
        ("Custom Dates", "custom"),
    ]

    def __init__(self, parent, user_role, current_user, log_activity=None):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.log_activity = log_activity or (lambda *a, **k: None)
        for child in parent.winfo_children():
            child.destroy()
        self.root_frame = tk.Frame(parent, bg=theme.SILVER)
        self.root_frame.pack(fill=tk.BOTH, expand=True)
        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        canvas = tk.Canvas(self.root_frame, bg=theme.SILVER, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.root_frame, orient=tk.VERTICAL, command=canvas.yview)
        self.body = tk.Frame(canvas, bg=theme.SILVER)
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(1, width=e.width))

        self._build_header()
        self._build_month_cards()
        self._build_period_report()
        self._build_entry_form()
        self._build_transactions_table()

    def _build_header(self):
        header = tk.Frame(self.body, bg=theme.NAVY, padx=16, pady=12)
        header.pack(fill=tk.X, pady=(0, 10))
        left = tk.Frame(header, bg=theme.NAVY)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(left, text="💰  FINANCE & ACCOUNTING", font=("Segoe UI", 14, "bold"),
                 bg=theme.NAVY, fg="white").pack(anchor="w")
        tk.Label(left, text="This-month overview · Custom period reports · Record revenue & expenses",
                 font=("Segoe UI", 8), bg=theme.NAVY, fg="#94a3b8").pack(anchor="w")
        right = tk.Frame(header, bg=theme.NAVY)
        right.pack(side=tk.RIGHT)
        _fixed_btn(right, "🔄 Refresh", self.refresh_all, "#0284c7").pack(side=tk.LEFT, padx=4)
        if HAS_MATPLOTLIB:
            _fixed_btn(right, "📊 Chart", self.show_chart, "#7c3aed").pack(side=tk.LEFT, padx=4)

    def _build_month_cards(self):
        card, body = theme.section_card(self.body, "This Month (Current Calendar Month)")
        card.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.month_cards_row = tk.Frame(body, bg=theme.WHITE)
        self.month_cards_row.pack(fill=tk.X, pady=4)

    def _build_period_report(self):
        card, body = theme.section_card(self.body, "Customised Report — Choose Period & Export Excel")
        card.pack(fill=tk.X, padx=10, pady=(0, 10))

        period_row = tk.Frame(body, bg=theme.WHITE)
        period_row.pack(fill=tk.X, pady=(4, 6))
        tk.Label(period_row, text="Period:", font=("Segoe UI", 9, "bold"),
                 bg=theme.WHITE, fg=theme.TEXT_DARK).pack(side=tk.LEFT, padx=(0, 10))
        self.period_var = tk.StringVar(value="1m")
        for label, key in self.PERIOD_OPTIONS:
            tk.Radiobutton(
                period_row, text=label, variable=self.period_var, value=key,
                font=("Segoe UI", 9), bg=theme.WHITE, activebackground=theme.WHITE,
                command=self._on_period_changed,
            ).pack(side=tk.LEFT, padx=6)

        self.custom_row = tk.Frame(body, bg=theme.WHITE)
        tk.Label(self.custom_row, text="From (YYYY-MM-DD):", bg=theme.WHITE, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.ent_custom_start = tk.Entry(self.custom_row, width=12, font=("Segoe UI", 9))
        self.ent_custom_start.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(self.custom_row, text="To (YYYY-MM-DD):", bg=theme.WHITE, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.ent_custom_end = tk.Entry(self.custom_row, width=12, font=("Segoe UI", 9))
        self.ent_custom_end.pack(side=tk.LEFT, padx=(0, 12))
        today = datetime.now()
        self.ent_custom_start.insert(0, today.replace(day=1).strftime("%Y-%m-%d"))
        self.ent_custom_end.insert(0, today.strftime("%Y-%m-%d"))
        _fixed_btn(self.custom_row, "Apply", self._refresh_period_summary, "#0284c7").pack(side=tk.LEFT, padx=4)

        self.lbl_period_summary = tk.Label(
            body, text="", font=("Segoe UI", 10), bg=theme.WHITE,
            fg=theme.TEXT_DARK, justify="left", anchor="w",
        )
        self.lbl_period_summary.pack(fill=tk.X, pady=(6, 8))

        btn_row = tk.Frame(body, bg=theme.WHITE)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        _fixed_btn(btn_row, "📁  Export to Excel", self.export_excel, "#16a34a").pack(side=tk.LEFT, padx=(0, 8))
        _fixed_btn(btn_row, "🔄  Refresh Summary", self._refresh_period_summary, "#0284c7").pack(side=tk.LEFT, padx=4)
        if HAS_MATPLOTLIB:
            _fixed_btn(btn_row, "📊  Chart for This Period", self.show_chart, "#7c3aed").pack(side=tk.LEFT, padx=8)

    def _build_entry_form(self):
        card, body = theme.section_card(self.body, "Record Revenue / Expense")
        card.pack(fill=tk.X, padx=10, pady=(0, 10))
        form = tk.Frame(body, bg=theme.WHITE)
        form.pack(fill=tk.X, pady=4)
        tk.Label(form, text="Type:", bg=theme.WHITE, font=("Segoe UI", 9)).grid(row=0, column=0, padx=5, pady=6, sticky="e")
        self.combo_type = ttk.Combobox(form, values=["Revenue", "Expense"], width=12, state="readonly")
        self.combo_type.current(0)
        self.combo_type.grid(row=0, column=1, padx=5, pady=6)
        tk.Label(form, text="Category / Source:", bg=theme.WHITE, font=("Segoe UI", 9)).grid(row=0, column=2, padx=5, pady=6, sticky="e")
        self.ent_category = tk.Entry(form, width=16, font=("Segoe UI", 9))
        self.ent_category.insert(0, "Other")
        self.ent_category.grid(row=0, column=3, padx=5, pady=6)
        tk.Label(form, text="Amount (Rs):", bg=theme.WHITE, font=("Segoe UI", 9)).grid(row=0, column=4, padx=5, pady=6, sticky="e")
        self.ent_amount = tk.Entry(form, width=12, font=("Segoe UI", 9))
        self.ent_amount.grid(row=0, column=5, padx=5, pady=6)
        tk.Label(form, text="Description:", bg=theme.WHITE, font=("Segoe UI", 9)).grid(row=0, column=6, padx=5, pady=6, sticky="e")
        self.ent_desc = tk.Entry(form, width=22, font=("Segoe UI", 9))
        self.ent_desc.grid(row=0, column=7, padx=5, pady=6)
        _fixed_btn(form, "💾 Save Entry", self.save_entry, "#16a34a").grid(row=0, column=8, padx=10, pady=6)

    def _build_transactions_table(self):
        card, body = theme.section_card(self.body, "Recent Transactions (All-time)")
        card.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        filter_row = tk.Frame(body, bg=theme.WHITE)
        filter_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(filter_row, text="Show:", bg=theme.WHITE, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        self.filter_var = tk.StringVar(value="All")
        for label in ("All", "Revenue", "Expense"):
            tk.Radiobutton(
                filter_row, text=label, variable=self.filter_var, value=label,
                bg=theme.WHITE, font=("Segoe UI", 9), command=self.load_table,
            ).pack(side=tk.LEFT, padx=4)
        _fixed_btn(filter_row, "🔄 Reload Table", self.load_table, "#64748b").pack(side=tk.RIGHT, padx=4)

        cols = ("id", "type", "category", "amount", "date", "desc")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
        headers = {"id": "ID", "type": "Type", "category": "Category / Source",
                   "amount": "Amount (Rs)", "date": "Date", "desc": "Description"}
        widths = {"id": 60, "type": 90, "category": 160, "amount": 110, "date": 100, "desc": 280}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, anchor="center", width=widths[c], minwidth=50)
        tree_scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_all(self):
        self._refresh_month_cards()
        self._refresh_period_summary()
        self.load_table()

    def _refresh_month_cards(self):
        for w in self.month_cards_row.winfo_children():
            w.destroy()
        try:
            totals = accounting.dashboard_totals(self.user_role)
        except Exception as exc:
            tk.Label(self.month_cards_row, text=f"Could not load totals: {exc}",
                     bg=theme.WHITE, fg=theme.DANGER, font=("Segoe UI", 9)).pack(anchor="w")
            return
        month_net = totals.get("month_net_income", totals["month_revenue"] - totals["month_expense"])
        month_label = datetime.now().strftime("%B %Y")

        def _mini(parent, title, value, color):
            f = tk.Frame(parent, bg=color, padx=14, pady=12)
            f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            tk.Label(f, text=title, font=("Segoe UI", 8), bg=color, fg="white").pack(anchor="w")
            tk.Label(f, text=value, font=("Segoe UI", 16, "bold"), bg=color, fg="white").pack(anchor="w")

        _mini(self.month_cards_row, f"Revenue — {month_label}", f"Rs. {totals['month_revenue']:,.0f}", "#16a34a")
        _mini(self.month_cards_row, f"Expenses — {month_label}", f"Rs. {totals['month_expense']:,.0f}", "#dc2626")
        _mini(self.month_cards_row, f"Net Income — {month_label}", f"Rs. {month_net:,.0f}",
              "#0284c7" if month_net >= 0 else "#b91c1c")
        _mini(self.month_cards_row, "Today",
              f"Rev {totals['today_revenue']:,.0f} · Exp {totals['today_expense']:,.0f}", "#475569")

    def _on_period_changed(self):
        if self.period_var.get() == "custom":
            self.custom_row.pack(fill=tk.X, pady=(0, 6))
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
                text=(f"📌  {t['label']}\n"
                      f"    Date range:  {t['start_date']}  →  {t['end_date']}\n"
                      f"    Revenue: Rs. {t['revenue']:,.2f}      |      "
                      f"Spends / Expenses: Rs. {t['expense']:,.2f}      |      "
                      f"Net Income: {sign}Rs. {t['net_income']:,.2f}"),
                fg=theme.TEXT_DARK,
            )
        except Exception as exc:
            self.lbl_period_summary.config(text=f"Could not load period summary: {exc}", fg=theme.DANGER)

    def load_table(self):
        self.tree.delete(*self.tree.get_children())
        filt = self.filter_var.get() if hasattr(self, "filter_var") else "All"
        try:
            if filt in ("All", "Revenue"):
                for r in accounting.list_revenue(self.user_role) or []:
                    r_id, source, student_id, amount, date, desc, method = r
                    self.tree.insert("", tk.END, values=(r_id, "Revenue", source, f"{amount:,.2f}", date, desc or ""), tags=("rev",))
            if filt in ("All", "Expense"):
                for r in accounting.list_expense(self.user_role) or []:
                    e_id, category, amount, date, desc, vendor, method = r
                    self.tree.insert("", tk.END, values=(e_id, "Expense", category, f"{amount:,.2f}", date, desc or ""), tags=("exp",))
            self.tree.tag_configure("rev", foreground="#166534")
            self.tree.tag_configure("exp", foreground="#991b1b")
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
        safe = t["label"].replace(" ", "_").replace("/", "-").replace("–", "-").replace("→", "-").replace("(", "").replace(")", "")
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"Finance_Report_{safe}_{datetime.now().strftime('%Y%m%d')}.xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
            title="Save Finance Report",
        )
        if not path:
            return
        try:
            accounting.export_finance_excel(self.user_role, key, path, recorded_by=self.current_user, custom_start=cs, custom_end=ce)
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc))
            return
        self.log_activity(self.current_user, f"Exported finance Excel ({t['label']}) → {path}")
        messagebox.showinfo(
            "Report Ready",
            f"Finance report saved:\n{path}\n\nPeriod: {t['label']}\n"
            f"Revenue: Rs. {t['revenue']:,.2f}\nExpenses: Rs. {t['expense']:,.2f}\nNet Income: Rs. {t['net_income']:,.2f}",
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
        plt.figure(figsize=(6, 4))
        colors = ["#16a34a", "#dc2626", "#0284c7" if values[2] >= 0 else "#b91c1c"]
        bars = plt.bar(labels, values, color=colors)
        plt.ylabel("Rs.")
        plt.title(title)
        for bar, val in zip(bars, values):
            plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:,.0f}",
                     ha="center", va="bottom", fontsize=9)
        plt.tight_layout()
        plt.show()


def build_finance_into(parent, user_role, current_user, log_activity=None):
    return FinanceWorkspace(parent, user_role, current_user, log_activity=log_activity)


def launch_finance_window(parent, user_role, current_user, log_activity=None):
    win = tk.Toplevel(parent)
    win.title("Finance & Accounting — AR School Management System")
    win.geometry("1100x720")
    win.minsize(900, 560)
    win.config(bg=theme.SILVER)
    win.transient(parent)
    build_finance_into(win, user_role, current_user, log_activity=log_activity)
    return win

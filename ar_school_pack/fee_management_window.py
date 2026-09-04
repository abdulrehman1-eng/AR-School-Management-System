"""
fee_management_window.py — Unified Fee Management (single user-facing
fee screen, replacing separate Collect Fee / Fee Cycles Quick Actions).

UI redesigned to follow modern school-management SaaS dashboard hierarchy
while preserving all existing business logic, permissions and calculations.
"""

import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import db
import rbac
import reports
import theme
import fee_cycles
import additional_fees
import whatsapp_notify

PAYMENT_METHODS = ["Cash", "Bank", "Online Transfer", "Other"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

STATUS_COLORS = {
    "PAID": theme.SUCCESS, "ADVANCE": theme.INFO, "PARTIAL": theme.WARNING,
    "OVERDUE": theme.DANGER, "PENDING": theme.TEXT_MUTED,
}

# Soft accent colours used only for visual hierarchy (do not affect logic)
ACCENT_BLUE = getattr(theme, "BRAND_BLUE", "#2563eb")
ACCENT_PURPLE = "#7c3aed"
ACCENT_GREEN = theme.SUCCESS
ACCENT_RED = theme.DANGER
CARD_BG = theme.WHITE
MUTED = theme.TEXT_MUTED
DARK = theme.TEXT_DARK
BORDER = getattr(theme, "SILVER_BORDER", "#e2e8f0")


class FeeManagementWindow:
    def __init__(self, parent, user_role, current_user, on_change=None):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.on_change = on_change  # optional callback to refresh Students Directory
        self.student = None
        self.cycles = []
        self.selected_cycle = None

        if not rbac.can(self.user_role, "student.fee.view"):
            messagebox.showerror(
                "Permission Denied",
                f"Role '{self.user_role}' is not permitted to view fee information.",
                parent=parent,
            )
            return

        self.can_edit = rbac.can(self.user_role, "student.fee.edit")
        self.can_admin = rbac.can(self.user_role, "fee.cycle.generate")
        self.can_reports = rbac.can(self.user_role, "fee.reports.view")

        self.win = tk.Toplevel(parent)
        self.win.title("School Fee Management System")
        self.win.geometry("1020x780")
        self.win.minsize(880, 620)
        self.win.config(bg=theme.SILVER)
        self.win.transient(parent)

        self._build_ui()
        self.win.after(120, lambda: self.ent_search.focus_set())

    def _notify_change(self):
        """Tell the parent app to refresh Students Directory (Paid/Balance)."""
        if not self.on_change:
            return
        try:
            self.on_change()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Small UI helpers (visual only)
    # ------------------------------------------------------------------
    def _metric_card(self, parent, title, value, accent, icon_text=""):
        """Compact metric tile used in Outstanding Fee Summary."""
        card = tk.Frame(
            parent, bg=CARD_BG,
            highlightbackground=BORDER, highlightthickness=1,
            padx=12, pady=10,
        )
        top = tk.Frame(card, bg=CARD_BG)
        top.pack(fill=tk.X)
        tk.Label(top, text=title, font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED).pack(side=tk.LEFT)
        if icon_text:
            tk.Label(top, text=icon_text, font=theme.FONT_SMALL, bg=CARD_BG, fg=accent).pack(side=tk.RIGHT)
        tk.Label(card, text=value, font=theme.FONT_BODY_BOLD, bg=CARD_BG, fg=accent).pack(anchor="w", pady=(4, 0))
        # accent underline
        tk.Frame(card, bg=accent, height=3).pack(fill=tk.X, pady=(8, 0))
        return card

    def _section_title(self, parent, text, badge=None, badge_kind="warning"):
        row = tk.Frame(parent, bg=CARD_BG)
        row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(row, text=text, font=theme.FONT_BODY_BOLD, bg=CARD_BG, fg=DARK).pack(side=tk.LEFT)
        if badge:
            kind_map = {
                "success": ("#dcfce7", theme.SUCCESS),
                "warning": ("#fef3c7", theme.WARNING),
                "danger": ("#fee2e2", theme.DANGER),
                "info": ("#e0f2fe", theme.INFO),
            }
            bg, fg = kind_map.get(badge_kind, ("#f1f5f9", MUTED))
            lbl = tk.Label(row, text=f"  {badge}  ", font=theme.FONT_SMALL,
                           bg=bg, fg=fg)
            lbl.pack(side=tk.LEFT, padx=(8, 0))
        return row

    # ------------------------------------------------------------------
    # UI scaffold
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---- Top header (matches reference: SCHOOL FEE MANAGEMENT SYSTEM) ----
        header = tk.Frame(self.win, bg=theme.NAVY, padx=20, pady=16)
        header.pack(fill=tk.X)

        left = tk.Frame(header, bg=theme.NAVY)
        left.pack(side=tk.LEFT, fill=tk.Y)
        title_row = tk.Frame(left, bg=theme.NAVY)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="🛡", font=("Segoe UI", 16), bg=theme.NAVY, fg="white").pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(title_row, text="SCHOOL FEE MANAGEMENT SYSTEM",
                 font=theme.FONT_H1, bg=theme.NAVY, fg="white").pack(side=tk.LEFT)
        tk.Label(left, text="Manage student fees, collect payments and generate receipts",
                 font=theme.FONT_SMALL, bg=theme.NAVY, fg="#94a3b8").pack(anchor="w", pady=(2, 0))

        # Search box in header (reference style)
        search_wrap = tk.Frame(header, bg=theme.NAVY)
        search_wrap.pack(side=tk.RIGHT, padx=(12, 0))
        search_inner = tk.Frame(search_wrap, bg="white", highlightthickness=0)
        search_inner.pack()
        self.ent_search = tk.Entry(search_inner, font=theme.FONT_BODY, width=28,
                                   relief="flat", bg="white", fg=DARK)
        self.ent_search.pack(side=tk.LEFT, padx=(10, 4), ipady=6)
        self.ent_search.insert(0, "Search student by ID or name...")
        self.ent_search.bind("<FocusIn>", self._on_search_focus_in)
        self.ent_search.bind("<FocusOut>", self._on_search_focus_out)
        self.ent_search.bind("<Return>", lambda e: self.search_student())
        btn_search = tk.Button(
            search_inner, text="🔍", font=theme.FONT_BODY, bg=ACCENT_BLUE, fg="white",
            relief="flat", padx=10, pady=4, cursor="hand2",
            command=self.search_student,
        )
        btn_search.pack(side=tk.LEFT, padx=(0, 2), pady=2)

        # ---- Scrollable container ----
        outer = tk.Frame(self.win, bg=theme.SILVER)
        outer.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(outer, bg=theme.SILVER, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        body = tk.Frame(self._canvas, bg=theme.SILVER, padx=18, pady=16)
        self._canvas_window = self._canvas.create_window((0, 0), window=body, anchor="nw")
        self._body = body

        def _on_frame_configure(event=None):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas_configure(event):
            self._canvas.itemconfig(self._canvas_window, width=event.width)

        body.bind("<Configure>", _on_frame_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            if event.num == 4 or event.delta > 0:
                self._canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                self._canvas.yview_scroll(1, "units")

        self._canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._canvas.bind_all("<Button-4>", _on_mousewheel)
        self._canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(event=None):
            self._canvas.unbind_all("<MouseWheel>")
            self._canvas.unbind_all("<Button-4>")
            self._canvas.unbind_all("<Button-5>")

        self.win.bind("<Destroy>", _unbind_wheel)

        # ---- Student quick bar (ID / Name / Class / Status) ----
        self.quick_bar = tk.Frame(body, bg=theme.SILVER)
        self.quick_bar.pack(fill=tk.X, pady=(0, 10))
        self.lbl_search_status = tk.Label(
            self.quick_bar, text="", font=theme.FONT_SMALL,
            bg=theme.SILVER, fg=theme.DANGER,
        )
        self.lbl_search_status.pack(side=tk.LEFT)
        self.btn_back = theme.primary_button(
            self.quick_bar, "← Back to Students", self._clear_student_view, bg=theme.SLATE
        )
        self.btn_back.pack(side=tk.RIGHT)
        self.btn_back.pack_forget()

        # ---- Student Profile + Outstanding Fee Summary (side by side) ----
        top_row = tk.Frame(body, bg=theme.SILVER)
        top_row.pack(fill=tk.X, pady=(0, 10))

        # Profile card
        self.profile_card, self.info_body = theme.section_card(top_row, "STUDENT PROFILE")
        self.profile_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self.info_labels = {}
        self._build_profile_placeholder()

        # Outstanding summary card
        self.summary_outer, self.summary_body = theme.section_card(top_row, "OUTSTANDING FEE SUMMARY")
        self.summary_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._render_empty_summary()

        # ---- Fee Details strip (Monthly Fee / Prev Bal / Discount / Paid / Remaining) ----
        self.details_card, self.details_body = theme.section_card(body, "FEE DETAILS")
        self.details_card.pack(fill=tk.X, pady=(0, 10))
        self._render_empty_details()

        # ---- Collect Payment + Apply Discount (side by side) ----
        self.actions_row = tk.Frame(body, bg=theme.SILVER)
        self.actions_row.pack(fill=tk.X, pady=(0, 10))
        self.pay_card = None
        self.disc_card = None
        self._render_empty_actions()

        # ---- Live Receipt Preview ----
        self.receipt_card, self.receipt_body = theme.section_card(body, "LIVE RECEIPT PREVIEW")
        self.receipt_frame = tk.Frame(self.receipt_body, bg=CARD_BG)
        self.receipt_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.receipt_card.pack_forget()

        # ---- Monthly Fee Cycle History ----
        self.hist_card, hist_body = theme.section_card(body, "MONTHLY FEE CYCLE HISTORY")
        self.hist_card.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        columns = ("period", "fee", "discount", "prev_bal", "due", "paid", "balance", "status", "action")
        headers = {
            "period": "Month/Year", "fee": "Fee", "discount": "Discount",
            "prev_bal": "Prev Bal", "due": "Amount Due", "paid": "Paid",
            "balance": "Balance", "status": "Status", "action": "Action",
        }
        widths = {
            "period": 100, "fee": 80, "discount": 75, "prev_bal": 75,
            "due": 90, "paid": 80, "balance": 90, "status": 80, "action": 55,
        }
        self.tree_cycles = ttk.Treeview(hist_body, columns=columns, show="headings", height=5)
        for c in columns:
            self.tree_cycles.heading(c, text=headers[c])
            self.tree_cycles.column(c, width=widths[c], anchor="center")
        self.tree_cycles.pack(fill=tk.BOTH, expand=True)
        self.tree_cycles.bind("<<TreeviewSelect>>", self._on_row_select)
        for status, color in STATUS_COLORS.items():
            self.tree_cycles.tag_configure(status, foreground=color)
        tk.Label(
            hist_body,
            text="Click a month to review or act on that cycle instead of the latest one.",
            font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED,
        ).pack(anchor="w", pady=(6, 0))

        # ---- Additional Fees (card grid style) ----
        self.add_fee_card, add_fee_body = theme.section_card(
            body, "ADDITIONAL FEES (ANNUAL / EXAM / LAB / OTHER)"
        )
        self.add_fee_card.pack(fill=tk.X, pady=(0, 10))
        self.add_fee_body = add_fee_body
        self.tree_add_fees = None
        self.selected_add_charge = None
        self._render_additional_fees_empty()

        # ---- Admin Tools (organized action section) ----
        if self.can_edit or self.can_admin or self.can_reports:
            admin_card, admin_body = theme.section_card(body, "ADMIN TOOLS")
            admin_card.pack(fill=tk.X, pady=(0, 6))
            admin_row = tk.Frame(admin_body, bg=CARD_BG)
            admin_row.pack(fill=tk.X)

            tools = []
            if self.can_admin or self.can_edit:
                tools.append(("⚡  Generate Current Month Fee Cycle", self._generate_current_month_cycles, theme.SLATE))
            if self.can_admin:
                tools.append(("🔄  Refresh Overdue Statuses", self._refresh_overdue, theme.SLATE))
                tools.append(("🏫  Bulk Additional Fees", self._bulk_assign_additional_fees, theme.SLATE))
            if self.can_reports:
                tools.append(("📊  Reports", self._open_reports_dialog, theme.SLATE))

            for i, (label, cmd, bg) in enumerate(tools):
                btn = theme.primary_button(admin_row, label, cmd, bg=bg)
                btn.pack(side=tk.LEFT, padx=(0 if i == 0 else 8, 0), ipady=4)
        else:
            self.btn_generate = None

    def _on_search_focus_in(self, _e=None):
        if self.ent_search.get() == "Search student by ID or name...":
            self.ent_search.delete(0, tk.END)
            self.ent_search.config(fg=DARK)

    def _on_search_focus_out(self, _e=None):
        if not self.ent_search.get().strip():
            self.ent_search.insert(0, "Search student by ID or name...")
            self.ent_search.config(fg=MUTED)

    def _build_profile_placeholder(self):
        for w in self.info_body.winfo_children():
            w.destroy()
        info_row = tk.Frame(self.info_body, bg=CARD_BG)
        info_row.pack(fill=tk.X)
        photo_box = tk.Frame(
            info_row, bg="#e2e8f0", width=78, height=92,
            highlightbackground=BORDER, highlightthickness=1,
        )
        photo_box.pack(side=tk.LEFT, padx=(0, 14), pady=2)
        photo_box.pack_propagate(False)
        self.lbl_fee_photo = tk.Label(
            photo_box, text="No\nPhoto", bg="#e2e8f0",
            fg=MUTED, font=theme.FONT_SMALL,
        )
        self.lbl_fee_photo.pack(expand=True)

        grid = tk.Frame(info_row, bg=CARD_BG)
        grid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for i, (label, key) in enumerate([
            ("Name", "name"), ("Student ID", "student_id"),
            ("Class/Section", "class_sec"), ("Status", "status"),
        ]):
            tk.Label(grid, text=label, font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED).grid(
                row=i, column=0, sticky="w", padx=(0, 10), pady=3
            )
            lbl = tk.Label(grid, text="—", font=theme.FONT_BODY_BOLD, bg=CARD_BG, fg=DARK)
            lbl.grid(row=i, column=1, sticky="w", pady=3)
            self.info_labels[key] = lbl

    def _render_empty_summary(self):
        for w in self.summary_body.winfo_children():
            w.destroy()
        tk.Label(
            self.summary_body,
            text="Search a student to see outstanding fee summary.",
            font=theme.FONT_BODY, bg=CARD_BG, fg=MUTED,
        ).pack(anchor="w", pady=8)
        if hasattr(self, "receipt_card"):
            self.receipt_card.pack_forget()

    def _render_empty_details(self):
        for w in self.details_body.winfo_children():
            w.destroy()
        tk.Label(
            self.details_body,
            text="Fee details will appear after selecting a student / cycle.",
            font=theme.FONT_BODY, bg=CARD_BG, fg=MUTED,
        ).pack(anchor="w", pady=4)

    def _render_empty_actions(self):
        for w in self.actions_row.winfo_children():
            w.destroy()
        placeholder = tk.Frame(self.actions_row, bg=theme.SILVER)
        placeholder.pack(fill=tk.X)
        tk.Label(
            placeholder,
            text="Collect Payment and Apply Discount panels appear when a cycle with outstanding balance is selected.",
            font=theme.FONT_SMALL, bg=theme.SILVER, fg=MUTED,
        ).pack(anchor="w")

    def _clear_student_view(self):
        self.student = None
        self.selected_cycle = None
        self.ent_search.delete(0, tk.END)
        self.ent_search.insert(0, "Search student by ID or name...")
        self.ent_search.config(fg=MUTED)
        self.lbl_search_status.config(text="")
        self.btn_back.pack_forget()
        self._clear_info()
        self._render_empty_summary()
        self._render_empty_details()
        self._render_empty_actions()
        self.receipt_card.pack_forget()
        self._render_additional_fees_empty()

    # ------------------------------------------------------------------
    # Search / load
    # ------------------------------------------------------------------
    def search_student(self):
        raw = self.ent_search.get().strip()
        if raw == "Search student by ID or name...":
            raw = ""
        sid = raw
        self.lbl_search_status.config(text="")

        if not sid:
            self.lbl_search_status.config(text="Enter a Student ID.")
            return

        row = db.run(
            "SELECT student_id, name, class_sec, status, phone, photo_path "
            "FROM students WHERE student_id=?",
            (sid,), fetchone=True,
        )
        if not row:
            # fallback: try name search
            row = db.run(
                "SELECT student_id, name, class_sec, status, phone, photo_path "
                "FROM students WHERE name LIKE ? LIMIT 1",
                (f"%{sid}%",), fetchone=True,
            )
        if not row:
            self.student = None
            self._clear_info()
            self._render_empty_summary()
            self._render_empty_details()
            self._render_empty_actions()
            self.lbl_search_status.config(text=f"⚠ No student found for '{sid}'.")
            return

        s_id, name, cls, status, phone, photo_path = row
        self.student = {
            "student_id": s_id, "name": name, "class_sec": cls or "-",
            "status": status or "Active", "phone": phone or "",
            "photo_path": photo_path or "",
        }
        self.info_labels["name"].config(text=name)
        self.info_labels["student_id"].config(text=s_id)
        self.info_labels["class_sec"].config(text=cls or "-")
        status_txt = status or "Active"
        self.info_labels["status"].config(
            text=f"● {status_txt}",
            fg=theme.SUCCESS if status_txt == "Active" else theme.WARNING,
        )

        # Student photo
        try:
            from student_photos_util import apply_photo_to_label
            apply_photo_to_label(
                self.lbl_fee_photo, photo_path, size=(72, 86),
                student_id=s_id, placeholder_text="No\nPhoto",
            )
        except Exception:
            try:
                self.lbl_fee_photo.configure(image="", text="No\nPhoto")
                self.lbl_fee_photo.image = None
            except Exception:
                pass

        if status_txt != "Active":
            self.lbl_search_status.config(text=f"⚠ Student '{name}' is Archived — read-only.")

        self.btn_back.pack(side=tk.RIGHT)
        self.receipt_card.pack_forget()
        # Load additional fees FIRST so Total Remaining includes them when cycle is selected
        self._load_additional_fees()
        self._load_history()

    def _clear_info(self):
        for key, lbl in self.info_labels.items():
            lbl.config(text="—", fg=DARK)
        try:
            self.lbl_fee_photo.configure(image="", text="No\nPhoto")
            self.lbl_fee_photo.image = None
        except Exception:
            pass
        self.tree_cycles.delete(*self.tree_cycles.get_children())

    def _cycle_balance(self, c):
        """Outstanding on a single monthly cycle."""
        return round((c.get("amount_due") or 0) - (c.get("amount_paid") or 0), 2)

    def _total_monthly_outstanding(self):
        """Sum of outstanding across ALL monthly cycles for current student."""
        if not self.cycles:
            return 0.0
        return round(sum(self._cycle_balance(c) for c in self.cycles if self._cycle_balance(c) > 0), 2)

    def _total_additional_outstanding(self):
        """Sum of outstanding additional fees (exam/annual/lab/other)."""
        return round(getattr(self, "_additional_outstanding_total", 0.0) or 0.0, 2)

    def _total_student_outstanding(self):
        """Combined monthly + additional outstanding (true student balance)."""
        return round(self._total_monthly_outstanding() + self._total_additional_outstanding(), 2)

    def _older_unpaid_cycles(self, reference_cycle):
        """Cycles older than reference that still have outstanding balance (FIFO order)."""
        if not reference_cycle or not self.cycles:
            return []
        ref_key = (reference_cycle.get("billing_year", 0), reference_cycle.get("billing_month", 0))
        older = []
        for c in self.cycles:
            key = (c.get("billing_year", 0), c.get("billing_month", 0))
            if key < ref_key and self._cycle_balance(c) > 0:
                older.append(c)
        # oldest first
        older.sort(key=lambda x: (x.get("billing_year", 0), x.get("billing_month", 0)))
        return older

    def _load_history(self, preserve_selected_id=None):
        self.tree_cycles.delete(*self.tree_cycles.get_children())
        if not self.student:
            self._render_empty_summary()
            self._render_empty_details()
            self._render_empty_actions()
            return
        self.cycles = fee_cycles.get_student_ledger(self.user_role, self.student["student_id"])
        self._cycles_by_id = {c["id"]: c for c in self.cycles}
        for c in self.cycles:
            period = f"{MONTH_NAMES[c['billing_month'] - 1]} {c['billing_year']}"
            balance = self._cycle_balance(c)
            tag = c["status"]
            is_current = (
                c["billing_month"] == datetime.now().month
                and c["billing_year"] == datetime.now().year
            )
            period_disp = f"{period}  ▶" if is_current else period
            self.tree_cycles.insert(
                "", tk.END, iid=str(c["id"]), tags=(tag,),
                values=(
                    period_disp,
                    f"Rs. {c['fee_amount']:,.0f}",
                    f"Rs. {c['discount']:,.0f}",
                    f"Rs. {c['previous_balance']:,.0f}",
                    f"Rs. {c['amount_due']:,.0f}",
                    f"Rs. {c['amount_paid']:,.0f}",
                    f"Rs. {balance:,.0f}",
                    c["status"].title() if c["status"] else "—",
                    "👁",
                ),
            )

        if self.cycles:
            target_id = (
                preserve_selected_id
                if (preserve_selected_id and preserve_selected_id in self._cycles_by_id)
                else self.cycles[0]["id"]
            )
            self.tree_cycles.selection_set(str(target_id))
            self._select_cycle(self._cycles_by_id.get(target_id))
        else:
            self._select_cycle(None)

    def _on_row_select(self, event=None):
        sel = self.tree_cycles.selection()
        if not sel:
            return
        self._select_cycle(self._cycles_by_id.get(int(sel[0])))

    # ------------------------------------------------------------------
    # Current Fee Summary + Details + Actions
    # ------------------------------------------------------------------
    def _select_cycle(self, cycle):
        self.selected_cycle = cycle
        for w in self.summary_body.winfo_children():
            w.destroy()
        for w in self.details_body.winfo_children():
            w.destroy()
        for w in self.actions_row.winfo_children():
            w.destroy()

        if not cycle:
            tk.Label(
                self.summary_body,
                text="No fee cycle found for this student yet."
                     + ("\nUse Admin Tools → Generate Current Month Fee Cycle to start one."
                        if self.can_edit else ""),
                font=theme.FONT_BODY, bg=CARD_BG, fg=MUTED, justify="left",
            ).pack(anchor="w", pady=6)
            self._render_empty_details()
            self._render_empty_actions()
            return

        balance = self._cycle_balance(cycle)
        period = f"{MONTH_NAMES[cycle['billing_month'] - 1]} {cycle['billing_year']}"
        paid = cycle["amount_paid"] or 0
        due = cycle["amount_due"] or 0
        progress = 0
        if due > 0:
            progress = min(100, max(0, int(round((paid / due) * 100))))

        # Combined outstanding = all monthly cycles + additional fees
        total_monthly = self._total_monthly_outstanding()
        total_additional = self._total_additional_outstanding()
        total_all = round(total_monthly + total_additional, 2)
        older = self._older_unpaid_cycles(cycle)
        older_due = round(sum(self._cycle_balance(o) for o in older), 2)

        # ---- Outstanding Fee Summary metrics ----
        metrics = tk.Frame(self.summary_body, bg=CARD_BG)
        metrics.pack(fill=tk.X, pady=(0, 6))
        metrics.columnconfigure((0, 1, 2, 3), weight=1)

        tiles = [
            ("Monthly Fee", f"Rs. {cycle['fee_amount']:,.2f}", ACCENT_BLUE, "📄"),
            ("Paid", f"Rs. {paid:,.2f}", ACCENT_GREEN, "✓"),
            ("Discount", f"Rs. {cycle['discount']:,.2f}", ACCENT_PURPLE, "🏷"),
            # Show TOTAL student remaining (monthly all cycles + additional)
            ("Total Remaining", f"Rs. {total_all:,.2f}", ACCENT_RED, "⚠"),
        ]
        for i, (title, val, accent, icon) in enumerate(tiles):
            card = self._metric_card(metrics, title, val, accent, icon)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))

        # Breakdown line under metrics
        breakdown = tk.Frame(self.summary_body, bg=CARD_BG)
        breakdown.pack(fill=tk.X, pady=(2, 4))
        parts = [f"This cycle: Rs. {balance:,.0f}"]
        if older_due > 0:
            parts.append(f"Older months: Rs. {older_due:,.0f}")
        if total_additional > 0:
            parts.append(f"Additional fees: Rs. {total_additional:,.0f}")
        tk.Label(
            breakdown,
            text="  ·  ".join(parts),
            font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED,
        ).pack(anchor="w")

        # Payment progress bar
        prog_frame = tk.Frame(self.summary_body, bg=CARD_BG)
        prog_frame.pack(fill=tk.X, pady=(8, 2))
        tk.Label(prog_frame, text="Payment Progress", font=theme.FONT_SMALL,
                 bg=CARD_BG, fg=MUTED).pack(side=tk.LEFT)
        tk.Label(prog_frame, text=f"{progress}%", font=theme.FONT_SMALL,
                 bg=CARD_BG, fg=DARK).pack(side=tk.RIGHT)

        bar_bg = tk.Frame(self.summary_body, bg="#e2e8f0", height=8)
        bar_bg.pack(fill=tk.X, pady=(2, 4))
        bar_bg.pack_propagate(False)
        if progress > 0:
            fill_w = max(4, int(progress * 2.4))  # approximate visual width
            bar_fill = tk.Frame(bar_bg, bg=ACCENT_GREEN if progress >= 100 else ACCENT_BLUE, height=8)
            bar_fill.place(x=0, y=0, relheight=1, relwidth=progress / 100.0)

        # ---- Fee Details strip ----
        self.details_card.configure()  # ensure visible
        title_row = self._section_title(
            self.details_body,
            f"FEE DETAILS — {period.upper()}",
            badge=cycle["status"].title() if cycle.get("status") else None,
            badge_kind={
                "PAID": "success", "ADVANCE": "info", "PARTIAL": "warning",
                "OVERDUE": "danger", "PENDING": "info",
            }.get(cycle.get("status"), "info"),
        )

        figures = tk.Frame(self.details_body, bg=CARD_BG)
        figures.pack(fill=tk.X)
        rows = [
            ("Monthly Fee", cycle["fee_amount"], DARK),
            ("Previous Balance", cycle["previous_balance"], DARK),
            ("Discount", cycle["discount"], ACCENT_PURPLE),
            ("Paid", paid, ACCENT_GREEN),
            ("Remaining", balance, ACCENT_RED if balance > 0 else ACCENT_GREEN),
        ]
        for i, (label, value, color) in enumerate(rows):
            col = tk.Frame(figures, bg=CARD_BG)
            col.grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else 18, 0), pady=2)
            tk.Label(col, text=label, font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED).pack(anchor="w")
            tk.Label(col, text=f"Rs. {value:,.2f}", font=theme.FONT_BODY_BOLD,
                     bg=CARD_BG, fg=color).pack(anchor="w")

        # ---- Collect Payment + Apply Discount ----
        can_act = self.can_edit and (self.student or {}).get("status", "Active") == "Active"

        if can_act and balance > 0:
            # Collect Payment card
            pay_outer = tk.Frame(
                self.actions_row, bg=CARD_BG,
                highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10,
            )
            pay_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            tk.Label(pay_outer, text="💵  COLLECT PAYMENT", font=theme.FONT_BODY_BOLD,
                     bg=CARD_BG, fg=DARK).pack(anchor="w", pady=(0, 8))

            row1 = tk.Frame(pay_outer, bg=CARD_BG)
            row1.pack(fill=tk.X, pady=3)
            tk.Label(row1, text="Amount (Rs.)", font=theme.FONT_SMALL, bg=CARD_BG,
                     width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_pay_amount = tk.Entry(row1, font=theme.FONT_BODY)
            self.ent_pay_amount.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)
            self.ent_pay_amount.insert(0, f"{balance:.2f}")

            row2 = tk.Frame(pay_outer, bg=CARD_BG)
            row2.pack(fill=tk.X, pady=3)
            tk.Label(row2, text="Method", font=theme.FONT_SMALL, bg=CARD_BG,
                     width=14, anchor="w").pack(side=tk.LEFT)
            self.cmb_pay_method = ttk.Combobox(
                row2, values=PAYMENT_METHODS, state="readonly", font=theme.FONT_BODY
            )
            self.cmb_pay_method.current(0)
            self.cmb_pay_method.pack(side=tk.LEFT, fill=tk.X, expand=True)

            row3 = tk.Frame(pay_outer, bg=CARD_BG)
            row3.pack(fill=tk.X, pady=3)
            tk.Label(row3, text="Remarks (Optional)", font=theme.FONT_SMALL, bg=CARD_BG,
                     width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_pay_remarks = tk.Entry(row3, font=theme.FONT_BODY)
            self.ent_pay_remarks.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

            # Remaining after payment preview
            preview = tk.Frame(pay_outer, bg="#ecfdf5", padx=10, pady=8)
            preview.pack(fill=tk.X, pady=(10, 6))
            tk.Label(preview, text="Remaining After Payment", font=theme.FONT_SMALL,
                     bg="#ecfdf5", fg=MUTED).pack(anchor="w")
            self.lbl_remaining_preview = tk.Label(
                preview, text="Rs. 0.00", font=theme.FONT_BODY_BOLD,
                bg="#ecfdf5", fg=ACCENT_GREEN,
            )
            self.lbl_remaining_preview.pack(anchor="w")
            tk.Label(
                preview, text="This payment will clear all outstanding dues.",
                font=theme.FONT_SMALL, bg="#ecfdf5", fg=ACCENT_GREEN,
            ).pack(anchor="w", pady=(2, 0))

            def _update_preview(*_a):
                try:
                    amt = float(self.ent_pay_amount.get().strip() or 0)
                    rem = max(0.0, round(balance - amt, 2))
                    self.lbl_remaining_preview.config(
                        text=f"Rs. {rem:,.2f}",
                        fg=ACCENT_GREEN if rem <= 0 else ACCENT_RED,
                    )
                except ValueError:
                    pass

            self.ent_pay_amount.bind("<KeyRelease>", _update_preview)

            self.btn_pay = theme.primary_button(
                pay_outer, "✓  RECORD PAYMENT", self._record_payment, bg=theme.SUCCESS
            )
            self.btn_pay.pack(fill=tk.X, pady=(4, 0), ipady=5)

            # Apply Discount card
            disc_outer = tk.Frame(
                self.actions_row, bg=CARD_BG,
                highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10,
            )
            disc_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            tk.Label(disc_outer, text="🏷  APPLY DISCOUNT", font=theme.FONT_BODY_BOLD,
                     bg=CARD_BG, fg=DARK).pack(anchor="w", pady=(0, 8))

            drow1 = tk.Frame(disc_outer, bg=CARD_BG)
            drow1.pack(fill=tk.X, pady=3)
            tk.Label(drow1, text="Amount (Rs.)", font=theme.FONT_SMALL, bg=CARD_BG,
                     width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_disc_amount = tk.Entry(drow1, font=theme.FONT_BODY)
            self.ent_disc_amount.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

            drow2 = tk.Frame(disc_outer, bg=CARD_BG)
            drow2.pack(fill=tk.X, pady=3)
            tk.Label(drow2, text="Reason", font=theme.FONT_SMALL, bg=CARD_BG,
                     width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_disc_reason = tk.Entry(drow2, font=theme.FONT_BODY)
            self.ent_disc_reason.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

            # spacer so button aligns better
            tk.Frame(disc_outer, bg=CARD_BG, height=48).pack()

            self.btn_discount = theme.primary_button(
                disc_outer, "🏷  APPLY DISCOUNT", self._apply_discount, bg=ACCENT_PURPLE
            )
            self.btn_discount.pack(fill=tk.X, pady=(4, 0), ipady=5)

        elif balance <= 0:
            ok = tk.Frame(self.actions_row, bg="#ecfdf5", padx=14, pady=12)
            ok.pack(fill=tk.X)
            tk.Label(
                ok,
                text=f"✅  This cycle is fully settled ({cycle['status']}). No payment due.",
                font=theme.FONT_BODY, bg="#ecfdf5", fg=ACCENT_GREEN,
            ).pack(anchor="w")
        elif not can_act:
            tk.Label(
                self.actions_row,
                text="You do not have permission to record payments, or this student is archived.",
                font=theme.FONT_SMALL, bg=theme.SILVER, fg=MUTED,
            ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Payment Recording & Direct Receipt Rendering
    # ------------------------------------------------------------------
    def _record_payment(self):
        """Record payment with FIFO allocation across older unpaid months first.

        1. Apply amount to oldest pending/partial cycles first (so their status
           becomes PAID and data stays clean).
        2. Apply leftover to the selected (usually current) cycle.
        3. Reload ledger so UI + Students Directory balance update correctly.
        """
        if not self.selected_cycle or not self.student:
            return
        try:
            amount = float(self.ent_pay_amount.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Amount", "Enter a valid payment amount.", parent=self.win)
            return
        if amount <= 0:
            messagebox.showerror("Invalid Amount", "Payment amount must be greater than zero.", parent=self.win)
            return

        c = dict(self.selected_cycle)  # Preserve cycle snapshot prior to backend update
        cycle_bal = self._cycle_balance(c)
        older = self._older_unpaid_cycles(c)
        older_due = round(sum(self._cycle_balance(o) for o in older), 2)
        total_monthly_due = round(older_due + max(0.0, cycle_bal), 2)

        # Allow payment up to full monthly outstanding; warn only beyond that
        if amount > total_monthly_due + 0.009:
            if not messagebox.askyesno(
                "Overpayment Warning",
                f"Payment (Rs. {amount:,.2f}) exceeds total monthly outstanding "
                f"(Rs. {total_monthly_due:,.2f}).\n"
                "Extra will be recorded as advance/credit on the selected cycle. Continue?",
                parent=self.win,
            ):
                return

        # Explain FIFO when older months exist
        if older_due > 0 and amount > 0:
            if not messagebox.askyesno(
                "Clear Older Months First",
                f"This student has Rs. {older_due:,.2f} pending from previous month(s).\n\n"
                f"Payment will be applied oldest → newest (FIFO):\n"
                f"  1) Previous months cleared first\n"
                f"  2) Remaining goes to {MONTH_NAMES[c['billing_month']-1]} {c['billing_year']}\n\n"
                "Continue?",
                parent=self.win,
            ):
                return

        method = self.cmb_pay_method.get() or "Cash"
        remarks = self.ent_pay_remarks.get().strip()

        self.btn_pay.config(state="disabled", text="Processing...")
        self.win.update_idletasks()

        remaining_to_apply = amount
        last_result = None
        applied_summary = []  # [(period, paid_amount), ...]

        try:
            # --- FIFO: older cycles first ---
            for old in older:
                if remaining_to_apply <= 0:
                    break
                old_bal = self._cycle_balance(old)
                if old_bal <= 0:
                    continue
                pay_amt = min(remaining_to_apply, old_bal)
                result = fee_cycles.record_payment(
                    self.user_role, old["id"], pay_amt, method,
                    self.current_user,
                    remarks=(remarks or "") + f" [FIFO from {MONTH_NAMES[c['billing_month']-1]} {c['billing_year']} payment]",
                )
                last_result = result
                remaining_to_apply = round(remaining_to_apply - pay_amt, 2)
                period = f"{MONTH_NAMES[old['billing_month']-1]} {old['billing_year']}"
                applied_summary.append((period, pay_amt))

            # --- Leftover (or full amount) on selected cycle ---
            if remaining_to_apply > 0:
                result = fee_cycles.record_payment(
                    self.user_role, c["id"], remaining_to_apply, method,
                    self.current_user, remarks=remarks,
                )
                last_result = result
                period = f"{MONTH_NAMES[c['billing_month']-1]} {c['billing_year']}"
                applied_summary.append((period, remaining_to_apply))
                remaining_to_apply = 0.0

        except (ValueError, rbac.PermissionDenied) as e:
            messagebox.showerror("Could Not Record Payment", str(e), parent=self.win)
            self.btn_pay.config(state="normal", text="✓  RECORD PAYMENT")
            # Partial applies may have succeeded — reload anyway
            self._load_history(preserve_selected_id=c["id"])
            self._notify_change()
            return
        except Exception as e:
            messagebox.showerror("Database Error", f"Could not record the payment:\n{e}", parent=self.win)
            self.btn_pay.config(state="normal", text="✓  RECORD PAYMENT")
            self._load_history(preserve_selected_id=c["id"])
            self._notify_change()
            return

        # Reload ledger + additional fees so statuses and balances refresh
        self._load_history(preserve_selected_id=c["id"])
        self._load_additional_fees()
        self._notify_change()

        # Receipt for the primary (selected) allocation
        receipt_no = (last_result or {}).get("receipt_no", "—")
        primary_paid = next((a for p, a in applied_summary if p.endswith(str(c["billing_year"]))), amount)
        # Prefer amount applied to selected cycle; fall back to full amount
        selected_applied = amount
        for p, a in applied_summary:
            if f"{MONTH_NAMES[c['billing_month']-1]} {c['billing_year']}" == p:
                selected_applied = a
                break
        self._show_receipt_actions(receipt_no, c, selected_applied, method)

        # Show FIFO summary if multiple cycles touched
        if len(applied_summary) > 1:
            lines = "\n".join(f"  • {p}: Rs. {a:,.2f}" for p, a in applied_summary)
            messagebox.showinfo(
                "Payment Allocated (FIFO)",
                f"Total Rs. {amount:,.2f} applied as:\n{lines}\n\n"
                "Previous months are now updated to Paid where fully cleared.",
                parent=self.win,
            )

        # WhatsApp Integration
        try:
            remaining = self._total_student_outstanding()
            msg = db.render_msg_template(
                "msg_template_fee_payment",
                amount=f"{amount:,.0f}",
                student_name=self.student["name"],
                remaining=f"{remaining:,.0f}",
                status=c.get("status", ""),
                school_name=db.get_setting("school_name", "AR Academy"),
            )
            whatsapp_notify.open_whatsapp(self.student.get("phone", ""), msg)
        except Exception:
            pass  # never block fee flow if WhatsApp fails

    def _show_receipt_actions(self, receipt_no, cycle_before, current_payment, method):
        for w in self.receipt_frame.winfo_children():
            w.destroy()

        s = self.student
        previous_paid = cycle_before["amount_paid"] or 0
        total_fee = cycle_before["fee_amount"]
        balance = round((cycle_before["amount_due"] or 0) - (previous_paid + current_payment), 2)
        pay_date = datetime.now().strftime("%d-%b-%Y")
        period = f"{MONTH_NAMES[cycle_before['billing_month'] - 1]} {cycle_before['billing_year']}"

        # Two-column layout: live preview | note + buttons
        content = tk.Frame(self.receipt_frame, bg=CARD_BG)
        content.pack(fill=tk.BOTH, expand=True)

        # Left: receipt preview card
        preview = tk.Frame(
            content, bg=CARD_BG,
            highlightbackground=BORDER, highlightthickness=1, padx=14, pady=12,
        )
        preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        tk.Label(preview, text="ABC SCHOOL SYSTEM", font=theme.FONT_BODY_BOLD,
                 bg=CARD_BG, fg=DARK).pack()
        tk.Label(preview, text="Fee Payment Receipt", font=theme.FONT_SMALL,
                 bg=CARD_BG, fg=MUTED).pack(pady=(0, 8))

        meta = tk.Frame(preview, bg=CARD_BG)
        meta.pack(fill=tk.X)
        left_m = tk.Frame(meta, bg=CARD_BG)
        left_m.pack(side=tk.LEFT, fill=tk.X, expand=True)
        right_m = tk.Frame(meta, bg=CARD_BG)
        right_m.pack(side=tk.RIGHT)

        for lbl, val in [
            ("Student Name:", s["name"]),
            ("Student ID:", s["student_id"]),
            ("Class/Section:", s["class_sec"]),
        ]:
            r = tk.Frame(left_m, bg=CARD_BG)
            r.pack(anchor="w")
            tk.Label(r, text=lbl, font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED).pack(side=tk.LEFT)
            tk.Label(r, text=f"  {val}", font=theme.FONT_SMALL, bg=CARD_BG, fg=DARK).pack(side=tk.LEFT)

        for lbl, val in [
            ("Receipt No:", receipt_no),
            ("Date:", pay_date),
            ("Month:", period),
        ]:
            r = tk.Frame(right_m, bg=CARD_BG)
            r.pack(anchor="e")
            tk.Label(r, text=lbl, font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED).pack(side=tk.LEFT)
            tk.Label(r, text=f"  {val}", font=theme.FONT_SMALL, bg=CARD_BG, fg=DARK).pack(side=tk.LEFT)

        # Table header
        tbl = tk.Frame(preview, bg=CARD_BG)
        tbl.pack(fill=tk.X, pady=(12, 0))
        hdr = tk.Frame(tbl, bg=theme.NAVY, padx=6, pady=4)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="DESCRIPTION", font=theme.FONT_SMALL, bg=theme.NAVY, fg="white").pack(side=tk.LEFT)
        tk.Label(hdr, text="AMOUNT (Rs.)", font=theme.FONT_SMALL, bg=theme.NAVY, fg="white").pack(side=tk.RIGHT)

        lines = [
            ("Monthly Fee", f"{total_fee:,.2f}", DARK),
            ("Previous Balance", f"{cycle_before.get('previous_balance', 0):,.2f}", DARK),
            ("Discount", f"{cycle_before.get('discount', 0):,.2f}", DARK),
            ("Paid Amount", f"{current_payment:,.2f}", ACCENT_GREEN),
            ("REMAINING AMOUNT", f"{max(0, balance):,.2f}", ACCENT_RED if balance > 0 else ACCENT_GREEN),
        ]
        for i, (desc, amt, color) in enumerate(lines):
            bg = "#f8fafc" if i % 2 == 0 else CARD_BG
            row = tk.Frame(tbl, bg=bg, padx=6, pady=3)
            row.pack(fill=tk.X)
            tk.Label(row, text=desc, font=theme.FONT_SMALL, bg=bg, fg=DARK).pack(side=tk.LEFT)
            tk.Label(row, text=amt, font=theme.FONT_SMALL, bg=bg, fg=color).pack(side=tk.RIGHT)

        # Right: note + action buttons
        side = tk.Frame(content, bg=CARD_BG, width=220)
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)

        note = tk.Frame(side, bg="#eff6ff", padx=10, pady=10)
        note.pack(fill=tk.X, pady=(0, 10))
        tk.Label(note, text="ℹ  Note:", font=theme.FONT_SMALL, bg="#eff6ff", fg=ACCENT_BLUE).pack(anchor="w")
        tk.Label(
            note,
            text="This is a live preview. Receipt will be generated after recording the payment.",
            font=theme.FONT_SMALL, bg="#eff6ff", fg=MUTED, wraplength=200, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        def gen(path):
            reports.generate_fee_receipt(
                receipt_no, s["student_id"], s["name"], "-", s["class_sec"],
                total_fee, previous_paid, current_payment, balance,
                pay_date, self.current_user, path, payment_method=method,
            )

        def do_open():
            out_path = os.path.join(
                os.getcwd(),
                f"Fee_Receipt_{s['student_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
            )
            try:
                gen(out_path)
            except Exception as e:
                messagebox.showerror(
                    "PDF Error",
                    f"Payment was saved, but the receipt PDF failed:\n{e}",
                    parent=self.win,
                )
                return
            opened = False
            try:
                if os.name == "nt":
                    os.startfile(out_path)
                    opened = True
                elif shutil.which("xdg-open"):
                    os.system(f'xdg-open "{out_path}"')
                    opened = True
                elif shutil.which("open"):
                    os.system(f'open "{out_path}"')
                    opened = True
            except Exception:
                opened = False
            messagebox.showinfo(
                "Receipt Ready",
                f"Receipt opened:\n{out_path}" if opened else f"Receipt saved:\n{out_path}",
                parent=self.win,
            )

        def do_save_as():
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                initialfile=f"Fee_Receipt_{s['student_id']}.pdf",
                filetypes=[("PDF Files", "*.pdf")], parent=self.win,
            )
            if not path:
                return
            try:
                gen(path)
            except Exception as e:
                messagebox.showerror(
                    "PDF Error",
                    f"Payment was saved, but the receipt PDF failed:\n{e}",
                    parent=self.win,
                )
                return
            messagebox.showinfo("Saved", f"Receipt saved:\n{path}", parent=self.win)

        theme.primary_button(side, "🖨  PRINT RECEIPT", do_open, bg=theme.NAVY).pack(
            fill=tk.X, pady=(0, 6), ipady=4
        )
        theme.primary_button(side, "✉  EMAIL RECEIPT", do_save_as, bg=ACCENT_BLUE).pack(
            fill=tk.X, ipady=4
        )

        # Ensure receipt card stays visible
        self.receipt_card.pack(fill=tk.X, pady=(0, 10), before=self.hist_card)
        self.win.update_idletasks()

    # ------------------------------------------------------------------
    # Discount
    # ------------------------------------------------------------------
    def _apply_discount(self):
        if not self.selected_cycle:
            return
        try:
            amount = float(self.ent_disc_amount.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Amount", "Enter a valid discount amount.", parent=self.win)
            return
        reason = self.ent_disc_reason.get().strip()
        if not messagebox.askyesno(
            "Confirm Discount",
            f"Apply a Rs. {amount:,.2f} discount to this cycle?\nReason: {reason or '(none given)'}",
            parent=self.win,
        ):
            return
        try:
            fee_cycles.apply_discount(
                self.user_role, self.selected_cycle["id"], amount, reason, self.current_user
            )
        except (ValueError, rbac.PermissionDenied) as e:
            messagebox.showerror("Could Not Apply Discount", str(e), parent=self.win)
            return
        self._load_history(preserve_selected_id=self.selected_cycle["id"])
        self._notify_change()
        messagebox.showinfo("Discount Applied", "Discount applied successfully.", parent=self.win)

    # ------------------------------------------------------------------
    # Admin Tools
    # ------------------------------------------------------------------
    def _generate_current_month_cycles(self):
        """Generate fee cycles for every Active student for the real system
        current month/year only. Existing cycles are skipped safely.
        Manual back-fill / future cycles are blocked by the backend.
        """
        now = datetime.now()
        month, year = now.month, now.year
        if not messagebox.askyesno(
            "Generate Current Month Cycles",
            f"Create fee cycles for {MONTH_NAMES[month - 1]} {year} "
            f"for every Active student?\n\n"
            "Students who already have a cycle for this month will be skipped.",
            parent=self.win,
        ):
            return
        try:
            result = fee_cycles.bulk_generate_cycle(
                self.user_role, month, year, actor=self.current_user,
            )
        except rbac.PermissionDenied as e:
            messagebox.showerror("Permission Denied", str(e), parent=self.win)
            return
        except ValueError as e:
            messagebox.showerror("Could Not Generate", str(e), parent=self.win)
            return
        messagebox.showinfo(
            "Bulk Generate Complete",
            f"Month: {MONTH_NAMES[month - 1]} {year}\n\n"
            f"Created: {len(result['created'])}\n"
            f"Already existed (skipped): {len(result['skipped'])}\n"
            f"Errors: {len(result['errors'])}",
            parent=self.win,
        )
        if self.student:
            self._load_history()
        self._notify_change()

    def _refresh_overdue(self):
        try:
            changed = fee_cycles.refresh_overdue_statuses(self.user_role, actor=self.current_user)
        except rbac.PermissionDenied as e:
            messagebox.showerror("Permission Denied", str(e), parent=self.win)
            return
        messagebox.showinfo("Statuses Refreshed", f"{changed} cycle(s) updated to OVERDUE.", parent=self.win)
        if self.student:
            self._load_history()
        self._notify_change()

    def _open_reports_dialog(self):
        dlg = tk.Toplevel(self.win)
        dlg.title("Fee Reports")
        dlg.geometry("640x480")
        dlg.config(bg=theme.SILVER)
        dlg.transient(self.win)

        now = datetime.now()
        top = tk.Frame(dlg, bg=theme.SILVER, padx=12, pady=10)
        top.pack(fill=tk.X)
        tk.Label(top, text="Month:", bg=theme.SILVER, font=theme.FONT_BODY).pack(side=tk.LEFT)
        ent_month = tk.Entry(top, width=4, font=theme.FONT_BODY)
        ent_month.insert(0, str(now.month))
        ent_month.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(top, text="Year:", bg=theme.SILVER, font=theme.FONT_BODY).pack(side=tk.LEFT)
        ent_year = tk.Entry(top, width=6, font=theme.FONT_BODY)
        ent_year.insert(0, str(now.year))
        ent_year.pack(side=tk.LEFT, padx=(4, 12))

        class_card, class_body = theme.section_card(dlg, "Class-wise (selected month)")
        class_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        tree_class = ttk.Treeview(
            class_body, columns=("class", "students", "due", "paid", "outstanding"),
            show="headings", height=6,
        )
        for c, t, w in [
            ("class", "Class", 100), ("students", "Students", 70),
            ("due", "Total Due", 90), ("paid", "Total Paid", 90),
            ("outstanding", "Outstanding", 90),
        ]:
            tree_class.heading(c, text=t)
            tree_class.column(c, width=w, anchor="center")
        tree_class.pack(fill=tk.BOTH, expand=True)

        month_card, month_body = theme.section_card(dlg, "Monthly Collection (selected year)")
        month_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        tree_month = ttk.Treeview(
            month_body, columns=("month", "cycles", "due", "paid", "outstanding"),
            show="headings", height=6,
        )
        for c, t, w in [
            ("month", "Month", 90), ("cycles", "Cycles", 70),
            ("due", "Total Due", 90), ("paid", "Total Paid", 90),
            ("outstanding", "Outstanding", 90),
        ]:
            tree_month.heading(c, text=t)
            tree_month.column(c, width=w, anchor="center")
        tree_month.pack(fill=tk.BOTH, expand=True)

        def refresh():
            try:
                month = int(ent_month.get().strip())
                year = int(ent_year.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Input", "Month/Year must be numbers.", parent=dlg)
                return
            tree_class.delete(*tree_class.get_children())
            for r in fee_cycles.class_wise_report(self.user_role, month, year):
                tree_class.insert(
                    "", tk.END,
                    values=(
                        r["class_sec"], r["students"],
                        f"{r['total_due']:,.0f}", f"{r['total_paid']:,.0f}",
                        f"{r['total_outstanding']:,.0f}",
                    ),
                )
            tree_month.delete(*tree_month.get_children())
            for r in fee_cycles.monthly_collection_report(self.user_role, year):
                tree_month.insert(
                    "", tk.END,
                    values=(
                        MONTH_NAMES[r["billing_month"] - 1], r["cycles"],
                        f"{r['total_due']:,.0f}", f"{r['total_paid']:,.0f}",
                        f"{r['total_outstanding']:,.0f}",
                    ),
                )

        theme.primary_button(top, "Refresh", refresh).pack(side=tk.LEFT)
        refresh()

    # ------------------------------------------------------------------
    # Additional Fees (Annual / Exam / Lab / Other)
    # ------------------------------------------------------------------
    def _render_additional_fees_empty(self):
        for w in self.add_fee_body.winfo_children():
            w.destroy()
        tk.Label(
            self.add_fee_body,
            text="Search a student to view / collect their additional fees.\n"
                 "Class-wise or school-wide assignment is available via Bulk Assign.",
            font=theme.FONT_BODY, bg=CARD_BG, fg=MUTED, justify="left",
        ).pack(anchor="w")
        self.tree_add_fees = None
        self.selected_add_charge = None
        btn_row = tk.Frame(self.add_fee_body, bg=CARD_BG)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        if self.can_admin or self.can_edit:
            theme.primary_button(
                btn_row, "🏫  BULK ASSIGN (CLASS / ALL)",
                self._bulk_assign_additional_fees, bg=theme.SLATE,
            ).pack(side=tk.LEFT)

    def _load_additional_fees(self):
        for w in self.add_fee_body.winfo_children():
            w.destroy()
        self.selected_add_charge = None
        self._additional_outstanding_total = 0.0
        if not self.student:
            self._render_additional_fees_empty()
            return
        try:
            additional_fees.ensure_tables()
            charges = additional_fees.get_student_charges(
                self.user_role, self.student["student_id"]
            )
        except Exception as exc:
            tk.Label(
                self.add_fee_body, text=f"Could not load additional fees: {exc}",
                font=theme.FONT_SMALL, bg=CARD_BG, fg=theme.DANGER,
            ).pack(anchor="w")
            return

        # Card grid for summary types (Annual / Exam / Lab / Other)
        type_totals = {}
        total_add_bal = 0.0
        for ch in charges:
            key = ch.get("type_name") or "Other"
            type_totals.setdefault(key, {"amount": 0.0, "paid": 0.0, "balance": 0.0})
            type_totals[key]["amount"] += float(ch.get("amount") or 0)
            type_totals[key]["paid"] += float(ch.get("amount_paid") or 0)
            bal = float(ch.get("balance") or 0)
            type_totals[key]["balance"] += bal
            if bal > 0:
                total_add_bal += bal
        self._additional_outstanding_total = round(total_add_bal, 2)

        icons = {
            "Annual": "📋", "Annual Fees": "📋",
            "Exam": "📝", "Exam Fees": "📝",
            "Lab": "⚗", "Lab Fees": "⚗",
            "Other": "📎", "Other Fees": "📎",
        }
        colors = {
            "Annual": ACCENT_BLUE, "Annual Fees": ACCENT_BLUE,
            "Exam": ACCENT_PURPLE, "Exam Fees": ACCENT_PURPLE,
            "Lab": "#d97706", "Lab Fees": "#d97706",
            "Other": "#db2777", "Other Fees": "#db2777",
        }

        cards_row = tk.Frame(self.add_fee_body, bg=CARD_BG)
        cards_row.pack(fill=tk.X, pady=(0, 10))
        cards_row.columnconfigure((0, 1, 2, 3), weight=1)

        display_order = ["Annual Fees", "Exam Fees", "Lab Fees", "Other Fees",
                         "Annual", "Exam", "Lab", "Other"]
        shown = set()
        col = 0
        for name in display_order:
            if name in type_totals and name not in shown:
                shown.add(name)
                t = type_totals[name]
                card = tk.Frame(
                    cards_row, bg=CARD_BG,
                    highlightbackground=BORDER, highlightthickness=1, padx=10, pady=10,
                )
                card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
                icon = icons.get(name, "📎")
                accent = colors.get(name, ACCENT_BLUE)
                tk.Label(card, text=f"{icon}  {name}", font=theme.FONT_SMALL,
                         bg=CARD_BG, fg=MUTED).pack(anchor="w")
                tk.Label(card, text=f"Rs. {t['amount']:,.2f}", font=theme.FONT_BODY_BOLD,
                         bg=CARD_BG, fg=accent).pack(anchor="w", pady=(4, 2))
                bal = t["balance"]
                tk.Label(
                    card,
                    text=f"Balance: Rs. {bal:,.0f}" if bal > 0 else "Settled",
                    font=theme.FONT_SMALL, bg=CARD_BG,
                    fg=ACCENT_RED if bal > 0 else ACCENT_GREEN,
                ).pack(anchor="w")
                col += 1
                if col >= 4:
                    break

        # Detailed table
        cols = ("type", "year", "amount", "discount", "paid", "balance", "status")
        self.tree_add_fees = ttk.Treeview(
            self.add_fee_body, columns=cols, show="headings", height=4,
        )
        headers = {
            "type": "Fee Type", "year": "Year", "amount": "Amount",
            "discount": "Discount", "paid": "Paid", "balance": "Balance",
            "status": "Status",
        }
        widths = {
            "type": 120, "year": 80, "amount": 80, "discount": 70,
            "paid": 70, "balance": 80, "status": 80,
        }
        for c in cols:
            self.tree_add_fees.heading(c, text=headers[c])
            self.tree_add_fees.column(c, width=widths[c], anchor="center")
        self.tree_add_fees.pack(fill=tk.X, pady=(0, 6))
        for st, color in STATUS_COLORS.items():
            self.tree_add_fees.tag_configure(st, foreground=color)

        self._add_charges_by_id = {}
        for ch in charges:
            self._add_charges_by_id[ch["id"]] = ch
            bal = ch["balance"]
            self.tree_add_fees.insert(
                "", tk.END, iid=str(ch["id"]), tags=(ch["status"],),
                values=(
                    ch["type_name"],
                    ch["academic_year"] or "—",
                    f"{ch['amount']:,.0f}",
                    f"{ch['discount']:,.0f}",
                    f"{ch['amount_paid']:,.0f}",
                    f"{bal:,.0f}",
                    ch["status"],
                ),
            )
        if not charges:
            self.tree_add_fees.insert(
                "", tk.END, values=("No additional fees yet", "—", "—", "—", "—", "—", "—"),
            )

        self.tree_add_fees.bind("<<TreeviewSelect>>", self._on_add_fee_select)

        btn_row = tk.Frame(self.add_fee_body, bg=CARD_BG)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        if self.can_edit and (self.student or {}).get("status", "Active") == "Active":
            theme.primary_button(
                btn_row, "➕  Assign to This Student", self._assign_additional_fee, bg=ACCENT_BLUE,
            ).pack(side=tk.LEFT, padx=(0, 6))
            theme.primary_button(
                btn_row, "💵  Collect Payment", self._collect_additional_payment, bg=theme.SUCCESS,
            ).pack(side=tk.LEFT, padx=(0, 6))
        if self.can_admin or self.can_edit:
            theme.primary_button(
                btn_row, "🏫  BULK ASSIGN (CLASS / ALL)",
                self._bulk_assign_additional_fees, bg=theme.SLATE,
            ).pack(side=tk.LEFT, padx=(0, 6))

    def _on_add_fee_select(self, _event=None):
        if not self.tree_add_fees:
            return
        sel = self.tree_add_fees.selection()
        if not sel:
            self.selected_add_charge = None
            return
        try:
            cid = int(sel[0])
        except (TypeError, ValueError):
            self.selected_add_charge = None
            return
        self.selected_add_charge = getattr(self, "_add_charges_by_id", {}).get(cid)

    def _assign_additional_fee(self):
        if not self.student or not self.can_edit:
            return
        try:
            types = additional_fees.list_fee_types(active_only=True)
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self.win)
            return
        if not types:
            messagebox.showinfo(
                "No Fee Types",
                "No additional fee types configured yet.\n"
                "Admin can add types (Annual Fee, Exam Fee, …) from this dialog.",
                parent=self.win,
            )

        dlg = tk.Toplevel(self.win)
        dlg.title("Assign Additional Fee")
        dlg.geometry("420x320")
        dlg.config(bg=CARD_BG)
        dlg.transient(self.win)
        dlg.grab_set()

        tk.Label(
            dlg, text=f"Student: {self.student['name']} ({self.student['student_id']})",
            font=theme.FONT_BODY_BOLD, bg=CARD_BG,
        ).pack(anchor="w", padx=14, pady=(12, 8))

        form = tk.Frame(dlg, bg=CARD_BG)
        form.pack(fill=tk.X, padx=14)

        tk.Label(form, text="Fee Type:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=0, column=0, sticky="w", pady=4
        )
        type_names = [t["name"] for t in types] or ["(none)"]
        type_map = {t["name"]: t for t in types}
        cmb_type = ttk.Combobox(form, values=type_names, state="readonly", width=22)
        if type_names:
            cmb_type.current(0)
        cmb_type.grid(row=0, column=1, sticky="w", pady=4)

        tk.Label(form, text="Amount (Rs.):", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=1, column=0, sticky="w", pady=4
        )
        ent_amt = tk.Entry(form, font=theme.FONT_BODY, width=14)
        ent_amt.grid(row=1, column=1, sticky="w", pady=4)

        def on_type(_e=None):
            t = type_map.get(cmb_type.get())
            if t and float(t.get("default_amount") or 0) > 0:
                ent_amt.delete(0, tk.END)
                ent_amt.insert(0, str(t["default_amount"]))

        cmb_type.bind("<<ComboboxSelected>>", on_type)
        on_type()

        tk.Label(form, text="Academic Year:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=2, column=0, sticky="w", pady=4
        )
        ent_year = tk.Entry(form, font=theme.FONT_BODY, width=14)
        try:
            import academic_year
            ent_year.insert(0, academic_year.get_current_year_label())
        except Exception:
            ent_year.insert(0, str(datetime.now().year))
        ent_year.grid(row=2, column=1, sticky="w", pady=4)

        tk.Label(form, text="Remarks:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=3, column=0, sticky="w", pady=4
        )
        ent_remarks = tk.Entry(form, font=theme.FONT_BODY, width=28)
        ent_remarks.grid(row=3, column=1, sticky="w", pady=4)

        def do_assign():
            t = type_map.get(cmb_type.get())
            if not t:
                messagebox.showerror("Error", "Select a fee type.", parent=dlg)
                return
            try:
                amount = float(ent_amt.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Amount", "Enter a valid number.", parent=dlg)
                return
            if amount <= 0:
                messagebox.showerror("Invalid Amount", "Amount must be greater than zero.", parent=dlg)
                return
            try:
                additional_fees.assign_charge(
                    self.user_role,
                    self.student["student_id"],
                    t["id"],
                    amount,
                    academic_year=ent_year.get().strip(),
                    remarks=ent_remarks.get().strip(),
                    actor=self.current_user,
                )
            except Exception as exc:
                messagebox.showerror("Could Not Assign", str(exc), parent=dlg)
                return
            messagebox.showinfo("Assigned", f"{t['name']} of Rs. {amount:,.0f} assigned.", parent=dlg)
            dlg.destroy()
            self._load_additional_fees()
            # Refresh outstanding summary so additional fee appears in Total Remaining
            if self.selected_cycle:
                self._select_cycle(self.selected_cycle)
            self._notify_change()

        theme.primary_button(dlg, "💾  Assign", do_assign, bg=theme.SUCCESS).pack(pady=14)

    def _collect_additional_payment(self):
        if not self.can_edit or not self.student:
            return
        ch = self.selected_add_charge
        if not ch:
            messagebox.showinfo(
                "Select Fee",
                "Select an additional fee row first, then click Collect Payment.",
                parent=self.win,
            )
            return
        balance = ch["balance"]
        if balance <= 0:
            messagebox.showinfo("Fully Paid", "This additional fee is already settled.", parent=self.win)
            return

        dlg = tk.Toplevel(self.win)
        dlg.title(f"Collect — {ch['type_name']}")
        dlg.geometry("400x240")
        dlg.config(bg=CARD_BG)
        dlg.transient(self.win)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=f"{ch['type_name']}  ·  Balance: Rs. {balance:,.2f}",
            font=theme.FONT_BODY_BOLD, bg=CARD_BG,
        ).pack(anchor="w", padx=14, pady=(12, 8))

        form = tk.Frame(dlg, bg=CARD_BG)
        form.pack(fill=tk.X, padx=14)
        tk.Label(form, text="Amount (Rs.):", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=0, column=0, sticky="w", pady=4
        )
        ent_amt = tk.Entry(form, font=theme.FONT_BODY, width=14)
        ent_amt.insert(0, f"{balance:.2f}")
        ent_amt.grid(row=0, column=1, sticky="w", pady=4)

        tk.Label(form, text="Method:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=1, column=0, sticky="w", pady=4
        )
        cmb_method = ttk.Combobox(form, values=PAYMENT_METHODS, state="readonly", width=14)
        cmb_method.current(0)
        cmb_method.grid(row=1, column=1, sticky="w", pady=4)

        tk.Label(form, text="Remarks:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=2, column=0, sticky="w", pady=4
        )
        ent_remarks = tk.Entry(form, font=theme.FONT_BODY, width=24)
        ent_remarks.grid(row=2, column=1, sticky="w", pady=4)

        def do_pay():
            try:
                amount = float(ent_amt.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Amount", "Enter a valid number.", parent=dlg)
                return
            if amount <= 0:
                messagebox.showerror("Invalid Amount", "Amount must be > 0.", parent=dlg)
                return
            try:
                result = additional_fees.record_payment(
                    self.user_role,
                    ch["id"],
                    amount,
                    cmb_method.get() or "Cash",
                    self.current_user,
                    remarks=ent_remarks.get().strip(),
                )
            except Exception as exc:
                messagebox.showerror("Could Not Record", str(exc), parent=dlg)
                return
            messagebox.showinfo(
                "Payment Recorded",
                f"Rs. {amount:,.2f} collected.\nReceipt: {result['receipt_no']}",
                parent=dlg,
            )
            dlg.destroy()
            self._load_additional_fees()
            if self.selected_cycle:
                self._select_cycle(self.selected_cycle)
            self._notify_change()

        theme.primary_button(dlg, "✅  Record Payment", do_pay, bg=theme.SUCCESS).pack(pady=14)

    # ------------------------------------------------------------------
    # Bulk Assign Additional Fees (Class-wise / All Students)
    # ------------------------------------------------------------------
    def _bulk_assign_additional_fees(self):
        """Dialog: assign Annual / Exam / Lab etc. to a class, multiple
        classes, or every Active student. Completely separate from
        monthly fee_cycles."""
        if not (self.can_admin or self.can_edit):
            messagebox.showerror(
                "Permission Denied",
                "You do not have permission to bulk-assign additional fees.",
                parent=self.win,
            )
            return
        try:
            additional_fees.ensure_tables()
            types = additional_fees.list_fee_types(active_only=True)
            classes = additional_fees.list_active_classes()
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self.win)
            return
        if not types:
            messagebox.showinfo(
                "No Fee Types",
                "No additional fee types configured yet.\n"
                "Default types (Annual, Exam, Lab…) are created on first use.",
                parent=self.win,
            )
            return

        dlg = tk.Toplevel(self.win)
        dlg.title("Bulk Assign Additional Fees")
        dlg.geometry("560x640")
        dlg.minsize(520, 600)
        dlg.config(bg=CARD_BG)
        dlg.transient(self.win)
        dlg.grab_set()
        dlg.resizable(True, True)

        tk.Label(
            dlg,
            text="Assign one additional fee to many students at once.\n"
                 "This does NOT touch monthly fee cycles.",
            font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED, justify="left",
        ).pack(anchor="w", padx=14, pady=(12, 6))

        form = tk.Frame(dlg, bg=CARD_BG)
        form.pack(fill=tk.X, padx=14)

        tk.Label(form, text="Fee Type:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=0, column=0, sticky="w", pady=4
        )
        type_names = [t["name"] for t in types]
        type_map = {t["name"]: t for t in types}
        cmb_type = ttk.Combobox(form, values=type_names, state="readonly", width=24)
        cmb_type.current(0)
        cmb_type.grid(row=0, column=1, sticky="w", pady=4)

        tk.Label(form, text="Amount (Rs.):", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=1, column=0, sticky="w", pady=4
        )
        ent_amt = tk.Entry(form, font=theme.FONT_BODY, width=16)
        ent_amt.grid(row=1, column=1, sticky="w", pady=4)

        def on_type(_e=None):
            t = type_map.get(cmb_type.get())
            if t and float(t.get("default_amount") or 0) > 0:
                ent_amt.delete(0, tk.END)
                ent_amt.insert(0, str(t["default_amount"]))

        cmb_type.bind("<<ComboboxSelected>>", on_type)
        on_type()

        tk.Label(form, text="Academic Year:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=2, column=0, sticky="w", pady=4
        )
        ent_year = tk.Entry(form, font=theme.FONT_BODY, width=16)
        try:
            import academic_year
            ent_year.insert(0, academic_year.get_current_year_label())
        except Exception:
            ent_year.insert(0, str(datetime.now().year))
        ent_year.grid(row=2, column=1, sticky="w", pady=4)

        tk.Label(form, text="Remarks:", bg=CARD_BG, font=theme.FONT_SMALL).grid(
            row=3, column=0, sticky="w", pady=4
        )
        ent_remarks = tk.Entry(form, font=theme.FONT_BODY, width=28)
        ent_remarks.grid(row=3, column=1, sticky="w", pady=4)

        scope_frame = tk.LabelFrame(
            dlg, text="Assign To", font=theme.FONT_BODY_BOLD,
            bg=CARD_BG, fg=DARK, padx=10, pady=8,
        )
        scope_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(10, 4))

        scope_var = tk.StringVar(value="classes")

        tk.Radiobutton(
            scope_frame, text="All Active Students (whole school)",
            variable=scope_var, value="all", bg=CARD_BG, font=theme.FONT_BODY, anchor="w",
        ).pack(fill=tk.X, pady=(0, 2))

        tk.Radiobutton(
            scope_frame, text="Selected Class(es) only",
            variable=scope_var, value="classes", bg=CARD_BG, font=theme.FONT_BODY, anchor="w",
        ).pack(fill=tk.X, pady=(0, 2))

        list_frame = tk.Frame(scope_frame, bg=CARD_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 2))
        tk.Label(
            list_frame,
            text="Hold Ctrl / Shift to select multiple classes:",
            font=theme.FONT_SMALL, bg=CARD_BG, fg=MUTED,
        ).pack(anchor="w")
        lb_classes = tk.Listbox(
            list_frame, selectmode=tk.EXTENDED, height=6,
            font=theme.FONT_BODY, exportselection=False,
        )
        lb_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=lb_classes.yview)
        lb_classes.configure(yscrollcommand=lb_scroll.set)
        lb_classes.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for c in classes:
            lb_classes.insert(tk.END, c)
        if not classes:
            lb_classes.insert(tk.END, "(no classes found)")
            lb_classes.config(state="disabled")

        lbl_preview = tk.Label(
            scope_frame, text="", font=theme.FONT_SMALL,
            bg=CARD_BG, fg=theme.INFO, anchor="w",
        )
        lbl_preview.pack(fill=tk.X, pady=(2, 0))

        def update_preview(*_args):
            try:
                if scope_var.get() == "all":
                    n = additional_fees.count_active_students(all_active=True)
                    lbl_preview.config(text=f"Will affect ≈ {n} Active student(s).")
                else:
                    sel = [lb_classes.get(i) for i in lb_classes.curselection()]
                    n = additional_fees.count_active_students(class_secs=sel)
                    if sel:
                        lbl_preview.config(
                            text=f"Selected {len(sel)} class(es) → ≈ {n} Active student(s)."
                        )
                    else:
                        lbl_preview.config(text="Select one or more classes above.")
            except Exception:
                lbl_preview.config(text="")

        scope_var.trace_add("write", update_preview)
        lb_classes.bind("<<ListboxSelect>>", update_preview)
        update_preview()

        # ---- Bottom fixed area (always visible) ----
        bottom = tk.Frame(dlg, bg=CARD_BG)
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=14, pady=(6, 12))

        skip_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            bottom,
            text="Skip students who already have this fee type + academic year",
            variable=skip_var, bg=CARD_BG, font=theme.FONT_SMALL, anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        def do_bulk():
            t = type_map.get(cmb_type.get())
            if not t:
                messagebox.showerror("Error", "Select a fee type.", parent=dlg)
                return
            try:
                amount = float(ent_amt.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Amount", "Enter a valid number.", parent=dlg)
                return
            if amount <= 0:
                messagebox.showerror("Invalid Amount", "Amount must be greater than zero.", parent=dlg)
                return

            all_active = scope_var.get() == "all"
            class_secs = None
            if not all_active:
                class_secs = [lb_classes.get(i) for i in lb_classes.curselection()]
                if not class_secs:
                    messagebox.showerror(
                        "No Class Selected",
                        "Select at least one class, or choose All Active Students.",
                        parent=dlg,
                    )
                    return

            scope_label = (
                "ALL Active students"
                if all_active
                else f"classes: {', '.join(class_secs)}"
            )
            if not messagebox.askyesno(
                "Confirm Bulk Assign",
                f"Assign {t['name']} of Rs. {amount:,.0f}\n"
                f"to {scope_label}?\n\n"
                f"Academic Year: {ent_year.get().strip() or '(none)'}\n"
                f"Skip existing: {'Yes' if skip_var.get() else 'No'}",
                parent=dlg,
            ):
                return

            try:
                result = additional_fees.bulk_assign(
                    self.user_role,
                    t["id"],
                    amount,
                    class_secs=class_secs,
                    all_active=all_active,
                    academic_year=ent_year.get().strip(),
                    remarks=ent_remarks.get().strip(),
                    actor=self.current_user,
                    skip_if_exists=skip_var.get(),
                )
            except (ValueError, rbac.PermissionDenied) as e:
                messagebox.showerror("Could Not Assign", str(e), parent=dlg)
                return
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=dlg)
                return

            messagebox.showinfo(
                "Bulk Assign Complete",
                f"Fee: {result.get('fee_type_name', t['name'])}  ·  "
                f"Rs. {result.get('amount', amount):,.0f}\n\n"
                f"Created: {len(result['created'])}\n"
                f"Skipped (already had it): {len(result['skipped'])}\n"
                f"Errors: {len(result['errors'])}",
                parent=dlg,
            )
            dlg.destroy()
            if self.student:
                self._load_additional_fees()
            self._notify_change()

        theme.primary_button(
            bottom, "💾  Assign Now", do_bulk, bg=theme.SUCCESS,
        ).pack(fill=tk.X, ipady=6)


def launch_fee_management_window(parent, user_role, current_user, on_change=None):
    """Public entry point — called from app.py.

    on_change: optional callback invoked after payment / discount / cycle
    changes so the Students Directory can refresh Paid Fee & Balance
    without a manual page reload.
    """
    return FeeManagementWindow(parent, user_role, current_user, on_change=on_change)

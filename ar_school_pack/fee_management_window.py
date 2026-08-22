"""
fee_management_window.py — Unified Fee Management (single user-facing
fee screen, replacing separate Collect Fee / Fee Cycles Quick Actions).
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
import whatsapp_notify

PAYMENT_METHODS = ["Cash", "Bank", "Online Transfer", "Other"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

STATUS_COLORS = {
    "PAID": theme.SUCCESS, "ADVANCE": theme.INFO, "PARTIAL": theme.WARNING,
    "OVERDUE": theme.DANGER, "PENDING": theme.TEXT_MUTED,
}


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
        self.win.title("Fee Management")
        self.win.geometry("880x780")
        self.win.minsize(820, 700)
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
    # UI scaffold
    # ------------------------------------------------------------------
    def _build_ui(self):
        header = tk.Frame(self.win, bg=theme.NAVY, padx=20, pady=14)
        header.pack(fill=tk.X)
        tk.Label(header, text="💰  FEE MANAGEMENT", font=theme.FONT_H1,
                 bg=theme.NAVY, fg="white").pack(anchor="w")
        tk.Label(header, text="Search a student to view their fee status and collect payment.",
                 font=theme.FONT_SMALL, bg=theme.NAVY, fg="#94a3b8").pack(anchor="w")

        body = tk.Frame(self.win, bg=theme.SILVER, padx=16, pady=14)
        body.pack(fill=tk.BOTH, expand=True)

        # ---- Search ----
        search_card, search_body = theme.section_card(body, "Student Search")
        search_card.pack(fill=tk.X, pady=(0, 10))
        row = tk.Frame(search_body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="Student ID:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_search = tk.Entry(row, font=theme.FONT_BODY, width=24)
        self.ent_search.pack(side=tk.LEFT, padx=8, ipady=3)
        self.ent_search.bind("<Return>", lambda e: self.search_student())
        theme.primary_button(row, "🔍 Search", self.search_student).pack(side=tk.LEFT, padx=4)
        self.lbl_search_status = tk.Label(row, text="", font=theme.FONT_SMALL,
                                           bg=theme.WHITE, fg=theme.DANGER)
        self.lbl_search_status.pack(side=tk.LEFT, padx=10)

        # ---- Student info ----
        info_card, self.info_body = theme.section_card(body, "Student Information")
        info_card.pack(fill=tk.X, pady=(0, 10))
        self.info_labels = {}
        grid = tk.Frame(self.info_body, bg=theme.WHITE)
        grid.pack(fill=tk.X)
        for i, (label, key) in enumerate([("Name", "name"), ("Student ID", "student_id"),
                                           ("Class/Section", "class_sec"), ("Status", "status")]):
            tk.Label(grid, text=f"{label}:", font=theme.FONT_SMALL, bg=theme.WHITE,
                     fg=theme.TEXT_MUTED).grid(row=0, column=i * 2, sticky="w", padx=(0, 4), pady=3)
            lbl = tk.Label(grid, text="—", font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.TEXT_DARK)
            lbl.grid(row=0, column=i * 2 + 1, sticky="w", padx=(0, 24), pady=3)
            self.info_labels[key] = lbl

        # ---- Current Fee Summary + actions ----
        self.summary_card, self.summary_body = theme.section_card(body, "Current Fee Summary")
        self.summary_card.pack(fill=tk.X, pady=(0, 10))

        # ---- Permanent Receipt Area (Outside summary_body) ----
        self.receipt_card, self.receipt_body = theme.section_card(body, "Receipt Area")
        self.receipt_frame = tk.Frame(self.receipt_body, bg=theme.WHITE)
        self.receipt_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.receipt_card.pack_forget()

        self._render_empty_summary()

        # ---- Monthly Fee Cycle History ----
        self.hist_card, hist_body = theme.section_card(body, "Monthly Fee Cycle History")
        self.hist_card.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        columns = ("period", "fee", "discount", "prev_bal", "due", "paid", "balance", "status")
        headers = {"period": "Month/Year", "fee": "Fee", "discount": "Discount", "prev_bal": "Prev Bal",
                   "due": "Amount Due", "paid": "Paid", "balance": "Balance", "status": "Status"}
        widths = {"period": 90, "fee": 70, "discount": 70, "prev_bal": 70, "due": 80,
                  "paid": 70, "balance": 80, "status": 80}
        self.tree_cycles = ttk.Treeview(hist_body, columns=columns, show="headings", height=8)
        for c in columns:
            self.tree_cycles.heading(c, text=headers[c])
            self.tree_cycles.column(c, width=widths[c], anchor="center")
        self.tree_cycles.pack(fill=tk.BOTH, expand=True)
        self.tree_cycles.bind("<<TreeviewSelect>>", self._on_row_select)
        for status, color in STATUS_COLORS.items():
            self.tree_cycles.tag_configure(status, foreground=color)
        tk.Label(hist_body, text="Click a month to review or act on that cycle instead of the latest one.",
                 font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(anchor="w", pady=(4, 0))

        # ---- Admin Tools ----
        if self.can_edit or self.can_admin or self.can_reports:
            admin_card, admin_body = theme.section_card(body, "Admin Tools")
            admin_card.pack(fill=tk.X)
            admin_row = tk.Frame(admin_body, bg=theme.WHITE)
            admin_row.pack(fill=tk.X)
            if self.can_edit:
                self.btn_generate = theme.primary_button(admin_row, "➕ Generate New Cycle",
                                                           self._open_generate_dialog, bg=theme.SLATE)
                self.btn_generate.pack(side=tk.LEFT, padx=(0, 6))
                self.btn_generate.config(state="disabled")
            if self.can_admin:
                theme.primary_button(admin_row, "📆 Bulk Generate Cycle", self._open_bulk_generate_dialog,
                                      bg=theme.SLATE).pack(side=tk.LEFT, padx=(0, 6))
                theme.primary_button(admin_row, "🔄 Refresh Overdue Statuses", self._refresh_overdue,
                                      bg=theme.SLATE).pack(side=tk.LEFT, padx=(0, 6))
            if self.can_reports:
                theme.primary_button(admin_row, "📊 Reports", self._open_reports_dialog,
                                      bg=theme.SLATE).pack(side=tk.LEFT)
        else:
            self.btn_generate = None

    def _render_empty_summary(self):
        for w in self.summary_body.winfo_children():
            w.destroy()
        tk.Label(self.summary_body, text="Search a student to see their fee summary.",
                 font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(anchor="w")
        self.receipt_card.pack_forget()

    # ------------------------------------------------------------------
    # Search / load
    # ------------------------------------------------------------------
    def search_student(self):
        sid = self.ent_search.get().strip()
        self.lbl_search_status.config(text="")

        if not sid:
            self.lbl_search_status.config(text="Enter a Student ID.")
            return

        row = db.run(
            "SELECT student_id, name, class_sec, status, phone FROM students WHERE student_id=?",
            (sid,), fetchone=True,
        )
        if not row:
            self.student = None
            self._clear_info()
            self._render_empty_summary()
            self.lbl_search_status.config(text=f"⚠ No student found with ID '{sid}'.")
            return

        s_id, name, cls, status, phone = row
        self.student = {"student_id": s_id, "name": name, "class_sec": cls or "-",
                         "status": status or "Active", "phone": phone or ""}
        self.info_labels["name"].config(text=name)
        self.info_labels["student_id"].config(text=s_id)
        self.info_labels["class_sec"].config(text=cls or "-")
        self.info_labels["status"].config(text=status or "Active")

        if (status or "Active") != "Active":
            self.lbl_search_status.config(text=f"⚠ Student '{name}' is Archived — read-only.")

        if self.btn_generate is not None:
            self.btn_generate.config(state="normal" if ((status or "Active") == "Active") else "disabled")

        self.receipt_card.pack_forget()
        self._load_history()

    def _clear_info(self):
        for lbl in self.info_labels.values():
            lbl.config(text="—")
        self.tree_cycles.delete(*self.tree_cycles.get_children())

    def _load_history(self, preserve_selected_id=None):
        self.tree_cycles.delete(*self.tree_cycles.get_children())
        if not self.student:
            self._render_empty_summary()
            return
        self.cycles = fee_cycles.get_student_ledger(self.user_role, self.student["student_id"])
        self._cycles_by_id = {c["id"]: c for c in self.cycles}
        for c in self.cycles:
            period = f"{MONTH_NAMES[c['billing_month'] - 1][:3]} {c['billing_year']}"
            balance = round((c["amount_due"] or 0) - (c["amount_paid"] or 0), 2)
            self.tree_cycles.insert(
                "", tk.END, iid=str(c["id"]), tags=(c["status"],),
                values=(period, f"{c['fee_amount']:,.0f}", f"{c['discount']:,.0f}",
                        f"{c['previous_balance']:,.0f}", f"{c['amount_due']:,.0f}",
                        f"{c['amount_paid']:,.0f}", f"{balance:,.0f}", c["status"]),
            )

        if self.cycles:
            target_id = preserve_selected_id if (preserve_selected_id and preserve_selected_id in self._cycles_by_id) else self.cycles[0]["id"]
            self.tree_cycles.selection_set(str(target_id))
        else:
            self._select_cycle(None)

    def _on_row_select(self, event=None):
        sel = self.tree_cycles.selection()
        if not sel:
            return
        self._select_cycle(self._cycles_by_id.get(int(sel[0])))

    # ------------------------------------------------------------------
    # Current Fee Summary
    # ------------------------------------------------------------------
    def _select_cycle(self, cycle):
        self.selected_cycle = cycle
        for w in self.summary_body.winfo_children():
            w.destroy()

        if not cycle:
            tk.Label(self.summary_body,
                      text="No fee cycle found for this student yet."
                           + ("\nUse \"➕ Generate New Cycle\" below to start one." if self.can_edit else ""),
                      font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED,
                      justify="left").pack(anchor="w")
            return

        balance = round((cycle["amount_due"] or 0) - (cycle["amount_paid"] or 0), 2)
        period = f"{MONTH_NAMES[cycle['billing_month'] - 1]} {cycle['billing_year']}"

        top = tk.Frame(self.summary_body, bg=theme.WHITE)
        top.pack(fill=tk.X, pady=(0, 10))
        tk.Label(top, text=period, font=theme.FONT_H2, bg=theme.WHITE, fg=theme.TEXT_DARK).pack(side=tk.LEFT)
        theme.status_badge(top, cycle["status"],
                            kind={"PAID": "success", "ADVANCE": "info", "PARTIAL": "warning",
                                  "OVERDUE": "danger", "PENDING": "info"}.get(cycle["status"], "info")
                            ).pack(side=tk.LEFT, padx=10)

        figures = tk.Frame(self.summary_body, bg=theme.WHITE)
        figures.pack(fill=tk.X, pady=(0, 10))
        rows = [
            ("Monthly Fee", cycle["fee_amount"]), ("Previous Balance", cycle["previous_balance"]),
            ("Discount", cycle["discount"]), ("Total Due", cycle["amount_due"]),
            ("Paid", cycle["amount_paid"]), ("Remaining", balance),
        ]
        for i, (label, value) in enumerate(rows):
            col = tk.Frame(figures, bg=theme.WHITE)
            col.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 30), pady=4)
            tk.Label(col, text=label, font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(anchor="w")
            color = theme.DANGER if label == "Remaining" and value > 0 else theme.TEXT_DARK
            tk.Label(col, text=f"Rs. {value:,.2f}", font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=color).pack(anchor="w")

        can_act = self.can_edit and (self.student or {}).get("status", "Active") == "Active"

        actions = tk.Frame(self.summary_body, bg=theme.WHITE)
        actions.pack(fill=tk.X)

        if can_act and balance > 0:
            pay_frame = tk.LabelFrame(actions, text="💵 Collect Payment", font=theme.FONT_BODY_BOLD,
                                       bg=theme.WHITE, fg=theme.TEXT_DARK, padx=10, pady=8)
            pay_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            row1 = tk.Frame(pay_frame, bg=theme.WHITE)
            row1.pack(fill=tk.X, pady=2)
            tk.Label(row1, text="Amount (Rs.)", font=theme.FONT_SMALL, bg=theme.WHITE, width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_pay_amount = tk.Entry(row1, font=theme.FONT_BODY)
            self.ent_pay_amount.pack(side=tk.LEFT, fill=tk.X, expand=True)
            row2 = tk.Frame(pay_frame, bg=theme.WHITE)
            row2.pack(fill=tk.X, pady=2)
            tk.Label(row2, text="Method", font=theme.FONT_SMALL, bg=theme.WHITE, width=14, anchor="w").pack(side=tk.LEFT)
            self.cmb_pay_method = ttk.Combobox(row2, values=PAYMENT_METHODS, state="readonly", font=theme.FONT_BODY)
            self.cmb_pay_method.current(0)
            self.cmb_pay_method.pack(side=tk.LEFT, fill=tk.X, expand=True)
            row3 = tk.Frame(pay_frame, bg=theme.WHITE)
            row3.pack(fill=tk.X, pady=2)
            tk.Label(row3, text="Remarks", font=theme.FONT_SMALL, bg=theme.WHITE, width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_pay_remarks = tk.Entry(row3, font=theme.FONT_BODY)
            self.ent_pay_remarks.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.btn_pay = theme.primary_button(pay_frame, "✅ Record Payment", self._record_payment, bg=theme.SUCCESS)
            self.btn_pay.pack(fill=tk.X, pady=(6, 0), ipady=4)

            disc_frame = tk.LabelFrame(actions, text="🏷 Apply Discount", font=theme.FONT_BODY_BOLD,
                                        bg=theme.WHITE, fg=theme.TEXT_DARK, padx=10, pady=8)
            disc_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            row1 = tk.Frame(disc_frame, bg=theme.WHITE)
            row1.pack(fill=tk.X, pady=2)
            tk.Label(row1, text="Amount (Rs.)", font=theme.FONT_SMALL, bg=theme.WHITE, width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_disc_amount = tk.Entry(row1, font=theme.FONT_BODY)
            self.ent_disc_amount.pack(side=tk.LEFT, fill=tk.X, expand=True)
            row2 = tk.Frame(disc_frame, bg=theme.WHITE)
            row2.pack(fill=tk.X, pady=2)
            tk.Label(row2, text="Reason", font=theme.FONT_SMALL, bg=theme.WHITE, width=14, anchor="w").pack(side=tk.LEFT)
            self.ent_disc_reason = tk.Entry(row2, font=theme.FONT_BODY)
            self.ent_disc_reason.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.btn_discount = theme.primary_button(disc_frame, "Apply Discount", self._apply_discount, bg=theme.SLATE)
            self.btn_discount.pack(fill=tk.X, pady=(6, 0), ipady=4)
        elif balance <= 0:
            tk.Label(actions, text=f"✅ This cycle is fully settled ({cycle['status']}). No payment due.",
                     font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.SUCCESS).pack(anchor="w")
        elif not can_act:
            tk.Label(actions, text="You do not have permission to record payments, or this student is archived.",
                     font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).pack(anchor="w")

    # ------------------------------------------------------------------
    # Payment Recording & Direct Receipt Rendering
    # ------------------------------------------------------------------
    def _record_payment(self):
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
        balance = round((c["amount_due"] or 0) - (c["amount_paid"] or 0), 2)
        if amount > balance:
            if not messagebox.askyesno(
                "Overpayment Warning",
                f"Payment (Rs. {amount:,.2f}) exceeds the outstanding balance (Rs. {balance:,.2f}).\n"
                "This will be recorded as an advance/credit. Continue?", parent=self.win):
                return

        method = self.cmb_pay_method.get() or "Cash"
        remarks = self.ent_pay_remarks.get().strip()

        self.btn_pay.config(state="disabled", text="Processing...")
        self.win.update_idletasks()
        try:
            result = fee_cycles.record_payment(self.user_role, c["id"], amount, method,
                                                self.current_user, remarks=remarks)
        except (ValueError, rbac.PermissionDenied) as e:
            messagebox.showerror("Could Not Record Payment", str(e), parent=self.win)
            self.btn_pay.config(state="normal", text="✅ Record Payment")
            return
        except Exception as e:
            messagebox.showerror("Database Error", f"Could not record the payment:\n{e}", parent=self.win)
            self.btn_pay.config(state="normal", text="✅ Record Payment")
            return

        # Reload ledger preserving currently active cycle
        self._load_history(preserve_selected_id=c["id"])
        self._notify_change()  # keep Students Directory Paid/Balance in sync

        # Explicitly render and retain the Receipt Card
        self._show_receipt_actions(result["receipt_no"], c, amount, method)

        # WhatsApp Integration — template from system_settings (user presses Send)
        try:
            remaining = round((c["amount_due"] or 0) - ((c["amount_paid"] or 0) + amount), 2)
            if remaining < 0:
                remaining = 0.0
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
        pay_date = datetime.now().strftime("%Y-%m-%d")

        cbody = self.receipt_frame
        tk.Label(cbody, text=f"✅ Payment Recorded — Receipt No: {receipt_no}",
                 font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.SUCCESS).pack(anchor="w", pady=(2, 2))
        tk.Label(cbody, text=f"Rs. {current_payment:,.2f} collected via {method}.",
                 font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_DARK).pack(anchor="w", pady=(0, 6))

        def gen(path):
            reports.generate_fee_receipt(receipt_no, s["student_id"], s["name"], "-", s["class_sec"],
                                          total_fee, previous_paid, current_payment, balance,
                                          pay_date, self.current_user, path, payment_method=method)

        def do_open():
            out_path = os.path.join(os.getcwd(), f"Fee_Receipt_{s['student_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf")
            try:
                gen(out_path)
            except Exception as e:
                messagebox.showerror("PDF Error", f"Payment was saved, but the receipt PDF failed:\n{e}", parent=self.win)
                return
            opened = False
            try:
                if os.name == "nt":
                    os.startfile(out_path); opened = True
                elif shutil.which("xdg-open"):
                    os.system(f'xdg-open "{out_path}"'); opened = True
                elif shutil.which("open"):
                    os.system(f'open "{out_path}"'); opened = True
            except Exception:
                opened = False
            messagebox.showinfo("Receipt Ready",
                                 f"Receipt opened:\n{out_path}" if opened else f"Receipt saved:\n{out_path}",
                                 parent=self.win)

        def do_save_as():
            path = filedialog.asksaveasfilename(defaultextension=".pdf",
                                                 initialfile=f"Fee_Receipt_{s['student_id']}.pdf",
                                                 filetypes=[("PDF Files", "*.pdf")], parent=self.win)
            if not path:
                return
            try:
                gen(path)
            except Exception as e:
                messagebox.showerror("PDF Error", f"Payment was saved, but the receipt PDF failed:\n{e}", parent=self.win)
                return
            messagebox.showinfo("Saved", f"Receipt saved:\n{path}", parent=self.win)

        btn_row = tk.Frame(cbody, bg=theme.WHITE)
        btn_row.pack(anchor="w", pady=(2, 4))
        theme.primary_button(btn_row, "🖨 Open / Print", do_open).pack(side=tk.LEFT, padx=(0, 6))
        theme.primary_button(btn_row, "💾 Save As...", do_save_as, bg=theme.SLATE).pack(side=tk.LEFT)

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
        if not messagebox.askyesno("Confirm Discount",
                                    f"Apply a Rs. {amount:,.2f} discount to this cycle?\nReason: {reason or '(none given)'}",
                                    parent=self.win):
            return
        try:
            fee_cycles.apply_discount(self.user_role, self.selected_cycle["id"], amount, reason, self.current_user)
        except (ValueError, rbac.PermissionDenied) as e:
            messagebox.showerror("Could Not Apply Discount", str(e), parent=self.win)
            return
        self._load_history(preserve_selected_id=self.selected_cycle["id"])
        self._notify_change()
        messagebox.showinfo("Discount Applied", "Discount applied successfully.", parent=self.win)

    # ------------------------------------------------------------------
    # Admin Tools
    # ------------------------------------------------------------------
    def _open_generate_dialog(self):
        if not self.student:
            messagebox.showinfo("Select a Student", "Search for a student first.", parent=self.win)
            return
        dlg = tk.Toplevel(self.win)
        dlg.title("Generate New Fee Cycle")
        dlg.geometry("360x360")
        dlg.config(bg=theme.WHITE)
        dlg.transient(self.win)
        dlg.grab_set()

        def field(label, default=""):
            tk.Label(dlg, text=label, font=theme.FONT_SMALL, bg=theme.WHITE,
                      fg=theme.TEXT_MUTED).pack(anchor="w", padx=16, pady=(10, 2))
            e = tk.Entry(dlg, font=theme.FONT_BODY)
            e.insert(0, default)
            e.pack(fill=tk.X, padx=16)
            return e

        now = datetime.now()
        ent_month = field("Billing Month (1-12)", str(now.month))
        ent_year = field("Billing Year", str(now.year))
        ent_fee = field("Fee Amount (Rs., blank = student's default fee)")
        ent_due = field("Due Date (YYYY-MM-DD, optional)")
        ent_grace = field("Grace Period (days)", "0")

        def submit():
            try:
                month = int(ent_month.get().strip())
                year = int(ent_year.get().strip())
                grace = int(ent_grace.get().strip() or 0)
                fee_amt = float(ent_fee.get().strip()) if ent_fee.get().strip() else None
                due = ent_due.get().strip()
                if due:
                    datetime.strptime(due, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Invalid Input", "Check month/year/fee/due-date format.", parent=dlg)
                return
            try:
                fee_cycles.generate_cycle(self.user_role, self.student["student_id"], month, year,
                                           fee_amount=fee_amt, due_date=due, grace_period_days=grace,
                                           actor=self.current_user)
            except (ValueError, rbac.PermissionDenied) as e:
                messagebox.showerror("Could Not Generate Cycle", str(e), parent=dlg)
                return
            dlg.destroy()
            self._load_history()
            self._notify_change()
            messagebox.showinfo("Cycle Generated", f"Fee cycle for {month:02d}/{year} created.", parent=self.win)

        theme.primary_button(dlg, "Create Cycle", submit, bg=theme.SUCCESS).pack(fill=tk.X, padx=16, pady=16, ipady=6)

    def _open_bulk_generate_dialog(self):
        dlg = tk.Toplevel(self.win)
        dlg.title("Bulk Generate Fee Cycle")
        dlg.geometry("320x220")
        dlg.config(bg=theme.WHITE)
        dlg.transient(self.win)
        dlg.grab_set()

        now = datetime.now()
        tk.Label(dlg, text="Billing Month (1-12)", font=theme.FONT_SMALL, bg=theme.WHITE).pack(anchor="w", padx=16, pady=(14, 2))
        ent_month = tk.Entry(dlg, font=theme.FONT_BODY); ent_month.insert(0, str(now.month)); ent_month.pack(fill=tk.X, padx=16)
        tk.Label(dlg, text="Billing Year", font=theme.FONT_SMALL, bg=theme.WHITE).pack(anchor="w", padx=16, pady=(10, 2))
        ent_year = tk.Entry(dlg, font=theme.FONT_BODY); ent_year.insert(0, str(now.year)); ent_year.pack(fill=tk.X, padx=16)

        def submit():
            try:
                month = int(ent_month.get().strip())
                year = int(ent_year.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Input", "Month/Year must be numbers.", parent=dlg)
                return
            try:
                result = fee_cycles.bulk_generate_cycle(self.user_role, month, year, actor=self.current_user)
            except rbac.PermissionDenied as e:
                messagebox.showerror("Permission Denied", str(e), parent=dlg)
                return
            dlg.destroy()
            messagebox.showinfo(
                "Bulk Generate Complete",
                f"Created: {len(result['created'])}\n"
                f"Already existed (skipped): {len(result['skipped'])}\n"
                f"Errors: {len(result['errors'])}",
                parent=self.win,
            )
            if self.student:
                self._load_history()
            self._notify_change()

        theme.primary_button(dlg, "Generate for All Active Students", submit, bg=theme.SUCCESS).pack(
            fill=tk.X, padx=16, pady=16, ipady=6)

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
        ent_month = tk.Entry(top, width=4, font=theme.FONT_BODY); ent_month.insert(0, str(now.month)); ent_month.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(top, text="Year:", bg=theme.SILVER, font=theme.FONT_BODY).pack(side=tk.LEFT)
        ent_year = tk.Entry(top, width=6, font=theme.FONT_BODY); ent_year.insert(0, str(now.year)); ent_year.pack(side=tk.LEFT, padx=(4, 12))

        class_card, class_body = theme.section_card(dlg, "Class-wise (selected month)")
        class_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        tree_class = ttk.Treeview(class_body, columns=("class", "students", "due", "paid", "outstanding"),
                                   show="headings", height=6)
        for c, t, w in [("class", "Class", 100), ("students", "Students", 70), ("due", "Total Due", 90),
                         ("paid", "Total Paid", 90), ("outstanding", "Outstanding", 90)]:
            tree_class.heading(c, text=t); tree_class.column(c, width=w, anchor="center")
        tree_class.pack(fill=tk.BOTH, expand=True)

        month_card, month_body = theme.section_card(dlg, "Monthly Collection (selected year)")
        month_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        tree_month = ttk.Treeview(month_body, columns=("month", "cycles", "due", "paid", "outstanding"),
                                   show="headings", height=6)
        for c, t, w in [("month", "Month", 90), ("cycles", "Cycles", 70), ("due", "Total Due", 90),
                         ("paid", "Total Paid", 90), ("outstanding", "Outstanding", 90)]:
            tree_month.heading(c, text=t); tree_month.column(c, width=w, anchor="center")
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
                tree_class.insert("", tk.END, values=(r["class_sec"], r["students"],
                                                        f"{r['total_due']:,.0f}", f"{r['total_paid']:,.0f}",
                                                        f"{r['total_outstanding']:,.0f}"))
            tree_month.delete(*tree_month.get_children())
            for r in fee_cycles.monthly_collection_report(self.user_role, year):
                tree_month.insert("", tk.END, values=(MONTH_NAMES[r["billing_month"] - 1], r["cycles"],
                                                        f"{r['total_due']:,.0f}", f"{r['total_paid']:,.0f}",
                                                        f"{r['total_outstanding']:,.0f}"))

        theme.primary_button(top, "Refresh", refresh).pack(side=tk.LEFT)
        refresh()


def launch_fee_management_window(parent, user_role, current_user, on_change=None):
    """Public entry point — called from app.py.

    on_change: optional callback invoked after payment / discount / cycle
    changes so the Students Directory can refresh Paid Fee & Balance
    without a manual page reload.
    """
    return FeeManagementWindow(parent, user_role, current_user, on_change=on_change)
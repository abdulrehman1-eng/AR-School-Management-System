"""
reports.py — Professional PDF generation for ID cards, payslips, marksheets,
fee receipts, and attendance reports.

All documents pull organization identity from branding (with system_settings
fallback) so one branding change updates every slip.
ID cards place a real Code128 barcode along the bottom edge so a USB
barcode/QR scanner can mark attendance by scanning the card.
"""

import io
import os
from datetime import datetime

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import mm, inch
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

try:
    from barcode import Code128
    from barcode.writer import ImageWriter
    HAS_BARCODE = True
except ImportError:
    HAS_BARCODE = False

# ---------------------------------------------------------------------------
# Branding helpers
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#0f172a")
NAVY_MID = colors.HexColor("#1e293b")
BLUE = colors.HexColor("#0284c7")
BLUE_LIGHT = colors.HexColor("#38bdf8")
SILVER = colors.HexColor("#f1f5f9")
SILVER_BORDER = colors.HexColor("#e2e8f0")
MUTED = colors.HexColor("#64748b")
SUCCESS = colors.HexColor("#16a34a")
DANGER = colors.HexColor("#dc2626")
WARNING = colors.HexColor("#d97706")
WHITE = colors.white
BLACK = colors.black


def _brand():
    """School identity: branding table first, then system_settings."""
    data = {
        "org_name": "AR Academy",
        "logo_path": "",
        "address": "",
        "phone": "",
        "email": "",
    }
    try:
        from branding import get_branding
        b = get_branding() or {}
        for k in data:
            if b.get(k):
                data[k] = b[k]
    except Exception:
        pass
    try:
        import db
        mapping = {
            "org_name": "school_name",
            "logo_path": "school_logo_path",
            "address": "school_address",
            "phone": "school_phone",
            "email": "school_email",
        }
        for local, key in mapping.items():
            val = db.get_setting(key, "") or ""
            if val and (not data[local] or data[local] in ("My School / Academy", "AR Academy")):
                data[local] = val
            elif val and local != "org_name":
                if not data[local]:
                    data[local] = val
        # Prefer explicit school_name when set
        sn = db.get_setting("school_name", "") or ""
        if sn:
            data["org_name"] = sn
    except Exception:
        pass
    return data


def _qr_image_reader(data: str):
    if not HAS_QRCODE:
        return None
    try:
        img = qrcode.make(str(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        from reportlab.lib.utils import ImageReader
        return ImageReader(buf)
    except Exception:
        return None


def _barcode_image_reader(data: str, module_height=12.0):
    """Code128 barcode image for the student ID (attendance swipe/scan)."""
    if not HAS_BARCODE:
        return None
    try:
        buf = io.BytesIO()
        Code128(str(data), writer=ImageWriter()).write(
            buf,
            options={
                "write_text": False,
                "module_height": module_height,
                "module_width": 0.4,
                "quiet_zone": 1.5,
                "background": "white",
                "foreground": "black",
            },
        )
        buf.seek(0)
        from reportlab.lib.utils import ImageReader
        return ImageReader(buf)
    except Exception:
        return None


def _draw_page_header(c, title, page_width=612, top=792):
    """Full-width professional document header band."""
    b = _brand()
    c.setFillColor(NAVY)
    c.rect(0, top - 48, page_width, 48, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(0, top - 52, page_width, 4, fill=1, stroke=0)

    # Optional logo
    logo = b.get("logo_path") or ""
    text_x = 40
    if logo and os.path.isfile(logo):
        try:
            c.drawImage(logo, 28, top - 44, width=32, height=32,
                        preserveAspectRatio=True, mask="auto")
            text_x = 68
        except Exception:
            pass

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(text_x, top - 24, (b["org_name"] or "School / Academy")[:42])
    c.setFont("Helvetica", 8)
    contact = "  ·  ".join(x for x in [b.get("address"), b.get("phone"), b.get("email")] if x)
    if contact:
        c.drawString(text_x, top - 38, contact[:90])

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(page_width / 2, top - 78, title)
    c.setStrokeColor(SILVER_BORDER)
    c.setLineWidth(0.8)
    c.line(40, top - 88, page_width - 40, top - 88)
    return top - 100


def _draw_page_footer(c, page_width=612):
    b = _brand()
    c.setStrokeColor(SILVER_BORDER)
    c.setLineWidth(0.6)
    c.line(40, 42, page_width - 40, 42)
    c.setFont("Helvetica", 7)
    c.setFillColor(MUTED)
    c.drawString(40, 28, (b["org_name"] or "")[:40])
    c.drawRightString(page_width - 40, 28, "Computer-generated · Valid without stamp")
    c.drawCentredString(page_width / 2, 16, datetime.now().strftime("Printed %d-%b-%Y %H:%M"))


def _kv_row(c, x, y, label, value, label_w=110, font_size=10):
    c.setFont("Helvetica", font_size)
    c.setFillColor(MUTED)
    c.drawString(x, y, f"{label}")
    c.setFillColor(BLACK)
    c.setFont("Helvetica-Bold", font_size)
    c.drawString(x + label_w, y, str(value)[:48])
    return y - 16


# ===========================================================================
# ID CARD — premium design, barcode at bottom for attendance scan
# ===========================================================================
def generate_id_card(student_id, name, cls, out_path, father_name="", dob="", phone="",
                     session="", photo_path=None, emergency_phone=""):
    """Professional student ID card.

    - DOB is intentionally not shown (kept as unused kwarg for older callers).
    - Emergency contact number is shown when provided.
    - Code128 barcode along the bottom edge for attendance scanning.
    """
    b = _brand()
    c = canvas.Canvas(out_path, pagesize=letter)
    session = session or datetime.now().strftime("%Y")
    issue_date = datetime.now().strftime("%d-%b-%Y")

    # Card size (portrait, print-friendly)
    card_w, card_h = 340, 500
    card_x = (612 - card_w) / 2
    card_y = 260

    # Soft shadow
    c.setFillColor(colors.HexColor("#94a3b8"))
    c.roundRect(card_x + 5, card_y - 5, card_w, card_h, 14, fill=1, stroke=0)

    # Card base
    c.setFillColor(WHITE)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.8)
    c.roundRect(card_x, card_y, card_w, card_h, 14, fill=1, stroke=1)

    # ===== TOP HEADER (navy + blue accent) =====
    c.setFillColor(NAVY)
    c.rect(card_x + 1.8, card_y + card_h - 88, card_w - 3.6, 86.2, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(card_x + 1.8, card_y + card_h - 92, card_w - 3.6, 5, fill=1, stroke=0)

    # Decorative side stripe
    c.setFillColor(BLUE)
    c.rect(card_x + 1.8, card_y + 90, 5, card_h - 180, fill=1, stroke=0)

    org = (b["org_name"] or "ACADEMY").upper()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(card_x + card_w / 2, card_y + card_h - 36, org[:30])
    c.setFont("Helvetica", 8)
    c.setFillColor(BLUE_LIGHT)
    c.drawCentredString(card_x + card_w / 2, card_y + card_h - 54, "STUDENT IDENTITY CARD")
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#94a3b8"))
    c.drawCentredString(card_x + card_w / 2, card_y + card_h - 70, f"Academic Session  {session}")

    # ===== PHOTO =====
    photo_x, photo_y = card_x + 28, card_y + 300
    photo_w, photo_h = 96, 110
    # outer frame
    c.setFillColor(SILVER)
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.5)
    c.roundRect(photo_x - 2, photo_y - 2, photo_w + 4, photo_h + 4, 8, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.roundRect(photo_x, photo_y, photo_w, photo_h, 6, fill=1, stroke=0)
    photo_drawn = False
    if photo_path and os.path.isfile(photo_path):
        try:
            c.drawImage(
                photo_path, photo_x + 2, photo_y + 2,
                width=photo_w - 4, height=photo_h - 4,
                preserveAspectRatio=True, anchor="c", mask="auto",
            )
            photo_drawn = True
        except Exception:
            photo_drawn = False
    if not photo_drawn:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawCentredString(photo_x + photo_w / 2, photo_y + photo_h / 2 - 4, "PHOTO")

    # ===== DETAILS (right of photo) — no DOB; includes Emergency =====
    dx = photo_x + photo_w + 16
    dy = photo_y + photo_h - 6

    def detail(label, value, dy):
        c.setFont("Helvetica", 6.5)
        c.setFillColor(MUTED)
        c.drawString(dx, dy, str(label).upper())
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColor(NAVY)
        c.drawString(dx, dy - 12, str(value or "-")[:24])
        return dy - 30

    dy = detail("Student ID", student_id, dy)
    dy = detail("Name", name, dy)
    dy = detail("Father / Guardian", father_name, dy)
    dy = detail("Class / Section", cls, dy)
    dy = detail("Contact", phone, dy)
    dy = detail("Emergency No.", emergency_phone or "—", dy)

    # ===== META ROW =====
    c.setStrokeColor(SILVER_BORDER)
    c.setLineWidth(0.8)
    c.line(card_x + 22, card_y + 278, card_x + card_w - 22, card_y + 278)

    c.setFont("Helvetica", 7)
    c.setFillColor(MUTED)
    c.drawString(card_x + 28, card_y + 258, f"Issued: {issue_date}")
    school_bits = " · ".join(x for x in [b.get("phone"), b.get("address")] if x)
    if school_bits:
        c.drawString(card_x + 28, card_y + 244, school_bits[:50])

    # Small QR (secondary)
    qr = _qr_image_reader(str(student_id))
    if qr:
        c.setStrokeColor(SILVER_BORDER)
        c.setLineWidth(0.5)
        c.setFillColor(WHITE)
        c.roundRect(card_x + card_w - 78, card_y + 230, 54, 54, 4, fill=1, stroke=1)
        c.drawImage(qr, card_x + card_w - 76, card_y + 232, width=50, height=50)

    # ===== BOTTOM BARCODE STRIP (primary scan target) =====
    strip_h = 86
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(card_x + 1.8, card_y + 1.8, card_w - 3.6, strip_h, fill=1, stroke=0)
    c.setStrokeColor(SILVER_BORDER)
    c.setLineWidth(0.7)
    c.line(card_x + 16, card_y + strip_h, card_x + card_w - 16, card_y + strip_h)

    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(BLUE)
    c.drawCentredString(card_x + card_w / 2, card_y + strip_h - 12, "▼  SCAN BARCODE FOR ATTENDANCE  ▼")

    barcode_img = _barcode_image_reader(str(student_id), module_height=15.0)
    bar_w, bar_h = card_w - 56, 46
    bar_x = card_x + 28
    bar_y = card_y + 24
    if barcode_img:
        c.drawImage(
            barcode_img, bar_x, bar_y, width=bar_w, height=bar_h,
            preserveAspectRatio=True, anchor="c",
        )
    else:
        c.setFillColor(NAVY)
        c.roundRect(bar_x, bar_y + 6, bar_w, 32, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(card_x + card_w / 2, bar_y + 16, f"* {student_id} *")

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(card_x + card_w / 2, card_y + 10, str(student_id))

    # Outside-card notes
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(MUTED)
    c.drawCentredString(306, 232, "This card is property of the school. If found, please return to the office.")
    c.drawCentredString(306, 218, "Scan the barcode strip at the bottom to mark student attendance.")

    c.save()
    return out_path


# ===========================================================================
# FEE RECEIPT
# ===========================================================================
def generate_fee_receipt(receipt_no, student_id, name, father_name, cls, total_fee, previous_paid,
                         current_payment, balance, payment_date, received_by, out_path,
                         payment_method="Cash"):
    c = canvas.Canvas(out_path, pagesize=letter)
    y = _draw_page_header(c, "FEE PAYMENT RECEIPT")

    # Receipt meta box (right)
    c.setFillColor(SILVER)
    c.roundRect(380, y - 10, 180, 52, 6, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(392, y + 26, "RECEIPT NO.")
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(392, y + 12, str(receipt_no))
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(392, y - 2, f"Date: {payment_date}")

    # Student block
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(NAVY)
    c.drawString(50, y + 30, "Student Details")
    y -= 8
    y = _kv_row(c, 50, y, "Student ID", student_id)
    y = _kv_row(c, 50, y, "Student Name", name)
    y = _kv_row(c, 50, y, "Father / Guardian", father_name or "-")
    y = _kv_row(c, 50, y, "Class / Section", cls or "-")

    y -= 8
    c.setStrokeColor(SILVER_BORDER)
    c.line(50, y, 560, y)
    y -= 24

    # Fee table header
    c.setFillColor(NAVY)
    c.roundRect(50, y - 4, 510, 22, 4, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(62, y + 2, "DESCRIPTION")
    c.drawRightString(540, y + 2, "AMOUNT (Rs.)")
    y -= 28

    rows = [
        ("Total Fee (this cycle / overall)", total_fee),
        ("Previously Paid", previous_paid),
        ("Current Payment Received", current_payment),
        ("Remaining Balance", balance),
    ]
    c.setFont("Helvetica", 10)
    for i, (label, amt) in enumerate(rows):
        if i % 2 == 0:
            c.setFillColor(SILVER)
            c.rect(50, y - 4, 510, 18, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 10)
        c.drawString(62, y, label)
        bold = label.startswith("Current") or label.startswith("Remaining")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        if label.startswith("Remaining"):
            c.setFillColor(SUCCESS if balance <= 0 else DANGER)
        c.drawRightString(540, y, f"{float(amt):,.2f}")
        c.setFillColor(BLACK)
        y -= 20

    y -= 6
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9)
    c.drawString(62, y, f"Payment Method:  {payment_method}")
    y -= 28

    # Status banner
    if balance <= 0:
        c.setFillColor(SUCCESS)
        banner = "✓  PAID IN FULL"
    else:
        c.setFillColor(WARNING)
        banner = f"BALANCE DUE:  Rs. {float(balance):,.2f}"
    c.roundRect(50, y - 6, 510, 26, 6, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(305, y + 2, banner)
    y -= 50

    # Signature line
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.7)
    c.line(380, y, 540, y)
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawCentredString(460, y - 12, "Authorized Signature")
    c.setFillColor(BLACK)
    c.setFont("Helvetica", 9)
    c.drawString(50, y, f"Received by: {received_by}")

    _draw_page_footer(c)
    c.save()
    return out_path


# ===========================================================================
# ATTENDANCE REPORT
# ===========================================================================
def generate_attendance_report(student_id, name, cls, month_label, total_working_days, present,
                               absent, leave, late, percentage, day_rows, out_path):
    c = canvas.Canvas(out_path, pagesize=letter)
    y = _draw_page_header(c, "MONTHLY ATTENDANCE REPORT")

    y = _kv_row(c, 50, y, "Student ID", student_id)
    y = _kv_row(c, 50, y, "Name", name)
    y = _kv_row(c, 50, y, "Class / Section", cls or "-")
    y = _kv_row(c, 50, y, "Month", month_label)
    y -= 10

    # Summary cards
    cards = [
        ("Working Days", str(total_working_days), NAVY),
        ("Present", str(present), SUCCESS),
        ("Absent", str(absent), DANGER),
        ("Leave", str(leave), BLUE),
        ("Late", str(late), WARNING),
    ]
    card_w = 95
    for i, (lab, val, col) in enumerate(cards):
        x = 50 + i * (card_w + 8)
        c.setFillColor(SILVER)
        c.roundRect(x, y - 36, card_w, 48, 6, fill=1, stroke=0)
        c.setFillColor(col)
        c.rect(x, y - 36, 4, 48, fill=1, stroke=0)
        c.setFont("Helvetica", 7)
        c.setFillColor(MUTED)
        c.drawString(x + 12, y + 2, lab.upper())
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(NAVY)
        c.drawString(x + 12, y - 18, val)

    y -= 60
    pct_color = SUCCESS if percentage >= 75 else DANGER
    c.setFillColor(pct_color)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, f"Attendance Rate:  {percentage:.1f}%")
    y -= 20

    c.setStrokeColor(SILVER_BORDER)
    c.line(50, y, 560, y)
    y -= 18
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(NAVY)
    c.drawString(50, y, "Date-by-Date Record")
    y -= 16

    c.setFillColor(NAVY)
    c.rect(50, y - 4, 510, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(60, y, "DATE")
    c.drawString(200, y, "STATUS")
    y -= 18

    c.setFont("Helvetica", 9)
    for date, status in day_rows:
        if y < 70:
            c.showPage()
            y = 750
            c.setFont("Helvetica", 9)
        st_color = {
            "Present": SUCCESS, "Absent": DANGER, "Leave": BLUE, "Late": WARNING,
        }.get(status, BLACK)
        c.setFillColor(BLACK)
        c.drawString(60, y, str(date))
        c.setFillColor(st_color)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(200, y, str(status))
        c.setFont("Helvetica", 9)
        y -= 13

    _draw_page_footer(c)
    c.save()
    return out_path


# ===========================================================================
# PAYSLIP
# ===========================================================================
def generate_payslip(teacher_id, name, month, basic_sal, absents, deductions, net_sal, out_path):
    c = canvas.Canvas(out_path, pagesize=letter)
    y = _draw_page_header(c, "MONTHLY SALARY PAYSLIP")

    y = _kv_row(c, 50, y, "Teacher ID", teacher_id)
    y = _kv_row(c, 50, y, "Teacher Name", name)
    y = _kv_row(c, 50, y, "Month / Year", month)
    y -= 12
    c.setStrokeColor(SILVER_BORDER)
    c.line(50, y, 560, y)
    y -= 24

    c.setFillColor(NAVY)
    c.roundRect(50, y - 4, 510, 22, 4, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(62, y + 2, "EARNINGS / DEDUCTIONS")
    c.drawRightString(540, y + 2, "AMOUNT (Rs.)")
    y -= 28

    rows = [
        ("Basic Monthly Salary", float(basic_sal), False),
        (f"Absents Marked ({absents} day(s))", 0, False),
        ("Salary Deductions", float(deductions), True),
    ]
    for label, amt, is_ded in rows:
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 10)
        c.drawString(62, y, label)
        if is_ded:
            c.setFillColor(DANGER)
            c.drawRightString(540, y, f"- {amt:,.2f}")
        elif amt:
            c.drawRightString(540, y, f"{amt:,.2f}")
        else:
            c.setFillColor(MUTED)
            c.drawRightString(540, y, "—")
        y -= 20

    y -= 10
    c.setFillColor(SUCCESS)
    c.roundRect(50, y - 8, 510, 32, 6, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(62, y + 2, "NET PAYABLE SALARY")
    c.drawRightString(540, y + 2, f"Rs. {float(net_sal):,.2f}")

    y -= 70
    c.setStrokeColor(MUTED)
    c.line(380, y, 540, y)
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawCentredString(460, y - 12, "Authorized Signature")

    _draw_page_footer(c)
    c.save()
    return out_path


# ===========================================================================
# MARKSHEET
# ===========================================================================
def generate_marksheet(student_id, name, cls, result, out_path, exam_label="All Exams"):
    c = canvas.Canvas(out_path, pagesize=letter)
    y = _draw_page_header(c, "STUDENT MARKSHEET")

    y = _kv_row(c, 50, y, "Student ID", student_id)
    y = _kv_row(c, 50, y, "Name", name)
    y = _kv_row(c, 50, y, "Class", cls or "-")
    y = _kv_row(c, 50, y, "Examination", exam_label)
    y -= 10

    # Table header
    c.setFillColor(NAVY)
    c.roundRect(50, y - 4, 510, 22, 4, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(60, y + 2, "SUBJECT")
    c.drawString(280, y + 2, "OBTAINED")
    c.drawString(370, y + 2, "TOTAL")
    c.drawString(450, y + 2, "PERCENT")
    c.drawString(520, y + 2, "RESULT")
    y -= 24

    for i, s in enumerate(result.get("subjects") or []):
        if y < 120:
            c.showPage()
            y = 750
        if i % 2 == 0:
            c.setFillColor(SILVER)
            c.rect(50, y - 4, 510, 18, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 9)
        c.drawString(60, y, str(s["subject"])[:28])
        c.drawString(280, y, f"{s['obtained']:.1f}")
        c.drawString(370, y, f"{s['total']:.1f}")
        c.drawString(450, y, f"{s['percent']:.1f}%")
        passed = s.get("pass", s.get("percent", 0) >= 33)
        c.setFillColor(SUCCESS if passed else DANGER)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(520, y, "PASS" if passed else "FAIL")
        y -= 18

    y -= 12
    c.setStrokeColor(SILVER_BORDER)
    c.line(50, y, 560, y)
    y -= 22

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, f"Total:  {result['total_obtained']:.1f}  /  {result['total_marks']:.1f}")
    y -= 18
    c.drawString(50, y, f"Percentage:  {result['percentage']:.2f}%")
    y -= 18
    c.drawString(50, y, f"Grade:  {result['grade']}")
    y -= 28

    passed = result.get("passed", False)
    c.setFillColor(SUCCESS if passed else DANGER)
    c.roundRect(50, y - 6, 200, 26, 6, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(150, y + 2, "PASS" if passed else "FAIL")

    _draw_page_footer(c)
    c.save()
    return out_path

"""
branding.py — Centralized organization branding.

Every screen/PDF that needs the org name/logo/contact info reads it from
here instead of hardcoding it, so changing it in Settings updates it
everywhere automatically (per spec section 26).
"""

import db


def get_branding():
    row = db.run(
        "SELECT org_name, logo_path, address, phone, email FROM branding WHERE id=1",
        fetchone=True,
    )
    if not row:
        return {"org_name": "My School / Academy", "logo_path": "", "address": "", "phone": "", "email": ""}
    return {"org_name": row[0], "logo_path": row[1], "address": row[2], "phone": row[3], "email": row[4]}


def set_branding(org_name, logo_path, address, phone, email):
    db.run(
        "UPDATE branding SET org_name=?, logo_path=?, address=?, phone=?, email=? WHERE id=1",
        (org_name, logo_path, address, phone, email), commit=True,
    )

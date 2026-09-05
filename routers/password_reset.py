"""
Password reset flow:
  GET  /forgot-password          → form to enter email
  POST /forgot-password          → send reset email
  GET  /reset-password/{token}   → form to enter new password
  POST /reset-password/{token}   → save new password
"""
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import (
    clear_reset_token,
    generate_reset_token,
    hash_password,
    send_reset_email,
    verify_reset_token,
)
from database import User, get_db
from templating import templates

router = APIRouter()

_APP_URL = os.getenv("APP_URL", "http://localhost:8000")


# ── GET /forgot-password ──────────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request, "forgot_password.html", {"sent": False, "error": None}
    )


# ── POST /forgot-password ─────────────────────────────────────────────────────

@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    # Always show "sent" message to avoid user enumeration
    user = db.query(User).filter(User.email == email.strip().lower()).first()

    if user and user.status == "activo":
        try:
            token = generate_reset_token(user, db)
            reset_url = f"{_APP_URL}/reset-password/{token}"
            send_reset_email(user.email, reset_url)
        except Exception as exc:
            # Surface config errors to admin so they can fix SMTP settings
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {"sent": False, "error": str(exc)},
            )

    return templates.TemplateResponse(
        request, "forgot_password.html", {"sent": True, "error": None}
    )


# ── GET /reset-password/{token} ───────────────────────────────────────────────

@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    user = verify_reset_token(token, db)
    if not user:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "invalid": True, "done": False, "error": None},
        )
    return templates.TemplateResponse(
        request, "reset_password.html",
        {"token": token, "invalid": False, "done": False, "error": None},
    )


# ── POST /reset-password/{token} ──────────────────────────────────────────────

@router.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = verify_reset_token(token, db)
    if not user:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "invalid": True, "done": False, "error": None},
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "invalid": False, "done": False,
             "error": "La contrasena debe tener al menos 6 caracteres."},
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "invalid": False, "done": False,
             "error": "Las contrasenas no coinciden."},
        )

    user.password_hash = hash_password(password)
    clear_reset_token(user, db)

    return templates.TemplateResponse(
        request, "reset_password.html",
        {"token": token, "invalid": False, "done": True, "error": None},
    )

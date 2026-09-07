"""Auth utilities: hashing, sessions, role guards, password reset."""
import os
import secrets
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import bcrypt
from fastapi import Request, HTTPException, Depends
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from database import User, get_db

# Stateless signed-cookie sessions — no server-side storage needed, so they
# survive fine across serverless cold starts / multiple Vercel instances.
# SECRET_KEY MUST be set in prod (Vercel env var); random fallback is only
# fine for local dev (it just means everyone gets logged out on restart).
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="picker-hunt-session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session(user_id: str) -> str:
    """Create a signed, stateless session token embedding the user id."""
    return _serializer.dumps({"uid": user_id})


def destroy_session(token: str) -> None:
    """No-op: stateless tokens have nothing to revoke server-side."""
    pass


def get_session_user_id(request: Request) -> Optional[str]:
    """Read + verify the '__session' cookie, return the user id or None."""
    token = request.cookies.get("__session")
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def get_current_user(
    request: Request,
    db=Depends(get_db),
) -> User:
    user_id = get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="No autenticado")
    user = User.query(db).filter(id=user_id, status="activo").first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario inactivo o no existe")
    return user


def require_role(*roles: str):
    """Dependency factory: ensure user has one of the given roles."""
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Sin permisos suficientes")
        return user
    return dependency


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores")
    return user


def seed_admin(db) -> None:
    """Ensure admin users exist for all stores.

    Store 929: username="admin"    | env var ADMIN_PASSWORD  | default "admin123"
    Store 96:  username="admin96"  | env var ADMIN96_PASSWORD | default "admin96"
    """
    # ── Admin Local 929 ──
    forced_929 = os.getenv("ADMIN_PASSWORD", "").strip()
    admin929 = User.query(db).filter(username="admin").first()

    if admin929 and forced_929:
        admin929.password_hash = hash_password(forced_929)
        admin929.status = "activo"
        admin929.store = "929"
        admin929.save(db)
        print("Admin 929 password actualizado desde ADMIN_PASSWORD.")
    elif not admin929:
        password = forced_929 or "admin123"
        User(
            name="Administrador L929",
            username="admin",
            password_hash=hash_password(password),
            role="admin",
            status="activo",
            store="929",
        ).save(db)
        print("Admin 929 creado.")
    elif admin929 and not admin929.store:
        admin929.store = "929"
        admin929.save(db)

    # ── Admin Local 96 ──
    forced_96 = os.getenv("ADMIN96_PASSWORD", "").strip()
    admin96 = User.query(db).filter(username="admin96").first()

    if admin96 and forced_96:
        admin96.password_hash = hash_password(forced_96)
        admin96.status = "activo"
        admin96.store = "96"
        admin96.save(db)
        print("Admin 96 password actualizado desde ADMIN96_PASSWORD.")
    elif not admin96:
        password = forced_96 or "admin96"
        User(
            name="Administrador L96",
            username="admin96",
            password_hash=hash_password(password),
            role="admin",
            status="activo",
            store="96",
        ).save(db)
        print("Admin 96 creado.")
    elif admin96 and not admin96.store:
        admin96.store = "96"
        admin96.save(db)


# ── Password Reset ────────────────────────────────────────────────────────────

RESET_EXPIRY_HOURS = 1


def generate_reset_token(user: User, db) -> str:
    """Generate a reset token, save it to the user doc, return it."""
    token = secrets.token_urlsafe(48)
    user.reset_token = token
    user.reset_expires = datetime.now(timezone.utc) + timedelta(hours=RESET_EXPIRY_HOURS)
    user.save(db)
    return token


def verify_reset_token(token: str, db) -> Optional[User]:
    """Return the User if token is valid and not expired, else None."""
    user = User.query(db).filter(reset_token=token).first()
    if not user or not user.reset_expires:
        return None
    expires = user.reset_expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return None
    return user


def clear_reset_token(user: User, db) -> None:
    user.reset_token = None
    user.reset_expires = None
    user.save(db)


def send_reset_email(to_email: str, reset_url: str) -> None:
    """Send password reset email via SMTP. Reads config from env vars."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        raise RuntimeError(
            "SMTP_USER y SMTP_PASSWORD no configurados. "
            "Agregalos como variables de entorno en Vercel."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Recuperacion de contrasena — Picker Hunt"
    msg["From"] = smtp_user
    msg["To"] = to_email

    html_body = f"""\
    <html><body style="font-family:sans-serif;background:#f3f4f6;padding:24px">
      <div style="max-width:480px;margin:auto;background:white;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.1)">
        <div style="text-align:center;margin-bottom:24px">
          <span style="font-size:40px">&#9889;</span>
          <h1 style="color:#0053e2;margin:8px 0">Picker Hunt</h1>
        </div>
        <p style="color:#374151">Recibimos una solicitud para restablecer tu contrasena.</p>
        <p style="color:#374151">Haz clic en el boton para crear una nueva contrasena.
           El enlace expira en <strong>{RESET_EXPIRY_HOURS} hora(s)</strong>.</p>
        <div style="text-align:center;margin:28px 0">
          <a href="{reset_url}"
             style="background:#0053e2;color:white;padding:12px 28px;border-radius:8px;
                    text-decoration:none;font-weight:bold;display:inline-block">
            Restablecer contrasena
          </a>
        </div>
        <p style="color:#9ca3af;font-size:12px">Si no solicitaste esto, ignora este correo.
           Tu contrasena no cambiar&aacute; hasta que hagas clic en el enlace.</p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">
        <p style="color:#9ca3af;font-size:11px;text-align:center">Picker Hunt &copy; 2025 &middot; Walmart</p>
      </div>
    </body></html>
    """

    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())

"""User management routes (admin only) + auth (login/logout)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from database import User, get_db
from auth import (
    hash_password, verify_password,
    create_session, destroy_session,
    get_current_user, require_admin, require_admin_or_subgerente,
)
from templating import templates

router = APIRouter()

VALID_ROLES   = {"admin", "buscador", "shopper", "subgerente"}
VALID_STORES  = {"929", "96"}
# Un subgerente solo puede crear gente de piso, nunca otro mando ni admin.
SUBGERENTE_CREATABLE_ROLES = {"buscador", "shopper"}


# ── Auth ───────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    store: str = Query(default="929"),
):
    store = store if store in {"929", "96"} else "929"
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "store": store},
    )


@router.post("/login")
async def do_login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    store: Annotated[str, Form()] = "929",
    db=Depends(get_db),
):
    store = store if store in {"929", "96"} else "929"
    user = User.query(db).filter(username=username, status="activo").first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Usuario o contrasena incorrectos", "store": store},
            status_code=401,
        )
    # Aislar tiendas: el usuario solo puede entrar al login de SU tienda
    if (user.store or "929") != store:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Este usuario no pertenece a este local", "store": store},
            status_code=403,
        )
    token = create_session(user.id)
    response = RedirectResponse("/dashboard", status_code=303)
    # secure=True only over HTTPS (prod/Vercel) — plain HTTP localhost needs
    # secure=False or the browser silently refuses to send the cookie back.
    response.set_cookie(
        "__session", token, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("__session")
    if token:
        destroy_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("__session")
    return response


# ── Admin: list users ─────────────────────────────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_admin_or_subgerente),
):
    # Ve solo usuarios de SU tienda (admin y subgerente por igual)
    users = User.query(db).filter(store=(user.store or "929")).order_by("name").all()
    creatable_roles = VALID_ROLES if user.role == "admin" else SUBGERENTE_CREATABLE_ROLES
    return templates.TemplateResponse(
        request, "admin_users.html",
        {
            "current_user": user, "users": users, "current_store": user.store or "929",
            "creatable_roles": sorted(creatable_roles),
            "can_manage": user.role == "admin",
        },
    )


# ── Admin: create user ────────────────────────────────────────────────────────

@router.post("/admin/users/create")
async def create_user(
    name: Annotated[str, Form()],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
    store: Annotated[str, Form()] = "929",
    db=Depends(get_db),
    current: User = Depends(require_admin_or_subgerente),
):
    if role not in VALID_ROLES:
        raise HTTPException(400, "Rol invalido")
    if current.role == "subgerente" and role not in SUBGERENTE_CREATABLE_ROLES:
        raise HTTPException(403, "Un subgerente solo puede crear buscadores o shoppers")
    # Forzar que el nuevo usuario sea de la misma tienda que quien lo crea
    user_store = current.store or "929"
    existing = User.query(db).filter(username=username).first()
    if existing:
        raise HTTPException(400, "Username ya existe")
    User(
        name=name,
        username=username,
        email=email.strip().lower() or None,
        password_hash=hash_password(password),
        role=role,
        status="activo",
        store=user_store,
    ).save(db)
    return RedirectResponse("/admin/users", status_code=303)


# ── Admin: edit user ──────────────────────────────────────────────────────────

@router.post("/admin/users/{user_id}/edit")
async def edit_user(
    user_id: str,
    name: Annotated[str, Form()],
    role: Annotated[str, Form()],
    status: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db=Depends(get_db),
    current: User = Depends(require_admin),
):
    target = User.get(db, user_id)
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    if role not in VALID_ROLES:
        raise HTTPException(400, "Rol invalido")
    target.name   = name
    target.role   = role
    target.status = status
    target.email  = email.strip().lower() or None
    if password.strip():
        target.password_hash = hash_password(password)
    target.save(db)
    return RedirectResponse("/admin/users", status_code=303)


# ── Admin: delete user ────────────────────────────────────────────────────────

@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    user_id: str,
    db=Depends(get_db),
    current: User = Depends(require_admin),
):
    if user_id == current.id:
        raise HTTPException(400, "No puedes eliminarte a ti mismo")
    target = User.get(db, user_id)
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    target.delete(db)
    return RedirectResponse("/admin/users", status_code=303)

"""
Picker Hunt — main FastAPI entry point.
Dashboard + item routes. Live updates happen via client-side polling
(see templates/base.html) instead of WebSockets, since serverless hosts
(Vercel) don't support long-lived connections or background asyncio tasks.
"""
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import Item, ItemHistory, User, create_tables, get_db, backend
from auth import seed_admin, get_session_user_id
from templating import templates
from routers import items as items_router
from routers import users as users_router
from routers import history as history_router
from routers import password_reset as password_reset_router
from routers import tareas as tareas_router
from routers import cron as cron_router


# ── App Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    seed_admin(backend)
    yield


app = FastAPI(title="Picker Hunt", lifespan=lifespan)


# ── Request timing (diagnostico) ────────────────────────────────────────────
# Log cuanto tarda CADA request. En Vercel esto queda en los logs de la
# funcion, asi que si algo tarda ~10s (cerca del timeout del plan Hobby)
# vamos a poder ver a que ruta le pasa y desde cuando, en vez de adivinar.

@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    print(f"[timing] {request.method} {request.url.path} -> "
          f"{response.status_code} en {elapsed_ms:.0f} ms")
    return response


# ── Health Check ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Simple liveness probe."""
    return {"status": "ok"}


# ── Global error handlers (no más JSON crudo en el browser) ──────────────────

_HTTP_FRIENDLY: dict[int, str] = {
    401: " Sesión expirada. Por favor inicia sesión de nuevo.",
    403: " No tienes permisos para realizar esa acción.",
    404: " El recurso solicitado no existe.",
    422: " Datos inválidos en el formulario. Intenta de nuevo.",
    500: " Error interno del servidor. Intenta de nuevo.",
}


def _is_browser_request(request: Request) -> bool:
    """True for any request that a browser page-navigation or HTML form makes.

    Covers:
    - Regular navigations:     Accept: text/html, ...
    - fetch() with no Accept:  Accept: */*
    - Prefetch / preload:      Accept: */*
    Pure API callers (curl, axios without a header) send nothing OR a very
    specific accept like application/json, which we leave as JSON.
    """
    accept = request.headers.get("accept", "")
    # Explicit wildcard = browser fetch() call or form submit from mobile
    if accept in ("", "*/*"):
        return True
    return "text/html" in accept


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Redirect to dashboard with a friendly toast instead of raw JSON."""
    # Cron/API-only endpoints are never hit by an actual browser tab — always
    # return plain JSON so Vercel Cron logs show the real status, not a 303.
    if request.url.path.startswith("/cron/") or not _is_browser_request(request):
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})

    if exc.status_code in (401, 403):
        return RedirectResponse("/login", status_code=303)

    msg = _HTTP_FRIENDLY.get(exc.status_code,
                             f" Error {exc.status_code}: {exc.detail}")
    encoded = urllib.parse.quote(msg)
    return RedirectResponse(f"/dashboard?flash={encoded}", status_code=303)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Form validation errors → friendly redirect instead of 422 JSON."""
    if not _is_browser_request(request):
        return JSONResponse(status_code=422,
                            content={"detail": exc.errors()})
    msg = urllib.parse.quote(" Datos inválidos en el formulario. Intenta de nuevo.")
    return RedirectResponse(f"/dashboard?flash={msg}", status_code=303)


app.include_router(items_router.router)
app.include_router(users_router.router)
app.include_router(history_router.router)
app.include_router(password_reset_router.router)
app.include_router(tareas_router.router)
app.include_router(cron_router.router)


# ── Handler genérico para errores 500 no controlados ───────────────────────
# FastAPI devuelve JSON por defecto para excepciones Python genéricas.
# Este handler las convierte en redirects amigables para el browser.

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort: catch any unhandled Python exception before it leaks as JSON."""
    print(f"[unhandled] {type(exc).__name__}: {exc}")
    if not _is_browser_request(request):
        return JSONResponse(status_code=500,
                            content={"detail": "Error interno del servidor."})
    msg = urllib.parse.quote("Error interno del servidor. Intenta de nuevo.")
    return RedirectResponse(f"/dashboard?flash={msg}", status_code=303)


# ── Root: store selector ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/store-select", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "store_select.html", {})


# ── Dashboard helpers ────────────────────────────────────────────────────────

NEW_ITEM_SECONDS = 45  # highlight items newer than this


NO_ENCONTRADO_TTL = 10 * 60   # segundos que el item permanece visible tras marcarse

# El dashboard hace polling cada 8s desde cada dispositivo conectado. Antes,
# CADA poll disparaba un full-scan + delete_all() de la coleccion Items solo
# para purgar los no_encontrado vencidos -- con varios buscadores/shoppers
# con la pantalla abierta eso es un escaneo completo de Firestore varias
# veces por segundo, sumando latencia (y, bajo carga, chance de pegarle al
# timeout de la funcion serverless). Throttleado a 1 vez por minuto por
# instancia: el TTL es de 10 minutos, asi que un atraso de hasta 60s en la
# purga es imperceptible para el usuario.
_CLEANUP_INTERVAL_SECONDS = 60
_last_cleanup_at = 0.0


def _attach_relations(items: list[Item], db) -> None:
    """Resolve item.creator / item.claimer, mirroring the old SQLAlchemy
    relationship() attributes so templates don't need to change."""
    user_ids = {i.created_by_id for i in items if i.created_by_id}
    user_ids |= {i.claimed_by_id for i in items if i.claimed_by_id}
    users_by_id = {uid: User.get(db, uid) for uid in user_ids}
    for item in items:
        item.creator = users_by_id.get(item.created_by_id)
        item.claimer = users_by_id.get(item.claimed_by_id)


def _build_items(db, store: str = "929"):
    """Return active items for a specific store, sorted by quantity DESC, then created_at DESC.
    Deletes no_encontrado items whose TTL (10 min) ya expiró.
    """
    now = datetime.now(timezone.utc)

    # Limpiar items no_encontrado que ya pasaron los 10 minutos (global, todas
    # las tiendas) -- throttleado, ver comentario junto a _CLEANUP_INTERVAL_SECONDS.
    global _last_cleanup_at
    if time.monotonic() - _last_cleanup_at >= _CLEANUP_INTERVAL_SECONDS:
        _last_cleanup_at = time.monotonic()
        cutoff = now - timedelta(seconds=NO_ENCONTRADO_TTL)
        (
            Item.query(db)
            .filter(status="no_encontrado")
            .filter_not_none("no_encontrado_at")
            .filter_lt("no_encontrado_at", cutoff)
            .delete_all()
        )

    items = (
        Item.query(db)
        .filter(store=store)
        .order_by("quantity", desc=True)
        .order_by("created_at", desc=True)
        .all()
    )
    _attach_relations(items, db)
    for item in items:
        expires = item.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        item._seconds_left = max(0, int((expires - now).total_seconds()))
        created = item.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        item._is_new = (now - created).total_seconds() < NEW_ITEM_SECONDS
        # Segundos restantes para desaparecer (solo no_encontrado)
        if item.status == "no_encontrado" and item.no_encontrado_at:
            nf_at = item.no_encontrado_at
            if nf_at.tzinfo is None:
                nf_at = nf_at.replace(tzinfo=timezone.utc)
            item._disappears_in = max(0, int((nf_at + timedelta(seconds=NO_ENCONTRADO_TTL) - now).total_seconds()))
        else:
            item._disappears_in = None
    return items, now


def _get_nf_history(db, store: str = "929", limit: int = 30):
    """Últimos no_encontrado del historial para mostrar en panel shopper."""
    return (
        ItemHistory.query(db)
        .filter(status="no_encontrado")
        .order_by("closed_at", desc=True)
        .limit(limit)
        .all()
    )


def _build_shopper_stats(items: list) -> list[dict]:
    """Aggregate active items by shopper: request count + total units requested.

    Only includes items whose creator has role 'shopper'.
    Returns a list of dicts sorted by total units descending.
    """
    stats: dict[str, dict] = {}
    for item in items:
        if not item.creator or item.creator.role != "shopper":
            continue
        key = item.creator.id
        if key not in stats:
            stats[key] = {
                "nombre":      item.creator.name,
                "solicitudes": 0,
                "unidades":    0,
            }
        stats[key]["solicitudes"] += 1
        stats[key]["unidades"]    += item.quantity
    return sorted(stats.values(), key=lambda x: x["unidades"], reverse=True)


# ── Dashboard ─────────────────────────────────────────────────────────────────

# Human-readable messages for error query params from form redirects
_ERROR_MESSAGES: dict[str, str] = {
    "item_no_disponible":   " Este ítem ya no está disponible (puede que alguien más lo respondió).",
    "item_no_encontrado":   " Ítem no encontrado. Puede que ya fue cerrado.",
    "item_no_confirmable":  " Este ítem no se puede confirmar (ya fue cerrado o no está en estado Encontrado).",
    "ya_buscando":          " Ya hay alguien buscando ese ítem.",
    "resultado_invalido":   " Resultado inválido. Intenta de nuevo.",
    "tipo_imagen_invalido": " Tipo de imagen no permitido. Usá JPEG, PNG, WEBP o GIF.",
    "imagen_muy_grande":    " La foto pesa demasiado y no se pudo guardar. Probá de nuevo con otra foto o sin foto.",
}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db=Depends(get_db),
):
    user_id = get_session_user_id(request)
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    # Lookup directo por id -- evita escanear toda la coleccion de usuarios
    # en cada carga del dashboard (ver auth.get_current_user para el detalle).
    user = User.get(db, user_id)
    if not user or user.status != "activo":
        return RedirectResponse("/login", status_code=303)

    items, now = _build_items(db, store=user.store or "929")
    nf_history    = _get_nf_history(db, store=user.store or "929") if user.role == "shopper" else []
    shopper_stats = _build_shopper_stats(items) if user.role == "admin" else []

    # Error message from form redirects (e.g. item already responded)
    error_key = request.query_params.get("error", "")
    quien = request.query_params.get("quien", "")
    error_msg = _ERROR_MESSAGES.get(error_key, "")
    if error_key == "ya_buscando" and quien:
        error_msg = f"\u26a0\ufe0f {quien} ya est\u00e1 buscando ese \u00edtem."

    # Flash messages from the global exception handler
    flash = request.query_params.get("flash", "")
    if flash and not error_msg:
        error_msg = urllib.parse.unquote(flash)

    return templates.TemplateResponse(
        request, "dashboard.html",
        {"current_user": user, "items": items, "now": now,
         "error_msg": error_msg, "nf_history": nf_history,
         "shopper_stats": shopper_stats},
    )


# ── Partial: items list (HTMX live swap) ─────────────────────────────────────

@app.get("/dashboard/items", response_class=HTMLResponse)
async def dashboard_items(
    request: Request,
    db=Depends(get_db),
):
    """Returns only the items-container fragment for HTMX live updates."""
    user_id = get_session_user_id(request)
    if not user_id:
        return HTMLResponse(status_code=204)  # silently ignore unauthenticated

    user = User.get(db, user_id)
    if not user or user.status != "activo":
        return HTMLResponse(status_code=204)

    items, now = _build_items(db, store=user.store or "929")
    nf_history    = _get_nf_history(db, store=user.store or "929") if user.role == "shopper" else []
    shopper_stats = _build_shopper_stats(items) if user.role == "admin" else []
    return templates.TemplateResponse(
        request, "partials/items_list.html",
        {"current_user": user, "items": items, "now": now,
         "nf_history": nf_history, "shopper_stats": shopper_stats},
        headers={"Cache-Control": "no-store"},
    )

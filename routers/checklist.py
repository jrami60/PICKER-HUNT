"""
Checklist Completitud -- router.
Mirrors the Walmart Chile "Check Rutinas Completitud" flow:
  Nuevo -> fill Local/Fecha/Seccion/Turno -> check routines -> Resumen.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from database import (
    ChecklistLocal, ChecklistSeccion, ChecklistRutina,
    ChecklistSesion, ChecklistRespuesta, User, get_db,
)
from auth import require_admin, get_session_user_id
from templating import templates

router = APIRouter(prefix="/checklist")

PREFIX = "/checklist"


def _require_user(request: Request, db) -> User | None:
    uid = get_session_user_id(request)
    if not uid:
        return None
    return User.query(db).filter(id=uid, status="activo").first()


def _seed_rutinas(db) -> None:
    """Create default routines if none exist."""
    if ChecklistRutina.query(db).count():
        return
    defaults = [
        ("Verificar stock de productos clave", "Inventario", 1),
        ("Revisar etiquetas de precios", "Precios", 2),
        ("Completar conteo de inventario", "Inventario", 3),
        ("Chequear fechas de vencimiento", "Calidad", 4),
        ("Ordenar y limpiar estanteria", "Presentacion", 5),
        ("Verificar dispositivos de escaneo", "Equipamiento", 6),
        ("Registrar incidencias del turno", "Gestion", 7),
        ("Confirmar recepcion de pedidos", "Logistica", 8),
        ("Revisar temperatura de refrigerados", "Calidad", 9),
        ("Entregar resumen al siguiente turno", "Gestion", 10),
    ]
    for desc, cat, orden in defaults:
        ChecklistRutina(descripcion=desc, categoria=cat, orden=orden).save(db)


def _attach_rutinas(respuestas: list[ChecklistRespuesta], db) -> list[ChecklistRespuesta]:
    """Resolve resp.rutina for each response and sort by rutina.orden,
    emulating the old SQLAlchemy `.join(ChecklistRutina)` behavior."""
    rutina_ids = {r.rutina_id for r in respuestas if r.rutina_id}
    rutinas_by_id = {rid: ChecklistRutina.get(db, rid) for rid in rutina_ids}
    for r in respuestas:
        r.rutina = rutinas_by_id.get(r.rutina_id)
    respuestas.sort(key=lambda r: r.rutina.orden if r.rutina else 0)
    return respuestas


# ── Home ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def checklist_home(request: Request, db=Depends(get_db)):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesiones = (
        ChecklistSesion.query(db)
        .order_by("creado_en", desc=True)
        .limit(20).all()
    )
    for s in sesiones:
        s.local = ChecklistLocal.get(db, s.local_id)
        s.seccion = ChecklistSeccion.get(db, s.seccion_id) if s.seccion_id else None
    return templates.TemplateResponse(
        request, "checklist/home.html",
        {"current_user": user, "sesiones": sesiones},
    )


# ── Nuevo checklist form ──────────────────────────────────────────────────────

@router.get("/nuevo", response_class=HTMLResponse)
async def checklist_nuevo(request: Request, db=Depends(get_db)):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    locales = ChecklistLocal.query(db).filter(activo="si").all()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request, "checklist/nuevo.html",
        {"current_user": user, "locales": locales, "today": today, "error": None},
    )


@router.post("/nuevo")
async def checklist_nuevo_post(
    request: Request,
    local_id: Annotated[str, Form()],
    fecha: Annotated[str, Form()],
    seccion_id: Annotated[str, Form()],
    turno: Annotated[str, Form()],
    db=Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    _seed_rutinas(db)

    local = ChecklistLocal.get(db, local_id)
    if not local:
        raise HTTPException(400, "Local no valido")

    sec_id = seccion_id if seccion_id else None

    sesion = ChecklistSesion(
        local_id=local_id,
        seccion_id=sec_id,
        fecha=fecha,
        turno=turno,
        creado_por_id=user.id,
    )
    sesion.save(db)

    # Create one response row per active routine
    rutinas = (
        ChecklistRutina.query(db)
        .filter(activo="si")
        .order_by("orden")
        .all()
    )

    for r in rutinas:
        ChecklistRespuesta(sesion_id=sesion.id, rutina_id=r.id).save(db)

    return RedirectResponse(f"{PREFIX}/{sesion.id}", status_code=303)


# ── Sesion: fill routines ─────────────────────────────────────────────────────

@router.get("/{sesion_id}", response_class=HTMLResponse)
async def checklist_sesion(
    sesion_id: str, request: Request, db=Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = ChecklistSesion.get(db, sesion_id)
    if not sesion:
        raise HTTPException(404, "Sesion no encontrada")
    sesion.local = ChecklistLocal.get(db, sesion.local_id)
    sesion.seccion = ChecklistSeccion.get(db, sesion.seccion_id) if sesion.seccion_id else None

    respuestas = _attach_rutinas(
        ChecklistRespuesta.query(db).filter(sesion_id=sesion_id).all(), db
    )
    total = len(respuestas)
    cumple = sum(1 for r in respuestas if r.resultado == "cumple")
    pct = round(cumple / total * 100) if total else 0

    return templates.TemplateResponse(
        request, "checklist/sesion.html",
        {
            "current_user": user,
            "sesion": sesion,
            "respuestas": respuestas,
            "total": total,
            "cumple": cumple,
            "pct": pct,
        },
    )


@router.post("/{sesion_id}/responder/{resp_id}")
async def responder(
    sesion_id: str,
    resp_id: str,
    resultado: Annotated[str, Form()],
    observacion: Annotated[str, Form()] = "",
    db=Depends(get_db),
    request: Request = None,
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    resp = ChecklistRespuesta.query(db).filter(id=resp_id, sesion_id=sesion_id).first()
    if not resp:
        raise HTTPException(404)
    if resultado not in ("cumple", "no_cumple", "pendiente"):
        raise HTTPException(400)
    resp.resultado = resultado
    resp.observacion = observacion
    resp.respondido_en = datetime.now(timezone.utc)
    resp.save(db)
    return RedirectResponse(f"{PREFIX}/{sesion_id}", status_code=303)


@router.post("/{sesion_id}/cerrar")
async def cerrar_sesion(
    sesion_id: str, db=Depends(get_db), request: Request = None,
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = ChecklistSesion.get(db, sesion_id)
    if sesion:
        sesion.cerrado = "si"
        sesion.save(db)
    return RedirectResponse(f"{PREFIX}/{sesion_id}/resumen", status_code=303)


# ── Resumen de sesion ─────────────────────────────────────────────────────────

@router.get("/{sesion_id}/resumen", response_class=HTMLResponse)
async def resumen_sesion(
    sesion_id: str, request: Request, db=Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = ChecklistSesion.get(db, sesion_id)
    if not sesion:
        raise HTTPException(404)
    sesion.local = ChecklistLocal.get(db, sesion.local_id)
    sesion.seccion = ChecklistSeccion.get(db, sesion.seccion_id) if sesion.seccion_id else None

    respuestas = _attach_rutinas(
        ChecklistRespuesta.query(db).filter(sesion_id=sesion_id).all(), db
    )
    total = len(respuestas)
    cumple = sum(1 for r in respuestas if r.resultado == "cumple")
    no_cumple = sum(1 for r in respuestas if r.resultado == "no_cumple")
    pct = round(cumple / total * 100) if total else 0
    return templates.TemplateResponse(
        request, "checklist/resumen.html",
        {
            "current_user": user,
            "sesion": sesion,
            "respuestas": respuestas,
            "total": total,
            "cumple": cumple,
            "no_cumple": no_cumple,
            "pct": pct,
        },
    )


# ── Resumen global ────────────────────────────────────────────────────────────

@router.get("/admin/resumen", response_class=HTMLResponse)
async def resumen_global(
    request: Request,
    fecha_desde: str = Query(default=""),
    fecha_hasta: str = Query(default=""),
    db=Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    q = ChecklistSesion.query(db)
    if fecha_desde:
        q = q.filter_gte("fecha", fecha_desde)
    if fecha_hasta:
        q = q.filter_lte("fecha", fecha_hasta)
    sesiones = q.order_by("creado_en", desc=True).limit(100).all()
    for s in sesiones:
        s.local = ChecklistLocal.get(db, s.local_id)
        s.seccion = ChecklistSeccion.get(db, s.seccion_id) if s.seccion_id else None
    return templates.TemplateResponse(
        request, "checklist/resumen_global.html",
        {
            "current_user": user,
            "sesiones": sesiones,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
    )


# ── Config: locales ───────────────────────────────────────────────────────────

@router.get("/config", response_class=HTMLResponse)
async def config_page(
    request: Request, db=Depends(get_db),
    user: User = Depends(require_admin),
):
    locales = ChecklistLocal.query(db).order_by("nombre").all()
    rutinas = ChecklistRutina.query(db).order_by("orden").all()
    return templates.TemplateResponse(
        request, "checklist/config.html",
        {"current_user": user, "locales": locales, "rutinas": rutinas},
    )


@router.post("/config/local/crear")
async def crear_local(
    nombre: Annotated[str, Form()],
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    if not nombre.strip():
        raise HTTPException(400, "Nombre requerido")
    ChecklistLocal(nombre=nombre.strip()).save(db)
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/local/{local_id}/seccion")
async def crear_seccion(
    local_id: str,
    nombre: Annotated[str, Form()],
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    local = ChecklistLocal.get(db, local_id)
    if not local:
        raise HTTPException(404)
    ChecklistSeccion(local_id=local_id, nombre=nombre.strip()).save(db)
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/rutina/crear")
async def crear_rutina(
    descripcion: Annotated[str, Form()],
    categoria: Annotated[str, Form()] = "",
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    last = ChecklistRutina.query(db).order_by("orden", desc=True).first()
    orden = (last.orden + 1) if last else 1
    ChecklistRutina(descripcion=descripcion.strip(), categoria=categoria.strip(), orden=orden).save(db)
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/rutina/{rutina_id}/toggle")
async def toggle_rutina(
    rutina_id: str,
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    r = ChecklistRutina.get(db, rutina_id)
    if r:
        r.activo = "no" if r.activo == "si" else "si"
        r.save(db)
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


# ── AJAX: secciones por local ─────────────────────────────────────────────────

@router.get("/api/secciones/{local_id}")
async def api_secciones(local_id: str, db=Depends(get_db)):
    secciones = (
        ChecklistSeccion.query(db)
        .filter(local_id=local_id, activo="si")
        .order_by("nombre")
        .all()
    )
    return [{"id": s.id, "nombre": s.nombre} for s in secciones]

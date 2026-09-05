"""
Checklist Completitud — router.
Mirrors the Walmart Chile "Check Rutinas Completitud" flow:
  Nuevo → fill Local/Fecha/Sección/Turno → check routines → Resumen.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import (
    ChecklistLocal, ChecklistSeccion, ChecklistRutina,
    ChecklistSesion, ChecklistRespuesta, User, get_db,
)
from auth import get_current_user, require_admin, get_session_user_id
from templating import templates

router = APIRouter(prefix="/checklist")

PREFIX = "/checklist"


def _require_user(request: Request, db: Session) -> User | None:
    uid = get_session_user_id(request)
    if not uid:
        return None
    return db.query(User).filter(User.id == uid, User.status == "activo").first()


def _seed_rutinas(db: Session) -> None:
    """Create default routines if none exist."""
    if db.query(ChecklistRutina).count():
        return
    defaults = [
        ("Verificar stock de productos clave", "Inventario", 1),
        ("Revisar etiquetas de precios", "Precios", 2),
        ("Completar conteo de inventario", "Inventario", 3),
        ("Chequear fechas de vencimiento", "Calidad", 4),
        ("Ordenar y limpiar estantería", "Presentación", 5),
        ("Verificar dispositivos de escaneo", "Equipamiento", 6),
        ("Registrar incidencias del turno", "Gestión", 7),
        ("Confirmar recepción de pedidos", "Logística", 8),
        ("Revisar temperatura de refrigerados", "Calidad", 9),
        ("Entregar resumen al siguiente turno", "Gestión", 10),
    ]
    for desc, cat, orden in defaults:
        db.add(ChecklistRutina(descripcion=desc, categoria=cat, orden=orden))
    db.commit()


# ── Home ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def checklist_home(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesiones = (
        db.query(ChecklistSesion)
        .order_by(ChecklistSesion.creado_en.desc())
        .limit(20).all()
    )
    return templates.TemplateResponse(
        request, "checklist/home.html",
        {"current_user": user, "sesiones": sesiones},
    )


# ── Nuevo checklist form ──────────────────────────────────────────────────────

@router.get("/nuevo", response_class=HTMLResponse)
async def checklist_nuevo(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    locales = db.query(ChecklistLocal).filter(ChecklistLocal.activo == "si").all()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request, "checklist/nuevo.html",
        {"current_user": user, "locales": locales, "today": today, "error": None},
    )


@router.post("/nuevo")
async def checklist_nuevo_post(
    request: Request,
    local_id: Annotated[int, Form()],
    fecha: Annotated[str, Form()],
    seccion_id: Annotated[str, Form()],
    turno: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    _seed_rutinas(db)

    local = db.query(ChecklistLocal).filter(ChecklistLocal.id == local_id).first()
    if not local:
        raise HTTPException(400, "Local no válido")

    sec_id = int(seccion_id) if seccion_id and seccion_id.isdigit() else None

    sesion = ChecklistSesion(
        local_id=local_id,
        seccion_id=sec_id,
        fecha=fecha,
        turno=turno,
        creado_por_id=user.id,
    )
    db.add(sesion)
    db.flush()

    # Create one response row per active routine
    rutinas = db.query(ChecklistRutina).filter(
        ChecklistRutina.activo == "si"
    ).order_by(ChecklistRutina.orden).all()

    for r in rutinas:
        db.add(ChecklistRespuesta(sesion_id=sesion.id, rutina_id=r.id))
    db.commit()

    return RedirectResponse(f"{PREFIX}/{sesion.id}", status_code=303)


# ── Sesión: fill routines ─────────────────────────────────────────────────────

@router.get("/{sesion_id}", response_class=HTMLResponse)
async def checklist_sesion(
    sesion_id: int, request: Request, db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = db.query(ChecklistSesion).filter(ChecklistSesion.id == sesion_id).first()
    if not sesion:
        raise HTTPException(404, "Sesión no encontrada")

    respuestas = (
        db.query(ChecklistRespuesta)
        .filter(ChecklistRespuesta.sesion_id == sesion_id)
        .join(ChecklistRutina)
        .order_by(ChecklistRutina.orden)
        .all()
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
    sesion_id: int,
    resp_id: int,
    resultado: Annotated[str, Form()],
    observacion: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    request: Request = None,
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    resp = db.query(ChecklistRespuesta).filter(
        ChecklistRespuesta.id == resp_id,
        ChecklistRespuesta.sesion_id == sesion_id,
    ).first()
    if not resp:
        raise HTTPException(404)
    if resultado not in ("cumple", "no_cumple", "pendiente"):
        raise HTTPException(400)
    resp.resultado = resultado
    resp.observacion = observacion
    resp.respondido_en = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(f"{PREFIX}/{sesion_id}", status_code=303)


@router.post("/{sesion_id}/cerrar")
async def cerrar_sesion(
    sesion_id: int, db: Session = Depends(get_db), request: Request = None,
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = db.query(ChecklistSesion).filter(ChecklistSesion.id == sesion_id).first()
    if sesion:
        sesion.cerrado = "si"
        db.commit()
    return RedirectResponse(f"{PREFIX}/{sesion_id}/resumen", status_code=303)


# ── Resumen de sesión ─────────────────────────────────────────────────────────

@router.get("/{sesion_id}/resumen", response_class=HTMLResponse)
async def resumen_sesion(
    sesion_id: int, request: Request, db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sesion = db.query(ChecklistSesion).filter(ChecklistSesion.id == sesion_id).first()
    if not sesion:
        raise HTTPException(404)
    respuestas = (
        db.query(ChecklistRespuesta)
        .filter(ChecklistRespuesta.sesion_id == sesion_id)
        .join(ChecklistRutina).order_by(ChecklistRutina.orden).all()
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
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    q = db.query(ChecklistSesion).order_by(ChecklistSesion.creado_en.desc())
    if fecha_desde:
        q = q.filter(ChecklistSesion.fecha >= fecha_desde)
    if fecha_hasta:
        q = q.filter(ChecklistSesion.fecha <= fecha_hasta)
    sesiones = q.limit(100).all()
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
    request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    locales = db.query(ChecklistLocal).order_by(ChecklistLocal.nombre).all()
    rutinas = db.query(ChecklistRutina).order_by(ChecklistRutina.orden).all()
    return templates.TemplateResponse(
        request, "checklist/config.html",
        {"current_user": user, "locales": locales, "rutinas": rutinas},
    )


@router.post("/config/local/crear")
async def crear_local(
    nombre: Annotated[str, Form()],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if not nombre.strip():
        raise HTTPException(400, "Nombre requerido")
    db.add(ChecklistLocal(nombre=nombre.strip()))
    db.commit()
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/local/{local_id}/seccion")
async def crear_seccion(
    local_id: int,
    nombre: Annotated[str, Form()],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    local = db.query(ChecklistLocal).filter(ChecklistLocal.id == local_id).first()
    if not local:
        raise HTTPException(404)
    db.add(ChecklistSeccion(local_id=local_id, nombre=nombre.strip()))
    db.commit()
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/rutina/crear")
async def crear_rutina(
    descripcion: Annotated[str, Form()],
    categoria: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    last = db.query(ChecklistRutina).order_by(ChecklistRutina.orden.desc()).first()
    orden = (last.orden + 1) if last else 1
    db.add(ChecklistRutina(descripcion=descripcion.strip(), categoria=categoria.strip(), orden=orden))
    db.commit()
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


@router.post("/config/rutina/{rutina_id}/toggle")
async def toggle_rutina(
    rutina_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    r = db.query(ChecklistRutina).filter(ChecklistRutina.id == rutina_id).first()
    if r:
        r.activo = "no" if r.activo == "si" else "si"
        db.commit()
    return RedirectResponse(f"{PREFIX}/config", status_code=303)


# ── AJAX: secciones por local ─────────────────────────────────────────────────

@router.get("/api/secciones/{local_id}")
async def api_secciones(local_id: int, db: Session = Depends(get_db)):
    secciones = db.query(ChecklistSeccion).filter(
        ChecklistSeccion.local_id == local_id,
        ChecklistSeccion.activo == "si",
    ).order_by(ChecklistSeccion.nombre).all()
    return [{"id": s.id, "nombre": s.nombre} for s in secciones]

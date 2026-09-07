"""Routes for subgerente task planning."""
import base64
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from database import Tarea, User, get_db
from auth import require_role
from templating import templates

router = APIRouter(prefix="/subgerente")

MAX_IMAGE_BYTES = 4 * 1024 * 1024   # 4 MB
ALLOWED_MIME    = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@router.get("/tareas", response_class=HTMLResponse)
async def list_tareas(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("subgerente", "admin")),
):
    tareas = (
        Tarea.query(db)
        .order_by("fecha_planificacion", desc=True)
        .order_by("creado_en", desc=True)
        .all()
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request, "subgerente_tareas.html",
        {"current_user": user, "tareas": tareas, "today": today},
    )


@router.post("/tareas/create")
async def create_tarea(
    request: Request,
    descripcion: Annotated[str, Form()],
    fecha_planificacion: Annotated[str, Form()],
    image: Optional[UploadFile] = File(None),
    db=Depends(get_db),
    user: User = Depends(require_role("subgerente", "admin")),
):
    if not descripcion.strip():
        raise HTTPException(400, "La descripcion no puede estar vacia")

    image_data: Optional[str] = None
    image_mime: Optional[str] = None

    if image and image.filename:
        mime = image.content_type or "image/jpeg"
        if mime not in ALLOWED_MIME:
            raise HTTPException(400, f"Tipo de imagen no permitido: {mime}")
        raw = await image.read()
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "Imagen muy grande (max 4 MB)")
        image_data = base64.b64encode(raw).decode()
        image_mime = mime

    Tarea(
        descripcion=descripcion.strip(),
        fecha_planificacion=fecha_planificacion,
        estado="pendiente",
        creado_por_id=user.id,
        image_data=image_data,
        image_mime=image_mime,
    ).save(db)
    return RedirectResponse("/subgerente/tareas", status_code=303)


@router.post("/tareas/{tarea_id}/status")
async def update_tarea_status(
    tarea_id: str,
    estado: Annotated[str, Form()],
    db=Depends(get_db),
    user: User = Depends(require_role("subgerente", "admin")),
):
    if estado not in ("pendiente", "en_progreso", "completada"):
        raise HTTPException(400, "Estado invalido")

    tarea = Tarea.get(db, tarea_id)
    if not tarea:
        raise HTTPException(404, "Tarea no encontrada")

    tarea.estado = estado
    tarea.save(db)
    return RedirectResponse("/subgerente/tareas", status_code=303)


@router.post("/tareas/{tarea_id}/delete")
async def delete_tarea(
    tarea_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("subgerente", "admin")),
):
    tarea = Tarea.get(db, tarea_id)
    if not tarea:
        raise HTTPException(404, "Tarea no encontrada")

    tarea.delete(db)
    return RedirectResponse("/subgerente/tareas", status_code=303)


@router.get("/tareas/{tarea_id}/image")
async def get_tarea_image(
    tarea_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("subgerente", "admin", "buscador", "shopper")),
):
    tarea = Tarea.get(db, tarea_id)
    if not tarea or not tarea.image_data:
        raise HTTPException(404, "Imagen no disponible")
    raw = base64.b64decode(tarea.image_data)
    mime = tarea.image_mime or "image/jpeg"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "max-age=3600"})

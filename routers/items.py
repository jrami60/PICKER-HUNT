"""Item routes: create, respond (buscador/shopper), confirm (shopper), force-close (admin)."""
import base64
import re
from datetime import datetime, timezone, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from database import Item, ItemHistory, User, get_db
from auth import get_current_user, require_role, require_admin
from templating import templates

router = APIRouter()

COUNTDOWN_MINUTES = 15
CODE_RE = re.compile(r"^\d{1,7}$")
MAX_IMAGE_BYTES = 3 * 1024 * 1024   # 3 MB (already compressed client-side)
ALLOWED_MIME    = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _archive_item(item: Item, db, responded_by: str = None,
                  responded_by_id: str = None, comment: str = None,
                  shipping_info: str = None, confirmed_by: str = None) -> None:
    """Move item to the item_history collection."""
    now = datetime.now(timezone.utc)
    created = item.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed = int((now - created).total_seconds())

    creator = User.get(db, item.created_by_id)
    ItemHistory(
        item_code=item.code,
        description=item.description,
        quantity=item.quantity,
        status=item.status,
        created_at=item.created_at,
        closed_at=now,
        time_used_seconds=elapsed,
        responded_by=responded_by,
        responded_by_id=responded_by_id,
        comment=comment,
        shipping_info=shipping_info,
        confirmed_by=confirmed_by,
        created_by=creator.name if creator else "Sistema",
    ).save(db)
    item.delete(db)


# ── Image endpoint ───────────────────────────────────────────────────────────

@router.get("/items/{item_id}/image")
async def item_image(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = Item.get(db, item_id)
    if not item or not item.image_data:
        raise HTTPException(404, "Imagen no disponible")
    raw  = base64.b64decode(item.image_data)
    mime = item.image_mime or "image/jpeg"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "max-age=3600"})


# ── Admin + Shopper: create item ──────────────────────────────────────────────

@router.post("/items/create")
async def create_item(
    request: Request,
    code: Annotated[str, Form()],
    description: Annotated[str, Form()],
    quantity: Annotated[int, Form()],
    image: Optional[UploadFile] = File(None),
    db=Depends(get_db),
    user: User = Depends(require_role("admin", "shopper")),
):
    if not CODE_RE.match(code):
        raise HTTPException(400, "Codigo invalido: 1-7 digitos numericos")
    if quantity < 1:
        raise HTTPException(400, "Cantidad debe ser >= 1")

    image_data: Optional[str] = None
    image_mime: Optional[str] = None
    if image and image.filename:
        mime = image.content_type or "image/jpeg"
        if mime not in ALLOWED_MIME:
            raise HTTPException(400, f"Tipo de imagen no permitido: {mime}")
        raw = await image.read()
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "Imagen muy grande (max 3 MB)")
        image_data = base64.b64encode(raw).decode()
        image_mime = mime

    now = datetime.now(timezone.utc)
    Item(
        code=code,
        description=description,
        quantity=quantity,
        status="pendiente",
        created_at=now,
        expires_at=now + timedelta(minutes=COUNTDOWN_MINUTES),
        created_by_id=user.id,
        image_data=image_data,
        image_mime=image_mime,
        store=user.store or "929",
    ).save(db)

    return RedirectResponse("/dashboard", status_code=303)


# ── Admin: delete item (hard delete, no history) ─────────────────────────────

@router.post("/items/{item_id}/delete")
async def delete_item(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    item = Item.get(db, item_id)
    if not item:
        return RedirectResponse("/dashboard?error=item_no_encontrado", status_code=303)
    item.status = "eliminado"
    _archive_item(item, db,
                  responded_by=f"[Admin] {user.name}",
                  responded_by_id=user.id,
                  comment="Eliminado manualmente por el administrador.")
    return RedirectResponse("/dashboard", status_code=303)


# ── Admin: bulk delete ────────────────────────────────────────────────────────

@router.post("/items/bulk-delete")
async def bulk_delete_items(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    """Delete multiple items at once. Accepts form field 'item_ids' (repeatable)."""
    form = await request.form()
    item_ids = [i for i in form.getlist("item_ids") if i]
    if not item_ids:
        return RedirectResponse("/dashboard", status_code=303)

    items = Item.query(db).filter_in("id", item_ids).all()
    for item in items:
        item.status = "eliminado"
        _archive_item(item, db,
                      responded_by=f"[Admin] {user.name}",
                      responded_by_id=user.id,
                      comment="Eliminado en lote por el administrador.")
    return RedirectResponse("/dashboard", status_code=303)


# ── Admin: force close ────────────────────────────────────────────────────────

@router.post("/items/{item_id}/force-close")
async def force_close(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_admin),
):
    item = Item.get(db, item_id)
    if not item:
        return RedirectResponse("/dashboard?error=item_no_encontrado", status_code=303)
    item.status = "no_encontrado"
    _archive_item(item, db, responded_by=f"[Admin] {user.name}", responded_by_id=user.id)

    return RedirectResponse("/dashboard", status_code=303)


# ── Buscador: claim / unclaim ────────────────────────────────────────────────

@router.post("/items/{item_id}/claim")
async def claim_item(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("buscador", "admin")),
):
    item = Item.query(db).filter(id=item_id, status="pendiente").first()
    if not item:
        return RedirectResponse("/dashboard?error=item_no_disponible", status_code=303)
    if item.claimed_by_id and item.claimed_by_id != user.id:
        claimer = User.get(db, item.claimed_by_id)
        claimer_name = claimer.name if claimer else "Alguien"
        return RedirectResponse(
            f"/dashboard?error=ya_buscando&quien={claimer_name}", status_code=303
        )
    item.claimed_by_id = user.id
    item.claimed_at = datetime.now(timezone.utc)
    item.save(db)
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/items/{item_id}/unclaim")
async def unclaim_item(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("buscador", "admin")),
):
    item = Item.get(db, item_id)
    if not item:
        return RedirectResponse("/dashboard?error=item_no_encontrado", status_code=303)
    if item.claimed_by_id == user.id or user.role == "admin":
        item.claimed_by_id = None
        item.claimed_at = None
        item.save(db)
    return RedirectResponse("/dashboard", status_code=303)


# ── Buscador: respond ───────────────────────────────────────────────────

@router.post("/items/{item_id}/respond")
async def respond_item(
    item_id: str,
    resultado: Annotated[str, Form()],  # "encontrado" | "no_encontrado" | "en_sala"
    comment: Annotated[str, Form()] = "",
    image: Optional[UploadFile] = File(None),
    db=Depends(get_db),
    user: User = Depends(require_role("buscador", "admin")),
):
    item = Item.query(db).filter(id=item_id, status="pendiente").first()
    if not item:
        return RedirectResponse("/dashboard?error=item_no_disponible", status_code=303)
    if resultado not in ("encontrado", "no_encontrado", "en_sala"):
        return RedirectResponse("/dashboard?error=resultado_invalido", status_code=303)

    item.status = resultado
    item.responded_by_id = user.id
    item.comment = comment
    item.responded_at = datetime.now(timezone.utc)

    # Procesar foto opcional del buscador
    if image and image.filename:
        mime = image.content_type or "image/jpeg"
        if mime not in ALLOWED_MIME:
            return RedirectResponse("/dashboard?error=tipo_imagen_invalido", status_code=303)
        raw = await image.read()
        if len(raw) > MAX_IMAGE_BYTES:
            return RedirectResponse("/dashboard?error=imagen_muy_grande", status_code=303)
        item.image_data = base64.b64encode(raw).decode()
        item.image_mime = mime

    if resultado == "no_encontrado":
        item.no_encontrado_at = datetime.now(timezone.utc)
    item.save(db)

    # no_encontrado se archiva de inmediato; los demas quedan activos para confirmacion del shopper
    if resultado == "no_encontrado":
        _archive_item(item, db, responded_by=user.name,
                      responded_by_id=user.id, comment=comment)

    return RedirectResponse("/dashboard", status_code=303)


# ── Shopper: confirm shipment (items encontrado o en_sala) ────────────────────────

@router.post("/items/{item_id}/confirm")
async def confirm_shipment(
    item_id: str,
    shipping_info: Annotated[str, Form()] = "",
    db=Depends(get_db),
    user: User = Depends(require_role("shopper", "admin")),
):
    item = Item.query(db).filter(id=item_id).first()
    if not item or item.status not in ("encontrado", "en_sala"):
        return RedirectResponse("/dashboard?error=item_no_confirmable", status_code=303)

    item.shipping_info = shipping_info
    item.confirmed_by_id = user.id
    item.save(db)

    responded_user = User.get(db, item.responded_by_id)
    _archive_item(
        item, db,
        responded_by=responded_user.name if responded_user else None,
        responded_by_id=item.responded_by_id,
        comment=item.comment,
        shipping_info=shipping_info,
        confirmed_by=user.name,
    )

    return RedirectResponse("/dashboard", status_code=303)


# ── Shopper: cancelar solicitud propia ─────────────────────────────────────

@router.post("/items/{item_id}/cancel")
async def cancel_item(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("shopper", "admin")),
):
    """Shopper cancels their own pending request. Archives with status 'cancelado'."""
    item = Item.query(db).filter(id=item_id, status="pendiente").first()
    if not item:
        return RedirectResponse("/dashboard?error=item_no_disponible", status_code=303)
    # Solo el creador puede cancelar (admin siempre puede)
    if user.role != "admin" and item.created_by_id != user.id:
        return RedirectResponse("/dashboard?error=sin_permiso", status_code=303)

    item.status = "cancelado"
    _archive_item(
        item, db,
        responded_by=f"[Cancelado] {user.name}",
        responded_by_id=user.id,
        comment="Solicitud cancelada por el shopper.",
    )

    return RedirectResponse("/dashboard", status_code=303)


# ── Shopper: cumple protocolo (item pendiente expirado) ───────────────────────

@router.post("/items/{item_id}/cumple-protocolo")
async def cumple_protocolo(
    item_id: str,
    db=Depends(get_db),
    user: User = Depends(require_role("shopper", "admin")),
):
    """Shopper confirms they followed the protocol for a pending/expired item.

    The item is archived as 'no_encontrado' with a protocol-compliance note.
    This is the only way an expired item leaves the active list.
    """
    item = Item.query(db).filter(id=item_id, status="pendiente").first()
    if not item:
        return RedirectResponse("/dashboard?error=item_no_disponible", status_code=303)

    item.status = "no_encontrado"
    _archive_item(
        item, db,
        responded_by=f"[Protocolo] {user.name}",
        responded_by_id=user.id,
        comment="Shopper confirmo cumplimiento de protocolo.",
    )

    return RedirectResponse("/dashboard", status_code=303)

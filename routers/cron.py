"""Scheduled maintenance endpoints, triggered by Vercel Cron (see vercel.json).

Not meant for browser/user access -- protected by CRON_SECRET so a random
visitor can't hit the URL and wipe photos on demand.
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from database import Item, get_db

router = APIRouter()


def _check_cron_secret(authorization: Optional[str]) -> None:
    """Vercel Cron automatically sends 'Authorization: Bearer <CRON_SECRET>'
    when the CRON_SECRET env var is set on the Vercel project. Reject
    anything else -- fail closed if the secret isn't configured at all.
    """
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        raise HTTPException(500, "CRON_SECRET no configurado en el servidor")
    if authorization != f"Bearer {secret}":
        raise HTTPException(401, "No autorizado")


@router.get("/cron/clear-photos")
async def clear_photos(
    authorization: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    """Wipe image_data/image_mime from every item that still has one.

    Runs daily (see vercel.json's "crons" entry). Keeps the item docs
    themselves intact -- only drops the base64-encoded photo payload -- so
    Firestore storage doesn't bloat with picking-request photos nobody
    needs after the fact.
    """
    _check_cron_secret(authorization)

    items = Item.query(db).filter_not_none("image_data").all()
    for item in items:
        item.image_data = None
        item.image_mime = None
        item.save(db)
    return {"status": "ok", "items_cleared": len(items)}

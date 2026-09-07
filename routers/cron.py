"""Scheduled maintenance endpoints, triggered by Vercel Cron (see vercel.json).

Not meant for browser/user access — protected by CRON_SECRET so a random
visitor can't hit the URL and wipe photos on demand.
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database import Item, get_db

router = APIRouter()


def _check_cron_secret(authorization: Optional[str]) -> None:
    """Vercel Cron automatically sends 'Authorization: Bearer <CRON_SECRET>'
    when the CRON_SECRET env var is set on the Vercel project. Reject
    anything else — fail closed if the secret isn't configured at all.
    """
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        raise HTTPException(500, "CRON_SECRET no configurado en el servidor")
    if authorization != f"Bearer {secret}":
        raise HTTPException(401, "No autorizado")


@router.get("/cron/clear-photos")
async def clear_photos(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wipe image_data/image_mime from every item that still has one.

    Runs daily at 22:00 hora Chile via Vercel Cron (see vercel.json's
    "crons" entry, scheduled for 01:00 UTC). Keeps the item rows themselves
    intact — only drops the base64-encoded photo payload — so the DB
    doesn't bloat with picking-request photos nobody needs after the fact.
    """
    _check_cron_secret(authorization)

    updated = (
        db.query(Item)
        .filter(Item.image_data.isnot(None))
        .update({Item.image_data: None, Item.image_mime: None}, synchronize_session=False)
    )
    db.commit()
    return {"status": "ok", "items_cleared": updated}

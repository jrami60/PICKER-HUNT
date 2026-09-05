"""History routes: view + export (CSV/Excel) + buscador daily summary + productividad."""
import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from database import ItemHistory, get_db, User
from auth import require_admin, require_role
from templating import templates

router = APIRouter()


def _parse_date_naive(date_str: str, end_of_day: bool = False) -> datetime | None:
    """Parse 'YYYY-MM-DD' → naive UTC datetime for SQLite comparison.

    SQLite stores DateTime columns without timezone info, so tz-aware
    datetimes silently break comparisons. We always work in naive UTC.
    end_of_day=True sets time to 23:59:59 so the full day is included.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(hour=23, minute=59, second=59) if end_of_day else dt
    except ValueError:
        return None


def _get_history(
    db: Session,
    status: str = None,
    date_from: str = None,
    date_to: str = None,
    item_code: str = None,
):
    """Query history with optional filters.

    Special status values:
    - 'cumplo_protocolo'  → no_encontrado rows where responded_by starts with '[Protocolo]'
    - any other value     → filter by status column directly
    """
    q = db.query(ItemHistory).order_by(ItemHistory.closed_at.desc())

    if status == "cumplo_protocolo":
        q = q.filter(
            ItemHistory.status == "no_encontrado",
            ItemHistory.responded_by.like("[Protocolo]%"),
        )
    elif status:
        q = q.filter(ItemHistory.status == status)

    if item_code:
        q = q.filter(ItemHistory.item_code.ilike(f"%{item_code}%"))

    if date_from and (dt := _parse_date_naive(date_from)):
        q = q.filter(ItemHistory.closed_at >= dt)
    if date_to and (dt := _parse_date_naive(date_to, end_of_day=True)):
        q = q.filter(ItemHistory.closed_at <= dt)

    return q.all()


def _format_seconds(s: int | None) -> str:
    if s is None:
        return "—"
    m, sec = divmod(s, 60)
    return f"{m}m {sec}s"


@router.get("/admin/history", response_class=HTMLResponse)
async def view_history(
    request: Request,
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    item_code: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    records = _get_history(db, status or None, date_from or None, date_to or None, item_code or None)

    # Pre-compute counts in Python — no Jinja2 regex needed
    found_count   = sum(1 for r in records if r.status == "encontrado")
    proto_count   = sum(1 for r in records
                        if r.status == "no_encontrado"
                        and (r.responded_by or "").startswith("[Protocolo]"))
    nfound_count  = sum(1 for r in records if r.status == "no_encontrado") - proto_count
    deleted_count = sum(1 for r in records if r.status == "eliminado")

    return templates.TemplateResponse(
        request, "admin_history.html",
        {
            "current_user":      user,
            "records":           records,
            "filter_status":     status,
            "filter_date_from":  date_from,
            "filter_date_to":    date_to,
            "filter_item_code":  item_code,
            "format_seconds":    _format_seconds,
            "found_count":       found_count,
            "nfound_count":      nfound_count,
            "proto_count":       proto_count,
            "deleted_count":     deleted_count,
        },
    )


@router.get("/admin/history/export/csv")
async def export_csv(
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    item_code: str = Query(default=""),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    records = _get_history(db, status or None, date_from or None, date_to or None, item_code or None)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Código", "Descripción", "Cantidad", "Estado",
        "Creado por", "Respondido por", "Tiempo usado",
        "Comentario", "Info envío", "Confirmado por",
        "Fecha creación", "Fecha cierre",
    ])
    for r in records:
        writer.writerow([
            r.item_code, r.description, r.quantity, r.status,
            r.created_by or "", r.responded_by or "",
            _format_seconds(r.time_used_seconds),
            r.comment or "", r.shipping_info or "", r.confirmed_by or "",
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            r.closed_at.strftime("%Y-%m-%d %H:%M") if r.closed_at else "",
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=historial.csv"},
    )


@router.get("/admin/history/export/excel")
async def export_excel(
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    item_code: str = Query(default=""),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    records = _get_history(db, status or None, date_from or None, date_to or None, item_code or None)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historial"

    headers = [
        "Código", "Descripción", "Cantidad", "Estado",
        "Creado por", "Respondido por", "Tiempo usado",
        "Comentario", "Info envío", "Confirmado por",
        "Fecha creación", "Fecha cierre",
    ]
    header_fill = PatternFill(start_color="0053E2", end_color="0053E2", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for row_idx, r in enumerate(records, 2):
        ws.append([
            r.item_code, r.description, r.quantity, r.status,
            r.created_by or "", r.responded_by or "",
            _format_seconds(r.time_used_seconds),
            r.comment or "", r.shipping_info or "", r.confirmed_by or "",
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            r.closed_at.strftime("%Y-%m-%d %H:%M") if r.closed_at else "",
        ])

    for col in ws.columns:
        max_len = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=historial.xlsx"},
    )


# ── Buscador: resumen del día ─────────────────────────────────────────────────

def _today_bounds_utc() -> tuple[datetime, datetime]:
    """Return (start_of_today_utc, now_utc) for filtering today's records."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


@router.get("/buscador/resumen", response_class=HTMLResponse)
async def buscador_resumen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("buscador", "admin")),
):
    """Daily summary for buscadores: no_encontrado + cumplo-protocolo items."""
    start, now = _today_bounds_utc()

    today_records = (
        db.query(ItemHistory)
        .filter(
            ItemHistory.closed_at >= start,
            ItemHistory.closed_at <= now,
        )
        .order_by(ItemHistory.closed_at.desc())
        .all()
    )

    # Split by type:
    # - No encontrados reales (buscador respondió, sin tag [Protocolo])
    # - Cumplo Protocolo (shopper cerró con protocolo, responded_by tiene [Protocolo])
    no_encontrados = [
        r for r in today_records
        if r.status == "no_encontrado"
        and not (r.responded_by or "").startswith("[Protocolo]")
    ]
    cumplo_protocolo = [
        r for r in today_records
        if r.status == "no_encontrado"
        and (r.responded_by or "").startswith("[Protocolo]")
    ]

    return templates.TemplateResponse(
        request, "buscador_resumen.html",
        {
            "current_user":    user,
            "no_encontrados":  no_encontrados,
            "cumplo_protocolo": cumplo_protocolo,
            "fecha_hoy":       now.strftime("%d/%m/%Y"),
            "format_seconds":  _format_seconds,
        },
    )


# ── Admin: productividad por buscador ─────────────────────────────────────────

def _build_productividad(
    db: Session,
    date_from: str = None,
    date_to: str = None,
) -> list[dict]:
    """Aggregate ItemHistory per buscador.
    
    A 'real' buscador response is one whose responded_by doesn't start with
    '[' (i.e. not [Admin], [Protocolo], [Cancelado], etc.).
    """
    q = db.query(ItemHistory)
    if date_from and (dt := _parse_date_naive(date_from)):
        q = q.filter(ItemHistory.closed_at >= dt)
    if date_to and (dt := _parse_date_naive(date_to, end_of_day=True)):
        q = q.filter(ItemHistory.closed_at <= dt)

    records = q.all()

    # Group only real buscador actions (responded_by without '[' prefix)
    stats: dict[str, dict] = defaultdict(lambda: {
        "nombre": "",
        "encontrados": 0,
        "no_encontrados": 0,
        "total": 0,
        "tiempo_total": 0,
        "tiempos": [],
    })

    for r in records:
        rb = r.responded_by or ""
        # Skip system-generated entries
        if not rb or rb.startswith("["):
            continue
        # Key by id if available, else by name string
        key = str(r.responded_by_id) if r.responded_by_id else rb
        s = stats[key]
        s["nombre"] = rb
        s["total"] += 1
        if r.status == "encontrado":
            s["encontrados"] += 1
        elif r.status == "no_encontrado":
            s["no_encontrados"] += 1
        if r.time_used_seconds is not None:
            s["tiempos"].append(r.time_used_seconds)
            s["tiempo_total"] += r.time_used_seconds

    result = []
    for s in stats.values():
        tiempos = s["tiempos"]
        s["promedio_seg"] = int(sum(tiempos) / len(tiempos)) if tiempos else None
        s["eficiencia"] = (
            round(s["encontrados"] / s["total"] * 100, 1) if s["total"] else 0
        )
        result.append(s)

    return sorted(result, key=lambda x: x["total"], reverse=True)


@router.get("/admin/productividad", response_class=HTMLResponse)
async def productividad(
    request: Request,
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    filas = _build_productividad(db, date_from or None, date_to or None)
    return templates.TemplateResponse(
        request, "admin_productividad.html",
        {
            "current_user": user,
            "filas": filas,
            "filter_date_from": date_from,
            "filter_date_to": date_to,
            "format_seconds": _format_seconds,
        },
    )

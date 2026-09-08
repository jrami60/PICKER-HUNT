"""
Firestore setup and model definitions for Picker Hunt.

No ORM — see firestore_backend.py for the small Model/Query base classes
used by every entity below. Real Firestore in prod; an in-memory backend
locally when no credentials are configured (zero-setup dev, data resets
on restart — see firestore_backend.MemoryBackend).

Credentials: set FIREBASE_CREDENTIALS_JSON (the full service-account JSON
as a single-line string) as an env var — never commit that file. Locally
you can instead point GOOGLE_APPLICATION_CREDENTIALS at a path on disk
(add it to .gitignore if it lives inside the repo).
"""
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from firestore_backend import FirestoreBackend, MemoryBackend, Model

load_dotenv()

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()

_is_memory = not (FIREBASE_PROJECT_ID or _CREDENTIALS_JSON or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))

_init_start = time.monotonic()
if _is_memory:
    backend = MemoryBackend()
    print("[startup] Backend: MEMORIA (RAM) -- los datos NO persisten entre reinicios. "
          "Configura FIREBASE_CREDENTIALS_JSON si esto corre en produccion.")
else:
    from google.cloud import firestore
    from google.oauth2 import service_account

    if _CREDENTIALS_JSON:
        info = json.loads(_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(info)
        client = firestore.Client(project=info.get("project_id") or FIREBASE_PROJECT_ID or None,
                                   credentials=credentials)
    else:
        # Falls back to GOOGLE_APPLICATION_CREDENTIALS file path, or the
        # default metadata credentials if running on GCP infra.
        client = firestore.Client(project=FIREBASE_PROJECT_ID or None)
    backend = FirestoreBackend(client)
    print(f"[startup] Backend: FIRESTORE real -- cliente inicializado en "
          f"{(time.monotonic() - _init_start) * 1000:.0f} ms (incluye import de google-cloud-firestore).")


def get_db():
    """FastAPI dependency — yields the shared backend (real or in-memory)."""
    yield backend


def create_tables() -> None:
    """No-op: Firestore is schemaless. Kept so main.py's lifespan hook
    doesn't need to change shape."""
    pass


_now = lambda: datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# Entities
# ══════════════════════════════════════════════════════════════════════════

class User(Model):
    _collection = "users"
    _fields = {
        "name": None,
        "username": None,
        "email": None,
        "password_hash": None,
        "role": None,               # admin | buscador | shopper | subgerente
        "status": "activo",         # activo | inactivo
        "store": "929",             # "929" | "96"
        "created_at": _now,
        "created_by": None,         # id del usuario que lo creo (None = admin/seed)
        "reset_token": None,
        "reset_expires": None,
    }


class Item(Model):
    _collection = "items"
    _fields = {
        "code": None,
        "description": None,
        "quantity": None,
        "status": "pendiente",      # pendiente | encontrado | en_sala | no_encontrado | eliminado | cancelado
        "created_at": _now,
        "expires_at": None,
        "created_by_id": None,
        "responded_by_id": None,
        "comment": None,
        "shipping_info": None,
        "confirmed_by_id": None,
        "image_data": None,
        "image_mime": None,
        "claimed_by_id": None,
        "claimed_at": None,
        "no_encontrado_at": None,
        "responded_at": None,
        "store": "929",
    }
    # Populated on demand by callers (see main.py's _build_items) — not
    # stored in Firestore, just like SQLAlchemy's lazy-loaded relationships
    # used to be. Declared here only so attribute access never explodes.
    creator = None
    claimer = None


class ItemHistory(Model):
    _collection = "item_history"
    _fields = {
        "item_code": None,
        "description": None,
        "quantity": None,
        "status": None,
        "created_at": None,
        "closed_at": _now,
        "time_used_seconds": None,
        "responded_by": None,
        "responded_by_id": None,
        "comment": None,
        "shipping_info": None,
        "confirmed_by": None,
        "created_by": None,
    }


class Tarea(Model):
    _collection = "tareas"
    _fields = {
        "descripcion": None,
        "estado": "pendiente",      # pendiente | en_progreso | completada
        "creado_en": _now,
        "fecha_planificacion": None,  # YYYY-MM-DD
        "creado_por_id": None,
        "image_data": None,
        "image_mime": None,
    }


# ── Checklist Completitud ───────────────────────────────────────────────────

class ChecklistLocal(Model):
    _collection = "checklist_locales"
    _fields = {"nombre": None, "activo": "si"}


class ChecklistSeccion(Model):
    _collection = "checklist_secciones"
    _fields = {"local_id": None, "nombre": None, "activo": "si"}


class ChecklistRutina(Model):
    _collection = "checklist_rutinas"
    _fields = {"descripcion": None, "categoria": None, "orden": 0, "activo": "si"}


class ChecklistSesion(Model):
    _collection = "checklist_sesiones"
    _fields = {
        "local_id": None,
        "seccion_id": None,
        "fecha": None,       # YYYY-MM-DD
        "turno": None,       # AM | PM | Noche
        "creado_por_id": None,
        "creado_en": _now,
        "cerrado": "no",
    }
    # Attached on demand (see routers/checklist.py) — mirrors the old
    # SQLAlchemy relationship() attributes of the same name.
    local = None
    seccion = None


class ChecklistRespuesta(Model):
    _collection = "checklist_respuestas"
    _fields = {
        "sesion_id": None,
        "rutina_id": None,
        "resultado": "pendiente",  # pendiente | cumple | no_cumple
        "observacion": None,
        "respondido_en": None,
    }
    rutina = None

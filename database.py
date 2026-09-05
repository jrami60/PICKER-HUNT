"""
Database setup and models for Picker Hunt.
SQLAlchemy. Postgres (Supabase) in prod, SQLite fallback for local dev.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String,
    DateTime, Text, ForeignKey, event
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship

import os

# DATABASE_URL wins if set (e.g. Supabase Postgres connection string).
# Falls back to a local SQLite file so `uvicorn main:app` just works
# out of the box with zero setup.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_is_sqlite = not DATABASE_URL

if _is_sqlite:
    _db_path = os.getenv("DATABASE_PATH", "./pickerhunt.db")
    DATABASE_URL = f"sqlite:///{_db_path}"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Supabase (and most managed Postgres) close idle connections — recycle
    # proactively and verify liveness before handing out a pooled connection.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        # Wait up to 5 s for locks to release before raising "database is locked".
        dbapi_conn.execute("PRAGMA busy_timeout=5000")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(200), nullable=True, index=True)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)  # admin | buscador | shopper | subgerente
    status = Column(String(10), default="activo")  # activo | inactivo
    store = Column(String(10), default="929")      # "929" | "96"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reset_token = Column(String(100), nullable=True, index=True)
    reset_expires = Column(DateTime, nullable=True)

    items_created = relationship("Item", back_populates="creator", foreign_keys="Item.created_by_id")
    responses = relationship("ItemHistory", back_populates="responder")


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(7), nullable=False, index=True)
    description = Column(Text, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(20), default="pendiente")  # pendiente | encontrado | no_encontrado
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    responded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    comment = Column(Text, nullable=True)
    shipping_info = Column(Text, nullable=True)
    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    image_data = Column(Text, nullable=True)        # base64-encoded image
    image_mime = Column(String(50), nullable=True)  # e.g. image/jpeg
    claimed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    no_encontrado_at = Column(DateTime, nullable=True)  # set when marked no_encontrado
    responded_at = Column(DateTime, nullable=True)       # set when buscador responds (any status)
    store = Column(String(10), default="929")            # "929" | "96"

    creator = relationship("User", back_populates="items_created", foreign_keys=[created_by_id])
    claimer = relationship("User", foreign_keys=[claimed_by_id])


class ItemHistory(Base):
    __tablename__ = "item_history"

    id = Column(Integer, primary_key=True, index=True)
    item_code = Column(String(7), nullable=False)
    description = Column(Text, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    time_used_seconds = Column(Integer, nullable=True)
    responded_by = Column(String(100), nullable=True)
    responded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    comment = Column(Text, nullable=True)
    shipping_info = Column(Text, nullable=True)
    confirmed_by = Column(String(100), nullable=True)
    created_by = Column(String(100), nullable=True)

    responder = relationship("User", back_populates="responses", foreign_keys=[responded_by_id])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
    if _is_sqlite:
        # Postgres (Supabase) gets its full schema from supabase_schema.sql
        # up front, so these SQLite-only ALTER TABLE shims don't apply there.
        _migrate_users_table()
        _migrate_items_table()


def _migrate_items_table():
    """Add new columns to existing items table if they don't exist."""
    new_cols = [
        ("image_data",       "TEXT"),
        ("image_mime",       "TEXT"),
        ("claimed_by_id",    "INTEGER"),
        ("claimed_at",       "DATETIME"),
        ("shipping_info",      "TEXT"),
        ("confirmed_by_id",    "INTEGER"),
        ("no_encontrado_at",   "DATETIME"),
        ("responded_at",       "DATETIME"),
        ("store",              "TEXT DEFAULT '929'"),
    ]
    with engine.connect() as conn:
        existing = [
            row[1]
            for row in conn.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(items)")
            )
        ]
        for col_name, col_type in new_cols:
            if col_name not in existing:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE items ADD COLUMN {col_name} {col_type}"
                    )
                )
                conn.commit()
                print(f"DB migration: added column items.{col_name}")
        # Ensure all NULL store values default to '929'
        conn.execute(__import__("sqlalchemy").text("UPDATE items SET store='929' WHERE store IS NULL"))
        conn.commit()


def _migrate_users_table():
    """Add new columns to existing users table if they don't exist (SQLite safe)."""
    new_cols = [
        ("email",         "TEXT"),
        ("reset_token",   "TEXT"),
        ("reset_expires", "DATETIME"),
        ("image_data",    "TEXT"),
        ("image_mime",    "TEXT"),
        ("store",         "TEXT DEFAULT '929'"),
    ]
    with engine.connect() as conn:
        existing = [
            row[1]
            for row in conn.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(users)")
            )
        ]
        for col_name, col_type in new_cols:
            if col_name not in existing:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
                    )
                )
                conn.commit()
                print(f"DB migration: added column users.{col_name}")
        # Ensure all NULL store values default to '929'
        conn.execute(__import__("sqlalchemy").text("UPDATE users SET store='929' WHERE store IS NULL"))
        conn.commit()


class Tarea(Base):
    __tablename__ = "tareas"

    id = Column(Integer, primary_key=True, index=True)
    descripcion = Column(Text, nullable=False)
    estado = Column(String(20), default="pendiente")  # pendiente | en_progreso | completada
    creado_en = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    fecha_planificacion = Column(String(10), nullable=True)  # YYYY-MM-DD
    creado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    image_data = Column(Text, nullable=True)        # base64-encoded image
    image_mime = Column(String(50), nullable=True)  # e.g. image/jpeg

    creador = relationship("User", foreign_keys=[creado_por_id])


# ══════════════════════════════════════════════════════════════════════════════
# Checklist Completitud models
# ══════════════════════════════════════════════════════════════════════════════

class ChecklistLocal(Base):
    __tablename__ = "checklist_locales"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False, unique=True)
    activo = Column(String(5), default="si")  # si | no
    secciones = relationship("ChecklistSeccion", back_populates="local", cascade="all, delete-orphan")
    sesiones = relationship("ChecklistSesion", back_populates="local")


class ChecklistSeccion(Base):
    __tablename__ = "checklist_secciones"
    id = Column(Integer, primary_key=True, index=True)
    local_id = Column(Integer, ForeignKey("checklist_locales.id"), nullable=False)
    nombre = Column(String(100), nullable=False)
    activo = Column(String(5), default="si")
    local = relationship("ChecklistLocal", back_populates="secciones")
    sesiones = relationship("ChecklistSesion", back_populates="seccion")


class ChecklistRutina(Base):
    """Catalog of routines to check. Can be scoped to a section or global."""
    __tablename__ = "checklist_rutinas"
    id = Column(Integer, primary_key=True, index=True)
    descripcion = Column(Text, nullable=False)
    categoria = Column(String(100), nullable=True)
    orden = Column(Integer, default=0)
    activo = Column(String(5), default="si")
    respuestas = relationship("ChecklistRespuesta", back_populates="rutina")


class ChecklistSesion(Base):
    """One checklist instance: local + date + section + shift."""
    __tablename__ = "checklist_sesiones"
    id = Column(Integer, primary_key=True, index=True)
    local_id = Column(Integer, ForeignKey("checklist_locales.id"), nullable=False)
    seccion_id = Column(Integer, ForeignKey("checklist_secciones.id"), nullable=True)
    fecha = Column(String(10), nullable=False)       # YYYY-MM-DD
    turno = Column(String(10), nullable=False)        # AM | PM | Noche
    creado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    creado_en = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    cerrado = Column(String(5), default="no")         # no | si
    local = relationship("ChecklistLocal", back_populates="sesiones")
    seccion = relationship("ChecklistSeccion", back_populates="sesiones")
    respuestas = relationship("ChecklistRespuesta", back_populates="sesion", cascade="all, delete-orphan")


class ChecklistRespuesta(Base):
    """Answer to one routine inside a session."""
    __tablename__ = "checklist_respuestas"
    id = Column(Integer, primary_key=True, index=True)
    sesion_id = Column(Integer, ForeignKey("checklist_sesiones.id"), nullable=False)
    rutina_id = Column(Integer, ForeignKey("checklist_rutinas.id"), nullable=False)
    resultado = Column(String(15), default="pendiente")  # pendiente | cumple | no_cumple
    observacion = Column(Text, nullable=True)
    respondido_en = Column(DateTime, nullable=True)
    sesion = relationship("ChecklistSesion", back_populates="respuestas")
    rutina = relationship("ChecklistRutina", back_populates="respuestas")

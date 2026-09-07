"""
Tiny, explicit data-access layer used by database.py's model classes.

Not an ORM, not pretending to be SQLAlchemy — just the exact handful of
operations this app needs (equality/in/range filters, order_by, limit),
implemented once and shared by two interchangeable backends:

  - FirestoreBackend  → real Google Cloud Firestore (prod + real dev testing)
  - MemoryBackend     → in-process dict store (zero-setup local dev, no
                        credentials needed, data resets every restart)

Design trade-off (documented on purpose, not accidental): every query fetches
the full collection and filters/sorts in Python instead of pushing filters
down into native Firestore queries. That's the right call at this app's
actual scale (a couple of stores, low hundreds of docs per collection) and
sidesteps Firestore's composite-index ceremony entirely. If item_history
ever grows into the tens of thousands of rows, revisit this — not before
(YAGNI).
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════════
# Backends — both expose the same tiny interface:
#   .get(collection, doc_id) -> dict | None
#   .add(collection, data) -> new_id
#   .set(collection, doc_id, data) -> None   (create-or-replace with a known id)
#   .delete(collection, doc_id) -> None
#   .stream_all(collection) -> list[tuple[id, dict]]
# ══════════════════════════════════════════════════════════════════════════

class MemoryBackend:
    """In-memory fallback so `uvicorn main:app` works with zero setup.

    Mirrors the old SQLite-fallback philosophy from the Postgres days —
    just simpler, since Firestore has no schema to migrate.
    """

    def __init__(self):
        self._data: dict[str, dict[str, dict]] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def _coll(self, collection: str) -> dict[str, dict]:
        return self._data.setdefault(collection, {})

    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        doc = self._coll(collection).get(doc_id)
        return dict(doc) if doc is not None else None

    def add(self, collection: str, data: dict) -> str:
        with self._lock:
            doc_id = f"mem{next(self._counter)}"
            self._coll(collection)[doc_id] = dict(data)
            return doc_id

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self._coll(collection)[doc_id] = dict(data)

    def delete(self, collection: str, doc_id: str) -> None:
        self._coll(collection).pop(doc_id, None)

    def stream_all(self, collection: str) -> list[tuple[str, dict]]:
        return [(k, dict(v)) for k, v in self._coll(collection).items()]


class FirestoreBackend:
    """Thin wrapper around google.cloud.firestore.Client."""

    def __init__(self, client):
        self._client = client

    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        snap = self._client.collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def add(self, collection: str, data: dict) -> str:
        _, ref = self._client.collection(collection).add(data)
        return ref.id

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self._client.collection(collection).document(doc_id).set(data)

    def delete(self, collection: str, doc_id: str) -> None:
        self._client.collection(collection).document(doc_id).delete()

    def stream_all(self, collection: str) -> list[tuple[str, dict]]:
        return [(doc.id, doc.to_dict()) for doc in self._client.collection(collection).stream()]


# ══════════════════════════════════════════════════════════════════════════
# Query builder — the one obvious way to filter/sort/limit a collection.
# ══════════════════════════════════════════════════════════════════════════

_MISSING = object()


class Query:
    def __init__(self, model_cls: type["Model"], backend):
        self._model_cls = model_cls
        self._backend = backend
        self._eq: dict[str, Any] = {}
        self._in: dict[str, list] = {}
        self._not_none: list[str] = []
        self._lt: dict[str, Any] = {}
        self._lte: dict[str, Any] = {}
        self._gt: dict[str, Any] = {}
        self._gte: dict[str, Any] = {}
        self._startswith: dict[str, str] = {}
        self._contains_ci: dict[str, str] = {}
        self._order: list[tuple[str, bool]] = []
        self._limit_n: Optional[int] = None

    # ── filter builders (chainable) ─────────────────────────────────────
    def filter(self, **kwargs) -> "Query":
        self._eq.update(kwargs)
        return self

    def filter_in(self, field: str, values: list) -> "Query":
        self._in[field] = list(values)
        return self

    def filter_not_none(self, field: str) -> "Query":
        self._not_none.append(field)
        return self

    def filter_lt(self, field: str, value) -> "Query":
        self._lt[field] = value
        return self

    def filter_lte(self, field: str, value) -> "Query":
        self._lte[field] = value
        return self

    def filter_gt(self, field: str, value) -> "Query":
        self._gt[field] = value
        return self

    def filter_gte(self, field: str, value) -> "Query":
        self._gte[field] = value
        return self

    def filter_startswith(self, field: str, prefix: str) -> "Query":
        self._startswith[field] = prefix
        return self

    def filter_contains_ci(self, field: str, needle: str) -> "Query":
        self._contains_ci[field] = needle.lower()
        return self

    def order_by(self, field: str, desc: bool = False) -> "Query":
        self._order.append((field, desc))
        return self

    def limit(self, n: int) -> "Query":
        self._limit_n = n
        return self

    # ── matching ─────────────────────────────────────────────────────────
    def _matches(self, doc_id: str, doc: dict) -> bool:
        for k, v in self._eq.items():
            actual = doc_id if k == "id" else doc.get(k)
            if actual != v:
                return False
        for k, values in self._in.items():
            actual = doc_id if k == "id" else doc.get(k)
            if actual not in values:
                return False
        for k in self._not_none:
            if doc.get(k) is None:
                return False
        for k, v in self._lt.items():
            dv = doc.get(k)
            if dv is None or not (dv < v):
                return False
        for k, v in self._lte.items():
            dv = doc.get(k)
            if dv is None or not (dv <= v):
                return False
        for k, v in self._gt.items():
            dv = doc.get(k)
            if dv is None or not (dv > v):
                return False
        for k, v in self._gte.items():
            dv = doc.get(k)
            if dv is None or not (dv >= v):
                return False
        for k, prefix in self._startswith.items():
            if not (doc.get(k) or "").startswith(prefix):
                return False
        for k, needle in self._contains_ci.items():
            if needle not in (doc.get(k) or "").lower():
                return False
        return True

    def _sort_key_value(self, v):
        """None-safe sort key: Nones sort first regardless of direction."""
        return (v is None, v)

    def _raw(self) -> list[tuple[str, dict]]:
        # Fast path: filtering by id alone → single doc lookup, no full scan.
        if list(self._eq.keys()) == ["id"] and not any([
            self._in, self._not_none, self._lt, self._lte, self._gt,
            self._gte, self._startswith, self._contains_ci,
        ]):
            doc = self._backend.get(self._model_cls._collection, self._eq["id"])
            docs = [(self._eq["id"], doc)] if doc is not None else []
        else:
            docs = [
                (i, d) for i, d in self._backend.stream_all(self._model_cls._collection)
                if self._matches(i, d)
            ]
        for field, desc in reversed(self._order):
            docs.sort(key=lambda pair: self._sort_key_value(pair[1].get(field)), reverse=desc)
        if self._limit_n is not None:
            docs = docs[: self._limit_n]
        return docs

    # ── terminal operations ─────────────────────────────────────────────
    def all(self) -> list["Model"]:
        return [self._model_cls._from_doc(i, d) for i, d in self._raw()]

    def first(self) -> Optional["Model"]:
        docs = self._raw()[:1] if self._limit_n is None else self._raw()
        return self._model_cls._from_doc(*docs[0]) if docs else None

    def count(self) -> int:
        return len(self._raw())

    def delete_all(self) -> None:
        for doc_id, _ in self._raw():
            self._backend.delete(self._model_cls._collection, doc_id)


# ══════════════════════════════════════════════════════════════════════════
# Model base class — plain attribute-holding objects, no magic descriptors.
# ══════════════════════════════════════════════════════════════════════════

class Model:
    """Subclasses set `_collection` (str) and `_fields` (dict of name->default)."""

    _collection: str = ""
    _fields: dict[str, Any] = {}

    def __init__(self, id: Optional[str] = None, **kwargs):
        self.id = id
        for name, default in self._fields.items():
            value = kwargs.pop(name, _MISSING)
            setattr(self, name, default() if callable(default) and value is _MISSING else
                    (default if value is _MISSING else value))
        if kwargs:
            raise TypeError(f"{type(self).__name__} got unexpected field(s): {list(kwargs)}")

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self._fields}

    @classmethod
    def _from_doc(cls, doc_id: str, data: dict) -> "Model":
        obj = cls.__new__(cls)
        obj.id = doc_id
        for name, default in cls._fields.items():
            setattr(obj, name, data.get(name, default() if callable(default) else default))
        return obj

    def save(self, db) -> "Model":
        """Insert (no id yet) or overwrite (id already set). Returns self."""
        data = self.to_dict()
        if self.id is None:
            self.id = db.add(self._collection, data)
        else:
            db.set(self._collection, self.id, data)
        return self

    def delete(self, db) -> None:
        if self.id is not None:
            db.delete(self._collection, self.id)

    @classmethod
    def get(cls, db, doc_id: Optional[str]) -> Optional["Model"]:
        if not doc_id:
            return None
        data = db.get(cls._collection, doc_id)
        return cls._from_doc(doc_id, data) if data is not None else None

    @classmethod
    def query(cls, db) -> Query:
        return Query(cls, db)

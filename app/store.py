"""The saved WizCloud session: one per server and company, shared by every instance (backlog 0301, S0).

Why it is saved. Autoscale starts a fresh process for a request after an idle spell, and the vendor
issues a session for 24 hours, "once a day" (docs.wizcloud.co.il). Held in memory only, every cold
start would open a new one. Whether a new session revokes the previous one is not documented (spec
§10): if it does, two instances each holding their own would knock each other out. So the session
lives in the service's database, and a renewal runs under a Postgres advisory lock after reading the
row again: an instance whose session was rejected takes the one another instance already renewed,
instead of opening a third.

Why it is sealed. A session string carries all of Chava's rights in the books for 24 hours, and it
never leaves the service (spec §2.5; on 18.11 one was saved as a cloud network secret). The row holds
AES-GCM ciphertext under a key derived from WIZ_PRIVATE_KEY, which lives only in Replit Secrets, bound
to the server and the company. The Database pane, an export, a backup or a role granted by mistake
hold a blob. The key is never stored, and a row sealed under an earlier key cannot be opened: the
next renewal replaces it.

Best effort, with traces (norm 2026-08-12). A store that fails never stops a read of the books - the
client carries on with a session of its own - but the failure is said, in /check and in /health.
Errors carry a kind from STORE_KINDS and the exception's class or SQLSTATE, never its text, which can
carry the database host and user.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .wizcloud import StoreErrorBase

TABLE = "wiz_sessions"
# What the writes below name, per table. app/migrate.py checks the live schema against it.
CHECKED_TABLES = {TABLE: ("server", "db", "sealed", "shape", "issued_at", "saved_at")}

CONNECT_TIMEOUT_S = 10
# A renewal waits this long for another instance's renewal, then gives up and says so. createSession's
# budget (app/wizcloud.SESSION_TIMEOUT) is per phase, not per request: connect 5 s, TLS 5 s, the write
# 10 s and each read 10 s - about 30 s before the first byte at worst, and DNS has no bound at all
# (review 19.11). So the wait covers the bounded phases with a margin, and a slower vendor costs an
# unsaved extra session, said as store_lock_timeout: harmless, since a new session does not revoke the
# old one (seen on Demo 19.11) and a renewal is taken only when it is newer than the refused session.
LOCK_TIMEOUT = "35s"
# A plain read (load, and /health) waits at most this long for a lock someone else holds on the table, or
# for any one statement: a stuck table costs a new session or a stale /health, never a hung request.
READ_LOCK_TIMEOUT = "5s"
READ_STATEMENT_TIMEOUT = "10s"
_KEY_INFO = b"analiza-finance/wiz-session/v1"
_NONCE_BYTES = 12


def _ms(limit: str, budget_s: Optional[float]) -> str:
    """A Postgres timeout: `limit` ('35s'), or less when the caller's time box has less left (review 22.11)."""
    if budget_s is None:
        return limit
    return f"{max(1, min(int(limit.rstrip('s')) * 1000, int(budget_s * 1000)))}ms"


STORE_KINDS = (
    "store_unreachable",  # no connection: the database is down, asleep too long, or the URL is wrong
    "store_schema",  # the table or a column is missing: python3 -m app.migrate has not run on this database
    "store_lock_timeout",  # another instance held the renewal lock past LOCK_TIMEOUT
    "store_error",  # any other database error
)


class StoreError(StoreErrorBase):
    def __init__(self, kind: str, detail: str = ""):
        assert kind in STORE_KINDS, kind
        self.kind = kind
        self.detail = detail
        super().__init__(kind + (f": {detail}" if detail else ""))

    def as_dict(self) -> dict:
        out: dict = {"ok": False, "kind": self.kind}
        if self.detail:
            out["detail"] = self.detail
        return out


# ---- sealing -------------------------------------------------------------------------------


def derive_key(private_key: str) -> bytes:
    """32 bytes for AES-256-GCM, from the private key. Never stored anywhere."""
    if not private_key:
        raise ValueError("no private key to derive the session key from")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_KEY_INFO).derive(private_key.encode("utf-8"))


def _aad(server: str, db: str) -> bytes:
    return f"{server}/{db}".encode("utf-8")


def seal(key: bytes, server: str, db: str, token: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, token.encode("utf-8"), _aad(server, db))


def unseal(key: bytes, server: str, db: str, blob: Optional[bytes]) -> Optional[str]:
    """The session string, or None when the blob was sealed under another key or for another company."""
    blob = bytes(blob or b"")
    if len(blob) <= _NONCE_BYTES:
        return None
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], _aad(server, db)).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError):
        return None


# ---- the store -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Saved:
    """A row as this instance can use it. `token` is empty when the row could not be opened."""

    token: str = field(repr=False)
    shape: str
    issued_at: float  # epoch seconds

    @property
    def readable(self) -> bool:
        return bool(self.token)


def _lock_id(server: str, db: str) -> int:
    digest = hashlib.sha256(f"analiza-finance:wiz-session:{server}/{db}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def _guard() -> Iterator[None]:
    """Turn a database error into a StoreError. Anything else (a WizError from inside a renewal)
    passes through untouched."""
    import psycopg
    from psycopg import errors as pgerr

    try:
        yield
    except (pgerr.UndefinedTable, pgerr.UndefinedColumn) as exc:
        raise StoreError("store_schema", exc.sqlstate or type(exc).__name__) from None
    except pgerr.LockNotAvailable as exc:
        raise StoreError("store_lock_timeout", exc.sqlstate or type(exc).__name__) from None
    except psycopg.OperationalError as exc:
        raise StoreError("store_unreachable", getattr(exc, "sqlstate", None) or type(exc).__name__) from None
    except psycopg.Error as exc:
        raise StoreError("store_error", getattr(exc, "sqlstate", None) or type(exc).__name__) from None


_SELECT = f"SELECT sealed, shape, extract(epoch FROM issued_at)::float8 FROM {TABLE} WHERE server = %s AND db = %s"


class PgSessionStore:
    """The session in the service's own Postgres. `url` and the key never leave this object."""

    kind = "postgres"

    def __init__(self, url: str, private_key: str):
        self._url = url
        self._key = derive_key(private_key)

    def __repr__(self) -> str:
        return "PgSessionStore()"

    def _connect(self, budget_s: Optional[float] = None):
        """psycopg.connect, where any failure is a StoreError. Some are not psycopg errors at all - a
        password with a %-escape that is not UTF-8, a host label over 63 characters - and would have
        made /check a 500 instead of costing a session (review 19.11). Only the connect is wrapped:
        the renewal's body raises WizError, which must stay a WizError."""
        import psycopg

        try:
            # libpq waits at least 2 s whatever it is given
            timeout = CONNECT_TIMEOUT_S if budget_s is None else max(2, min(CONNECT_TIMEOUT_S, int(budget_s)))
            return psycopg.connect(self._url, connect_timeout=timeout)
        except psycopg.Error:
            raise
        except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
            raise StoreError("store_unreachable", type(exc).__name__) from None

    def _open(self, row, server: str, db: str) -> Optional[Saved]:
        if row is None:
            return None
        sealed, shape, issued_at = row
        return Saved(token=unseal(self._key, server, db, sealed) or "", shape=shape, issued_at=float(issued_at))

    def load(self, server: str, db: str, budget_s: Optional[float] = None) -> Optional[Saved]:
        with _guard():
            with self._connect(budget_s) as conn:
                conn.execute(f"SET LOCAL lock_timeout = '{_ms(READ_LOCK_TIMEOUT, budget_s)}'")
                conn.execute(f"SET LOCAL statement_timeout = '{_ms(READ_STATEMENT_TIMEOUT, budget_s)}'")
                row = conn.execute(_SELECT, (server, db)).fetchone()
        return self._open(row, server, db)

    @contextmanager
    def renewal(self, server: str, db: str, budget_s: Optional[float] = None) -> Iterator["Renewal"]:
        """One transaction holding this company's renewal lock. Leaving the block commits; an
        exception rolls back. Another instance's renewal of the same company waits for it, at most
        LOCK_TIMEOUT or what is left of the caller's time box (`budget_s`)."""
        with _guard():
            with self._connect(budget_s) as conn:
                conn.execute(f"SET LOCAL lock_timeout = '{_ms(LOCK_TIMEOUT, budget_s)}'")
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_id(server, db),))
                yield Renewal(self, conn, server, db)


class Renewal:
    def __init__(self, store: PgSessionStore, conn, server: str, db: str):
        self._store, self._conn, self._server, self._db = store, conn, server, db

    def current(self) -> Optional[Saved]:
        row = self._conn.execute(_SELECT, (self._server, self._db)).fetchone()
        return self._store._open(row, self._server, self._db)

    def save(self, token: str, shape: str, issued_at: float) -> None:
        self._conn.execute(
            f"INSERT INTO {TABLE} (server, db, sealed, shape, issued_at, saved_at) "
            "VALUES (%s, %s, %s, %s, to_timestamp(%s), now()) "
            "ON CONFLICT (server, db) DO UPDATE SET sealed = EXCLUDED.sealed, shape = EXCLUDED.shape, "
            "issued_at = EXCLUDED.issued_at, saved_at = EXCLUDED.saved_at",
            (self._server, self._db, seal(self._store._key, self._server, self._db, token), shape, issued_at),
        )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def saved_times(cur, server: str, db: str) -> Optional[dict]:
    """For /health, on the caller's cursor: the saved session's shape and times (epoch seconds). Never
    the blob, never the session; needs no key."""
    cur.execute(
        f"SELECT shape, extract(epoch FROM issued_at)::float8, extract(epoch FROM saved_at)::float8 "
        f"FROM {TABLE} WHERE server = %s AND db = %s",
        (server, db),
    )
    row = cur.fetchone()
    if row is None:
        return None
    shape, issued_at, saved_at = row
    return {"shape": shape, "issued_at": issued_at, "saved_at": saved_at}


def describe(times: Optional[dict], now: float) -> Optional[dict]:
    if times is None:
        return None
    return {
        "shape": times["shape"],
        "issued_at": _iso(times["issued_at"]),
        "age_s": int(now - times["issued_at"]),
        "saved_at": _iso(times["saved_at"]),
    }

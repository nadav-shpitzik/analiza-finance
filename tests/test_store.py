"""The saved session: sealing, and the store's SQL against a real Postgres (TEST_DATABASE_URL)."""

import psycopg
import pytest

from app import migrate, store
from app.store import PgSessionStore, StoreError, derive_key, describe_saved, seal, unseal
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, needs_pg

SERVER, DB = "lb19.wizcloud.co.il", "wizdb2245n4"


# ---- sealing --------------------------------------------------------------------------------


def test_a_sealed_session_opens_only_with_its_key_and_company():
    key = derive_key(KEY)
    blob = seal(key, SERVER, DB, SESSION)
    assert SESSION.encode() not in blob and KEY.encode() not in blob
    assert unseal(key, SERVER, DB, blob) == SESSION
    assert unseal(derive_key(KEY + "x"), SERVER, DB, blob) is None  # a key Chava issued later
    assert unseal(key, SERVER, "wizdb2245n5", blob) is None  # Demo's row cannot serve the real company
    assert unseal(key, SERVER, DB, blob[:-1]) is None
    assert unseal(key, SERVER, DB, b"") is None and unseal(key, SERVER, DB, None) is None
    assert seal(key, SERVER, DB, SESSION) != blob  # a fresh nonce each time


def test_no_key_no_store():
    with pytest.raises(ValueError):
        derive_key("")
    assert KEY not in repr(PgSessionStore("postgresql://u:PASSWORD@h/d", KEY))
    assert "PASSWORD" not in repr(PgSessionStore("postgresql://u:PASSWORD@h/d", KEY))


def test_an_unreachable_database_is_a_kind_without_the_url(fake, clock):
    url = "postgresql://user:SECRETPW@127.0.0.1:1/nothing"  # port 1: refused at once
    with pytest.raises(StoreError) as err:
        PgSessionStore(url, KEY).load(SERVER, DB)
    assert err.value.kind == "store_unreachable"
    assert "SECRETPW" not in str(err.value) and "127.0.0.1" not in str(err.value)
    assert err.value.__cause__ is None
    c = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(url, KEY))
    assert c.companies() == fake.companies
    assert c.session().store_error["kind"] == "store_unreachable"


# ---- against Postgres ------------------------------------------------------------------------


@pytest.fixture
def pg():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS wiz_sessions, schema_migrations")
    yield TEST_DATABASE_URL
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS wiz_sessions, schema_migrations")


@needs_pg
def test_migrations_apply_once_and_the_ledger_is_checked(pg):
    first = migrate.apply_migrations(pg)
    assert first == {"applied": ["001_wiz_sessions.sql"], "skipped": [], "repaired": [], "missing": []}
    second = migrate.apply_migrations(pg)
    assert second == {"applied": [], "skipped": ["001_wiz_sessions.sql"], "repaired": [], "missing": []}
    state = migrate.schema_state(pg)
    assert state == {
        "tables": {"wiz_sessions": "present", "schema_migrations": "present"},
        "missing_columns": [],
        "migrations": ["001_wiz_sessions.sql"],
    }
    # The ledger says 001 ran, the table is gone (a restore, a reset): reported, then repaired.
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("DROP TABLE wiz_sessions")
    assert migrate.schema_state(pg)["missing_columns"] == ["wiz_sessions"]
    third = migrate.apply_migrations(pg)
    assert third["repaired"] == ["001_wiz_sessions.sql"] and third["missing"] == []


@needs_pg
def test_two_processes_share_one_session_and_the_row_is_sealed(pg, fake, clock):
    migrate.apply_migrations(pg)
    a = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    b = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    a.companies()
    b.companies()
    assert fake.issued == 1
    assert (a.session().origin, b.session().origin) == ("new", "saved")
    with psycopg.connect(pg) as conn:
        rows = conn.execute("SELECT server, db, sealed, shape FROM wiz_sessions").fetchall()
    assert len(rows) == 1 and rows[0][:2] == (SERVER, DB) and rows[0][3] == "text"
    assert SESSION.encode() not in bytes(rows[0][2])
    seen = describe_saved(pg, SERVER, DB, clock.t + 90)
    assert seen["shape"] == "text" and seen["age_s"] == 90
    assert SESSION not in str(seen)
    assert describe_saved(pg, SERVER, "wizdb2245n5", clock.t) is None


@needs_pg
def test_a_rejected_session_is_renewed_once_for_everyone(pg, fake, clock):
    migrate.apply_migrations(pg)
    a = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    b = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    a.companies()
    b.companies()
    # WizCloud now refuses the shared session (revoked, or expired early) and answers a new one.
    fake.valid_sessions = {"RENEWED-SESSION-42"}
    fake.session_body = "RENEWED-SESSION-42"
    a.companies()  # a renews and saves
    b.companies()  # b finds its session refused, and takes a's renewal from the row
    assert fake.issued == 2
    assert b.session().token == "RENEWED-SESSION-42" and b.session().origin == "saved"


@needs_pg
def test_a_key_change_replaces_the_row(pg, fake, clock):
    migrate.apply_migrations(pg)
    with PgSessionStore(pg, "OLDKEY-0000000000").renewal(SERVER, DB) as renewal:  # saved before Chava re-issued
        renewal.save("SESSION-UNDER-THE-OLD-KEY", "text", clock.t - 60)
    assert PgSessionStore(pg, KEY).load(SERVER, DB).readable is False
    c = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    c.companies()
    assert c.session().note == "the saved session was sealed under another key and was replaced"
    assert PgSessionStore(pg, KEY).load(SERVER, DB).token == SESSION


@needs_pg
def test_a_held_renewal_lock_times_out_and_is_said(pg, fake, clock, monkeypatch):
    migrate.apply_migrations(pg)
    monkeypatch.setattr(store, "LOCK_TIMEOUT", "1s")
    with psycopg.connect(pg) as holder:  # another instance, stuck inside its renewal
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (store._lock_id(SERVER, DB),))
        c = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
        assert c.companies() == fake.companies
        assert c.session().store_error == {"ok": False, "kind": "store_lock_timeout", "detail": "55P03"}
        assert c.session().saved is False


@needs_pg
def test_a_database_without_the_table_is_a_schema_kind(pg, fake, clock):
    c = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    assert c.companies() == fake.companies
    assert c.session().store_error == {"ok": False, "kind": "store_schema", "detail": "42P01"}

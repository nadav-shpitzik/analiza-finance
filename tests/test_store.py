"""The saved session: sealing, and the store's SQL against a real Postgres (TEST_DATABASE_URL)."""

import psycopg
import pytest

import httpx

from app import migrate, store
from app.store import PgSessionStore, StoreError, derive_key, describe, saved_times, seal, unseal
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, all_tables, drop_all, migration_files, needs_pg

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
    drop_all(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


@needs_pg
def test_migrations_apply_once_and_the_ledger_is_checked(pg):
    files = migration_files()
    first = migrate.apply_migrations(pg)
    assert first == {"applied": files, "skipped": [], "repaired": [], "missing": []}
    second = migrate.apply_migrations(pg)
    assert second == {"applied": [], "skipped": files, "repaired": [], "missing": []}
    state = schema_state(pg)
    assert state == {
        "tables": {t: "present" for t in all_tables()},
        "missing_columns": [],
        "migrations": files,
    }
    # The ledger says 001 ran, the table is gone (a restore, a reset): reported, then repaired.
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("DROP TABLE wiz_sessions")
    assert schema_state(pg)["missing_columns"] == ["wiz_sessions"]
    third = migrate.apply_migrations(pg)
    assert third["repaired"] == files and third["missing"] == []


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
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        seen = describe(saved_times(cur, SERVER, DB), clock.t + 90)
        other = saved_times(cur, SERVER, "wizdb2245n5")
    assert seen["shape"] == "text" and seen["age_s"] == 90
    assert SESSION not in str(seen)
    assert other is None and describe(other, clock.t) is None


@needs_pg
def test_a_rejected_session_is_renewed_once_for_everyone(pg, fake, clock):
    migrate.apply_migrations(pg)
    a = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    b = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
    a.companies()
    b.companies()
    # WizCloud now refuses the shared session (expired early, say) and answers a new one.
    fake.valid_sessions = {"RENEWED-SESSION-42"}
    fake.session_body = "RENEWED-SESSION-42"
    clock.t += 5
    a.companies()  # a renews and saves
    clock.t += 5
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


def schema_state(url):
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        return migrate.schema_state(cur)


class Vendor:
    """Sessions die only when the vendor drops them (a new one does not revoke the old: Demo 19.11)."""

    def __init__(self):
        self.n = 0
        self.valid = set()

    def handler(self, request):
        path = request.url.path.lstrip("/")
        if path.startswith("createSession/"):
            self.n += 1
            token = f"T{self.n}-" + "x" * 30
            self.valid.add(token)
            return httpx.Response(200, text=token)
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.valid:
            return httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Token is not valid"}})
        return httpx.Response(200, json=[{"dbName": DB}])


@needs_pg
def test_an_older_saved_session_is_not_taken_for_a_renewal(pg, clock):
    """The review's sequence (19.11): A renews but cannot save, so the row keeps an older session;
    when A's session is refused again, the row's older one is not a renewal and is not taken."""
    migrate.apply_migrations(pg)
    vendor = Vendor()

    def instance():
        return WizClient(SERVER, DB, KEY, transport=httpx.MockTransport(vendor.handler), clock=clock,
                         store=PgSessionStore(pg, KEY))

    instance().companies()  # B opens T1; the row holds T1
    a = instance()
    a.companies()  # A cold-starts and takes T1 from the row
    vendor.valid.clear()  # the vendor drops every live session
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("ALTER TABLE wiz_sessions ADD CONSTRAINT refuse CHECK (false) NOT VALID")
    clock.t += 5
    a.companies()  # A renews to T2, the save fails, the row keeps T1
    assert a.session().saved is False and vendor.n == 2
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("ALTER TABLE wiz_sessions DROP CONSTRAINT refuse")
    vendor.valid.clear()  # the vendor drops sessions again
    clock.t += 5
    assert a.companies() == [{"dbName": DB}]  # T1 in the row is older than T2: not taken
    assert vendor.n == 3 and a.session().token.startswith("T3-")


@needs_pg
def test_a_failed_migration_applies_nothing(pg, tmp_path, monkeypatch):
    (tmp_path / "001_wiz_sessions.sql").write_text((migrate.MIGRATIONS_DIR / "001_wiz_sessions.sql").read_text())
    (tmp_path / "002_broken.sql").write_text("CREATE TABLE broken (x int NOT NULL DEFAULT 'not a number');")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(psycopg.Error):
        migrate.apply_migrations(pg)
    assert schema_state(pg)["tables"] == {t: "absent" for t in all_tables()}


def test_the_shell_migration_prints_no_secret(monkeypatch, capsys):
    for url in ("postgresql://user:pa%zzSECRETPW@127.0.0.1:1/x", "postgresql://user:SECRETPW@127.0.0.1:1/x"):
        monkeypatch.setenv("DATABASE_URL", url)
        assert migrate.main() == 1
        out = capsys.readouterr()
        text = out.out + out.err
        assert "SECRETPW" not in text and "user" not in text and "127.0.0.1" not in text
        assert "FAILED: " in out.err and "Nothing was applied" in out.err


@needs_pg
def test_a_successful_shell_migration_names_the_database_never_the_user(pg, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg)
    assert migrate.main() == 0
    out = capsys.readouterr().out
    user = psycopg.conninfo.conninfo_to_dict(pg).get("user") or ""
    assert out.startswith("database: ") and out.rstrip().endswith("every column the writes need")
    assert out.strip().splitlines()[-1].startswith("ok: ")
    assert not user or f"{user}@" not in out and f"{user}:" not in out


@needs_pg
def test_a_locked_table_ends_a_read_instead_of_hanging_it(pg, fake, clock, monkeypatch):
    """Review 19.11 (hardening): load() and /health's read had no lock or statement timeout."""
    import time as _time

    migrate.apply_migrations(pg)
    monkeypatch.setattr(store, "READ_LOCK_TIMEOUT", "1s")
    with psycopg.connect(pg) as holder:
        holder.execute("LOCK TABLE wiz_sessions IN ACCESS EXCLUSIVE MODE")  # held until this block ends
        started = _time.monotonic()
        with pytest.raises(StoreError) as err:
            PgSessionStore(pg, KEY).load(SERVER, DB)
        assert err.value.kind == "store_lock_timeout" and _time.monotonic() - started < 5
        c = WizClient(SERVER, DB, KEY, transport=fake.transport(), clock=clock, store=PgSessionStore(pg, KEY))
        monkeypatch.setattr(store, "LOCK_TIMEOUT", "1s")
        assert c.companies() == fake.companies  # the read goes on with a session of its own
        assert c.session().store_error["kind"] == "store_lock_timeout"

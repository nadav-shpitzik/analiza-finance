import json
import shutil
from pathlib import Path

import httpx
import psycopg
from fastapi.testclient import TestClient

from app import config, main
from app.main import deploy_build, summarize_companies, summarize_triggers
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, FakeStore, dump, needs_pg

ROOT = Path(__file__).resolve().parent.parent


def test_health_never_calls_wizcloud_and_never_shows_the_key(app_client, fake):
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert fake.requests == []
    assert body["service"] == "analiza-finance"
    assert body["read_only"] is True
    assert body["config"] == {
        "private_key_set": True,
        "server": "lb19.wizcloud.co.il",
        "company": "demo",
        "db": "wizdb2245n4",
        "database_url_set": False,
        "problems": [],
    }
    assert body["store"] == {"configured": False, "reason": "DATABASE_URL is not set: the session is kept in memory only"}
    assert KEY not in r.text


def test_the_service_answers_by_its_new_name(app_client):
    assert app_client.get("/").json() == {"service": "analiza-finance", "endpoints": ["/health", "/check"]}
    assert main.app.title == "analiza-finance"


def test_check_happy_path(app_client, fake):
    fake.triggers = [{"url": "https://hooks.example.com/in?secret=abc", "table": "accounts"}]
    r = app_client.get("/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["cached"] is False
    assert body["session"] == {
        "ok": True,
        "shape": "text",
        "length": len(SESSION),
        "source": "new",
        "issued_at": "2027-01-15T08:00:00+00:00",
        "age_s": 0,
        "store": "none",
        "saved": False,
    }
    assert body["companies"]["count"] == 2
    assert body["companies"]["items"] == [
        {"db": "wizdb2245n4", "name": "Demo"},
        {"db": "wizdb2245n5", "name": "אנליזה מודיעין סייבר"},
    ]
    assert body["companies"]["includes_configured_db"] is True
    assert body["triggers"]["items"] == [{"table": "accounts", "host": "hooks.example.com"}]
    assert "secret=abc" not in r.text
    assert KEY not in r.text and SESSION not in r.text


def test_check_is_cached_for_a_minute(app_client, fake, clock):
    app_client.get("/check")
    n = len(fake.requests)
    assert app_client.get("/check").json()["cached"] is True
    assert len(fake.requests) == n
    clock.t += 61
    assert app_client.get("/check").json()["cached"] is False
    assert len(fake.requests) > n


def test_check_reports_a_bad_key_as_503_without_the_key(app_client, fake, monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", "WRONGKEY-123456789")
    r = app_client.get("/check")
    assert r.status_code == 503
    assert r.json()["error"] == {"ok": False, "kind": "bad_key", "status": 400, "message": "iligal token"}
    assert "WRONGKEY-123456789" not in r.text
    assert app_client.get("/health").json()["last_check"]["error"]["kind"] == "bad_key"


def test_check_without_the_secret_makes_no_request(app_client, fake, monkeypatch):
    monkeypatch.delenv("WIZ_PRIVATE_KEY")
    r = app_client.get("/check")
    assert r.status_code == 503
    assert r.json()["error"]["kind"] == "config_missing"
    assert fake.requests == []


def test_switching_to_the_real_company_is_one_variable(monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    assert config.load().db == "wizdb2245n5"
    monkeypatch.setenv("WIZ_COMPANY", "wizdb2245n5")
    assert config.load().problems  # a raw db name is not accepted, only the two names


def test_server_must_be_a_wizcloud_balancer(monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("WIZ_SERVER", "evil.example.com")
    assert config.load().problems


def test_config_repr_hides_the_key(monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    assert KEY not in repr(config.load())


def test_summaries_keep_names_and_drop_values():
    s = summarize_companies({"statusCode": 200, "repdata": [{"DB": "wizdb1n1", "CompanyName": "X", "VatNum": "123456789"}]}, "wizdb1n1")
    assert s["items"] == [{"db": "wizdb1n1", "name": "X"}]
    assert "123456789" not in dump(s)
    assert s["fields"] == ["CompanyName", "DB", "VatNum"]
    assert summarize_triggers([]) == {"ok": True, "count": 0, "fields": [], "items": []}
    assert summarize_triggers(None) == {"ok": True, "count": 0, "items": []}
    odd = summarize_companies({"weird": {"a": 1}}, "wizdb1n1")
    assert odd == {"ok": True, "shape": "dict", "tree": {"weird": {"a": "int"}}}


def test_companies_nested_under_status_are_found():
    nested = {"statusCode": 200, "status": {"companies": [{"dbname": "wizdb2245n5", "name": "אנליזה"}]}}
    assert summarize_companies(nested, "wizdb2245n5")["items"] == [{"db": "wizdb2245n5", "name": "אנליזה"}]
    as_string = {"statusCode": 200, "status": '[{"db": "wizdb2245n4", "desc": "Demo"}]'}
    assert summarize_companies(as_string, "wizdb2245n4")["items"] == [{"db": "wizdb2245n4", "name": "Demo"}]
    secret_text = {"statusCode": 200, "status": "OK 123456789"}
    s = summarize_companies(secret_text, "x")
    assert s["tree"] == {"statusCode": "int", "status": "str(12)"} and "123456789" not in dump(s)


def test_live_check_doc_summary_keeps_numbers_and_drops_people():
    from scripts.live_check import doc_summary, parse_ids

    doc = {
        "DocumentDetails": [
            [{"DocNumber": 50892, "AccountName": "ישראל ישראלי", "AccountKey": "10093", "Phone": "050-0000000",
              "ValueDate": "2026-11-01", "Tftal": 1170.0}],
            [{"ItemName": "קורס", "Quantity": 1, "Price": 1000.0, "Details": "ת.ז 123456789"}],
        ]
    }
    s = doc_summary(doc)
    header = s["shape"]["nested"]["DocumentDetails"]["first"][0]["first"][0]
    moves = s["shape"]["nested"]["DocumentDetails"]["first"][1]["first"][0]
    assert header["safe_values"] == {"DocNumber": 50892, "ValueDate": "2026-11-01", "Tftal": 1170.0}
    assert "AccountName" in header["fields"]
    assert moves["safe_values"] == {"Quantity": 1, "Price": 1000.0}
    text = dump(s)
    for leak in ("ישראל ישראלי", '"10093"', "050-0000000", "123456789", '"קורס"'):
        assert leak not in text
    assert doc_summary({}) == {"found": False}
    assert parse_ids("1, 2,x,30") == [1, 2, 30]
    assert len(parse_ids(",".join(str(i) for i in range(50)))) == 10


def test_live_check_reads_json_inside_a_string():
    from scripts.live_check import doc_summary

    s = doc_summary({"DocumentDetails": ['[{"DocNumber": 7, "AccountName": "x"}]']})
    assert s["shape"]["nested"]["DocumentDetails"]["first"][0]["first"][0]["safe_values"] == {"DocNumber": 7}


# ---- slice S0: the session is kept, the build is GitHub main's, the store is said ----------------


def test_the_session_outlives_the_one_minute_cache(app_client, fake, clock):
    assert app_client.get("/check").json()["session"]["source"] == "new"
    clock.t += 61
    second = app_client.get("/check").json()
    assert second["cached"] is False and second["session"]["source"] == "memory"
    assert second["session"]["age_s"] == 61
    assert fake.issued == 1


def test_a_cold_start_takes_the_saved_session(app_client, fake, clock):
    shared = FakeStore()  # the service's database, outliving any one process
    main.app.state.store_factory = lambda cfg: shared
    first = app_client.get("/check").json()["session"]
    assert (first["source"], first["saved"], first["store"]) == ("new", True, "fake")
    main.app.state.client = None  # Autoscale started a fresh process
    clock.t += 61
    later = app_client.get("/check").json()["session"]
    assert (later["source"], later["saved"], later["issued_at"]) == ("saved", True, first["issued_at"])
    assert fake.issued == 1


def test_a_broken_store_is_said_on_check_and_health_without_the_url(app_client, fake, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW@127.0.0.1:1/nothing")
    r = app_client.get("/check")
    assert r.status_code == 200  # the books were read; the store's failure is beside it
    session = r.json()["session"]
    assert (session["store"], session["saved"], session["store_error"]["kind"]) == ("postgres", False, "store_unreachable")
    h = app_client.get("/health")
    store = h.json()["store"]
    assert (store["configured"], store["ok"], store["error"]) == (True, False, "OperationalError")
    assert h.json()["config"]["database_url_set"] is True
    for text in (r.text, h.text):
        assert "SECRETPW" not in text and "127.0.0.1" not in text


def test_health_reads_the_store_at_most_every_fifteen_seconds(app_client, clock, monkeypatch):
    """/health is open: a flood must not turn into a connection per hit (review 19.11)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW@127.0.0.1:1/nothing")
    reads = []
    monkeypatch.setattr(main, "_read_store", lambda cfg: reads.append(1) or {
        "tables": {"wiz_sessions": "present", "schema_migrations": "present"}, "missing_columns": [],
        "migrations": ["001_wiz_sessions.sql"], "times": {"shape": "text", "issued_at": clock.t - 100, "saved_at": clock.t - 100}})
    for _ in range(5):
        body = app_client.get("/health").json()
    assert len(reads) == 1
    assert body["store"]["session"]["age_s"] == 100
    clock.t += 10
    assert app_client.get("/health").json()["store"]["session"]["age_s"] == 110  # the age moves, the read is reused
    assert len(reads) == 1
    clock.t += 6
    app_client.get("/health")
    assert len(reads) == 2


def test_startup_changes_no_schema_without_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(main.app) as c:
        body = c.get("/health").json()
    assert "migrations" not in body
    assert body["store"]["configured"] is False
    main.app.state.build = None


def test_build_is_read_with_deploy_sources_own_reader(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "deploy_source.py", tmp_path / "scripts" / "deploy_source.py")
    assert deploy_build(tmp_path)["source"] is None  # a dev run: no build_info.json, and it says so
    sha = "0123456789abcdef0123456789abcdef01234567"
    (tmp_path / "build_info.json").write_text(json.dumps(
        {"source": "github", "repo": "nadav-shpitzik/analiza-finance", "ref": "main", "sha": sha}))
    build = deploy_build(tmp_path)
    assert build["sha"] == sha
    assert build["commit_url"] == f"https://github.com/nadav-shpitzik/analiza-finance/commit/{sha}"
    assert deploy_build(tmp_path / "nowhere")["reason"].startswith("scripts/deploy_source.py unreadable")


def test_the_build_ships_this_repo_and_guards_the_schema():
    build = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")
    assert "--repo nadav-shpitzik/analiza-finance" in build
    assert "--schema migrations,app/migrate.py" in build
    replit = (ROOT / ".replit").read_text(encoding="utf-8")
    assert 'build = ["bash", "scripts/build.sh"]' in replit
    # the schema step installs main's requirements first: S0 is the first change that needs psycopg (review 19.11)
    assert 'args = "bash scripts/sync-from-github.sh && pip install -q -r requirements.txt && python3 -m app.migrate"' in replit


@needs_pg
def test_the_deployment_never_creates_its_schema_and_says_what_is_missing(app_client, fake, clock, monkeypatch):
    """Production's schema comes only from a Republish (app/migrate.py). A database that missed the
    development step shows it in /health and /check, and the read of the books goes on."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS wiz_sessions, schema_migrations")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    try:
        with TestClient(main.app) as c:
            store = c.get("/health").json()["store"]
            assert (store["ok"], store["missing_columns"]) == (False, ["wiz_sessions"])
            assert store["tables"] == {"wiz_sessions": "absent", "schema_migrations": "absent"}
            check = c.get("/check")
            assert check.status_code == 200
            assert check.json()["session"]["store_error"] == {"ok": False, "kind": "store_schema", "detail": "42P01"}
            from app.migrate import apply_migrations

            apply_migrations(TEST_DATABASE_URL)  # the Shell step, then (here) the same database
            clock.t += 61
            main.app.state.client = None
            assert c.get("/check").json()["session"]["saved"] is True
            clock.t += 30
            h = c.get("/health")
        store = h.json()["store"]
        assert store["ok"] is True and store["missing_columns"] == []
        assert store["tables"] == {"wiz_sessions": "present", "schema_migrations": "present"}
        assert store["session"]["age_s"] == 30 and store["session"]["shape"] == "text"
        assert SESSION not in h.text and KEY not in h.text
    finally:
        main.app.state.build = None
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS wiz_sessions, schema_migrations")


def test_the_session_test_reads_only_and_prints_no_session(fake):
    from scripts.live_check import session_test

    cfg = config.load({"WIZ_PRIVATE_KEY": KEY})
    both = session_test(cfg, transport=fake.transport())
    assert both["verdict"] == "one session: createSession answered the same string"  # the fake answers one string
    fake.issued = 0
    tokens = iter(["FIRST-SESSION-0001", "SECOND-SESSION-002"])

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            fake.session_body = next(tokens)
            fake.valid_sessions = {fake.session_body}  # each new session ends the one before
        return fake.handler(request)

    revoked = session_test(cfg, transport=httpx.MockTransport(handler))
    assert (revoked["first"], revoked["second"], revoked["first_after_second"]) == ("ok", "ok", "session_rejected")
    assert revoked["verdict"] == "revoked: a new session ends the previous one"
    assert "SESSION-0001" not in dump(revoked) and "SESSION-002" not in dump(revoked)
    paths = {r.url.path.split("/")[1] for r in fake.requests}
    assert paths <= {"createSession", "CompanyListToTokenApi"}


def test_a_network_blip_is_not_called_a_revocation(fake):
    """Review 19.11: any failure of the third read used to read as "revoked"."""
    from scripts.live_check import session_test

    reads = {"n": 0}

    def handler(request):
        if request.url.path.startswith("/CompanyListToTokenApi/"):
            reads["n"] += 1
            if reads["n"] == 3:
                raise httpx.ConnectError("blip", request=request)
        return fake.handler(request)

    tokens = iter(["FIRST-SESSION-0001", "SECOND-SESSION-002"])

    def two_strings(request):
        if request.url.path.startswith("/createSession/"):
            fake.session_body = next(tokens)
            fake.valid_sessions.add(fake.session_body)
        return handler(request)

    out = session_test(config.load({"WIZ_PRIVATE_KEY": KEY}), transport=httpx.MockTransport(two_strings))
    assert out["first_after_second"] == "network"
    assert out["verdict"] == "inconclusive: the third read failed with network"


def test_a_key_that_would_reshape_the_url_is_a_config_problem(monkeypatch):
    assert config.load({"WIZ_PRIVATE_KEY": "ABC?x=/wizdb2245n5"}).problems
    assert config.load({"WIZ_PRIVATE_KEY": "ABC-def_123+="}).ok


def test_a_renewal_during_the_round_is_what_check_reports(app_client, fake, clock):
    """Review 19.11: /check described the session it took before the reads. A read that renews it,
    and a save that fails, must show in the answer."""
    from app.store import StoreError

    shared = FakeStore()
    main.app.state.store_factory = lambda cfg: shared
    first = app_client.get("/check").json()["session"]
    assert (first["source"], first["saved"]) == ("new", True)
    fake.valid_sessions = {"RENEWED-IN-THE-ROUND-1"}  # the vendor dropped the session meanwhile
    fake.session_body = "RENEWED-IN-THE-ROUND-1"
    shared.fail_on_save = StoreError("store_error", "25006")
    clock.t += 61
    later = app_client.get("/check").json()
    assert later["ok"] is True
    s = later["session"]
    assert (s["renewed"], s["source"], s["saved"], s["store_error"]["kind"]) == (True, "new", False, "store_error")
    assert s["issued_at"] != first["issued_at"]


def test_a_failed_renewal_is_said_on_the_session(app_client, fake, clock):
    app_client.get("/check")
    fake.valid_sessions = set()
    fake.session_body = "iligal token"  # createSession now refuses the key
    fake.session_status = 400
    clock.t += 61
    body = app_client.get("/check").json()
    assert body["ok"] is False and body["session"]["renewal_failed"] is True
    assert body["companies"]["kind"] == "bad_key"


def test_a_check_already_running_answers_at_once(app_client, fake):
    app_client.get("/check")
    assert main._lock.acquire(blocking=False)  # a round in progress in another thread
    try:
        r = app_client.get("/check")
    finally:
        main._lock.release()
    assert r.status_code == 200 and r.json()["in_progress"] is True and r.json()["cached"] is True
    main._cache.update(at=0.0, result=None)
    assert main._lock.acquire(blocking=False)
    try:
        r = app_client.get("/check")
    finally:
        main._lock.release()
    assert r.status_code == 503 and r.json()["in_progress"] is True


def test_health_serves_the_last_store_read_while_another_request_reads(app_client, clock, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW@127.0.0.1:1/nothing")
    monkeypatch.setattr(main, "_read_store", lambda cfg: {
        "tables": {"wiz_sessions": "present", "schema_migrations": "present"}, "missing_columns": [],
        "migrations": [], "times": None})
    assert app_client.get("/health").json()["store"]["ok"] is True
    clock.t += 20  # the read is old; another request is reading right now
    assert main._store_read.acquire(blocking=False)
    try:
        store = app_client.get("/health").json()["store"]
    finally:
        main._store_read.release()
    assert store["ok"] is True and store["stale"] is True


def test_a_url_psycopg_cannot_even_parse_costs_a_session_not_the_read(app_client, fake, monkeypatch):
    """Review 19.11: a %-escape that is not UTF-8 in the password raised UnicodeDecodeError past the
    store's guard, and /check answered 500."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW%FF@127.0.0.1:1/x")
    r = app_client.get("/check")
    assert r.status_code == 200
    assert r.json()["session"]["store_error"] == {"ok": False, "kind": "store_unreachable", "detail": "UnicodeDecodeError"}
    assert "SECRETPW" not in r.text


def test_the_shell_migration_names_a_missing_module(monkeypatch, capsys):
    from app import migrate

    def missing(url):
        raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")

    monkeypatch.setenv("DATABASE_URL", "postgresql://user@127.0.0.1:1/x")
    monkeypatch.setattr(migrate, "apply_migrations", missing)
    assert migrate.main() == 1
    err = capsys.readouterr().err
    assert "no module 'psycopg' - run pip install -r requirements.txt first" in err


def test_a_round_that_ends_with_no_session_is_not_ok_and_live_check_reads_no_documents(fake, monkeypatch, capsys):
    """Review 19.11: renewal_failed still said ok: true, and live-check opened its document gate."""
    from scripts import live_check

    calls = {"create": 0}

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            calls["create"] += 1
            if calls["create"] > 1:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, text="FIRST-SESSION-0001")
        return httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Token is not valid"}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DOC_IDS", "1,2,3")
    monkeypatch.setattr(live_check, "WizClient", lambda *a, **k: WizClient(*a, transport=transport, **k))
    assert live_check.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["check"]["session"]["ok"] is False and out["check"]["session"]["renewal_failed"] is True
    assert "docs" not in out
    assert calls["create"] == 3  # the round's own: the first session, the companies retry, triggers' try; documents would add 3

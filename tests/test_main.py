from app import config
from app.main import summarize_companies, summarize_triggers
from tests.conftest import KEY, SESSION, dump


def test_health_never_calls_wizcloud_and_never_shows_the_key(app_client, fake):
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert fake.requests == []
    assert body["read_only"] is True
    assert body["config"] == {
        "private_key_set": True,
        "server": "lb19.wizcloud.co.il",
        "company": "demo",
        "db": "wizdb2245n4",
        "problems": [],
    }
    assert KEY not in r.text


def test_check_happy_path(app_client, fake):
    fake.triggers = [{"url": "https://hooks.example.com/in?secret=abc", "table": "accounts"}]
    r = app_client.get("/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["cached"] is False
    assert body["session"] == {"ok": True, "shape": "text", "length": len(SESSION)}
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
    assert odd == {"ok": True, "shape": "dict", "keys": ["weird"]}

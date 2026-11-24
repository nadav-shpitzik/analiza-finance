"""R1: the deployment's access log carries the desk's search text (a student's name) and live signed export links.

analiza-metrics ran uvicorn with --no-access-log (.replit there). analiza-finance's deployment runs
`python3 -m uvicorn app.main:app` with the access log on, and app/main.py NoTokenInAccessLog redacts only
`sessionToken=`. D2 adds GET /api/desk/totals?...&q=<search box> (sent on every pause in typing) and
GET /api/desk/export.xlsx?...&q=...&u=...&exp=...&sig=... (a two-minute bearer link to a file of names and money).
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode


from app import main  # noqa: E402,F401 - installs the access-log filter
from app.desk import routes as dr  # noqa: E402

REPO = Path(__file__).resolve().parents[1]   # the checkout, wherever it is (CI's runner is not this container)
NAME = "ישראלה ישראלי"          # what she types to find a student
SECRET = "review-secret"


def _line(path: str) -> str:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", path, "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(record)
    return record.getMessage()


def _signed_link() -> str:
    fields = ["1", str(int(time.time()) + 120), "b2c", "", "", NAME, "", ""]
    q = urlencode({"tab": "b2c", "state": "", "period": "", "q": NAME, "taken": "", "customer": "", "u": 1,
                   "exp": fields[1], "sig": dr.export_sig(SECRET, fields)})
    return "/api/desk/export.xlsx?" + q


def test_the_totals_line_does_not_carry_the_search_text():
    path = "/api/desk/totals?tab=b2c&state=&period=&q=" + quote(NAME) + "&taken=2026-11-24T08%3A00%3A00.000001%2B00%3A00"
    line = _line(path)
    assert quote(NAME) not in line and NAME not in line, line


def test_the_export_file_line_does_not_carry_the_search_text_or_a_live_signature():
    link = _signed_link()
    line = _line(link)
    sig = re.search(r"sig=([0-9a-f]{64})", link).group(1)
    assert sig not in line, line
    assert quote(NAME) not in line and quote_plus_name() not in line, line


def quote_plus_name() -> str:
    from urllib.parse import quote_plus
    return quote_plus(NAME)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_the_deployment_command_itself_logs_neither():
    """The [deployment] run command of .replit, as it is, against two requests the page makes."""
    replit = (REPO / ".replit").read_text(encoding="utf-8")
    section = replit[replit.index("[deployment]"):]
    run = re.search(r'^run = \["sh", "-c", "([^"]+)"\]', section, re.M).group(1)
    # the access log is on in production today (no --no-access-log); the filter must hold either way
    port = _free_port()
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
    env = dict(os.environ, MONDAY_CLIENT_SECRET=SECRET)
    env.pop("MONDAY_TOKEN", None)            # 503 before any monday read: the line is written all the same
    env.pop("DATABASE_URL", None)
    link = _signed_link()
    proc = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1).read()
                break
            except Exception:
                time.sleep(0.1)
        totals = "/api/desk/totals?tab=b2c&state=&period=&q=" + quote(NAME) + "&taken="
        for path in (totals, link):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5).read()
            except Exception:
                pass                          # 401/503: the access line is written whatever the answer
        time.sleep(0.5)
    finally:
        proc.terminate()
        out = proc.communicate(timeout=10)[0]
    sig = re.search(r"sig=([0-9a-f]{64})", link).group(1)
    assert "/api/desk/totals" in out           # the line is there
    assert quote(NAME) not in out and quote_plus_name() not in out, out[-600:]
    assert sig not in out

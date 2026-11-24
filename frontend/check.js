/* The finance desk's self-check (Analiza-Monday backlog 0301): /desk/?check=1. D1, 24.11: the skeleton's checklist, on a
   private board; since D2 the page itself is the desk (finance.js, mounted by desk.js), and this checklist is what
   /desk/?check=1 draws instead - unchanged, but started by desk.js (run), which hands it the page's one SDK object.

   What it proves, one line each on the screen (the desk move plan §2-§3; D1's acceptance in 0301):
   1. monday: the view is open inside monday and the SDK answers its context - the board, the user, the app's version;
   2. the identity: monday gives a sessionToken and the server verifies it (/api/desk, app/desk/auth.py);
   3. the boards: the reader is a member of Student Charge, of B2B Charge, or of both (the server's gate, app/desk/gate.py);
   4. the app's permissions for this person, as monday's context says them (permissions.approvedScopes, requiredScopes);
   5. monday.api as the person: one read, the name of the board the view is on. Every write of the new desk goes through
      this same door (ADR-0020 §2, ADR-0188), so a permission prompt, if monday has one for this person, shows here;
   6. the desk's reading: complete or not, and its counts per board, for the tabs the reader may see.

   The SDK is passed whenever monday supplies a context, whatever the board the view sits on (the plan §3.1:
   analiza-metrics' glue passed it only on the two charge boards). Everything is drawn with textContent: nothing from an
   answer is ever parsed as HTML. */
(function () {
  "use strict";

  var API_VERSION = "2026-07";
  var WAIT_MS = 3000;
  var MONDAY_WAIT_MS = 15000;
  var params = new URLSearchParams(window.location.search);
  var monday = null;        /* the page's SDK object, given by desk.js (run) */
  var sdkToken = "";

  var LINES = [
    ["monday", "התצוגה נפתחה מתוך מאנדיי"],
    ["identity", "השרת אימת את הזהות"],
    ["boards", "חברות בלוחות החיובים"],
    ["scopes", "הרשאות האפליקציה אצלך"],
    ["api", "קריאה דרך monday.api בשמך"],
    ["reading", "הקריאה של השולחן"]
  ];
  var MARK = { wait: "…", ok: "✓", no: "✗", info: "•" };
  var BOARD_ORDER = ["student_charge", "enrollments", "leads", "cohorts", "b2b_charge", "deals", "accounts"];
  var BOARD_NAMES = {
    student_charge: "Student Charge", enrollments: "Enrollments", leads: "Leads", cohorts: "Cohorts",
    b2b_charge: "B2B Charge", deals: "Deals", accounts: "Accounts"
  };
  var READ_ERRORS = {
    auth: "מאנדיי לא קיבלה את הטוקן של השירות",
    rate_limited: "מאנדיי ביקשה להאט",
    http: "מאנדיי לא ענתה כמצופה",
    graphql: "מאנדיי סירבה לשאילתה",
    not_visible: "לוח שהשולחן קורא לא נראה לטוקן של השירות",
    unknown_column: "עמודה שהשולחן קורא חסרה בלוח",
    deadline: "מאנדיי לא ענתה בזמן",
    too_many_pages: "לוח ארוך מדי לקריאה",
    never_read: "התבקשה עמודה שהשירות אף פעם לא קורא",
    crashed: "תקלה בשרת"
  };

  function byId(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null && text !== "") { n.textContent = String(text); }
    return n;
  }
  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var t = setTimeout(function () { reject(new Error("timeout")); }, ms);
      Promise.resolve(promise).then(function (v) { clearTimeout(t); resolve(v); },
        function (e) { clearTimeout(t); reject(e); });
    });
  }
  function yesNo(v) { return v === true ? "כן" : (v === false ? "לא" : "לא נאמר"); }
  function ago(seconds) {
    var s = Number(seconds);
    if (!isFinite(s) || s < 0) { return "זמן לא ידוע"; }
    if (s < 60) { return "לפני " + Math.round(s) + " שניות"; }
    if (s < 3600) { return "לפני " + Math.round(s / 60) + " דקות"; }
    return "לפני " + Math.round(s / 3600) + " שעות";
  }

  /* ---- the checklist ---- */
  function drawLines() {
    var list = byId("checks");
    LINES.forEach(function (line) {
      var li = el("li", "check is-wait");
      li.id = "check-" + line[0];
      li.appendChild(el("span", "mark", MARK.wait));
      var body = el("div", "body");
      body.appendChild(el("span", "label", line[1]));
      body.appendChild(el("span", "detail", "בודק"));
      li.appendChild(body);
      list.appendChild(li);
    });
  }
  /* a detail is a sentence, or a list of parts shown with " · " between them: each part is its own bidi island, so an
     English word inside one (a board's name, a scope) never pulls the next part's words across the separator */
  function setLine(key, state, detail) {
    var li = byId("check-" + key);
    if (!li) { return; }
    li.className = "check is-" + state;
    li.querySelector(".mark").textContent = MARK[state] || MARK.info;
    var box = li.querySelector(".detail");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    (Array.isArray(detail) ? detail : [detail || ""]).forEach(function (part, i) {
      if (i) { box.appendChild(document.createTextNode(" · ")); }
      box.appendChild(el("span", "part", part));
    });
  }

  function applyTheme(theme) {
    document.body.setAttribute("data-theme", theme === "dark" || theme === "black" ? "dark" : "light");
  }

  /* ---- monday ---- */
  function contextOf() {
    if (!monday) { return Promise.resolve(null); }
    return withTimeout(monday.get("context"), WAIT_MS).then(function (res) {
      var d = res && res.data;
      return d && typeof d === "object" && Object.keys(d).length ? d : null;
    }).catch(function () { return null; });
  }
  function boardOf(c) { return String((c && (c.boardId || (c.boardIds || [])[0])) || ""); }
  function describeContext(c) {
    var u = c.user || {}, v = c.appVersion || {}, parts = [];
    parts.push("לוח " + (boardOf(c) || "לא ידוע"));
    if (c.instanceType) { parts.push("סוג התצוגה " + c.instanceType); }
    parts.push("משתמש " + (u.id || "לא ידוע"));
    parts.push("צפייה בלבד: " + yesNo(u.isViewOnly));
    parts.push("אורח: " + yesNo(u.isGuest));
    if (v.name || v.status) { parts.push("גרסת האפליקציה " + (v.name || "") + (v.status ? " (" + v.status + ")" : "")); }
    return parts;
  }
  function scopesLine(c) {
    if (!c) { setLine("scopes", "no", "אין הקשר ממאנדיי, ולכן אין מה לבדוק"); return; }
    var p = c.permissions || null;
    if (!p || !Array.isArray(p.approvedScopes)) { setLine("scopes", "info", "מאנדיי לא מסר הרשאות בהקשר"); return; }
    var approved = p.approvedScopes, required = Array.isArray(p.requiredScopes) ? p.requiredScopes : [];
    var missing = required.filter(function (s) { return approved.indexOf(s) === -1; });
    var parts = ["מאושרות: " + (approved.join(", ") || "אף אחת")];
    if (required.length) { parts.push("נדרשות: " + required.join(", ")); }
    if (missing.length) { setLine("scopes", "no", ["חסר אישור של " + missing.join(", ")].concat(parts)); return; }
    setLine("scopes", "ok", parts);
  }
  function mondayText(e) {
    e = e || {};
    var ext = e.extensions || {}, data = e.data || {};
    var text = [e.message, e.error_message, ext.code, e.error_code, data.error_message, data.error_code].filter(Boolean).join(" ");
    return String(text || "בלי פירוט").slice(0, 200);
  }
  function apiLine(c) {
    if (!c || !monday || typeof monday.api !== "function") { setLine("api", "no", "אין הקשר ממאנדיי, ולכן אין קריאה בשמך"); return Promise.resolve(); }
    var board = boardOf(c);
    if (!board) { setLine("api", "no", "בהקשר אין לוח לקרוא"); return Promise.resolve(); }
    var query = "query ($b: [ID!]) { boards(ids: $b) { id name } }";
    return withTimeout(monday.api(query, { variables: { b: [board] }, apiVersion: API_VERSION }), MONDAY_WAIT_MS).then(function (res) {
      var errs = res && (res.errors || ((res.error_message || res.error_code) ? [res] : null));
      if (errs && errs.length) { setLine("api", "no", "מאנדיי סירבה: " + mondayText(errs[0])); return; }
      var boards = res && res.data && res.data.boards;
      if (!Array.isArray(boards) || !boards.length) { setLine("api", "no", "מאנדיי ענתה בלי הלוח"); return; }
      setLine("api", "ok", "קראה את שם הלוח הזה: " + (boards[0].name || boards[0].id));
    }, function (e) {
      setLine("api", "no", e && e.message === "timeout" ? "מאנדיי לא ענתה תוך 15 שניות" : "מאנדיי סירבה: " + mondayText(e));
    });
  }

  /* ---- the server ---- */
  function token(force) {
    var fromUrl = params.get("sessionToken") || "";
    if (!monday) { return Promise.resolve(fromUrl); }
    if (sdkToken && !force) { return Promise.resolve(sdkToken); }
    return withTimeout(monday.get("sessionToken"), WAIT_MS).then(function (res) {
      sdkToken = (res && typeof res.data === "string") ? res.data : "";
      return sdkToken || fromUrl;
    }).catch(function () { return sdkToken || fromUrl; });
  }
  /* resolves with the answer, or rejects with {status, error, body}. token_expired is asked again once with a fresh
     token: monday renews the sessionToken while the iframe stays open. */
  function desk(retried) {
    return token(!!retried).then(function (t) {
      if (!t) { throw { status: 0, error: "no_token", body: {} }; }
      return fetch("/api/desk", { headers: { "Authorization": "Bearer " + t }, cache: "no-store" });
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (b) {
        if (resp.status === 401 && b.error === "token_expired" && !retried) { return desk(true); }
        if (!resp.ok) {
          var wait = parseInt(resp.headers.get("Retry-After") || "", 10);
          throw { status: resp.status, error: b.error || ("http_" + resp.status), body: b, retryAfter: isNaN(wait) ? 0 : wait };
        }
        return b;
      });
    });
  }
  function serverRefused(e) {
    var key = (e && e.error) || "";
    var later = "לא נבדק: השרת לא הגיע לשלב הזה";
    if (key === "no_token") {
      setLine("identity", "no", "אין sessionToken: התצוגה עובדת רק מתוך מאנדיי");
    } else if (key === "app_not_configured") {
      var missing = (e.body && e.body.missing) || [];
      setLine("identity", "no", "השרת עוד לא הוגדר" + (missing.length ? ": חסר " + missing.join(", ") : ""));
    } else if (key === "unauthorized" || key === "token_expired") {
      setLine("identity", "no", "השרת לא אימת את ה-sessionToken (" + key + ")");
    } else if (key === "rate_limited") {
      setLine("identity", "no", "יותר מדי בקשות. אפשר לנסות שוב בעוד " + (e.retryAfter || 60) + " שניות");
    } else if (key === "not_a_board_member" || key === "guest" || key === "members_unavailable") {
      setLine("identity", "ok", "אומת");
      setLine("boards", "no", key === "guest" ? "אורחים לא רואים את השולחן"
        : key === "members_unavailable" ? "לא הצלחנו לקרוא עכשיו את חברי הלוחות. אפשר לנסות שוב בעוד דקה"
          : "אינך חבר באף אחד משני הלוחות: לא בלוח Student Charge ולא בלוח B2B Charge");
      setLine("reading", "no", later);
      return;
    } else if (e && e.status === undefined) {
      setLine("identity", "no", "השרת לא ענה");
    } else {
      setLine("identity", "no", "השרת ענה " + ((e && e.status) || "") + " (" + key + ")");
    }
    setLine("boards", "no", later);
    setLine("reading", "no", later);
  }
  function readingWords(code) { return READ_ERRORS[code] || (code ? "שגיאה " + code : "לא נקראה"); }
  function drawBoards(boards) {
    var body = byId("board-rows");
    while (body.firstChild) { body.removeChild(body.firstChild); }
    BOARD_ORDER.forEach(function (key) {
      var c = boards[key];
      if (!c) { return; }
      var whole = c.items_count !== undefined;
      var tr = el("tr", c.ok ? "" : "bad");
      tr.appendChild(el("th", "", BOARD_NAMES[key] || key)).setAttribute("scope", "row");
      tr.appendChild(el("td", "num", whole ? (c.items_count === null ? "לא נמסר" : c.items_count) : c.asked));
      tr.appendChild(el("td", "num", c.rows));
      tr.appendChild(el("td", "num", whole ? Math.max(0, (c.items_count || 0) - (c.rows || 0)) : c.missing));
      tr.appendChild(el("td", "num", c.strangers));
      tr.appendChild(el("td", "num", whole ? "" : c.archived));
      tr.appendChild(el("td", "mark-cell", c.ok ? MARK.ok : MARK.no));
      body.appendChild(tr);
    });
    byId("boards").hidden = !body.firstChild;
  }
  function serverAnswered(b) {
    var u = b.user || {}, tabs = b.tabs || {};
    setLine("identity", "ok", ["משתמש " + u.id, "צפייה בלבד: " + yesNo(u.view_only), "אורח: " + yesNo(u.guest),
      "שדות הזהות: " + ((u.claims || []).join(", ") || "אין")]);
    function member(v) { return v === true ? "חבר" : (v === false ? "לא חבר" : "לא ידוע: חברי הלוח לא נקראו עכשיו"); }
    var unknown = (tabs.b2c !== true && tabs.b2c !== false) || (tabs.b2b !== true && tabs.b2b !== false);
    setLine("boards", unknown ? "info" : "ok", ["בלוח Student Charge: " + member(tabs.b2c), "בלוח B2B Charge: " + member(tabs.b2b)]);
    var r = b.reading, last = b.last_attempt;
    if (!r) {
      setLine("reading", "no", "אין קריאה: " + readingWords(last && last.error) + (last && last.detail ? " (" + last.detail + ")" : ""));
      return;
    }
    var parts = [r.complete ? "שלמה" : "לא שלמה", ago(r.age_seconds), "ארכה " + r.seconds + " שניות", r.calls + " בקשות למאנדיי"];
    if (last && last.error) { parts.push("הניסיון האחרון נכשל: " + readingWords(last.error)); }
    setLine("reading", r.complete ? "ok" : "no", parts);
    drawBoards(r.boards || {});
  }

  /* ---- boot ---- */
  function boot() {
    drawLines();
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) { applyTheme("dark"); }
    contextOf().then(function (c) {
      if (c) {
        if (c.theme) { applyTheme(c.theme); }
        setLine("monday", "ok", describeContext(c));
      } else {
        setLine("monday", "no", monday ? "ה-SDK נטען, אבל מאנדיי לא מסרה הקשר תוך 3 שניות: כנראה מחוץ למאנדיי"
          : "ה-SDK לא נטען");
      }
      scopesLine(c);
      return Promise.all([
        desk(false).then(serverAnswered, serverRefused),
        apiLine(c)
      ]);
    }).then(function () {
      byId("foot").textContent = "נבדק ב-" + new Date().toLocaleString("he-IL");
    });
  }

  /* desk.js starts the checklist on /desk/?check=1, with the SDK object it made (null when the SDK did not load) */
  function run(sdk) {
    monday = sdk || null;
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); } else { boot(); }
  }
  window.FinanceDeskCheck = { run: run, _readingWords: readingWords, _apiVersion: API_VERSION };
})();

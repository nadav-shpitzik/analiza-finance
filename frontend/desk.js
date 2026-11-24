/* The finance desk's page (Analiza-Monday backlog 0301). D2: the glue that mounts the desk (finance.js) on the Board View
   "כספים" - analiza-metrics' app.js did the same for it there (its token, `call`, `api` and `enterFinance`).

   1. The SDK, made once here: the desk and the self-check use this one object.
   2. The token: monday's sessionToken (the URL's `sessionToken` only outside monday, for a local look), asked again once
      when the server says token_expired - monday renews it while the iframe stays open.
   3. The desk is mounted with `api(path)` and `post(path, payload, keep)` on this service's own routes, `monday` whenever
      monday supplied a context - whatever the board the view sits on, never chosen by its id (the desk move plan §3.1:
      analiza-metrics passed it on the two charge boards only) - and `board`, the view's board when it is one of the two
      charge boards: the desk opens on that board's tab.
   4. /desk/?check=1 draws D1's self-check (check.js) instead of the desk.

   Nothing here is drawn from an answer: the screens' words are this file's own, set with textContent. */
(function () {
  "use strict";

  var WAIT_MS = 3000;
  /* the two charge boards: Student Charge and B2B Charge */
  var FINANCE_BOARDS = { "5093448677": true, "5097721228": true };
  var params = new URLSearchParams(window.location.search);
  var monday = null;
  try { if (window.mondaySdk) { monday = window.mondaySdk(); } } catch (e) { monday = null; }
  var sdkToken = "";

  function byId(id) { return document.getElementById(id); }
  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var t = setTimeout(function () { reject(new Error("timeout")); }, ms);
      Promise.resolve(promise).then(function (v) { clearTimeout(t); resolve(v); },
        function (e) { clearTimeout(t); reject(e); });
    });
  }
  function applyTheme(theme) {
    document.body.setAttribute("data-theme", theme === "dark" || theme === "black" ? "dark" : "light");
  }

  /* ---- monday ---- */
  /* the view's context, or null when monday supplied none (outside monday, or no answer in time) */
  function contextOf() {
    if (!monday) { return Promise.resolve(null); }
    return withTimeout(monday.get("context"), WAIT_MS).then(function (res) {
      var d = res && res.data;
      return d && typeof d === "object" && Object.keys(d).length ? d : null;
    }).catch(function () { return null; });
  }
  function boardOf(c) { return String((c && (c.boardId || (c.boardIds || [])[0])) || ""); }

  /* ---- the server ---- */
  function getToken(force) {
    /* the URL token is a local-dev convenience; inside monday the SDK answers */
    var fromUrl = params.get("sessionToken") || "";
    if (!monday) { return Promise.resolve(fromUrl); }
    if (sdkToken && !force) { return Promise.resolve(sdkToken); }
    return withTimeout(monday.get("sessionToken"), WAIT_MS).then(function (res) {
      sdkToken = (res && typeof res.data === "string") ? res.data : "";
      return sdkToken || fromUrl;
    }).catch(function () { return sdkToken || fromUrl; });
  }
  /* resolves with the JSON body, or rejects with {error, blocked?, status?, body?, retryAfter?} - the shapes finance.js
     reads. token_expired is asked again once with a fresh token: monday rotates the sessionToken while the iframe stays
     open, and one refresh is the whole fix. */
  function call(method, path, payload, isRetry, keep) {
    return getToken(!!isRetry).then(function (token) {
      var init = { method: method, headers: { "Authorization": "Bearer " + token }, cache: "no-store" };
      if (payload !== undefined && payload !== null) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(payload);
      }
      /* a request sent as the view goes out of sight (the desk's last usage batch) may outlive the page */
      if (keep) { init.keepalive = true; }
      return fetch(path, init);
    }).then(function (resp) {
      if (resp.status === 401) {
        return resp.json().catch(function () { return {}; }).then(function (b) {
          if (b.error === "token_expired" && !isRetry) {
            return call(method, path, payload, true, keep);
          }
          throw { blocked: true, error: b.error || "unauthorized", status: 401, body: b };
        });
      }
      if (resp.status === 429) {
        return resp.json().catch(function () { return {}; }).then(function (b) {
          var hdr = parseInt(resp.headers.get("Retry-After") || "", 10);
          var wait = b.retry_after_seconds || (isNaN(hdr) ? 0 : hdr);
          throw { error: "rate_limited", retryAfter: wait, status: 429, body: b };
        });
      }
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; }).then(function (b) {
          /* the body is carried through, not flattened to a code: a caller may need more than `error` */
          throw { blocked: b.error === "app_not_configured", error: b.error || ("http_" + resp.status),
                  status: resp.status, body: b };
        });
      }
      return resp.json();
    });
  }
  function api(path) { return call("GET", path); }
  function post(path, payload, keep) { return call("POST", path, payload, false, !!keep); }

  /* ---- the screens ---- */
  var SCREENS = ["loading", "blocked", "finance"];
  function show(screen) {
    SCREENS.forEach(function (s) {
      var node = byId("screen-" + s);
      if (node) { node.hidden = s !== screen; }
    });
  }
  function blocked(reason) {
    byId("blocked-msg").textContent = reason;
    show("blocked");
  }

  /* ---- the desk ---- */
  function enterDesk() {
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) { applyTheme("dark"); }
    if (monday) {
      try {
        monday.listen("context", function (res) {
          var d = (res && res.data) || {};
          if (d.theme) { applyTheme(d.theme); }
        });
      } catch (e) { /* the detected theme stands */ }
    }
    show("loading");
    return Promise.all([getToken(false), contextOf()]).then(function (got) {
      var token = got[0], c = got[1];
      if (c && c.theme) { applyTheme(c.theme); }
      if (!token) {
        blocked("פתחו את התצוגה מתוך monday - שם היא מקבלת את הזהות שלכם.");
        return null;
      }
      if (!window.FinanceDesk) { blocked("קובץ התצוגה של הכספים לא נטען. רעננו את הדף."); return null; }
      var board = boardOf(c);
      return window.FinanceDesk.mount(byId("fin-root"), {
        api: api,
        /* `keep`: the desk's usage batch sent as the view goes out of sight */
        post: post,
        /* whenever monday supplied a context: the desk reads and writes through it, as the person */
        monday: c ? monday : null,
        /* the charge board the view sits on, if it is one: the desk opens on that board's tab */
        board: FINANCE_BOARDS[board] ? board : ""
      }).then(function () { show("finance"); });
    }).catch(function () {
      blocked("משהו השתבש. נסו שוב");
    });
  }

  /* ---- the self-check (D1), on /desk/?check=1 ---- */
  function enterCheck() {
    show(null);
    document.body.classList.add("is-check");
    byId("check").hidden = false;
    if (window.FinanceDeskCheck) { window.FinanceDeskCheck.run(monday); return; }
    byId("check").hidden = true;
    blocked("קובץ הבדיקה לא נטען. רעננו את הדף.");
  }

  /* the rest is for tests/test_desk_page.py only */
  window.FinanceDeskGlue = { _call: call, _token: getToken, _boards: FINANCE_BOARDS };
  if (params.get("check") === "1") { enterCheck(); } else { enterDesk(); }
})();

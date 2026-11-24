/* The finance desk ("כספים", knowledge backlog 0280 and docs/design/finance-desk-spec.md there).
 *
 * Ported from analiza-metrics frontend/finance.js at da671b2 (knowledge backlog 0301, D2), faithfully: the same
 * behaviour and the same words. What changed with the move, and only that:
 * 1. the paths: GET /api/desk and POST /api/desk/refresh, /api/desk/totals, /api/desk/export-link;
 * 2. an answer may carry no view yet (`view: null`, nothing read on the server): said, with the refresh, and never
 *    put over a view already on the screen (takeNewer);
 * 3. what the server no longer sends is read in the browser, as the person, through mondayCall when its detail is
 *    shown (the ID number already was): a refund's bank account in its details, a company's ח.פ in its billing details
 *    (liveEntry). The list "החזר לביצוע" lost its column "פרטי חשבון": filling it would read every refund's account;
 * 4. a company's billing details are shown read only: their edit went through analiza-metrics' server (its account
 *    route, which this service does not have), and comes back from the browser;
 * 5. the usage batches (POST /api/usage, not in this service yet) stop quietly at the first failure;
 * 6. three sentences that would no longer be true: a guest is told so (this gate refuses one by name), the ID number's
 *    "no access" no longer sends her to the Excel file (whose server reads no ID number), and the chip "אין תנאי תשלום"
 *    opens the company's ledger without promising an edit there.
 * In the comments below, app/finance.py and app/finance_api.py are analiza-metrics' modules: their port to this
 * service is under way, and each rule named there is the one the port keeps.
 *
 * Drawn by desk.js on the board view, into #fin-root, from /api/desk - which answers a tab only to
 * the members of the board it reads. This file adds no money up: every amount comes from the server's one
 * computation (app/finance.py) as a Decimal string, and is only formatted here. The students tab is one list
 * by the bookkeeper's state of each payment, a period and the search box (Nadav 10.11: "לפי סטטוסים אצלה"),
 * with an Excel file of exactly that list; the companies tab is its cards and the customers > deals picture.
 * One name for one thing (round 3, Nadav 11.11): a monday column or label is called here exactly as monday calls it;
 * only what the desk computes has a word of its own, and each such word is in the tab's legend ("מקרא"), sent by
 * the server with every state's sentence (`about`). */
(function () {
  "use strict";

  var NF = new Intl.NumberFormat("he-IL", { maximumFractionDigits: 0 });
  /* rows and details carry agorot, like Excel and Hashavshevet; tiles and figures stay in whole shekels (Nadav 10.11) */
  var NF2 = new Intl.NumberFormat("he-IL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var ACCOUNT_URL = "https://analiza-college-group.monday.com";
  var BOARD = { sc: "5093448677", b2: "5097721228" };
  /* the request form on the technical board (knowledge backlog 0273) - the desk's way to ask for more */
  var ASK_URL = ACCOUNT_URL + "/boards/5098225215/views/51829235";

  /* a refund's two dates, named as app/finance.py names them (tests/test_finance_js.py holds the two together):
     the transfer round set at approval, monday's own title since 11.11, and the legal day, request + 14 days */
  var PLANNED_LABEL = "מועד העברה מתוכנן";
  var LAST_DAY_LABEL = "מועד אחרון להחזר";
  /* a row's flags as chips, in monday's words: "חויב", "מועד דרישת תשלום", "אסמכתת זיכוי" - never a second name. "{status}"
     is the row's own "סטטוס תשלום", in quotes ("ריק" when it has none): the chip says which status the refund sits on.
     A state's own name is no chip (the row's state says it), so "החזר לביצוע" and "ללא תקבול" are none (QA 11.11). */
  var FLAG = {
    refund_overdue: PLANNED_LABEL + " עבר", refund_last_day_passed: LAST_DAY_LABEL + " עבר",
    refund_last_day_unknown: LAST_DAY_LABEL + " לא ידוע", invoice_missing: "חסרה אסמכתת חשבונית",
    receipt_pending: "שליחת קבלה: טרם", credit_ref_missing: "חסרה אסמכתת זיכוי", refund_amount_missing: "סכום החזר לא הוקלד",
    refund_amount_zero: "סכום החזר 0",
    refund_exceeds_paid: "סכום החזר גדול מסכום ששולם", amount_missing: "סכום ששולם לא הוקלד",
    credit_on_status: "סכום לזיכוי בסטטוס {status}", refund_amount_on_status: "סכום החזר בסטטוס {status}",
    refund_date_on_status: "תאריך החזר בסטטוס {status}", unlinked: "בלי הרשמה ובלי ליד מקור",
    invoice_late: "מועד דרישת התשלום עבר", no_contract: "בהרצה ללא חוזה", over_deal_value: "מעל שווי עסקה",
    billed_no_due: "חויב בלי מועד דרישת תשלום", billed_before_demand: "חויב, ומועד דרישת התשלום עוד לא הגיע",
    partial_amount_unknown: "סכום ששולם (כולל מע\"מ) לא הוקלד", billed_no_invoice: "חסרה אסמכתת חשבונית",
    paid_not_billed: "\"שולם\" לפני \"חויב\"", payer_mismatch: "החברה אינה Account משלם בעסקה",
    terms_missing: "אין תנאי תשלום", status_unknown: "תווית שהמסך לא מכיר"
  };
  /* the same flag on the other board, where its column has another name */
  var FLAG_ON = { b2: { amount_missing: "סכום לחיוב (ללא מע\"מ) לא הוקלד" } };
  var BAD = { refund_overdue: 1, refund_last_day_passed: 1, refund_exceeds_paid: 1, invoice_late: 1, no_contract: 1,
    over_deal_value: 1, status_unknown: 1, credit_on_status: 1, refund_amount_on_status: 1, refund_date_on_status: 1 };
  var TASK = { refund_last_day_unknown: 1, invoice_missing: 1, receipt_pending: 1, credit_ref_missing: 1,
    refund_amount_missing: 1, refund_amount_zero: 1, billed_no_due: 1, billed_before_demand: 1, partial_amount_unknown: 1,
    billed_no_invoice: 1, terms_missing: 1 };
  var FLAG_ORDER = ["status_unknown", "credit_on_status", "refund_amount_on_status", "refund_date_on_status",
    "refund_last_day_passed", "refund_overdue", "refund_exceeds_paid", "invoice_late", "no_contract", "over_deal_value",
    "refund_last_day_unknown", "invoice_missing", "billed_no_invoice", "receipt_pending", "credit_ref_missing",
    "refund_amount_missing", "refund_amount_zero", "billed_no_due", "billed_before_demand", "partial_amount_unknown",
    "amount_missing", "terms_missing", "unlinked", "paid_not_billed", "payer_mismatch"];
  var GROUP_NAME = { b2c: "סטודנטים", b2b: "לקוחות עסקיים" };
  /* the students' states (app/finance.py B2C_STATES): a colour each, by what she does with it */
  /* the companies' states (app/finance.py B2B_STATES) */
  var B2_COLOR = { exception: "var(--fin-unpaid)", to_bill: "var(--fin-approval)",
    overdue: "var(--fin-unpaid)", partial: "var(--fin-partial)", open: "var(--fin-pending)", planned: "var(--fin-pending)",
    pending: "var(--fin-pending)", paid: "var(--fin-paid)", cancelled: "var(--fin-refund)" };
  var STATE_COLOR = { exception: "var(--fin-unpaid)", refund_to_execute: "var(--fin-wait)",
    receipt_pending: "var(--fin-approval)",
    refund_asked: "var(--fin-pending)", done: "var(--fin-paid)", refunded: "var(--fin-refund)", no_money: "var(--fin-pending)" };
  var COHORT_FROM = { enrollment: "לפי ההרשמה", lead: "לפי הליד מקור (אין מחזור בהרשמה)", ambiguous: "לא שויך: יותר ממחזור אחד",
    no_cohort: "לא שויך: בהרשמה ובליד מקור אין מחזור", no_link: "לא שויך: התקבול לא מקושר להרשמה או לליד מקור",
    missing: "לא שויך: המחזור לא נמצא במאנדיי" };
  var NOTE = {
    cohort_no_link: ["תקבול אחד בלי הרשמה ובלי ליד מקור", "תקבולים בלי הרשמה ובלי ליד מקור"],
    cohort_no_cohort: ["תקבול מקושר אחד בלי מחזור", "תקבולים מקושרים בלי מחזור"],
    cohort_missing: ["תקבול אחד שהמחזור שלו לא נמצא במאנדיי", "תקבולים שהמחזור שלהם לא נמצא במאנדיי"],
    enrollments_missing: ["הרשמה אחת שתקבולים מקושרים אליה ומאנדיי לא מחזיר אותה", "הרשמות שתקבולים מקושרים אליהן ומאנדיי לא מחזיר אותן"],
    leads_missing: ["ליד מקור אחד שתקבולים מקושרים אליו ומאנדיי לא מחזיר אותו", "לידים שתקבולים מקושרים אליהם ומאנדיי לא מחזיר אותם"],
    cohort_ambiguous: ["תקבול אחד עם יותר ממחזור אחד (בהרשמה או בליד מקור)", "תקבולים עם יותר ממחזור אחד (בהרשמה או בליד מקור)"],
    refund_exceeds_paid: ["סכום החזר אחד גדול מסכום ששולם (נספר עד סכום ששולם)", "סכומי החזר גדולים מסכום ששולם (נספרו עד סכום ששולם)"],
    amount_missing: ["תקבול אחד שסכום ששולם בו לא הוקלד", "תקבולים שסכום ששולם בהם לא הוקלד"],
    payer_mismatch: ["חיוב עסקי אחד שהחברה בו אינה Account משלם בעסקה", "חיובים עסקיים שהחברה בהם אינה Account משלם בעסקה"],
    deals_split: ["עסקה אחת שהחיובים שלה רשומים על שתי חברות", "עסקאות שהחיובים שלהן רשומים על שתי חברות"],
    deals_missing: ["עסקה אחת שחיובים מקושרים אליה ומאנדיי לא מחזיר אותה", "עסקאות שחיובים מקושרים אליהן ומאנדיי לא מחזיר אותן"],
    accounts_missing: ["חברה אחת שחיובים מקושרים אליה ומאנדיי לא מחזיר אותה", "חברות שחיובים מקושרים אליהן ומאנדיי לא מחזיר אותן"],
    price_unclear: ["מחזור אחד שהמחיר בו אינו מספר אחד", "מחזורים שהמחיר בהם אינו מספר אחד"],
    expected_missing: ["יתרה פתוחה אחת בלי מחיר מחזור, ולכן בלי סכום", "יתרות פתוחות בלי מחיר מחזור, ולכן בלי סכום"],
    terms_unknown: ["חברה אחת שתנאי התשלום שלה לא מוכרים למסך (נספרה לפי מועד דרישת התשלום)", "חברות שתנאי התשלום שלהן לא מוכרים למסך (נספרו לפי מועד דרישת התשלום)"],
    amount_missing_b2b: ["חיוב עסקי אחד שסכום לחיוב (ללא מע\"מ) בו לא הוקלד", "חיובים עסקיים שסכום לחיוב (ללא מע\"מ) בהם לא הוקלד"],
    label_drift_b2c: ["תווית סטטוס אחת בתקבולי הסטודנטים ששמה השתנה במאנדיי", "תוויות סטטוס בתקבולי הסטודנטים ששמן השתנה במאנדיי"],
    voucher_label_unknown: ["הרשמה אחת עם תווית בסטטוס שובר משה\"ע שהמסך לא מכיר (הכסף נספר; רק התווית לא מוצגת)", "הרשמות עם תווית בסטטוס שובר משה\"ע שהמסך לא מכיר (הכסף נספר; רק התווית לא מוצגת)"],
    voucher_label_drift: ["תווית אחת בסטטוס שובר משה\"ע ששמה השתנה במאנדיי", "תוויות בסטטוס שובר משה\"ע ששמן השתנה במאנדיי"],
    label_drift_b2b: ["תווית סטטוס אחת בחיובים העסקיים ששמה השתנה במאנדיי", "תוויות סטטוס בחיובים העסקיים ששמן השתנה במאנדיי"],
    label_unknown_b2c: ["תקבול אחד עם סטטוס שהמסך לא מכיר: הוא ב\"חריג, לבדיקה\" ולא נספר בכסף", "תקבולים עם סטטוס שהמסך לא מכיר: הם ב\"חריג, לבדיקה\" ולא נספרו בכסף"],
    label_unknown_b2b: ["חיוב עסקי אחד עם סטטוס שהמסך לא מכיר: הוא ב\"חריג, לבדיקה\" ולא נספר ביתרה הפתוחה", "חיובים עסקיים עם סטטוס שהמסך לא מכיר: הם ב\"חריג, לבדיקה\" ולא נספרו ביתרה הפתוחה"]
    /* "bank_unread" left with the move (D2): the server no longer reads a bank account, so its read cannot fail; the
       page's own read says a failure in the refund's details (bankCell) */
  };
  /* a note that changes what the numbers mean, or what she can act on, is said on the page, not behind "פרטים" */
  var LOUD = { label_unknown_b2c: 1, label_unknown_b2b: 1 };
  var ERRORS = {
    /* D2: this service's gate refuses a guest by name (app/desk/gate.py); the words are D1's */
    guest: "אורחים לא רואים את השולחן.",
    not_a_board_member: "התצוגה הזאת מראה את לוחות החיובים, ואין לך גישה לאף אחד מהם.",
    members_unavailable: "לא הצלחנו לבדוק עכשיו מי רשאי לראות את לוחות החיובים. נסו שוב בעוד דקה.",
    finance_unavailable: "לא הצלחנו לקרוא עכשיו את לוחות החיובים. נסו שוב בעוד כמה דקות."
  };

  var deps = null, root = null, VIEW = null, BODY = null;
  var ROWS = { sc: [], b2: [] };
  var state = { tab: "b2c", query: "", notes: false, legend: false,
    /* the students list: a state ("tasks", "" for all, or a state key), a period key, the payments opened */
    sc: { state: "tasks", period: "", open: {} },
    /* the companies list: a state, a customer (account id), the list or the customers, the charges opened */
    b2: { state: "tasks", customer: "", view: "list", open: {} } };

  /* ---------- helpers ---------- */
  function num(x) { var n = Number(x); return isNaN(n) ? 0 : n; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
    });
  }
  function money(n) { return "<bdi>" + NF.format(Math.round(num(n))) + " ₪</bdi>"; }
  function money2(n) { return "<bdi>" + NF2.format(num(n)) + " ₪</bdi>"; }
  function ltr(s) { return s ? "<bdi dir=\"ltr\">" + esc(s) + "</bdi>" : ""; }
  function d(iso) { if (!iso) { return ""; } var p = iso.slice(0, 10).split("-"); return p[2] + "." + p[1] + "." + p[0].slice(2); }
  function plural(n, one, many) { return n === 1 ? one : NF.format(n) + " " + many; }
  function itemUrl(board, id) { return ACCOUNT_URL + "/boards/" + BOARD[board] + "/pulses/" + id; }
  /* a row's way to monday: a quiet icon under a header of its own, and the row's name (nameLink); the details carry the
     button too */
  var OPEN_ICON = "<svg viewBox=\"0 0 16 16\" width=\"16\" height=\"16\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M9.5 2.5h4v4M13.5 2.5 8 8M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg>";
  function openLink(r) {
    return "<a class=\"fin-open\" data-open=\"" + esc(r.id) + "\" href=\"" + itemUrl(r.board, r.id) + "\" target=\"_blank\" rel=\"noopener\"" +
      " aria-label=\"פתיחה במאנדיי\" title=\"פתיחה במאנדיי\">" + OPEN_ICON + "</a>";
  }
  /* the row's name opens its monday card over the view (openItemCard, in onClick), and outside monday its page in a new
     tab; a click anywhere else on the row still opens or closes its details */
  function nameLink(r, inner) {
    return "<a class=\"fin-name\" data-open=\"" + esc(r.id) + "\" href=\"" + itemUrl(r.board, r.id) + "\" target=\"_blank\" rel=\"noopener\">" +
      inner + "</a>";
  }
  function label(s) {
    /* a charge's name: a "2026-09" inside it stays in one piece in right-to-left text */
    return String(s || "").split(" · ").map(function (p) {
      return /^[\d./-]+$/.test(p) ? "<bdi class=\"fin-nw\">" + esc(p) + "</bdi>" : esc(p);
    }).join(" · ");
  }
  function chipClass(k) { return BAD[k] ? "fin-chip bad" : TASK[k] ? "fin-chip" : "fin-chip q"; }
  /* a row's "סטטוס תשלום" inside a sentence: quoted, as monday spells it, or "ריק" */
  function statusWord(r) { return r.status ? "\"" + r.status + "\"" : "ריק"; }
  function flagText(k, r) { return (((FLAG_ON[r.board] || {})[k]) || FLAG[k]).replace("{status}", statusWord(r)); }
  /* what to look at in a row, as chips: its flags in FLAG_ORDER, at most five and "ועוד N" */
  function rowChips(r) {
    var keys = FLAG_ORDER.filter(function (k) { return r.flags.indexOf(k) !== -1; });
    var out = keys.slice(0, 5).map(function (k) { return "<span class=\"" + chipClass(k) + "\">" + esc(flagText(k, r)) + "</span>"; });
    if (keys.length > 5) { out.push("<span class=\"fin-chip q\">ועוד " + (keys.length - 5) + "</span>"); }
    return out.length ? "<div class=\"fin-chips\">" + out.join("") + "</div>" : "";
  }
  /* the page's parts, the same on both tabs: a tab's money as a few figures (a label over each value, like the details
     panel's facts) with its note and, on the students tab, the period beside them; a panel; a calm empty list */
  function figs(pairs, note, control) {
    return "<div class=\"fin-figs\"><dl>" + pairs.map(function (x) {
      return "<div><dt>" + esc(x[0]) + "</dt><dd>" + x[1] + "</dd></div>";
    }).join("") + "</dl><span class=\"fin-figs-note\">" + esc(note) + "</span>" + (control || "") + "</div>";
  }
  function panel(inner) { return "<div class=\"fin-panel\">" + inner + "</div>"; }
  function empty(text, action) { return "<div class=\"fin-empty\"><p>" + esc(text) + "</p>" + (action || "") + "</div>"; }

  /* ---------- work lists: a card, or a search ---------- */
  var NOT_SET = "<span class=\"fin-subtle\">-</span>";
  /* a status cell as monday shows it: its label, "-" when empty, and when it holds a value monday gives no label to (a label
     with no name, or deleted), said so rather than shown empty (QA 2.10) */
  var UNNAMED = "<span class=\"fin-badtext fin-nw\">תווית בלי שם</span>";
  function lbl(r, field) { return (r.unnamed || []).indexOf(field) !== -1 ? UNNAMED : r[field] ? esc(r[field]) : NOT_SET; }
  /* beside a company or a due date: the company's "תנאי תשלום", or that it has none - its due dates are then the demand days
     themselves, and so is its lateness (QA 1.3) */
  var NO_TERMS = "<span class=\"fin-chip\">" + esc(FLAG.terms_missing) + "</span>";
  function termsMark(terms) { return terms ? " <bdi class=\"fin-subtle\">" + esc(terms) + "</bdi>" : " " + NO_TERMS; }
  var COLS = {
    date: { h: "תאריך תקבול", c: function (r) { return paidDate(r); } },
    student: { h: "סטודנט/ית", c: function (r) { return nameLink(r, esc(r.name)) + writtenMark(r); } },
    /* Student Charge's "נציג מכירות" (17.11): the Owner of the payment's lead, read with the lead (app/finance.py lead_rep) */
    rep: { h: "נציג מכירות", xs: 1, c: function (r) { return r.rep ? esc(r.rep) : NOT_SET; } },
    cohort: { h: "מחזור", xs: 1, c: function (r) { return r.cohort_code ? "<span title=\"" + esc(r.cohort_title) + "\">" + ltr(r.cohort_code) + "</span>" : "<span class=\"fin-subtle\">-</span>"; } },
    state: { h: "שיבוץ", c: function (r) { return stateChip(r.state); } },
    credit_ref: { h: "אסמכתת זיכוי", xs: 1, c: function (r) { return ltr(r.credit_ref) || "<span class=\"fin-subtle\">-</span>"; } },
    /* "סכום ששולם" as monday holds it: counted where the status brought money in, struck through where it did not */
    received: { h: "סכום ששולם", n: 1, t: "money_in", c: function (r) {
      if (r.paid_typed == null) { return NOT_SET; }
      return r.counted ? money2(r.money_in) : "<s class=\"fin-subtle\" title=\"" + esc("סטטוס תשלום \"" + (r.status || "ריק") + "\": לא נספר") + "\">" +
        money2(r.paid_typed) + "</s>";
    } },
    channel: { h: "ערוץ תשלום", xs: 1, c: function (r) { return lbl(r, "channel"); } },
    invoice: { h: "אסמכתת חשבונית", xs: 1, c: function (r) { return ltr(r.invoice) || NOT_SET; } },
    receipt: { h: "שליחת קבלה", xs: 1, c: function (r) { return lbl(r, "receipt"); } },
    /* in "חריג, לבדיקה" only when one of its rows has an approved refund (QA 1.1: the 4,000 to credit showed nowhere) */
    to_refund: { h: "סכום לזיכוי", n: 1, t: "credit_approved", opt: function (r) { return num(r.credit_approved) > 0; },
      c: function (r) { return num(r.credit_approved) ? money2(r.credit_approved) : NOT_SET; } },
    method: { h: "אופן החזר", xs: 1, c: function (r) { return lbl(r, "refund_method"); } },
    /* "פרטי חשבון" left the list "החזר לביצוע" with the move (D2): the server sends no bank account, and the page reads
       one only when its refund's details open (bankCell) - a column would read every refund's account */
    /* a refund's two dates, side by side wherever a refund is listed, with the day it was asked for before them */
    asked: { h: "תאריך בקשת ביטול", c: function (r) { return r.asked_date ? d(r.asked_date) : NOT_SET; } },
    /* always on a refund to execute; in a list of done refunds only when one of them has the date (older ones do not) */
    last_day: { h: LAST_DAY_LABEL, opt: function (r) { return refundOpen(r) || !!r.refund_last_day; },
      c: function (r) { return lastDayCell(r); } },
    planned: { h: PLANNED_LABEL, opt: function (r) { return r.state === "refund_to_execute" || !!r.planned; }, c: function (r) {
      if (!r.planned) { return NOT_SET; }
      return d(r.planned) + (r.flags.indexOf("refund_overdue") !== -1 ? " <span class=\"fin-badtext\">עבר</span>" : "");
    } },
    refund_date: { h: "תאריך החזר", c: function (r) { return d(r.refund_date); } },
    /* "סכום החזר"; a full refund without it counts what was paid, said by the asterisk under the list */
    refunded: { h: "סכום החזר", n: 1, t: "refunded", c: function (r) {
      return refundGuessed(r) ? "<span class=\"fin-subtle\" title=\"סכום החזר ריק או 0: מוצג כל סכום ששולם\">" + money2(r.refunded) + "*</span>"
        : money2(r.refunded);
    } },
    account: { h: "חברה", c: function (r) { return esc(r.ctx.account); } },
    deal: { h: "עסקה", xs: 1, c: function (r) { return esc(r.ctx.deal); } },
    charge: { h: "חיוב", c: function (r) { return nameLink(r, label(r.name)) + writtenMark(r); } },
    course: { h: "קוד קורס", xs: 1, c: function (r) { return ltr(r.course); } },
    amount: { h: "סכום לחיוב (ללא מע\"מ)", n: 1, t: "amount", c: function (r) {
      if (!num(r.amount)) { return "-"; }
      return r.state === "cancelled" ? "<s class=\"fin-subtle\" title=\"בוטל: לא נספר\">" + money2(r.amount) + "</s>" : money2(r.amount);
    } },
    ledger: { h: "יתרה פתוחה מצטברת", n: 1, c: function (r) { return num(r.ledger_open) ? money2(r.ledger_open) : "-"; } },
    status: { h: "מצב חיוב", c: function (r) { return lbl(r, "billing"); } },
    payment: { h: "סטטוס תשלום", c: function (r) { return lbl(r, "payment"); } },
    /* billed without a demand day, whatever was paid: said where the day would be (QA 1.4) */
    demand: { h: "מועד דרישת תשלום", opt: function (r) { return !!r.demand || billedNoDue(r); }, c: function (r) {
      return billedNoDue(r) ? "<span class=\"fin-chip bad\">חסר</span>" : d(r.demand);
    } },
    /* the due date and how it was counted: the company's terms beside it, or that it has none */
    due: { h: "מועד פירעון", c: function (r) {
      if (!r.due) { return ""; }
      return d(r.due) + (r.flags.indexOf("terms_missing") !== -1 ? " " + noTerms(r.account_id) : r.account_terms ? " <bdi class=\"fin-subtle\">" + esc(r.account_terms) + "</bdi>" : "");
    } },
    b2state: { h: "שיבוץ", c: function (r) { return stateChip(r.state, VIEW.b2b.states, B2_COLOR); } },
    paid_so_far: { h: "סכום ששולם (כולל מע\"מ)", n: 1, t: "paid_so_far", c: function (r) { return r.paid_so_far != null ? money2(r.paid_so_far) : "<span class=\"fin-badtext\">לא הוקלד</span>"; } },
    balance: { h: "יתרה לגבייה (כולל מע\"מ)", n: 1, t: "balance", c: function (r) { return r.balance != null ? money2(r.balance) : "-"; } },
    late: { h: "ימים בפיגור", n: 1, opt: function (r) { return r.days_late != null; }, c: function (r) { return r.days_late != null ? NF.format(r.days_late) : ""; } },
    notes: { h: "מה לבדוק", c: function (r) { return rowChips(r); } },
    open: { h: "פתיחה במאנדיי", c: function (r) { return openLink(r); } }
  };
  /* the companies list's columns and order, by the state chosen (app/finance.py B2B_SORT is the same order) */
  var B2_COLS = {
    "": ["demand", "account", "deal", "charge", "amount", "b2state", "due", "invoice", "open"],
    overdue: ["account", "deal", "charge", "amount", "due", "late", "invoice", "open"],
    partial: ["demand", "account", "charge", "amount", "paid_so_far", "balance", "due", "late", "invoice", "open"],
    to_bill: ["demand", "account", "deal", "charge", "course", "amount", "notes", "open"],
    /* billed and not due yet: read by when it falls due, so the due date and its terms come first */
    open: ["due", "account", "deal", "charge", "amount", "demand", "invoice", "open"],
    planned: ["demand", "account", "deal", "charge", "course", "amount", "notes", "open"],
    exception: ["demand", "account", "deal", "charge", "amount", "invoice", "notes", "open"]
  };
  var B2_SORT = { overdue: "-days_late", open: "due", "": "-demand", paid: "-demand", cancelled: "-demand", pending: "-demand" };
  /* the students list's columns, by the state chosen: a refund is read by its refund fields */
  var SC_COLS = {
    "": ["date", "student", "rep", "cohort", "received", "channel", "invoice", "receipt", "state", "open"],
    exception: ["date", "student", "rep", "cohort", "received", "to_refund", "channel", "invoice", "receipt", "notes", "open"],
    refund_to_execute: ["asked", "last_day", "planned", "student", "cohort", "to_refund", "method", "received", "invoice", "open"],
    refunded: ["refund_date", "last_day", "planned", "student", "cohort", "refunded", "method", "invoice", "credit_ref", "open"]
  };
  /* "החזר לביצוע" has its own order (refundKey); every other state is a field */
  var SC_SORT = { refunded: "-refund_date" };

  function sortRows(rows, key) {
    var desc = key.charAt(0) === "-", k = desc ? key.slice(1) : key;
    return rows.slice().sort(function (a, b) {
      var x = a[k] == null ? "" : a[k], y = b[k] == null ? "" : b[k];
      if (x === y) { return 0; }
      if (x === "") { return 1; }
      if (y === "") { return -1; }
      return (x < y ? -1 : 1) * (desc ? -1 : 1);
    });
  }
  /* The refunds to execute, as app/finance.py refund_order: an unknown last day first (no request day), then a last day
     that passed, the oldest first, then by the planned transfer day, an empty one last, and on one planned day by the last
     day. The flags carry the day they were computed on, so the page reads no clock of its own here. */
  function refundKey(r) {
    var g = r.flags.indexOf("refund_last_day_unknown") !== -1 ? 0 : r.flags.indexOf("refund_last_day_passed") !== -1 ? 1 : 2;
    /* on the same planned day, the nearest last day first (QA 2.6: ties came by descending id) */
    return [g, g === 1 ? r.refund_last_day : "", r.planned ? 0 : 1, r.planned || "", r.refund_last_day || ""];
  }
  function byRefundKey(a, b) {
    var x = refundKey(a), y = refundKey(b);
    for (var i = 0; i < x.length; i++) { if (x[i] !== y[i]) { return x[i] < y[i] ? -1 : 1; } }
    return 0;
  }
  /* "תאריך תקבול" as typed; where it was not, the day the row was created, marked so in the cell (QA 2.5) */
  var CREATED_WORD = "יום היצירה";
  function paidDate(r) {
    return r.date_from === "paid" ? d(r.date) : d(r.date) + " <span class=\"fin-subtle\">" + CREATED_WORD + "</span>";
  }
  function billedNoDue(r) { return r.flags.indexOf("billed_no_due") !== -1; }
  function cls(k) { return " class=\"k-" + k + (COLS[k].n ? " n" : "") + (COLS[k].xs ? " hide-xs" : "") + "\""; }
  /* the list's totals come from the server (GET /api/desk/totals, the same filter as the Excel file): asked once
     per filter and reading, and drawn as the table's last row when they land */
  var TOTALS = {};
  function totalsFor(params) {
    var key = JSON.stringify([params, BODY && BODY.taken_at]);
    var got = TOTALS[key];
    if (got) { return got.body || null; }
    TOTALS[key] = { body: null };
    params.taken = (BODY && BODY.taken_at) || "";
    var qs = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k] || ""); }).join("&");
    deps.api("/api/desk/totals?" + qs).then(function (body) { TOTALS[key].body = body; redraw(); })
      .catch(function () { TOTALS[key].body = { failed: true }; });
    return null;
  }
  function totalsRow(cols, t, what) {
    if (!t || t.failed || t.other_reading) { return ""; }   /* a total of another reading is not this list's total */
    var first = true;
    return "<tfoot><tr class=\"fin-total\">" + cols.map(function (k) {
      var f = COLS[k] && COLS[k].t, cell = "";
      if (f && t.sums[f] != null) { cell = money2(t.sums[f]); }
      else if (f && t.sums.partial_unknown) { cell = "<span class=\"fin-subtle\">חסר ב-" + plural(t.sums.partial_unknown, "חיוב אחד", "חיובים") + "</span>"; }
      if (!cell && first) { first = false; cell = "<b>סה\"כ</b> <span class=\"fin-subtle\">" + plural(t.count, what[0], what[1]) + "</span>"; }
      return "<td" + cls(k) + ">" + cell + "</td>";
    }).join("") + "</tr></tfoot>";
  }
  function table(rows, cols, detail, opened, totals) {
    cols = cols.filter(function (k) { return !COLS[k].opt || rows.some(COLS[k].opt); });
    return "<div class=\"fin-tw\" data-list=\"" + esc(listKey()) + "\"><table class=\"fin-t\"><thead><tr>" + cols.map(function (k) {
      return "<th" + cls(k) + ">" + esc(COLS[k].h) + "</th>";
    }).join("") + "</tr></thead><tbody>" + rows.map(function (r) {
      var open = detail && (opened || state.sc.open)[r.id];
      var attrs = detail ? " class=\"fin-rowbtn" + (open ? " open" : "") + "\" data-row=\"" + esc(r.id) + "\" tabindex=\"0\" aria-expanded=\"" + (open ? "true" : "false") + "\"" : "";
      return "<tr" + attrs + ">" + cols.map(function (k) { return "<td" + cls(k) + ">" + COLS[k].c(r) + "</td>"; }).join("") + "</tr>" +
        (open ? "<tr class=\"fin-detail\"><td colspan=\"" + cols.length + "\">" + detail(r) + "</td></tr>" : "");
    }).join("") + "</tbody>" + (totals ? totalsRow(cols, totals.body, totals.what) : "") + "</table></div>";
  }

  /* ---------- students: one list by state ---------- */
  function stateOf(states, key) { return (states || []).filter(function (s) { return s.key === key; })[0] || { key: key, label: key, about: "" }; }
  function stateLabel(states, key) { return stateOf(states, key).label; }
  function stateChip(key, states, colors) {
    var st = stateOf(states || VIEW.b2c.states, key);
    return "<span class=\"fin-statechip\" style=\"--c:" + ((colors || STATE_COLOR)[key] || "var(--fin-pending)") + "\">" + esc(st.label) + "</span>";
  }
  /* a chosen state's sentence (app/finance.py `about`): what is in its list and why, in monday's labels, above the list */
  function aboutHtml(tab, key) {
    var v = VIEW[tab], about = key === "tasks" ? v.tasks_about : key ? stateOf(v.states, key).about : "";
    return about ? "<p class=\"fin-about\">" + esc(about) + "</p>" : "";
  }
  /* what an asterisk in the list means, said under the list that shows one, and only then */
  function listNotes(notes) {
    return notes.filter(Boolean).map(function (t) { return "<p class=\"fin-listnote\">" + esc(t) + "</p>"; }).join("");
  }
  function scPeriod() {
    var ps = VIEW.b2c.periods;
    var p = ps.filter(function (x) { return x.key === state.sc.period; })[0];
    if (!p) { state.sc.period = ""; p = ps[0]; }   /* a month a newer reading no longer has: back to the whole span */
    return p;
  }
  /* The students' list filter. The SAME rule as app/finance.py filter_b2c, b2c_matches and sort_b2c - the Excel
     file is built there from what the page sends, so the two must select the same rows in the same order;
     tests/test_finance_js.py runs both on the same rows. Amounts are compared in whole agorot (no floats). */
  /* the search box's alphabet for an Excel file - app/finance.py query_exportable, the same characters */
  var EXPORTABLE_Q = /^[\u0590-\u05FFA-Za-z0-9 ,.\-\/₪'"()]{0,100}$/;
  function exportable() { return EXPORTABLE_Q.test(state.query); }
  function exportTitle(ok) {
    return !ok ? " title=\"הקריאה לא שלמה: אין לייצא סכומים שאין להסתמך עליהם. רענון יקרא שוב.\""
      : !exportable() ? " title=\"בחיפוש יש תו מיוחד: אפשר לסנן בו, אבל לא לייצא.\"" : "";
  }
  function agorot(s) {
    var m = /^(-?)([0-9]+)(?:\.([0-9]*))?$/.exec(String(s == null ? "" : s));
    if (!m) { return null; }
    var v = Number(m[2]) * 100 + Number(((m[3] || "") + "00").slice(0, 2));
    return m[1] ? -v : v;
  }
  function scMatch(r, q) {
    q = String(q || "").trim().toLowerCase();
    if (!q) { return true; }
    var numText = q.replace(/[,₪\s]/g, "");
    if (/^[0-9]+(\.[0-9]{1,2})?$/.test(numText)) {
      var n = agorot(numText);
      if ([r.money_in, r.refunded, r.credit_approved].some(function (a) { var x = agorot(a); return x && Math.abs(x - n) < 100; })) { return true; }
    }
    return [r.name, r.cohort_code, r.cohort_title, r.invoice, r.credit_ref].join(" ").toLowerCase().indexOf(q) !== -1;
  }
  function scFilter(rows, b2c, st, periodKey, q) {
    var months = {}, tasks = {};
    (periodKey ? periodKey.split("+") : []).forEach(function (m) { months[m] = 1; });
    b2c.states.forEach(function (x) { if (x.task) { tasks[x.key] = 1; } });
    var out = rows.filter(function (r) {
      var keep = st === "tasks" ? !!tasks[r.state] : (!st || st === r.state);
      return keep && (!periodKey || !!months[r.month]) && scMatch(r, q);
    });
    var one = st && st !== "tasks" ? st : "";
    if (one === "refund_to_execute") { return out.slice().sort(byRefundKey); }
    return sortRows(out, SC_SORT[one] || "-date");
  }
  /* A search narrowed by the list's filters (a state, a period, one company) offers the same search without them, in
     plain words: "בכל השיבוצים" read as the desk's jargon (Chava, 16.11), and it let go of the state only, so a search
     in one month or one company's ledger still found nothing. The button keeps the search and drops every filter
     (`unfilter`); the list's head says how many more it finds, counted the same way. */
  var UNFILTER = "חיפוש ללא סינון";
  function unfilterButton() { return "<button class=\"fin-linkbtn\" type=\"button\" data-unfilter=\"1\">" + UNFILTER + "</button>"; }
  function elsewhere(more) {
    return more > 0 ? " · <button class=\"fin-linkbtn\" type=\"button\" data-unfilter=\"1\">" + (more === 1 ? "עוד תוצאה אחת" : "עוד " + NF.format(more) + " תוצאות") +
      " ב" + UNFILTER + "</button>" : "";
  }
  /* the search stays, and nothing else narrows it: every state and the whole span, or every company */
  function unfilter() {
    if (state.tab === "b2b") { state.b2.state = ""; state.b2.customer = ""; state.b2.view = "list"; state.b2.open = {}; }
    else { state.sc.state = ""; state.sc.period = ""; state.sc.open = {}; }
  }
  function scRows() {
    scPeriod();
    return scFilter(ROWS.sc, VIEW.b2c, state.sc.state, state.sc.period, state.query);
  }
  /* a state's tile: its count, its name beside the state's dot (the same dot its rows carry), its money quiet. The sum of
     her work ("לטיפול") and "הכל" are no one state, so they carry no colour; "לטיפול" is the anchor, larger */
  function tile(key, label, count, money, task, attr, current, colors) {
    var on = (current === undefined ? state.sc.state : current) === key;
    var c = key && key !== "tasks" ? (colors || STATE_COLOR)[key] || "var(--fin-pending)" : "";
    return "<div class=\"fin-state" + (on ? " on" : "") + (task ? " task" : "") + (key === "tasks" ? " anchor" : "") + (count ? "" : " zero") +
      (c ? " dot" : "") + "\" role=\"button\" tabindex=\"0\" aria-pressed=\"" + (on ? "true" : "false") + "\" " + (attr || "data-bstate") + "=\"" + esc(key) + "\"" +
      (c ? " style=\"--c:" + c + "\"" : "") + ">" +
      "<span class=\"n\">" + NF.format(count) + "</span><span class=\"l\">" + esc(label) + "</span>" +
      (money != null ? "<span class=\"m\">" + money + "</span>" : "") + "</div>";
  }
  function statesHtml(p) {
    var tasks = VIEW.b2c.states.filter(function (s) { return s.task; }), rest = VIEW.b2c.states.filter(function (s) { return !s.task; });
    var open = tasks.reduce(function (a, s) { return a + p.states[s.key].count; }, 0);
    return "<div class=\"fin-states\" role=\"group\" aria-label=\"שיבוץ התקבולים\"><div class=\"fin-stategroup\">" +
      tile("tasks", "לטיפול", open, null, true) +
      tasks.map(function (s) { var x = p.states[s.key]; return tile(s.key, s.label, x.count, money(x.money), true); }).join("") +
      "</div><div class=\"fin-stategroup rest\">" +
      rest.filter(function (s) { return p.states[s.key].count; }).map(function (s) {
        var x = p.states[s.key]; return tile(s.key, s.label, x.count, s.key === "no_money" ? null : money(x.money), false);
      }).join("") + tile("", "הכל", p.count, null, false) + "</div></div>";
  }
  function periodSelect() {
    var groups = { all: [], month: [], vat: [] };
    VIEW.b2c.periods.forEach(function (p) { groups[p.kind].push(p); });
    function opts(list) {
      return list.map(function (p) {
        return "<option value=\"" + esc(p.key) + "\"" + (p.key === state.sc.period ? " selected" : "") + ">" + esc(p.label) + " (" + NF.format(p.count) + ")</option>";
      }).join("");
    }
    return "<label class=\"fin-field\"><span>תקופה</span><select id=\"fin-period\" class=\"fin-select\">" + opts(groups.all) +
      (groups.month.length ? "<optgroup label=\"חודש\">" + opts(groups.month) + "</optgroup>" : "") +
      (groups.vat.length ? "<optgroup label=\"דו-חודשי, לדיווח מע&quot;מ\">" + opts(groups.vat) + "</optgroup>" : "") + "</select></label>";
  }
  /* ---------- a row's details: its situation in one line, the facts the row does not show, what to look at ---------- */
  /* days from the server's today (VIEW.today) to a date: dates only, never money */
  function daysFrom(iso) {
    if (!iso) { return null; }
    return Math.round((Date.parse(iso.slice(0, 10) + "T00:00:00Z") - Date.parse(VIEW.today + "T00:00:00Z")) / 86400000);
  }
  function when(iso) {
    var n = daysFrom(iso);
    if (n === null) { return ""; }
    return n === 0 ? " (היום)" : n > 0 ? " (בעוד " + plural(n, "יום אחד", "ימים") + ")" : " (לפני " + plural(-n, "יום אחד", "ימים") + ")";
  }
  /* days to a refund's last day in the words of Inna's refunds app (analiza-transfer-student daysLeftText), so the two
     screens say the same thing about the same day */
  function lastDayText(n) {
    if (n === null || n === undefined) { return ""; }
    if (n < 0) { return "באיחור של " + (n === -1 ? "יום אחד" : NF.format(-n) + " ימים"); }
    if (n === 0) { return "היום הוא היום האחרון"; }
    return n === 1 ? "נותר יום אחד" : "נותרו " + NF.format(n) + " ימים";
  }
  /* "מועד אחרון להחזר": the date, and while the money has not gone out, how late or how near it is */
  /* an approved refund whose money has not been marked as gone: to execute, or in "חריג, לבדיקה" on another status */
  function refundOpen(r) { return r.flags.indexOf("refund_to_execute") !== -1 || r.flags.indexOf("credit_on_status") !== -1; }
  function lastDayCell(r) {
    var waiting = refundOpen(r);
    if (!r.refund_last_day) { return waiting ? "<span class=\"fin-badtext\">לא ידוע</span>" : NOT_SET; }
    if (!waiting) { return d(r.refund_last_day); }
    var n = daysFrom(r.refund_last_day);
    return d(r.refund_last_day) + " <span class=\"" + (n < 0 ? "fin-badtext" : "fin-subtle") + "\">" + esc(lastDayText(n)) + "</span>";
  }
  function bankText(b, holder) {
    b = b || {};
    return [b.bank && "בנק " + ltr(b.bank), b.bank_branch && "סניף " + ltr(b.bank_branch), b.bank_account && "מספר חשבון " + ltr(b.bank_account),
      holder && b.bank_holder && esc(b.bank_holder)].filter(Boolean).join(" · ");
  }
  /* a full refund without "סכום החזר": what was paid is counted as refunded (app/finance.py), and the list says so */
  function refundGuessed(r) {
    return (r.flags.indexOf("refund_amount_missing") !== -1 || r.flags.indexOf("refund_amount_zero") !== -1) && num(r.refunded) > 0;
  }
  /* the warnings a row carries, as sentences in monday's words. The references (invoice, credit note) are no warning:
     the Hashavshevet integration fills them, and she does not type them (Nadav 10.11) */
  function amountText(v) { return NF2.format(num(v)) + " ₪"; }
  var REFUNDED_LABELS = "\"תשלום הוחזר\" או \"הוחזר חלקי\"";
  var WARN = {
    invoice_late: "מועד דרישת התשלום עבר, והחיוב עוד לא חויב", no_contract: "העסקה בסטטוס \"בהרצה ללא חוזה\"",
    over_deal_value: "סך סכום לחיוב (ללא מע\"מ) בעסקה עובר את שווי עסקה", payer_mismatch: "החיוב רשום על חברה שאינה Account משלם בעסקה",
    /* an approved refund, a refund amount or a refund day on a status that says no refund went out (QA 1.1, 1.2): the
       sentence names the status and the value, so the contradiction can be read without opening monday */
    credit_on_status: function (r) {
      return "סכום לזיכוי " + amountText(r.credit_approved) + " בסטטוס תשלום " + statusWord(r) + ": החזר שאושר, ועוד לא סומן " + REFUNDED_LABELS;
    },
    refund_amount_on_status: function (r) {
      return "סכום החזר " + amountText(r.refund_typed) + ", וסטטוס תשלום " + statusWord(r) + " ולא " + REFUNDED_LABELS;
    },
    refund_date_on_status: function (r) {
      return "תאריך החזר " + d(r.refund_date) + ", וסטטוס תשלום " + statusWord(r) + " ולא " + REFUNDED_LABELS;
    },
    refund_amount_zero: function (r) {
      return "סטטוס תשלום " + statusWord(r) + ", וסכום החזר 0" +
        (r.status_idx === "4" ? ": נספר כל סכום ששולם, כמו שהסטטוס אומר" : "");
    },
    terms_missing: "לחברה אין תנאי תשלום, ולכן מועד הפירעון הוא מועד דרישת התשלום עצמו",
    status_unknown: "יש כאן תווית במאנדיי שהמסך לא מכיר", amount_missing: "סכום ששולם לא הוקלד",
    /* with its reference, so the invoice can be found and its demand day typed */
    billed_no_due: function (r) { return FLAG.billed_no_due + (r.invoice ? ", אסמכתת חשבונית " + r.invoice : ", ואין אסמכתת חשבונית"); },
    billed_before_demand: FLAG.billed_before_demand, paid_not_billed: "סטטוס תשלום \"שולם\" לפני מצב חיוב \"חויב\"",
    partial_amount_unknown: "שולם חלקית, וסכום ששולם (כולל מע\"מ) לא הוקלד",
    refund_overdue: FLAG.refund_overdue, refund_last_day_passed: FLAG.refund_last_day_passed,
    /* two causes, two fixes: link the payment to its enrollment, or type the day in the enrollment */
    refund_last_day_unknown: function (r) {
      return (r.enrollment_linked ? "בהרשמה אין תאריך בקשת ביטול" : "התקבול לא מקושר להרשמה") + ", ולכן " + LAST_DAY_LABEL + " לא ידוע";
    },
    refund_exceeds_paid: "סכום החזר גדול מסכום ששולם (נספר עד סכום ששולם)",
    refund_amount_missing: "סטטוס התשלום אומר שהוחזר, וסכום החזר לא הוקלד", unlinked: "התקבול לא מקושר להרשמה או לליד מקור"
  };
  var WARN_ON = { b2: { amount_missing: FLAG_ON.b2.amount_missing } };
  function warnings(r) {
    var w = r.flags.filter(function (f) { return WARN[f]; }).map(function (f) {
      var t = (WARN_ON[r.board] || {})[f] || WARN[f];
      return typeof t === "function" ? t(r) : t;
    });
    return w.length ? "<ul class=\"fin-dx-warn\">" + w.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>" : "";
  }
  /* every field always, in the same place, "-" where monday holds nothing (QA 2.9: the fields moved from row to row) */
  function facts(pairs) {
    return "<dl class=\"fin-dx-grid\">" + pairs.map(function (x) {
      return "<dt>" + esc(x[0]) + "</dt><dd>" + (x[1] || NOT_SET) + "</dd>";
    }).join("") + "</dl>";
  }
  /* a part of the details, headed by where its fields come from: the row itself, or the item it links to, with that item's
     name (QA 2.5: the deal's "סטטוס" under "עסקה", not a "סטטוס העסקה" monday does not have) */
  function block(title, name, pairs) {
    return "<section class=\"fin-dx-block\">" + (title ? "<h3 class=\"fin-dx-h\">" + esc(title) +
      (name ? " <span class=\"fin-dx-name\">" + name + "</span>" : "") + "</h3>" : "") + facts(pairs) + "</section>";
  }
  function related(blocks) { return "<div class=\"fin-dx-rel\">" + blocks.join("") + "</div>"; }
  function subtle(t) { return "<span class=\"fin-subtle\">" + esc(t) + "</span>"; }
  function dx(chip, situation, body, r) {
    return "<div class=\"fin-dx\"><div class=\"fin-dx-head\"><div class=\"fin-dx-sit\">" + chip + "<span>" + situation + "</span></div>" +
      "<a class=\"fin-btn fin-dx-open\" data-open=\"" + esc(r.id) + "\" href=\"" + itemUrl(r.board, r.id) + "\" target=\"_blank\" rel=\"noopener\">פתיחה במאנדיי</a></div>" +
      body + "</div>";
  }
  var NONE = "<span class=\"fin-subtle\">טרם נרשמה</span>";

  function scSituation(r) {
    var paid = "שולם " + money2(r.money_in) + " ב-" + d(r.date) + (r.date_from === "paid" ? "" : " (" + CREATED_WORD + ")") +
      (r.channel ? " · " + esc(r.channel) : "");
    switch (r.state) {
      case "receipt_pending": return paid + " · <b>שליחת קבלה: " + esc(r.receipt || "ריק") + "</b>";
      /* the two dates side by side: the legal day and its lateness, then the transfer round set at approval. The amount is
         monday's "סכום לזיכוי", never a word of its own (QA 2.5: "להחזיר") */
      case "refund_to_execute": return "<b>סכום לזיכוי " + money2(r.credit_approved) + "</b> · " + LAST_DAY_LABEL + " " +
        (r.refund_last_day ? d(r.refund_last_day) + " (" + esc(lastDayText(daysFrom(r.refund_last_day))) + ")" : "<b>לא ידוע</b>") +
        " · " + PLANNED_LABEL + " " + (r.planned ? d(r.planned) + when(r.planned) : "לא נקבע") +
        (r.refund_method ? " · " + esc(r.refund_method) : "");
      case "done": return paid + " · שליחת קבלה: " + esc(r.receipt);
      case "refunded": return "הוחזר " + money2(r.refunded) + (r.refund_date ? " ב-" + d(r.refund_date) : "") + (r.refund_method ? " · " + esc(r.refund_method) : "");
      case "refund_asked": return paid + " · " + esc(stateLabel(VIEW.b2c.states, "refund_asked"));
      case "no_money": return esc(r.status || "בלי סטטוס תשלום");
      default: return "<b>לבדיקה</b>: הסיבה למטה";
    }
  }
  /* a refund in play (app/finance.py is_refund_row): asked for, approved or done - and here also a row with a refund amount
     or day typed, so the details show what was typed */
  var REFUND_STATUS = { "4": 1, "5": 1, "9": 1 };
  function refundRow(r) { return !!REFUND_STATUS[r.status_idx] || num(r.credit_approved) > 0; }
  function refundInPlay(r) { return refundRow(r) || (r.refund_typed != null && num(r.refund_typed) !== 0) || !!r.refund_date; }
  function askedCell(r) {
    if (r.asked_date) { return d(r.asked_date); }
    return refundRow(r) ? subtle(r.enrollment_linked ? "אין בהרשמה" : "אין הרשמה מקושרת") : NOT_SET;
  }
  /* a payment's details (QA 2.9): the same fields in the same places on every row, in parts by where they come from - the
     refund (first, where a refund is in play: its two dates side by side), the payment's own row (no heading: it is the row
     opened), its enrollment, its cohort */
  function detailHtml(r) {
    var others = ROWS.sc.filter(function (x) { return x.person === r.person && x.id !== r.id; });
    var refund = refundInPlay(r) ? block("החזר", "", [
      [LAST_DAY_LABEL, lastDayCell(r)], [PLANNED_LABEL, COLS.planned.c(r)],
      ["סכום לזיכוי", num(r.credit_approved) ? money2(r.credit_approved) : NOT_SET],
      ["סכום החזר", r.refund_typed != null ? money2(r.refund_typed) : NOT_SET],
      ["תאריך החזר", r.refund_date ? d(r.refund_date) : NOT_SET], ["אופן החזר", lbl(r, "refund_method")],
      ["אסמכתת זיכוי", ltr(r.credit_ref) || NONE]]) +
      /* a refund to execute (the only rows whose account the old server read): its bank account, read live as the person
         now that its details are open (D2, bankCell) */
      (r.state === "refund_to_execute" ? "<div class=\"fin-dx-box\"><span class=\"fin-subtle\">פרטי חשבון</span> " + bankCell(r) + "</div>" : "") : "";
    var payment = block("", "", [
      ["סטטוס תשלום", lbl(r, "status")], ["שליחת קבלה", lbl(r, "receipt")],
      ["סכום ששולם", COLS.received.c(r)], ["תאריך תקבול", paidDate(r)],
      ["סוג חיוב", lbl(r, "type")], ["ערוץ תשלום", lbl(r, "channel")],
      ["אסמכתת חשבונית", ltr(r.invoice) || NONE],
      /* the lead's ID number, read live as the person when the details open (16.11): idCell; then the lead's Owner (17.11).
         "ליד מקור" (the lead's name, QA 2.5) left the details on 17.11 - Nadav: "זה לא אומר לה כלום ולא משנה" */
      [ID_TITLE, idCell(r)], ["נציג מכירות", COLS.rep.c(r)]]);
    var linked = related([
      block("הרשמה", r.enrollment_linked ? "" : subtle("התקבול לא מקושר להרשמה"), [
        ["סטטוס שובר משה\"ע", r.voucher ? esc(r.voucher) : NOT_SET], ["תאריך בקשת ביטול", askedCell(r)]]),
      block("מחזור", r.cohort_code ? ltr(r.cohort_code) + " " + esc(r.cohort_title) : subtle(COHORT_FROM[r.cohort_from] || ""),
        /* the cohort's "Registration Flow", by its name on the Deals board (17.11; app/finance.py FLOW_LABEL) */
        [["נוהל הרשמה", r.flow ? esc(r.flow) : NOT_SET]])]);
    /* the actions first, under the row's situation: the view is about 527 pixels tall inside monday, and the facts below
       sit in the same place on every row of a list - its rows all offer the same actions */
    return dx(stateChip(r.state), scSituation(r), actionsBox(r) + refund + payment + linked + warnings(r) +
      (others.length ? "<div class=\"fin-dx-more\"><span class=\"fin-subtle\">תקבולים נוספים של אותו סטודנט/ית:</span> " + others.map(function (x) {
        return "<span class=\"fin-nw\">" + d(x.date) + " " + money2(x.money_in) + " " + stateChip(x.state) + "</span>";
      }).join(" · ") + "</div>" : ""), r);
  }

  function b2cHtml() {
    var v = VIEW.b2c, p = scPeriod(), rows = scRows();
    var key = state.sc.state, one = key && key !== "tasks" ? key : "";
    var cols = SC_COLS[one] || SC_COLS[""];
    var what = key === "tasks" ? "לטיפול" : key ? stateLabel(v.states, key) : "כל התקבולים";
    var sum = one && !state.query.trim() ? " · " + money(p.states[one].money) : "";
    var head = "<div class=\"fin-listhead\"><div><h2>" + esc(what) + " <span class=\"fin-subtle\">· " + esc(p.label) + "</span></h2>" +
      "<div class=\"fin-subtle\">" + plural(rows.length, "תקבול אחד", "תקבולים") + sum + (state.query.trim() ? " · חיפוש: " + esc(state.query) + elsewhere(scFilter(ROWS.sc, v, "", "", state.query).length - rows.length) : "") +
      "</div>" + aboutHtml("b2c", key) + "</div><div class=\"fin-actions\">" +
      "<button class=\"fin-btn\" type=\"button\" data-export=\"b2c\"" + (rows.length && VIEW.checks.b2c_ok && exportable() ? "" : " disabled") +
      exportTitle(VIEW.checks.b2c_ok) + ">ייצוא לאקסל</button></div></div>";
    /* the period's money; the period itself sits beside it, since it sets the tiles and the list as well */
    var figures = figs([["התקבלו", money(p.money_in)],
      /* the tile "החזר בוצע" leaves out a refund that sits under "חריג": say how much, from the server's figure */
      ["הוחזרו", money(p.refunded) + (num(p.states.exception.refunded) ? " <small>(מתוכם " + money(p.states.exception.refunded) + " ב&quot;" +
        esc(stateLabel(v.states, "exception")) + "&quot;)</small>" : "")],
      ["סטודנטים", NF.format(p.students)]], "הסכומים כולל מע\"מ", periodSelect());
    /* an asterisk is explained under the list that shows it, and only when it does */
    var guessed = cols.indexOf("refunded") !== -1 && rows.some(refundGuessed);
    var tot = { body: totalsFor({ tab: "b2c", state: key, period: state.sc.period, q: state.query.trim() }), what: ["תקבול אחד", "תקבולים"] };
    var list = rows.length ? table(rows, cols, detailHtml, null, exportable() ? tot : null) + listNotes([
      guessed && "* סכום החזר ריק או 0, ולכן מוצג כל סכום ששולם (סטטוס תשלום \"תשלום הוחזר\")"])
      : empty(state.query.trim() ? "אין תקבולים שמתאימים לחיפוש." : key === "tasks" ? "אין תקבולים לטיפול." : "אין כאן תקבולים.",
        /* the search without its filters, only where a filter narrows it: a state, or a period */
        state.query.trim() && (key !== "" || state.sc.period !== "") ? unfilterButton() : "");
    return figures + statesHtml(p) + panel(head + list);
  }
  function exportList(tab) {
    /* a newer reading on its way is waited for first, so the file is built from the list the reader then sees */
    return Promise.resolve(pending).then(function () {
      var b2 = tab === "b2b" || tab === "b2b_customers", f = b2 ? state.b2 : state.sc;
      var body = { tab: tab === "b2b_customers" ? tab : b2 ? "b2b" : "b2c", state: tab === "b2b_customers" ? "" : f.state,
        period: b2 ? "" : state.sc.period, customer: tab === "b2b" ? state.b2.customer : "",
        q: state.query.trim(), taken: (BODY && BODY.taken_at) || "" };
      return deps.post("/api/desk/export-link", body);
    }).then(function (res) {
      var url = new URL(res.url, window.location.href).href;
      /* monday may sandbox the view against downloads: a new tab escapes it, and the file names itself */
      if (deps.monday) {
        return Promise.resolve(deps.monday.execute("openLinkInTab", { url: url })).catch(function () { window.open(url, "_blank", "noopener"); });
      }
      var a = document.createElement("a"); a.href = url; a.rel = "noopener"; document.body.appendChild(a); a.click(); a.remove();
    }).catch(function (e) { flash(errorText(e)); });
  }

  /* ---------- companies: one list by state, and the customers' aging ---------- */
  function b2Match(r, q) {
    q = String(q || "").trim().toLowerCase();
    if (!q) { return true; }
    var numText = q.replace(/[,₪\s]/g, "");
    if (/^[0-9]+(\.[0-9]{1,2})?$/.test(numText)) {
      var n = agorot(numText);
      if ([r.amount, r.paid_so_far, r.balance].some(function (a) { var x = a == null ? null : agorot(a); return x && Math.abs(x - n) < 100; })) { return true; }
    }
    return [r.account, r.deal, r.course, r.invoice, r.name].join(" ").toLowerCase().indexOf(q) !== -1;
  }
  /* The SAME rule as app/finance.py filter_b2b (tests/test_finance_js.py runs both on the same rows). */
  function b2Filter(rows, b2b, st, customer, q) {
    var tasks = {};
    b2b.states.forEach(function (x) { if (x.task) { tasks[x.key] = 1; } });
    var out = rows.filter(function (r) {
      var keep = st === "tasks" ? !!tasks[r.state] : (!st || st === r.state);
      return keep && (!customer || r.account_id === customer) && b2Match(r, q);
    });
    if (customer && !st) { return sortRows(out, "ledger_key"); }   /* one customer, every state: the ledger, oldest first */
    var key = st === "tasks" ? "_" : st;
    return sortRows(out, Object.prototype.hasOwnProperty.call(B2_SORT, key) ? B2_SORT[key] : "demand");
  }
  function b2Situation(r) {
    var terms = r.account_terms ? " · <bdi>" + esc(r.account_terms) + "</bdi>" : r.flags.indexOf("terms_missing") !== -1 ? " · " + noTerms(r.account_id) : "";
    var demand = r.demand ? " · מועד דרישת תשלום " + d(r.demand) + when(r.demand) : "";
    var label = stateLabel(VIEW.b2b.states, r.state);
    switch (r.state) {
      case "overdue": return "<b>בפיגור " + plural(r.days_late, "יום אחד", "ימים") + "</b> · מועד פירעון " + d(r.due) + terms;
      case "open": return r.due ? "מועד פירעון " + d(r.due) + when(r.due) + terms : esc(r.billing);
      case "partial": return r.balance != null
        ? "סכום ששולם (כולל מע\"מ) " + money2(r.paid_so_far) + " · <b>יתרה לגבייה " + money2(r.balance) + "</b> <small>כולל מע\"מ</small>" +
          (r.days_late ? " · בפיגור " + plural(r.days_late, "יום אחד", "ימים") : "")
        : esc(r.payment) + " · <b>סכום ששולם (כולל מע\"מ) לא הוקלד</b>";
      /* one state for both labels (Nadav 11.11): the row's own monday label says which */
      case "to_bill": return "<b>" + esc(label) + "</b> · " + esc(r.billing) + demand;
      case "planned": return esc(label) + " · " + esc(r.billing) + demand;
      case "pending": return esc(label) + ": " + esc(r.billing || "מצב חיוב ריק");
      case "paid": case "cancelled": return esc(label);
      default: return "<b>לבדיקה</b>: הסיבה למטה";
    }
  }
  /* a charge's details (QA 2.5, 2.9): the charge's own fields, then its company and its deal, each under its own heading -
     the deal's "סטטוס", "שווי עסקה" and "Account משלם" as monday names them, under "עסקה" */
  function b2Detail(r) {
    var body = block("", "", [
      ["מצב חיוב", lbl(r, "billing")], ["סטטוס תשלום", lbl(r, "payment")],
      ["מועד דרישת תשלום", COLS.demand.c(r)], ["מועד פירעון", r.due ? d(r.due) : NOT_SET],
      ["סכום לחיוב (ללא מע\"מ)", COLS.amount.c(r)], ["סכום ששולם (כולל מע\"מ)", r.paid_so_far != null ? money2(r.paid_so_far) : NOT_SET],
      ["סוג רכיב", lbl(r, "component")], ["קוד קורס", ltr(r.course)],
      ["B2B2C Month", ltr(r.month)], ["אסמכתת חשבונית", ltr(r.invoice) || NONE]]) +
      related([
        block("חברה", esc(r.ctx.account), [["תנאי תשלום", r.account_terms ? "<bdi>" + esc(r.account_terms) + "</bdi>" : r.account_no_terms ? noTerms(r.account_id) : NOT_SET]]),
        block("עסקה", esc(r.ctx.deal), [["סטטוס", r.deal_stage ? esc(r.deal_stage) : NOT_SET],
          ["שווי עסקה", r.deal_value != null ? money2(r.deal_value) : NOT_SET], ["Account משלם", r.payer ? esc(r.payer) : NOT_SET]])]) +
      warnings(r);
    return dx(stateChip(r.state, VIEW.b2b.states, B2_COLOR), b2Situation(r), actionsBox(r) + body, r);
  }
  function b2Tiles(b) {
    var mine = state.b2.customer && b.by_customer[state.b2.customer];   /* a chosen customer's tiles are its own */
    var N = mine ? mine.numbers : b.numbers, cur = state.b2.view === "customers" ? null : state.b2.state;
    var tasks = b.states.filter(function (s) { return s.task; }), rest = b.states.filter(function (s) { return !s.task; });
    var open = tasks.reduce(function (a, s) { return a + N[s.key].count; }, 0);
    function t(s) {
      var x = N[s.key], m = s.key === "cancelled" ? null : money(x.money);
      if (s.key === "partial" && x.count) {
        m += " <small>סכום לחיוב (ללא מע\"מ)</small>" + (x.balance_unknown ? "<br><span class=\"fin-badtext\">סכום ששולם (כולל מע\"מ) לא הוקלד " +
          (x.balance_unknown === 1 ? "בחיוב אחד" : "ב-" + NF.format(x.balance_unknown) + " חיובים") + "</span>"
          : "<br>יתרה לגבייה " + money(x.balance) + " <small>כולל מע\"מ</small>");
      }
      return tile(s.key, s.label, x.count, m, s.task, "data-b2state", cur, B2_COLOR);
    }
    return "<div class=\"fin-states\" role=\"group\" aria-label=\"שיבוץ החיובים\"><div class=\"fin-stategroup\">" +
      tile("tasks", "לטיפול", open, null, true, "data-b2state", cur, B2_COLOR) + tasks.map(t).join("") +
      "</div><div class=\"fin-stategroup rest\">" + rest.filter(function (s) { return N[s.key].count; }).map(t).join("") +
      tile("", "הכל", mine ? mine.count : b.count, null, false, "data-b2state", cur, B2_COLOR) + "</div></div>";
  }
  /* the aging by customer, narrowed by the search box: a customer's name holds the text (app/finance.py filter_customers) */
  function customersShown(b) {
    var q = state.query.trim().toLowerCase();
    return b.customers.filter(function (c) { return !q || String(c.name).toLowerCase().indexOf(q) !== -1; });
  }
  function bandHeading(x) { return x.heading || x.label; }
  /* a company's figure under an aging column (app/finance.py aging_value): a band's open money, the balance left where a
     partly paid charge has its paid amount typed, or the figure itself */
  function agingValue(c, key) {
    if (key.indexOf("aging_") === 0) { return c.aging[key.slice(6)]; }
    if (key === "partial_balance") { return c.partial_count - c.partial_unknown ? c.partial_balance : null; }
    return c[key];
  }
  /* the aging's columns are the server's (app/finance.py AGING_COLUMNS), so its Excel file is headed exactly as this table
     (QA 2.12); an optional one is drawn only when a company shown has something in it, as `aging_columns` decides */
  function agingCols(b, shown) {
    return b.aging_columns.filter(function (col) {
      return !col.shown_when || shown.some(function (c) { return num(agingValue(c, col.shown_when)); });
    });
  }
  function customersHtml(b) {
    var shown = customersShown(b);
    if (!b.customers.length) { return empty("אין חברה עם משהו לחיוב החודש, ביתרה פתוחה, ששולם חלקית או ב\"חריג, לבדיקה\"."); }
    if (!shown.length) { return empty("אין חברה שהשם שלה מתאים לחיפוש."); }
    /* the A/R aging (Nadav 10.11 "תעשה את 2"): what to do first (to bill this month, partly paid, the tasks), then the open
       money by days past due under one header, and its total last, beside the bands it sums */
    var cols = agingCols(b, shown);
    /* a group's cells carry its edges down the whole table, so its first and last columns read as inside it (QA 2.3: the
       centred "לפי מועד פירעון" seemed to cover the middle bands only) */
    function edge(i) {
      var g = cols[i].group;
      if (!g) { return ""; }
      return " fin-band" + (i === 0 || cols[i - 1].group !== g ? " fin-band-first" : "") +
        (i === cols.length - 1 || cols[i + 1].group !== g ? " fin-band-last" : "");
    }
    function numCell(v, strong, i) {
      return "<td class=\"n k-" + cols[i].key + edge(i) + "\">" + (num(v) ? (strong ? "<b>" + money2(v) + "</b>" : money2(v)) : "-") + "</td>";
    }
    var head1 = "", head2 = "";
    cols.forEach(function (col, i) {
      if (!col.group) {
        head1 += "<th rowspan=\"2\"" + (col.kind === "text" ? "" : " class=\"n\"") + ">" + esc(col.header) + "</th>";
        return;
      }
      if (i === 0 || cols[i - 1].group !== col.group) {
        var n = 0;
        while (i + n < cols.length && cols[i + n].group === col.group) { n++; }
        head1 += "<th colspan=\"" + n + "\" class=\"fin-group fin-band fin-band-first fin-band-last\" scope=\"colgroup\">" + esc(col.group) + "</th>";
      }
      head2 += "<th class=\"n k-" + col.key + edge(i) + "\">" + esc(col.header) + "</th>";
    });
    var t = totalsFor({ tab: "b2b_customers", q: state.query.trim() });
    /* a total of another reading is not this table's total (as totalsRow): no row, never a broken page */
    var foot = t && !t.failed && !t.other_reading && t.sums && exportable() ? "<tfoot><tr class=\"fin-total\">" + cols.map(function (col, i) {
      var k = col.key;
      if (k === "name") { return "<td><b>סה\"כ</b> <span class=\"fin-subtle\">" + plural(t.count, "חברה אחת", "חברות") + "</span></td>"; }
      if (k === "terms" || k === "tasks") { return "<td></td>"; }
      if (k === "partial_balance") { return numCell(t.sums.partial_count - t.sums.partial_unknown ? t.sums.partial_balance : 0, false, i); }
      return numCell(t.sums[k], k === "open", i);
    }).join("") + "</tr></tfoot>" : "";
    return "<div class=\"fin-tw\" data-list=\"" + esc(listKey()) + "\"><table class=\"fin-t fin-aging-t\"><thead><tr>" + head1 + "</tr><tr class=\"fin-sub\">" + head2 +
      "</tr></thead><tbody>" + shown.map(function (c) {
        return "<tr class=\"fin-rowbtn\" tabindex=\"0\" data-customer=\"" + esc(c.id) + "\">" + cols.map(function (col, i) {
          var k = col.key;
          if (k === "name") { return "<td>" + esc(c.name) + "</td>"; }
          /* the company's terms, or that it has none (QA 1.3) */
          if (k === "terms") { return "<td class=\"k-terms\">" + (c.terms ? "<bdi>" + esc(c.terms) + "</bdi>" : c.terms_missing ? noTerms(c.id) : NOT_SET) + "</td>"; }
          if (k === "tasks") { return "<td class=\"n\">" + NF.format(c.tasks) + "</td>"; }
          /* the older the band, the heavier: weight marks it, not another red */
          return numCell(agingValue(c, k), k === "aging_d61_90" || k === "aging_d90" || k === "open", i);
        }).join("") + "</tr>";
      }).join("") + "</tbody>" + foot + "</table></div>";
  }
  function b2bHtml() {
    var b = VIEW.b2b, f = state.b2;
    var who = f.customer ? (b.rows.filter(function (x) { return x.account_id === f.customer; })[0] || {}).account : "";
    var rows = b2Filter(ROWS.b2, b, f.state, f.customer, state.query);
    var key = f.state === "tasks" ? "" : f.state;
    var what = f.state === "tasks" ? "לטיפול" : f.state ? stateLabel(b.states, f.state) : "כל החיובים";
    var views = "<div class=\"fin-seg\" role=\"group\" aria-label=\"תצוגה\">" +
      "<button type=\"button\" class=\"fin-btn" + (f.view === "list" ? " on" : "") + "\" aria-pressed=\"" + (f.view === "list") + "\" data-b2view=\"list\">רשימה</button>" +
      "<button type=\"button\" class=\"fin-btn" + (f.view === "customers" ? " on" : "") + "\" aria-pressed=\"" + (f.view === "customers") + "\" data-b2view=\"customers\">לפי לקוח</button></div>";
    var mine = f.customer && b.by_customer[f.customer];
    /* a chosen customer: its own figures and its aging, as a ledger card (כרטסת) opens with */
    var figures = mine ? figs([["יתרה פתוחה", money(mine.open)], ["מתוכה בפיגור", money(mine.overdue)],
      [stateLabel(b.states, "to_bill"), money(mine.to_bill)]], "הסכומים ללא מע\"מ") +
      "<div class=\"fin-aging\" aria-label=\"לפי מועד פירעון\">" + b.bands.filter(function (x) { return num(mine.aging[x.key]); }).map(function (x) {
        return "<span><span class=\"fin-subtle\">" + esc(bandHeading(x)) + "</span> " + money(mine.aging[x.key]) + "</span>";
      }).join("") + "</div>"
      : figs([["יתרה פתוחה", money(b.open)], ["מתוכה בפיגור", money(b.overdue) + (num(b.overdue_no_terms)
        ? " <small>(מזה " + money(b.overdue_no_terms) + " בחברות בלי תנאי תשלום)</small>" : "")],
      ["חברות עם משהו פתוח", NF.format(b.customers.length)]], "הסכומים ללא מע\"מ");
    if (mine) { figures += billingPanel(f.customer, mine); }
    if (f.view === "customers") {
      return figures + b2Tiles(b) + panel("<div class=\"fin-listhead\"><div><h2>לפי לקוח</h2><div class=\"fin-subtle\">כל חברה עם משהו לחיוב החודש, " +
        "ביתרה פתוחה, ששולם חלקית או בחריג. לחיצה על חברה פותחת את הכרטסת שלה.</div></div><div class=\"fin-actions\">" + views +
        "<button class=\"fin-btn\" type=\"button\" data-export=\"b2b_customers\"" + (customersShown(b).length && VIEW.checks.b2b_ok && exportable() ? "" : " disabled") +
        exportTitle(VIEW.checks.b2b_ok) + ">ייצוא לאקסל</button></div></div>" + customersHtml(b));
    }
    var head = "<div class=\"fin-listhead\"><div><h2>" + esc(what) + (who ? " <span class=\"fin-subtle\">· " + esc(who) + "</span>" +
      (mine && (mine.terms || mine.terms_missing) ? termsMark(mine.terms) : "") : "") + "</h2>" +
      "<div class=\"fin-subtle\">" + plural(rows.length, "חיוב אחד", "חיובים") +
      (f.state && f.state !== "tasks" && !f.customer && !state.query.trim() && f.state !== "cancelled" ? " · " + money(b.numbers[f.state].money) + " ללא מע\"מ" : "") +
      (who ? " · <button class=\"fin-linkbtn\" type=\"button\" data-customer=\"\">כל החברות</button>" : "") +
      (state.query.trim() ? " · חיפוש: " + esc(state.query) + elsewhere(b2Filter(ROWS.b2, b, "", "", state.query).length - rows.length) : "") + "</div>" +
      aboutHtml("b2b", f.state) + "</div><div class=\"fin-actions\">" + views +
      "<button class=\"fin-btn\" type=\"button\" data-export=\"b2b\"" + (rows.length && VIEW.checks.b2b_ok && exportable() ? "" : " disabled") +
      exportTitle(VIEW.checks.b2b_ok) + ">ייצוא לאקסל</button></div></div>";
    var ledger = f.customer && !f.state;   /* the ledger: oldest first, with the open money owed up to each charge */
    var bcols = ledger ? ["demand", "deal", "charge", "amount", "b2state", "due", "ledger", "invoice", "open"] : (B2_COLS[key] || B2_COLS[""]);
    var btot = { body: totalsFor({ tab: "b2b", state: f.state === "tasks" ? "tasks" : f.state, customer: f.customer, q: state.query.trim() }),
      what: ["חיוב אחד", "חיובים"] };
    var list = rows.length ? table(rows, bcols, b2Detail, state.b2.open, exportable() ? btot : null)
      : empty(state.query.trim() ? "אין חיובים שמתאימים לחיפוש." : f.state === "tasks" && !f.customer ? "אין חיובים לטיפול." : "אין כאן חיובים.",
        /* a search: the same search without its filters (a state, one company), where one narrows it. No search, one
           company in one state: that company's every charge, its ledger */
        state.query.trim() ? (f.state !== "" || f.customer ? unfilterButton() : "")
          : f.customer && f.state !== "" ? "<button class=\"fin-linkbtn\" type=\"button\" data-b2state=\"\">כל החיובים של החברה</button>" : "");
    return figures + b2Tiles(b) + panel(head + list);
  }

  /* ---------- actions from the desk (round 3, slice 3) ----------
     What the page writes in monday AS THE PERSON: monday.api() of the SDK the page loads, so monday's own permissions apply,
     the board's activity log shows who wrote, and its automations and webhooks fire as on a board edit (knowledge ADR-0020
     §2) - never through this service's server. Which actions a row offers, the columns each writes and the status INDEX it
     sets come from the server (app/finance.py DESK_ACTIONS, sent as view.<tab>.actions and each row's `actions`): the page
     never writes a label's text. Every write goes out through mondayWrite(), the one place a mutation is composed, which
     refuses any (board, column) outside WRITABLE, a status index no action sets, and any value of another shape, before
     monday.api() is called (tests/test_finance_actions_js.py). An action reads the row live first and shows exactly what will be
     written; on "כתיבה" it writes, step by step, checks each step against monday's answer, puts the new values on the row
     at once, and asks the server for a refresh. A failure - a permission the app lacks among them - is said in words. */
  /* The page's allowlist: the ONE list of what it may write - (board, column), and the kind of value; for a status, the
     list of the indexes an action sets (review 11.11: a status column was allowed with every one of its labels, so a plan
     naming another index went out). Held to app/finance.py desk_writes() by a test; a write outside it is refused here,
     before anything is sent. */
  var WRITABLE = {
    "5093448677": { color_mm1kxpvt: [4, 5], numeric_mm62za1f: "number", text_mm8fjzfj: "text", text_mm6d6s97: "text",
      color_mm4cq1hw: [1] },
    "5097721228": { color_mm3zx2n7: [6], text_mm3y6snz: "text", date_mm4a6y8y: "date", color_mm4bhvta: [0, 1],
      numeric_mm8zqhag: "number" }
  };
  /* an allowlist entry's kind: a list of indexes is a status */
  function kindOf(entry) { return Array.isArray(entry) ? "status" : entry; }
  /* the API version the server's reader is pinned to (app/adapters/monday_board.py MONDAY_API_VERSION, held by a test) */
  var API_VERSION = "2026-07";
  var TEXT_MAX = 60;
  /* control characters, and the invisible ones that reorder or hide text (app/account_writer.py _CONTROL, the same; the
     Arabic letter mark U+061C since review 11.11) */
  var CONTROL = /[\u0000-\u001f\u007f\u0085\u061c\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/;
  /* how long the page waits for monday's answer to one request: the SDK's call has no limit of its own, and an answer that
     never came kept an action "writing" with every button off until the page was loaded again (review 11.11) */
  var MONDAY_WAIT_MS = 30000;
  var CELL = "{ id text value ... on StatusValue { index label } }";
  var READ_ROW = "query ($i: [ID!], $c: [String!], $b: [ID!]) { items(ids: $i) { id name board { id } column_values(ids: $c) " +
    CELL + " } boards(ids: $b) { columns(ids: $c) { id type settings_str } } }";
  var WRITE_ROW = "mutation ($b: ID!, $i: ID!, $v: JSON!, $c: [String!]) { change_multiple_column_values(board_id: $b, " +
    "item_id: $i, column_values: $v, create_labels_if_missing: false) { id column_values(ids: $c) " + CELL + " } }";
  /* a written column on the row the page shows: its label and index, or its value (the row is patched until a reading
     shows the same) */
  var ROW_FIELD = { color_mm1kxpvt: ["status", "status_idx"], color_mm4cq1hw: ["receipt", "receipt_idx"],
    numeric_mm62za1f: ["refund_typed"], text_mm8fjzfj: ["credit_ref"], text_mm6d6s97: ["invoice"],
    color_mm3zx2n7: ["billing", "billing_idx"], color_mm4bhvta: ["payment", "payment_idx"], text_mm3y6snz: ["invoice"],
    date_mm4a6y8y: ["demand"], numeric_mm8zqhag: ["paid_so_far"] };
  var NUMERIC_FIELD = { refund_typed: 1, paid_so_far: 1 };
  /* whether this person may write from here: "yes", "no" (with why), or "unknown" - monday did not say, so the page tries
     and learns from the answer. From monday's context (permissions.approvedScopes, user.isViewOnly), read at mount. */
  var CAN = { state: "unknown", why: "" };
  var ABILITY = {
    outside: "הפעולות נכתבות במאנדיי בשמך, ולכן הן זמינות רק כשהתצוגה פתוחה מתוך מאנדיי.",
    view_only: "במאנדיי יש לך הרשאת צפייה בלבד, ולכן אין כאן פעולות.",
    approve: "לאפליקציה יש הרשאת כתיבה חדשה שעוד לא אישרת: מאנדיי מבקש לאשר אותה בבאנר בראש התצוגה. עד אז אין כאן פעולות.",
    no_scope: "לאפליקציה עוד אין הרשאת כתיבה ללוחות במאנדיי, ולכן אין כאן פעולות. עד אז: פתיחה במאנדיי."
  };
  var WRITE_ERRORS = {
    no_scope: "לאפליקציה אין הרשאה לכתוב בלוח הזה",
    denied: "מאנדיי לא מרשה לך לכתוב בשורה הזאת",
    rate_limited: "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה",
    not_found: "השורה לא נמצאה בלוח (אולי נמחקה או הועברה)",
    columns_changed: "עמודה שהפעולה כותבת לא נמצאה בלוח; נדב צריך לבדוק",
    label_moved: "תווית שהפעולה כותבת השתנתה במאנדיי; נדב צריך לבדוק",
    write_refused: "הכתיבה הזאת מחוץ לעמודות שהמסך רשאי לכתוב, ולכן לא נשלחה",
    unverified: "מאנדיי ענתה, אבל הערך בשורה אינו מה שנשלח; לבדוק את השורה במאנדיי",
    timeout: "מאנדיי לא ענתה בזמן",
    monday: "מאנדיי לא קיבלה את הכתיבה"
  };
  var ACT = null;      /* the action on the screen: { key: "sc:123", action, phase, typed, plan, live, error, detail } */
  var DRAFT = {};      /* what was typed into an action's box, by "sc:123:<column>" */
  var WRITTEN = {};    /* rows written from here, by "sc:123": { fields, until } - laid over the row until a reading shows them */
  var WRITTEN_FOR_MS = 180000;

  function later(fn, ms) { return ((deps && deps.later) || setTimeout)(fn, ms); }
  function rowKey(r) { return r.board + ":" + r.id; }
  function rowByKey(rk) {
    var p = rk.split(":"), list = p[0] === "sc" ? ROWS.sc : ROWS.b2;
    return list.filter(function (r) { return r.id === p[1]; })[0] || null;
  }
  function tabOf(board) { return board === "sc" ? "b2c" : "b2b"; }
  /* the actions a row offers, as the server sent them, in its order */
  function rowActions(r) {
    var catalog = ((VIEW[tabOf(r.board)] || {}).actions || {}).list || [];
    return (r.actions || []).map(function (k) { return catalog.filter(function (a) { return a.key === k; })[0]; }).filter(Boolean);
  }
  function boardIdOf(r) { return ((VIEW[tabOf(r.board)] || {}).actions || {}).board || BOARD[r.board]; }
  function stepWrites(a) { return [].concat.apply([], a.steps); }
  function israelToday() {
    if (deps && deps.today) { return deps.today(); }
    try {
      var parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit" })
        .formatToParts(new Date(Date.now()));
      var part = function (t) { return (parts.filter(function (x) { return x.type === t; })[0] || {}).value; };
      return part("year") + "-" + part("month") + "-" + part("day");
    } catch (e) { return VIEW.today; }
  }

  /* ---- the one door: every monday request of the page goes through mondayCall, every write through mondayWrite ---- */
  function mondayErr(e) {
    e = e || {};
    var ext = e.extensions || {}, data = e.data || {};
    var text = [e.message, e.error_message, ext.code, e.error_code, data.error_message, data.error_code]
      .filter(Boolean).join(" ");
    /* a permission the APP lacks (a scope) and one the PERSON lacks (monday's board permissions) are told apart by the text
       monday sends - its exact wording is not verified yet, so the text itself is kept and shown under the sentence */
    var code = /scope|unauthorized field or type|missing.{0,20}permission|app.{0,20}(not|isn.t) (allowed|authori[sz]ed)/i.test(text) ? "no_scope"
      : /unauthori[sz]ed|not authori[sz]ed|permission|forbidden|access denied/i.test(text) ? "denied"
        : /rate.?limit|complexity|too many/i.test(text) ? "rate_limited" : "monday";
    return { code: code, text: String(text).slice(0, 200) };
  }
  function mondayCall(query, variables) {
    return new Promise(function (resolve, reject) {
      if (!deps || !deps.monday || typeof deps.monday.api !== "function") { reject({ code: "no_scope", text: "" }); return; }
      /* no answer in time is its own code: for a write, whether it landed is not known (errorWords) */
      var timer = setTimeout(function () { reject({ code: "timeout", text: "" }); }, (deps && deps.mondayWaitMs) || MONDAY_WAIT_MS);
      Promise.resolve().then(function () {
        return deps.monday.api(query, { variables: variables, apiVersion: API_VERSION });
      }).then(function (res) {
        clearTimeout(timer);
        var errs = res && (res.errors || ((res.error_message || res.error_code) ? [res] : null));
        if (errs && errs.length) { reject(mondayErr(errs[0])); return; }
        if (!res || !res.data) { reject(mondayErr({ message: "no data" })); return; }
        resolve(res.data);
      }, function (e) { clearTimeout(timer); reject(mondayErr(e)); });
    });
  }
  function shapeOk(entry, v) {
    var kind = kindOf(entry), keys = v && typeof v === "object" ? Object.keys(v) : [];
    if (kind === "status") { return keys.length === 1 && typeof v.index === "number" && entry.indexOf(v.index) !== -1; }
    if (kind === "date") { return keys.length === 1 && /^\d{4}-\d{2}-\d{2}$/.test(String(v.date)); }
    if (kind === "number") { return typeof v === "string" && /^[0-9]+(\.[0-9]{1,2})?$/.test(v); }
    if (kind === "text") { return typeof v === "string" && v.length <= TEXT_MAX && !CONTROL.test(v); }
    return false;
  }
  /* the columns of a write that the allowlist refuses: another board, another column, or a value of another shape */
  function refusedColumns(boardId, values) {
    var allow = Object.prototype.hasOwnProperty.call(WRITABLE, String(boardId)) ? WRITABLE[String(boardId)] : null;
    var cols = values && typeof values === "object" ? Object.keys(values) : [];
    if (!allow || !cols.length) { return cols.length ? cols : ["-"]; }
    return cols.filter(function (c) { return !Object.prototype.hasOwnProperty.call(allow, c) || !shapeOk(allow[c], values[c]); });
  }
  function cellsOf(list) {
    var out = {};
    (list || []).forEach(function (c) {
      if (!c || !c.id) { return; }
      out[c.id] = { text: String(c.text == null ? "" : c.text).trim(), index: c.index == null ? null : String(c.index),
        label: c.label == null ? "" : String(c.label) };
    });
    return out;
  }
  function mondayWrite(boardId, itemId, values) {
    var refused = refusedColumns(boardId, values);
    if (refused.length || !/^[0-9]+$/.test(String(itemId))) {
      return Promise.reject({ code: "write_refused", text: refused.join(", ") });
    }
    return mondayCall(WRITE_ROW, { b: String(boardId), i: String(itemId), v: JSON.stringify(values), c: Object.keys(values) })
      .then(function (data) { return cellsOf((data.change_multiple_column_values || {}).column_values); });
  }
  /* the row as monday holds it now, read as the person: on the board it should be on, every column there */
  function readRow(boardId, itemId, cols) {
    return mondayCall(READ_ROW, { i: [String(itemId)], c: cols, b: [String(boardId)] }).then(function (data) {
      var item = (data.items || [])[0];
      if (!item || String((item.board || {}).id) !== String(boardId)) { throw { code: "not_found", text: "" }; }
      var columns = {};
      ((((data.boards || [])[0]) || {}).columns || []).forEach(function (c) { if (c && c.id) { columns[c.id] = c; } });
      var missing = cols.filter(function (c) { return !columns[c]; });
      if (missing.length) { throw { code: "columns_changed", text: missing.join(", ") }; }
      return { name: item.name || "", cells: cellsOf(item.column_values), columns: columns };
    });
  }
  /* a status column's labels by index, as monday holds them now ({"4": "תשלום הוחזר"}) */
  function liveLabels(column) {
    try { var s = JSON.parse((column && column.settings_str) || "{}"); return (s && s.labels) || {}; } catch (e) { return {}; }
  }

  /* ---- what the person typed, and the plan: exactly what will be written ---- */
  function defaultDraft(r, w) {
    var f = (ROW_FIELD[w.column] || [])[0];
    return w.kind === "text" && f && r[f] ? String(r[f]) : "";
  }
  function cleanInput(w, raw) {
    var s = String(raw == null ? "" : raw).trim();
    if (w.kind === "number") {
      var t = s.replace(/[,\s₪]/g, "").replace(/^0+(?=[0-9])/, "");
      if (!/^[0-9]+(\.[0-9]{1,2})?$/.test(t) || !(Number(t) > 0)) {
        return { error: w.title + ": סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה." };
      }
      return { value: t };
    }
    if (CONTROL.test(s) || s.length > TEXT_MAX) { return { error: w.title + ": עד " + TEXT_MAX + " תווים, בשורה אחת." }; }
    if (!s && w.required) { return { error: w.title + ": חסר." }; }
    return { value: s };
  }
  function sameValue(kind, v, text) {
    return kind === "number" ? text !== "" && Number(text) === Number(v) : String(v) === String(text);
  }
  function planAction(a, live, typed, today, untouched) {
    var steps = [], lines = [], problems = [];
    a.steps.forEach(function (step, si) {
      var values = {};
      step.forEach(function (w) {
        var cell = live.cells[w.column] || { text: "", index: null, label: "" };
        var line = { step: si + 1, title: w.title, kind: w.kind, now: cell };
        if (w.kind === "status") {
          if (liveLabels(live.columns[w.column])[w.index] !== w.label) { problems.push(w); return; }
          if (cell.index === String(w.index)) { line.same = true; lines.push(line); return; }
          values[w.column] = { index: Number(w.index) };
          line.to = w.label;
        } else if (w.kind === "date") {
          if (w.if_empty && cell.text) { line.kept = true; lines.push(line); return; }
          values[w.column] = { date: today };
          line.to = today;
        } else {
          var v = typed[w.column];
          if (v === undefined || (w.skip_empty && v === "")) { return; }
          if (sameValue(w.kind, v, cell.text)) { line.same = true; lines.push(line); return; }
          /* a box she did not touch holds the reading's value: when monday holds another one now, that is a change made
             since, never written over - "חויב" wrote an old reference back (review 11.11). Typed, it is hers. */
          if (untouched && untouched[w.column]) { line.kept = true; lines.push(line); return; }
          values[w.column] = v;
          line.to = v;
        }
        lines.push(line);
      });
      if (Object.keys(values).length) { steps.push(values); }
    });
    return { steps: steps, lines: lines, problems: problems };
  }
  function shownValue(kind, v) {
    if (v === "" || v == null) { return "ריק"; }
    if (kind === "number") { return amountText(v); }
    if (kind === "date") { return d(v); }
    return "\"" + v + "\"";
  }
  function shownCell(kind, cell) {
    if (kind === "status") { return cell.index == null ? "ריק" : cell.label ? "\"" + cell.label + "\"" : "תווית בלי שם"; }
    return shownValue(kind, cell.text);
  }
  function planLine(l) {
    var now = shownCell(l.kind, l.now);
    return "<li><b>" + esc(l.title) + "</b>: " + (l.same ? "כבר " + esc(now) + ", לא נכתב" : l.kept ? "כבר " + esc(now) + ", לא נכתב"
      : "עכשיו " + esc(now) + ", ייכתב " + esc(shownValue(l.kind === "status" ? "text" : l.kind, l.to))) + "</li>";
  }

  /* ---- the steps: each one mutation, checked against monday's answer before the next ---- */
  function wrongColumns(boardId, values, after) {
    var kinds = WRITABLE[String(boardId)] || {};
    return Object.keys(values).filter(function (c) {
      var v = values[c], a = after[c] || { text: "", index: null }, kind = kindOf(kinds[c]);
      if (kind === "status") { return a.index !== String(v.index); }
      if (kind === "date") { return a.text.slice(0, 10) !== v.date; }
      if (kind === "number") { return !sameValue("number", v, a.text); }
      return a.text !== v;
    });
  }
  function runSteps(boardId, itemId, steps) {
    var landed = [], cells = {};
    return steps.reduce(function (p, values) {
      return p.then(function () {
        return mondayWrite(boardId, itemId, values).then(function (after) {
          var wrong = wrongColumns(boardId, values, after);
          Object.keys(values).forEach(function (c) { if (after[c]) { cells[c] = after[c]; } });
          if (wrong.length) { throw { code: "unverified", text: wrong.join(", ") }; }
          landed.push(values);
        });
      });
    }, Promise.resolve()).then(function () { return cells; }, function (e) {
      e = e || {};
      e.landed = landed;
      e.cells = cells;
      throw e;
    });
  }
  /* the written values on the row at once, marked, until a reading shows them (WRITTEN). The values the row held before
     the write are kept too: a reading that still shows THEM has not caught up yet (monday's filter may lag behind a write)
     and is covered; a reading that shows a third value saw a later change, and is the truth */
  function rowFields(cells, cols) {
    var out = {};
    cols.forEach(function (c) {
      var f = ROW_FIELD[c], cell = cells[c] || { text: "", index: null, label: "" };
      if (!f) { return; }
      if (f.length === 2) { out[f[0]] = cell.index == null ? "" : cell.label; out[f[1]] = cell.index == null ? "" : cell.index; }
      else { out[f[0]] = cell.text; }
    });
    return out;
  }
  function noteWritten(rk, cells, before) {
    var cols = Object.keys(cells), fields = rowFields(cells, cols);
    if (!Object.keys(fields).length) { return; }
    WRITTEN[rk] = { fields: fields, before: rowFields((before && before.cells) || {}, cols), until: Date.now() + WRITTEN_FOR_MS };
    applyWritten();
  }
  function sameField(f, a, b) {
    if (NUMERIC_FIELD[f]) { return (a == null || a === "") ? (b == null || b === "") : Number(a) === Number(b); }
    return String(a == null ? "" : a) === String(b == null ? "" : b);
  }
  function applyWritten() {
    var now = Date.now();
    Object.keys(WRITTEN).forEach(function (rk) {
      var w = WRITTEN[rk], row = rowByKey(rk), keys = Object.keys(w.fields);
      if (!row || now > w.until) { delete WRITTEN[rk]; return; }
      var caught = keys.every(function (f) { return sameField(f, row[f], w.fields[f]); });
      var behind = keys.some(function (f) { return !sameField(f, row[f], w.fields[f]) && sameField(f, row[f], w.before[f]); });
      if (caught || !behind) { delete WRITTEN[rk]; return; }
      /* column by column: one still behind is covered, one that shows a third value saw a later change and stays the
         truth (review 11.11: a status behind covered a "סכום החזר" changed after the write) */
      keys.forEach(function (f) { if (!sameField(f, row[f], w.fields[f]) && sameField(f, row[f], w.before[f])) { row[f] = w.fields[f]; } });
      row.written = true;
    });
  }
  function settled() { return !Object.keys(WRITTEN).length; }
  /* After a write, a reading that STARTED after it: the server reads only what changed (about a second). monday's filter
     may lag behind a write by a moment, so while the row is not shown as written the page asks again - twice at most,
     each after the server's gap. */
  function refreshAfterWrite(tries) {
    Promise.resolve(pending).catch(function () {}).then(function () {
      if (pending) { refreshAfterWrite(tries); return; }
      if (BODY) { BODY.refreshing = true; }
      redraw();
      pending = deps.post("/api/desk/refresh").then(function (next) {
        takeNewer(next);
        redraw();
        if (!settled() && tries < 2) { later(function () { refreshAfterWrite(tries + 1); }, 5000); }
      }, function (e) {
        if (BODY) { BODY.refreshing = false; }
        redraw();
        if (e && e.error === "rate_limited" && tries < 2) {
          notBefore(e);
          later(function () { refreshAfterWrite(tries + 1); }, (num(e.retryAfter) || 5) * 1000);
        }
      }).then(function () { pending = null; });
    });
  }

  /* ---- the action's life on the screen ---- */
  function busyAct() { return !!ACT && (ACT.phase === "reading" || ACT.phase === "writing"); }
  function errorWords(e) {
    var why = WRITE_ERRORS[(e && e.code) || "monday"] || WRITE_ERRORS.monday;
    var landed = (e && e.landed) || [], titles = [];
    landed.forEach(function (values) { Object.keys(values).forEach(function (c) { titles.push(titleOf(c)); }); });
    /* a write monday answered with another value: something may be in the row, so never "nothing was written" */
    if (e && e.code === "unverified") { return (titles.length ? "נכתב " + titles.join(", ") + ". " : "") + why + "."; }
    /* a write sent and not answered in time (runSteps marks what it sent with `landed`): whether it landed is not known */
    if (e && e.code === "timeout" && e.landed) {
      return (titles.length ? "נכתב " + titles.join(", ") + ". " : "") +
        "מאנדיי לא ענתה על הכתיבה בזמן, ולכן לא ידוע אם נכתבה; לבדוק את השורה במאנדיי.";
    }
    if (!titles.length) { return why + ". לא נכתב כלום."; }
    return "נכתב רק " + titles.join(", ") + ", ושאר הפעולה לא: " + why + ".";
  }
  function titleOf(column) {
    var t = "";
    ["b2c", "b2b"].forEach(function (tab) {
      (((VIEW[tab] || {}).actions || {}).list || []).forEach(function (a) {
        stepWrites(a).forEach(function (w) { if (w.column === column) { t = w.title; } });
      });
    });
    return t || column;
  }
  function failAct(act, e) {
    if (e && e.code === "no_scope") { CAN = { state: "no", why: "no_scope" }; }
    if (e && e.cells && Object.keys(e.cells).length) { noteWritten(act.key, e.cells, act.live); }
    use("act", { action: act.action, phase: "failed", code: useWord(e && e.code) || "monday" });
    act.phase = "failed";
    act.error = errorWords(e);
    act.detail = (e && e.text) || "";
    redraw();
    flash(act.error);
  }
  /* a partial refund that is not smaller than its "סכום לזיכוי" is "תשלום הוחזר" or a typo: said in the plan, not refused */
  function planWarning(r, a, typed) {
    if (a.key !== "refund_partial") { return ""; }
    var x = agorot(typed[(stepWrites(a).filter(function (w) { return w.input; })[0] || {}).column]), credit = agorot(r.credit_approved);
    var full = (stepWrites(rowActions(r).filter(function (o) { return o.key === "refund_done"; })[0] || { steps: [] })
      .filter(function (w) { return w.kind === "status"; })[0] || {}).label;
    return x !== null && credit && x >= credit ? "הסכום אינו קטן מסכום לזיכוי (" + amountText(r.credit_approved) + ")" +
      (full ? ". אם כל סכום לזיכוי הוחזר, מסמנים \"" + full + "\"." : ".") : "";
  }
  function changedSince(before, now, cols) {
    return cols.some(function (c) {
      var x = before.cells[c] || {}, y = now.cells[c] || {};
      return (x.text || "") !== (y.text || "") || (x.index == null ? null : x.index) !== (y.index == null ? null : y.index);
    });
  }
  function settlePlan(act, live) {
    var plan = planAction(act.a, live, act.typed, israelToday(), act.untouched);
    act.live = live;
    act.plan = plan;
    if (plan.problems.length) {
      failAct(act, { code: "label_moved", text: plan.problems.map(function (w) { return w.title + " " + w.label; }).join(", ") });
      return false;
    }
    act.phase = plan.steps.length ? "confirm" : "nothing";
    /* the plan on the screen ("shown"), or nothing to write since monday already holds it */
    use("act", { action: act.action, phase: plan.steps.length ? "shown" : "nothing" });
    redraw();
    /* the plan is read out where it appears: focus on the box itself, never on "כתיבה" (a key held down must not write) */
    var box = root && root.querySelector ? root.querySelector(".fin-act-plan") : null;
    if (box && box.focus) { box.focus(); }
    return true;
  }
  function startAction(rk, key) {
    var r = rowByKey(rk);
    var a = r && rowActions(r).filter(function (x) { return x.key === key; })[0];
    if (!a || CAN.state === "no" || busyAct()) { return; }
    var typed = {}, untouched = {}, bad = "";
    stepWrites(a).forEach(function (w) {
      if (!w.input) { return; }
      var mine = Object.prototype.hasOwnProperty.call(DRAFT, rk + ":" + w.column);
      var raw = mine ? DRAFT[rk + ":" + w.column] : defaultDraft(r, w);
      if (!mine) { untouched[w.column] = true; }
      var got = cleanInput(w, raw);
      if (got.error) { bad = bad || got.error; } else { typed[w.column] = got.value; }
    });
    var act = ACT = { key: rk, action: key, a: a, button: a.button, after: a.after || "", phase: bad ? "invalid" : "reading",
      error: bad, typed: typed, untouched: untouched, warn: bad ? "" : planWarning(r, a, typed), notice: "" };
    use("act", { action: key, phase: bad ? "invalid" : "start" });
    redraw();
    if (bad) { return; }
    act.cols = stepWrites(a).map(function (w) { return w.column; });
    readRow(boardIdOf(r), r.id, act.cols).then(function (live) {
      if (ACT === act) { settlePlan(act, live); }        /* not when closed meanwhile */
    }, function (e) { if (ACT === act) { failAct(act, e); } });
  }
  function confirmAction() {
    var act = ACT;
    if (!act || act.phase !== "confirm") { return; }
    var r = rowByKey(act.key);
    if (!r) { ACT = null; redraw(); return; }
    act.phase = "writing";
    use("act", { action: act.action, phase: "confirm" });
    redraw();
    /* the row again, as the person, right before the write: a plan shown a while ago is written only if the row still
       holds what it showed; otherwise it is planned again from what is there now, and shown */
    readRow(boardIdOf(r), r.id, act.cols).then(function (live) {
      if (ACT !== act) { return null; }
      /* the cells, and also the labels the indexes stand for: a label renamed meanwhile went out under its old index, its
         new name on the row (review 11.11). A plan made now that is not the plan shown is shown, never written. */
      var now = planAction(act.a, live, act.typed, israelToday(), act.untouched), moved = changedSince(act.live, live, act.cols);
      if (moved || now.problems.length || JSON.stringify(now.steps) !== JSON.stringify(act.plan.steps)) {
        act.notice = moved ? "השורה השתנתה במאנדיי מאז שהתוכנית הוצגה: זו התוכנית לפי מה שיש בה עכשיו."
          : "מה שהפעולה כותבת השתנה מאז שהתוכנית הוצגה: זו התוכנית לפי מה שיש עכשיו.";
        settlePlan(act, live);
        return null;
      }
      return runSteps(boardIdOf(r), r.id, act.plan.steps);
    }).then(function (cells) {
      if (cells === null) { return; }
      /* what was typed is written: the boxes show the row's own value again */
      act.cols.forEach(function (c) { delete DRAFT[act.key + ":" + c]; });
      noteWritten(act.key, cells, act.live);
      use("act", { action: act.action, phase: "done" });
      act.phase = "done";
      redraw();
      flash("נכתב במאנדיי בשמך: " + act.plan.lines.filter(function (l) { return !l.same && !l.kept; }).map(function (l) {
        return l.title + " " + shownValue(l.kind === "status" ? "text" : l.kind, l.to);
      }).join(", ") + ". המסך מתעדכן.");
      refreshAfterWrite(0);
    }, function (e) {
      if (ACT !== act && !(e && e.landed)) { return; }
      failAct(act, e);
      /* something may be in monday that the screen does not show: what landed, a write monday kept otherwise, or one
         monday did not answer in time */
      if (e && ((e.landed && e.landed.length) || e.code === "unverified" || (e.code === "timeout" && e.landed))) {
        refreshAfterWrite(0);
      }
    });
  }
  function actStatus(act) {
    var close = "<button type=\"button\" class=\"fin-linkbtn\" data-act-close=\"1\">סגירה</button>";
    switch (act.phase) {
      case "invalid": return "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(act.error) + "</p>";
      case "reading": return "<p class=\"fin-act-msg\" role=\"status\">קורא את השורה במאנדיי...</p>";
      case "nothing": return (act.notice ? "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(act.notice) + "</p>" : "") +
        "<p class=\"fin-act-msg\" role=\"status\">הכל כבר כך במאנדיי: אין מה לכתוב. " + close + "</p>";
      case "writing": return "<p class=\"fin-act-msg\" role=\"status\">כותב במאנדיי...</p>";
      /* a status written moves the row to its state once the server reads it; a reference typed does not move it */
      case "done": return "<p class=\"fin-act-msg ok\" role=\"status\">נכתב במאנדיי בשמך. המסך מתעדכן" +
        (act.plan.lines.some(function (l) { return l.kind === "status" && !l.same; }) ? ", והשורה תעבור לשיבוץ שלה. " : ". ") +
        close + "</p>";
      case "failed": return "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(act.error) + " " + close + "</p>" +
        (act.detail ? "<p class=\"fin-subtle fin-act-detail\" dir=\"auto\">מאנדיי: " + esc(act.detail) + "</p>" : "");
      case "confirm": {
        var p = act.plan, two = p.steps.length > 1;
        return "<div class=\"fin-act-plan\" role=\"group\" aria-label=\"מה ייכתב\" tabindex=\"-1\">" +
          (act.notice ? "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(act.notice) + "</p>" : "") +
          "<p>ייכתב במאנדיי בשורה " + esc("\"" + act.live.name + "\"") +
          ", בשמך" + (two ? ", בשני שלבים" : "") + ":</p><ul>" + p.lines.map(planLine).join("") + "</ul>" +
          (act.warn ? "<p class=\"fin-badtext\">" + esc(act.warn) + "</p>" : "") +
          (act.after ? "<p class=\"fin-subtle\">" + esc(act.after) + "</p>" : "") +
          "<div class=\"fin-act-go\"><button type=\"button\" class=\"fin-btn fin-primary\" data-act-go=\"1\">כתיבה</button>" +
          "<button type=\"button\" class=\"fin-btn\" data-act-close=\"1\">ביטול</button></div></div>";
      }
      default: return "";
    }
  }
  function actionControl(r, a, off) {
    var rk = rowKey(r);
    var fields = stepWrites(a).filter(function (w) { return w.input; }).map(function (w) {
      var id = "fin-act-" + r.board + "-" + r.id + "-" + w.column;
      var k = rk + ":" + w.column;
      var val = Object.prototype.hasOwnProperty.call(DRAFT, k) ? DRAFT[k] : defaultDraft(r, w);
      return "<label class=\"fin-act-field\" for=\"" + esc(id) + "\"><span>" + esc(w.title) +
        (!w.required && !a.field ? " <span class=\"fin-subtle\">(לא חובה)</span>" : "") + "</span><input id=\"" + esc(id) +
        "\" class=\"fin-input" + (w.kind === "number" ? " num" : "") + "\" type=\"text\" data-act-input=\"" + esc(k) + "\" " +
        (w.kind === "number" ? "inputmode=\"decimal\" dir=\"ltr\" maxlength=\"16\"" : "dir=\"auto\" maxlength=\"" + TEXT_MAX + "\"") +
        " value=\"" + esc(val) + "\"" + (off ? " disabled" : "") + "></label>";
    }).join("");
    return "<div class=\"fin-act\">" + fields + "<button type=\"button\" class=\"fin-btn\" data-act=\"" + esc(rk + ":" + a.key) + "\"" +
      (off ? " disabled" : "") + (CAN.state === "no" ? " title=\"" + esc(ABILITY[CAN.why] || ABILITY.no_scope) + "\"" : "") + ">" +
      esc(a.button) + "</button></div>";
  }
  /* a row's actions, at the top of its details: the controls, and the one action on the screen - its plan, its progress,
     its result */
  function actionsBox(r) {
    var list = rowActions(r);
    if (!list.length) { return ""; }
    var act = ACT && ACT.key === rowKey(r) ? ACT : null;
    var off = CAN.state === "no" || busyAct() || (!!act && act.phase === "confirm");
    return "<section class=\"fin-dx-block fin-acts\" aria-label=\"פעולות\"><h3 class=\"fin-dx-h\">פעולות <span class=\"fin-dx-name\">" +
      "נכתבות במאנדיי בשמך</span></h3>" + (CAN.state === "no" ? "<p class=\"fin-act-why\">" + esc(ABILITY[CAN.why] || ABILITY.no_scope) + "</p>" : "") +
      "<div class=\"fin-act-row\">" + list.map(function (a) { return actionControl(r, a, off); }).join("") + "</div>" +
      (act ? actStatus(act) : "") + "</section>";
  }
  function writtenMark(r) { return r.written ? " <span class=\"fin-written\">נכתב, מתעדכן</span>" : ""; }
  /* whether this person may write, from monday's context: read once at mount (an app version's new permission is approved
     in monday's own banner, which loads the view again) */
  function readAbility() {
    if (!deps.monday) { CAN = { state: "no", why: "outside" }; return Promise.resolve(); }
    var asked = new Promise(function (resolve, reject) {
      setTimeout(function () { reject(new Error("timeout")); }, 3000);
      Promise.resolve().then(function () { return deps.monday.get("context"); }).then(resolve, reject);
    });
    return asked.then(function (res) {
      var c = (res && res.data) || {}, p = c.permissions || null, u = c.user || {};
      function has(list, s) { return list.indexOf(s) !== -1; }
      if (u.isViewOnly === true) { CAN = { state: "no", why: "view_only" }; }
      else if (p && Array.isArray(p.approvedScopes)) {
        if (has(p.approvedScopes, "boards:write") && has(p.approvedScopes, "boards:read")) { CAN = { state: "yes", why: "" }; }
        else if (Array.isArray(p.requiredScopes) && has(p.requiredScopes, "boards:write")) { CAN = { state: "no", why: "approve" }; }
        else { CAN = { state: "no", why: "no_scope" }; }
      } else { CAN = { state: "unknown", why: "" }; }
    }, function () { CAN = { state: "unknown", why: "" }; }).then(function () { if (VIEW) { redraw(); } });
  }

  /* ---------- how the desk is used (16.11) ----------
     Nadav: "נתוני שימוש שלה שם, כדי שנוכל לשפר את זה עם הזמן". What the reader does here - a tab, a tile, a period, a
     search, a row opened, an action, a file, the legend, an ID number read or copied - goes to POST /api/usage in small
     batches and is kept in the service's own Postgres (desk_events; app/usage.py holds the closed list of kinds and
     fields, and refuses anything else). An event is its kind, the page's age, the tab and the list's state, and its own
     closed words and counts: NEVER a name, an amount, the search's text, an ID number or an item id - a search goes as
     its shape (how many characters, which kind, how many rows with the filters and without). Best effort: nothing waits
     for it. D2: this service has no POST /api/usage yet, so the first batch that fails - for any reason - stops the
     sending for the visit, quietly: never a word on the screen. */
  var USE_MAX = 20, USE_FLUSH_MS = 30000, USE_SEARCH_MS = 1500;
  var USE = { q: [], timer: null, session: "", at: 0, off: false, opened: false, search: null, lastQuery: "" };
  function useSession() {
    var s = "";
    try {
      var a = new Uint8Array(16);
      window.crypto.getRandomValues(a);
      s = [].map.call(a, function (b) { return (b % 36).toString(36); }).join("");
    } catch (e) { s = ""; }
    return s || (Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^a-z0-9]/g, "").slice(0, 24);
  }
  /* an error as a usage word: lowercase Latin, digits and "_", starting with a letter (app/usage.py WORD) */
  function useWord(x) { return String(x || "").toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "").slice(0, 40); }
  function use(kind, d) {
    if (!deps || !deps.post || USE.off || !USE.session) { return; }
    var ev = { k: kind, t: Math.max(0, Math.round(Date.now() - USE.at)), tab: state.tab || "",
      st: (state.tab === "b2b" ? state.b2.state : state.sc.state) || "" };
    if (d) {
      ev.d = {};
      Object.keys(d).forEach(function (k) { if (d[k] !== undefined && d[k] !== null) { ev.d[k] = d[k]; } });
    }
    USE.q.push(ev);
    if (USE.q.length >= USE_MAX) { flushUse(false); return; }
    if (!USE.timer) {
      USE.timer = setTimeout(function () { USE.timer = null; flushUse(false); }, deps.useFlushMs != null ? deps.useFlushMs : USE_FLUSH_MS);
      if (USE.timer && USE.timer.unref) { USE.timer.unref(); }    /* node (the tests): a waiting batch never holds the process */
    }
  }
  /* `leaving`: the page is going out of sight, so the request is asked to outlive it (fetch keepalive, in desk.js) */
  function flushUse(leaving) {
    if (USE.timer) { clearTimeout(USE.timer); USE.timer = null; }
    if (!USE.q.length || USE.off || !deps || !deps.post) { return; }
    var events = USE.q.splice(0, USE_MAX);
    Promise.resolve().then(function () {
      return deps.post("/api/usage", { desk: "finance", session: USE.session, events: events }, !!leaving);
    }).catch(function () {
      /* nowhere to keep them, a server without the route (D2: this one, for now), or any other failure: no more for this
         visit, and nothing said */
      USE.off = true;
    });
  }
  /* a search as its shape, never its text: digits only (an amount, a reference, an ID number - its length tells), an
     amount with agorot, Hebrew, Latin, or a mix */
  function queryShape(q) {
    var s = String(q || "").trim(), t = s.replace(/[,₪\s]/g, "");
    var he = /[\u0590-\u05FF]/.test(s), la = /[A-Za-z]/.test(s), di = /[0-9]/.test(s);
    var kind = /^[0-9]+$/.test(t) ? "digits" : /^[0-9]+\.[0-9]{1,2}$/.test(t) ? "amount"
      : he && !la && !di ? "hebrew" : la && !he && !di ? "latin" : he || la ? "mixed" : "other";
    return { len: Math.min(s.length, 200), kind: kind };
  }
  /* how many rows the search finds in the list on the screen, and without its filters ("חיפוש ללא סינון") */
  function searchCounts() {
    var q = state.query;
    if (state.tab === "b2b") {
      if (!VIEW.b2b) { return { results: 0, unfiltered: 0 }; }
      if (state.b2.view === "customers") { var n = customersShown(VIEW.b2b).length; return { results: n, unfiltered: n }; }
      return { results: b2Filter(ROWS.b2, VIEW.b2b, state.b2.state, state.b2.customer, q).length,
        unfiltered: b2Filter(ROWS.b2, VIEW.b2b, "", "", q).length };
    }
    if (!VIEW.b2c) { return { results: 0, unfiltered: 0 }; }
    return { results: scRows().length, unfiltered: scFilter(ROWS.sc, VIEW.b2c, "", "", q).length };
  }
  /* a search is counted once it settles (USE_SEARCH_MS without a key), not per key pressed */
  function searchSettling() {
    clearTimeout(USE.search);
    USE.search = setTimeout(function () {
      var q = state.query.trim();
      if (!q || q === USE.lastQuery || !VIEW) { USE.lastQuery = q; return; }
      USE.lastQuery = q;
      var shape = queryShape(q), counts = searchCounts();
      use("search", { len: shape.len, kind: shape.kind, results: Math.min(counts.results, 100000), unfiltered: Math.min(counts.unfiltered, 100000) });
    }, deps && deps.useSearchMs != null ? deps.useSearchMs : USE_SEARCH_MS);
    if (USE.search && USE.search.unref) { USE.search.unref(); }
  }

  /* ---------- what is read live in the browser, as the person (16.11; D2) ----------
     The student's ID number, in a payment's details (16.11): Chava asked for it nearer to hand than the Excel file (a
     receipt in Hashavshevet needs it), and has a CRM seat since 16.11. It is read live IN THE BROWSER, AS THE PERSON, when
     a payment's details open: monday.api() of the SDK, through mondayCall like every read of the page, so monday's own
     permissions decide - without access to the Leads board monday answers nothing, and the cell says so. Never through
     this service's server: the server's answer carries no ID number (app/finance.py visible_part), the reading and the
     database hold none, and the page keeps it in its memory only - read again when a newer reading of the boards lands -
     never in storage and never in a usage event (USE). The rule is the Excel file's (app/finance.py with_id_numbers): the
     first of the payment's leads, in monday's order, that holds one.
     D2, the move: this service's server never reads an ID number, a bank account or a business number (app/monday/
     client.py NEVER_READ), so two more details are read here the same way, each only when it is shown: a refund to
     execute's bank account when its details open (the old server read it with every reading), and a company's ח.פ when
     its ledger shows the billing details. Each is kept in this page's memory only, by its row, and never sent anywhere:
     not to the server, not in a usage event, not to the console. */
  var ID_TITLE = "ת.ז";
  var LEADS_BOARD = "5089697337", ID_COLUMN = "text_mkzvem4", ID_LEADS_MAX = 10;
  /* Student Charge's bank account, as the old server read it for "החזר לביצוע" (app/finance.py BANK_FIELDS) */
  var BANK_COLUMNS = { bank_holder: "text_mm8cxm9y", bank: "text_mm8cv3v3", bank_branch: "text_mm8cgc0d", bank_account: "text_mm8ckgh8" };
  /* Accounts' "ח.פ" (app/account_writer.py FIELDS) */
  var ACCOUNTS_BOARD = "5089697343", VAT_COLUMN = "text_mm3y3gm7";
  var READ_CELLS = "query ($i: [ID!], $c: [String!], $b: [ID!]) { items(ids: $i) { id board { id } column_values(ids: $c) { id text } } " +
    "boards(ids: $b) { id columns(ids: $c) { id } } }";
  /* by payment id, by payment id, by account id: { phase: "reading" | "ok" | "failed", value, code, stale, shown } - memory
     only */
  var IDS = {}, BANKS = {}, VATS = {};
  var ID_ERRORS = {
    /* D2: no longer "(ת.ז נמצאת גם בקובץ האקסל)" - this service's server never reads an ID number, so its file has none */
    denied: "אין לך גישה ללוח הלידים במאנדיי", no_scope: "לאפליקציה אין הרשאה לקרוא את לוח הלידים",
    columns_changed: "העמודה ת.ז לא נמצאה בלוח הלידים; נדב צריך לבדוק", not_found: "הליד לא נמצא בלוח הלידים",
    rate_limited: "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה", timeout: "מאנדיי לא ענתה בזמן", monday: "לא נקראה ממאנדיי"
  };
  var BANK_ERRORS = {
    denied: "אין לך גישה לפרטי החשבון במאנדיי", no_scope: "לאפליקציה אין הרשאה לקרוא את לוח Student Charge",
    columns_changed: "עמודה של פרטי החשבון לא נמצאה בלוח Student Charge; נדב צריך לבדוק",
    not_found: "התקבול לא נמצא בלוח Student Charge",
    rate_limited: "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה", timeout: "מאנדיי לא ענתה בזמן", monday: "לא נקראו ממאנדיי"
  };
  var VAT_ERRORS = {
    denied: "אין לך גישה ללוח החברות במאנדיי", no_scope: "לאפליקציה אין הרשאה לקרוא את לוח החברות",
    columns_changed: "העמודה ח.פ לא נמצאה בלוח החברות; נדב צריך לבדוק", not_found: "החברה לא נמצאה בלוח החברות",
    rate_limited: "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה", timeout: "מאנדיי לא ענתה בזמן", monday: "לא נקרא ממאנדיי"
  };
  /* the cells of `ids` on `board`, read as the person: {item id: {column: text}} for the items monday returned on that
     board. A board that answers nothing is "denied" (the Leads board without a CRM seat answers nothing), a column it
     lacks "columns_changed", no item at all "not_found"; monday's own refusal comes by mondayErr's code */
  function readCells(board, ids, cols) {
    return mondayCall(READ_CELLS, { i: ids, c: cols, b: [board] }).then(function (data) {
      var b = (data.boards || []).filter(function (x) { return x && String(x.id) === board; })[0];
      if (!b) { throw { code: "denied" }; }
      var have = (b.columns || []).map(function (c) { return c && c.id; });
      if (cols.some(function (c) { return have.indexOf(c) === -1; })) { throw { code: "columns_changed" }; }
      var got = Object.create(null);
      (data.items || []).forEach(function (it) {
        if (!it || String((it.board || {}).id) !== board) { return; }
        var cells = Object.create(null), seen = {};
        cols.forEach(function (c) { cells[c] = ""; });
        (it.column_values || []).forEach(function (c) {
          if (!c || cols.indexOf(c.id) === -1 || seen[c.id]) { return; }
          seen[c.id] = 1;
          cells[c.id] = String(c.text == null ? "" : c.text).trim();
        });
        got[String(it.id)] = cells;
      });
      if (!Object.keys(got).length) { throw { code: "not_found" }; }
      return got;
    });
  }
  /* the number, or "" when none of the leads holds one; a refusal by code (ID_ERRORS) */
  function readIdNumber(r) {
    var ids = (r.lead_ids || []).slice(0, ID_LEADS_MAX).map(String);
    return readCells(LEADS_BOARD, ids, [ID_COLUMN]).then(function (got) {
      return ids.map(function (x) { return (got[x] || {})[ID_COLUMN] || ""; }).filter(Boolean)[0] || "";
    });
  }
  /* a payment's bank account, its four cells by the names bankText reads; a refusal by code (BANK_ERRORS) */
  function readBank(r) {
    var keys = Object.keys(BANK_COLUMNS);
    return readCells(BOARD.sc, [String(r.id)], keys.map(function (k) { return BANK_COLUMNS[k]; })).then(function (got) {
      var cells = got[String(r.id)];
      if (!cells) { throw { code: "not_found" }; }
      var out = {};
      keys.forEach(function (k) { out[k] = cells[BANK_COLUMNS[k]]; });
      return out;
    });
  }
  /* a company's ח.פ, or ""; a refusal by code (VAT_ERRORS) */
  function readVat(aid) {
    return readCells(ACCOUNTS_BOARD, [String(aid)], [VAT_COLUMN]).then(function (got) {
      var cells = got[String(aid)];
      if (!cells) { throw { code: "not_found" }; }
      return cells[VAT_COLUMN];
    });
  }
  /* a detail's entry in its store: read when it first draws, and again once a newer reading of the boards has landed (a
     return to the view, "רענון": a value corrected in monday then shows) or on "לנסות שוב". While the next one is on its
     way the last value stays on the screen. A read is asked only by a draw that finds no entry or a stale one, and the
     redraw its answer brings finds a fresh one: no read asks for another. `said` hears the result (the ID number's usage
     words); the value itself goes nowhere */
  function liveEntry(store, key, read, said) {
    var e = store[key];
    if (!e || (e.stale && e.phase !== "reading")) {
      if (!deps || !deps.monday) { return { phase: "outside" }; }
      e = readInto(store, key, read, said, e && e.phase === "ok" ? e : null);
    }
    return e.phase === "reading" && e.shown ? e.shown : e;
  }
  function readInto(store, key, read, said, shown) {
    var mine = store[key] = { phase: "reading", shown: shown };
    read().then(function (v) { mine.phase = "ok"; mine.value = v; },
      function (err) { mine.phase = "failed"; mine.code = (err && err.code) || "monday"; })
      .then(function () {
        mine.shown = null;
        if (said) { said(mine); }
        if (store[key] === mine && VIEW) { redraw(); }
      });
    return mine;
  }
  /* a newer reading of the boards: every value held is read again at its next draw, the last one shown meanwhile */
  function liveStale() {
    [IDS, BANKS, VATS].forEach(function (store) { Object.keys(store).forEach(function (k) { store[k].stale = true; }); });
  }
  function liveFailed(words, code, attr, key) {
    return "<span class=\"fin-badtext\">" + esc(words[code] || words.monday) + "</span> <button type=\"button\" class=\"fin-linkbtn\" " +
      attr + "=\"" + esc(key) + "\">לנסות שוב</button>";
  }
  function idSaid(e) {
    if (e.phase === "ok") { use(e.value ? "id_shown" : "id_empty"); }
    else { use("id_failed", { code: useWord(e.code) || "monday" }); }
  }
  function idCell(r) {
    if (!(r.lead_ids || []).length) { return NOT_SET; }
    var e = liveEntry(IDS, r.id, function () { return readIdNumber(r); }, idSaid);
    if (e.phase === "outside") { return subtle("מוצגת רק כשהתצוגה פתוחה מתוך מאנדיי"); }
    if (e.phase === "reading") { return subtle("קורא ממאנדיי..."); }
    if (e.phase === "failed") { return liveFailed(ID_ERRORS, e.code, "data-id-retry", r.id); }
    if (!e.value) { return subtle("אין בליד"); }
    return "<bdi dir=\"ltr\" class=\"fin-idnum\">" + esc(e.value) + "</bdi> <button type=\"button\" class=\"fin-iconbtn\" data-id-copy=\"" +
      esc(r.id) + "\" aria-label=\"" + COPY_LABEL + "\" title=\"" + COPY_LABEL + "\">" + COPY_ICON + "</button>";
  }
  /* a refund to execute's bank account in its details, as the old page showed it (bankText, the holder last), or "-" when
     monday holds none of its four cells */
  function bankCell(r) {
    var e = liveEntry(BANKS, r.id, function () { return readBank(r); });
    if (e.phase === "outside") { return subtle("מוצגים רק כשהתצוגה פתוחה מתוך מאנדיי"); }
    if (e.phase === "reading") { return subtle("קורא ממאנדיי..."); }
    if (e.phase === "failed") { return liveFailed(BANK_ERRORS, e.code, "data-bank-retry", r.id); }
    return bankText(e.value, true) || NOT_SET;
  }
  /* a company's ח.פ in its billing details, as the old page showed it (left to right), or "-" when monday holds none */
  function vatCell(aid) {
    var e = liveEntry(VATS, aid, function () { return readVat(aid); });
    if (e.phase === "outside") { return subtle("מוצג רק כשהתצוגה פתוחה מתוך מאנדיי"); }
    if (e.phase === "reading") { return subtle("קורא ממאנדיי..."); }
    if (e.phase === "failed") { return liveFailed(VAT_ERRORS, e.code, "data-vat-retry", aid); }
    return e.value ? ltr(e.value) : NOT_SET;
  }
  /* the copy button is an icon, no word (Nadav 16.11): the clipboard's usual two sheets, in the row's open icon's style;
     its name is said to a screen reader and on hover */
  var COPY_LABEL = "העתקת ת.ז";
  var COPY_ICON = "<svg viewBox=\"0 0 16 16\" width=\"16\" height=\"16\" aria-hidden=\"true\" focusable=\"false\">" +
    "<rect x=\"5.5\" y=\"5.5\" width=\"8\" height=\"8\" rx=\"1.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\"/>" +
    "<path d=\"M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" " +
    "stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg>";
  /* the number to the clipboard: the browser's own way, or the old one inside an iframe that does not allow it */
  function copyId(id) {
    var e = IDS[id], text = e && e.phase === "ok" ? e.value : "";
    if (!text) { return; }
    function said(ok) { use("id_copied", { code: ok ? "" : "failed" }); flash(ok ? "ת.ז הועתקה." : "לא הצלחנו להעתיק: לסמן את המספר ולהעתיק ידנית."); }
    function old() {
      var ok = false, t = document.createElement("textarea");
      t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t);
      try { t.select(); ok = !!document.execCommand("copy"); } catch (x) { ok = false; }
      t.remove();
      said(ok);
    }
    var clip = typeof navigator !== "undefined" && navigator.clipboard;
    if (clip && clip.writeText) { Promise.resolve().then(function () { return clip.writeText(text); }).then(function () { said(true); }, old); }
    else { old(); }
  }

  /* ---------- the customer's billing details, in the ledger (round 3, slice 3; D2) ----------
     Four columns of Accounts, under the server's form contract (view.b2b.account_form): its fields and their monday
     titles, in its order. In analiza-metrics the server also wrote them (its account route): this service has no
     such route and never writes Accounts from the browser's request, so the details are shown READ ONLY here, with one
     line saying their edit comes back - from the browser, as the person, like every other write of the page. The ח.פ is
     read live as the person when the details show (vatCell): the server never reads it. */
  var BILL_LATER = "עריכת פרטי החיוב תחזור בקרוב, מהדפדפן";
  function billForm() { return (VIEW.b2b && VIEW.b2b.account_form) || null; }
  function billingPanel(aid, mine) {
    var form = billForm();
    if (!form || !mine || !mine.billing) { return ""; }
    var cur = mine.billing;
    var head = "<div class=\"fin-billing-head\"><h2>פרטי חיוב</h2><span class=\"fin-subtle\">של החברה, בלוח החברות</span></div>";
    var body = facts(form.fields.map(function (f) {
      var v = cur[f.key];
      /* whatever the answer carries under this key, the ח.פ shown is the one monday gives the person now */
      if (f.key === "vat_id") { return [f.title, vatCell(aid)]; }
      if (f.key === "terms") { return [f.title, v ? "<bdi>" + esc(v) + "</bdi>" : mine.terms_missing ? NO_TERMS : NOT_SET]; }
      return [f.title, v ? (f.key === "invoice_name" ? esc(v) : ltr(v)) : NOT_SET];
    })) + "<p class=\"fin-act-why\">" + esc(BILL_LATER) + "</p>";
    return "<section class=\"fin-panel fin-billing\" aria-label=\"פרטי חיוב\">" + head + body + "</section>";
  }
  /* "אין תנאי תשלום" beside a company that has an account: a way to its ledger, where its billing details show */
  function noTerms(aid) {
    var mine = aid && VIEW.b2b && VIEW.b2b.by_customer[aid];
    if (!mine || !mine.billing || !billForm()) { return NO_TERMS; }
    return "<button type=\"button\" class=\"fin-chip fin-chiplink\" data-terms=\"" + esc(aid) + "\" title=\"" +
      esc("פתיחת הכרטסת של החברה") + "\">" + esc(FLAG.terms_missing) + "</button>";
  }

  /* ---------- frame ---------- */
  /* the legend ("מקרא", round 3): the words this desk computes, from the server, for the tab on the screen. monday's own
     columns and labels have no entry - they are called by their monday names */
  function legendHtml() {
    var parts = (VIEW[state.tab] && VIEW[state.tab].legend) || [];
    return "<section class=\"fin-legend\" id=\"fin-legend\" aria-label=\"מקרא\"><div class=\"fin-legend-head\"><h2>מקרא</h2>" +
      "<span class=\"fin-subtle\">המילים שהמסך מחשב בעצמו, השיבוצים, הסימונים, ומאיפה באות עמודות של מאנדיי. כל השאר נקרא " +
      "כאן בשם שלו במאנדיי.</span></div>" + parts.map(function (p) {
        return "<h3 class=\"fin-legend-part\">" + esc(p.title) + "</h3><dl>" + p.items.map(function (x) {
          return "<div><dt>" + esc(x.term) + "</dt><dd>" + esc(x.text) + "</dd></div>";
        }).join("") + "</dl>";
      }).join("") + "</section>";
  }
  function overviewHtml() {
    var tabs = ["b2c", "b2b"].filter(function (g) { return VIEW.tabs[g]; });
    if (!VIEW.tabs[state.tab]) { state.tab = tabs[0]; }
    var body = state.tab === "b2c" ? b2cHtml() : b2bHtml();
    return "<div class=\"fin-tabbar\"><div class=\"fin-tabs\" role=\"tablist\">" + tabs.map(function (g) {
      return "<button class=\"fin-tab" + (state.tab === g ? " on" : "") + "\" type=\"button\" role=\"tab\" aria-selected=\"" + (state.tab === g) +
        "\" data-fintab=\"" + g + "\">" + GROUP_NAME[g] + "</button>";
    }).join("") + "</div><button class=\"fin-linkbtn fin-legendbtn\" type=\"button\" data-legend=\"1\" aria-controls=\"fin-legend\" aria-expanded=\"" +
      state.legend + "\">" + (state.legend ? "סגירת המקרא" : "מקרא") + "</button></div>" + (state.legend ? legendHtml() : "") + body;
  }
  function healthHtml() {
    var c = VIEW.checks || {}, n = VIEW.data_notes || {};
    var line;
    if (c.ok) {
      var read = [];
      if (VIEW.tabs.b2c) { read.push(NF.format(c.b2c_items_count) + " תקבולי הסטודנטים"); }
      if (VIEW.tabs.b2b) { read.push(NF.format(c.b2b_items_count) + " החיובים העסקיים"); }
      line = "נקראו כל " + read.join(" ו-") + ".";
    } else {
      line = "<b class=\"fin-badtext\">הקריאה לא שלמה, ולכן אין להסתמך על הסכומים. רענון יקרא שוב.</b>";
    }
    if (BODY && BODY.stale) { line += " <span class=\"fin-badtext\">הנתונים ישנים: הקריאות האחרונות נכשלו.</span>"; }
    else if (BODY && BODY.last_attempt && BODY.last_attempt.ok === false) {
      line += " <span class=\"fin-badtext\">הקריאה האחרונה נכשלה, ומוצגים הנתונים הקודמים.</span>";
    }
    Object.keys(LOUD).forEach(function (k) {
      if (n[k]) { line += "<div class=\"fin-badtext\">" + (n[k] === 1 ? NOTE[k][0] : NF.format(n[k]) + " " + NOTE[k][1]) + ".</div>"; }
    });
    var keys = Object.keys(NOTE).filter(function (k) { return n[k] && !LOUD[k]; });
    var more = keys.length ? " <button class=\"fin-linkbtn\" type=\"button\" data-notes=\"1\">" + (state.notes ? "פחות" : "פרטים על הנתונים") + "</button>" : "";
    var list = state.notes && keys.length ? "<ul>" + keys.map(function (k) {
      return "<li>" + (n[k] === 1 ? NOTE[k][0] : NF.format(n[k]) + " " + NOTE[k][1]) + "</li>";
    }).join("") + "</ul>" : "";
    return "<section class=\"fin-health\">" + line + more + list + "</section>";
  }
  function asOf() {
    var at = (BODY && BODY.taken_at) || VIEW.read_at;
    if (!at) { return ""; }
    return new Date(at).toLocaleString("he-IL", { timeZone: "Asia/Jerusalem", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  var KEEP = ["fintab", "bstate", "b2state", "b2view", "customer", "row", "export", "notes", "refresh", "legend",
    "actInput", "act", "terms", "idCopy"];
  function focusKey() {
    var a = document.activeElement;
    if (!a || a === root || !root.contains(a) || !a.dataset) { return ""; }
    var k = KEEP.filter(function (x) { return a.dataset[x] !== undefined; })[0];
    /* a dataset name in camelCase is a data- attribute in kebab-case */
    return k ? "[data-" + k.replace(/[A-Z]/g, function (m) { return "-" + m.toLowerCase(); }) + "=\"" +
      a.dataset[k].replace(/["\\]/g, "\\$&") + "\"]" : "";
  }
  /* which list a table shows: the same key after a redraw is the same list, so it keeps its own scroll */
  function listKey() {
    return JSON.stringify([state.tab, state.sc.state, state.sc.period, state.b2.state, state.b2.view, state.b2.customer, state.query]);
  }
  /* A table taller or wider than its box scrolls inside it (QA 2.8), and a redraw builds it anew: opening a row's details,
     or a newer reading landing, must not throw the reader back to the box's top. Each list keeps its scroll across a
     redraw; another list starts at its top. */
  function tableScrolls() {
    var out = {};
    [].forEach.call(root.querySelectorAll ? root.querySelectorAll(".fin-tw[data-list]") : [], function (el) {
      out[el.getAttribute("data-list")] = [el.scrollTop, el.scrollLeft];
    });
    return out;
  }
  function render() {
    if (!VIEW) { noReading(); return; }        /* D2: nothing read on the server yet */
    var keep = focusKey(), scrolls = tableScrolls(), a = document.activeElement;
    /* an input being typed into keeps its caret: a redraw (a total landing, a newer reading) builds it anew */
    var caret = keep && a && a.tagName === "INPUT" && a.selectionStart != null ? [a.selectionStart, a.selectionEnd] : null;
    paint();
    [].forEach.call(root.querySelectorAll ? root.querySelectorAll(".fin-tw[data-list]") : [], function (el) {
      var at = scrolls[el.getAttribute("data-list")];
      if (at) { el.scrollTop = at[0]; el.scrollLeft = at[1]; }
    });
    var back = keep && root.querySelector(keep);
    if (back) {
      back.focus({ preventScroll: true });
      if (caret && back.setSelectionRange) { try { back.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text box */ } }
    }
  }
  var SEARCH_ICON = "<svg class=\"fin-searchicon\" viewBox=\"0 0 16 16\" width=\"16\" height=\"16\" aria-hidden=\"true\" focusable=\"false\">" +
    "<circle cx=\"7\" cy=\"7\" r=\"4.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\"/><path d=\"M10.5 10.5 13.5 13.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\"/></svg>";
  function paint() {
    root.innerHTML = "<div class=\"fin-wrap\"><div class=\"fin-top\"><h1>כספים</h1><span class=\"fin-asof\">נכון ל-" + esc(asOf()) +
      (BODY && BODY.refreshing ? " <span class=\"fin-refreshing\">מתעדכן</span>" : "") + "</span>" +
      "<span class=\"fin-spacer\"></span><span class=\"fin-searchbox\">" + SEARCH_ICON +
      "<input class=\"fin-search\" id=\"fin-q\" type=\"search\" aria-label=\"חיפוש\" placeholder=\"חיפוש: שם, סכום, אסמכתא\" value=\"" +
      esc(state.query) + "\"></span><button class=\"fin-btn\" type=\"button\" data-refresh=\"1\">רענון</button></div>" +
      overviewHtml() + "<div class=\"fin-foot\">" + healthHtml() +
      "<a class=\"fin-ask\" data-ask=\"1\" href=\"" + ASK_URL + "\" target=\"_blank\" rel=\"noopener\">חסר כאן משהו? פנייה לנדב</a></div></div>";
  }
  function message(text) {
    root.innerHTML = "<div class=\"fin-wrap\"><div class=\"fin-top\"><h1>כספים</h1></div><div class=\"fin-panel fin-empty\"><p class=\"fin-msg\">" + esc(text) + "</p>" +
      "<button class=\"fin-btn\" type=\"button\" data-retry=\"1\">לנסות שוב</button></div></div>";
  }
  /* D2: an answer with no view - the server has not read the boards yet (its first open, or an instance that has not
     read) - says so, with the refresh that reads them; a failed last attempt is said under it. An answer whose boards
     WERE read but whose view could not be built (`view_error`, app/desk/routes.py _view_of: a crash in the build) is
     another thing: a refresh reads the boards again and crashes the same way, so it is said as the server's fault, with
     no refresh offered as its fix (the adversarial review of 62fd993) */
  var VIEW_ERROR = "הנתונים נקראו ממאנדיי, אבל השולחן לא נבנה מהם. זו תקלה בשרת, ונדב צריך לבדוק אותה.";
  var VIEW_ERROR_KEPT = "הנתונים החדשים נקראו ממאנדיי, אבל השולחן לא נבנה מהם, ולכן מוצגים הנתונים הקודמים. זו תקלה בשרת, " +
    "ונדב צריך לבדוק אותה.";
  function noReading() {
    if (BODY && BODY.view_error) {
      root.innerHTML = "<div class=\"fin-wrap\"><div class=\"fin-top\"><h1>כספים</h1></div><div class=\"fin-panel fin-empty\">" +
        "<p class=\"fin-msg\">" + esc(VIEW_ERROR) + "</p></div></div>";
      return;
    }
    var failed = BODY && BODY.last_attempt && BODY.last_attempt.ok === false;
    root.innerHTML = "<div class=\"fin-wrap\"><div class=\"fin-top\"><h1>כספים</h1></div><div class=\"fin-panel fin-empty\"><p class=\"fin-msg\">הנתונים עוד לא נקראו</p>" +
      (failed ? "<p class=\"fin-msg\">הקריאה האחרונה נכשלה.</p>" : "") +
      "<button class=\"fin-btn\" type=\"button\" data-refresh=\"1\">רענון</button></div></div>";
  }

  /* ---------- data ---------- */
  function index(v) {
    liveStale();
    ROWS.sc = []; ROWS.b2 = [];
    if (v && v.b2c) { ROWS.sc = v.b2c.rows; }
    if (v && v.b2b) { ROWS.b2 = v.b2b.rows; }
    applyWritten();
  }
  function errorText(e) {
    var key = e && e.error;
    if (key === "rate_limited") {
      var wait = e.retryAfter || 0;
      return wait ? "אפשר לרענן שוב בעוד " + wait + " שניות." : "אפשר לרענן שוב עוד רגע.";
    }
    return ERRORS[key] || "לא הצלחנו לטעון את הנתונים (" + (key || "שגיאה") + ").";
  }
  /* An answer marked `refreshing` is the last reading, served at once. The newer one is read in the page's own
     second request (`?fresh=1`): on Autoscale an instance has CPU only while it answers, so the server never
     reads after its answer (knowledge ADR-0087). When it lands, the page redraws in place. */
  var pending = null;
  /* when the answer on the screen landed, by this computer's clock: its age is the server's `age_seconds` plus the time
     since, so a computer whose clock is off still counts it right */
  var bodyAt = 0;
  function took(body) { BODY = body; bodyAt = Date.now(); }
  /* a later answer (the page's own second request, a refresh, a return): taken, and its view drawn when it is another
     reading than the one on the screen. D2: an answer without a view (no reading yet on the instance that answered)
     never replaces the view on the screen */
  function takeNewer(next) {
    if (!next.view && VIEW) { if (BODY) { BODY.refreshing = false; } return; }
    var newer = next.taken_at !== (BODY && BODY.taken_at);
    took(next);
    if (newer || !VIEW) { VIEW = next.view || null; index(VIEW); }
  }
  function followUp(body) {
    if (!body.refreshing || pending) { return pending; }
    pending = deps.api("/api/desk?fresh=1").then(function (next) {
      takeNewer(next);
      redraw();
    }).catch(function () {
      if (BODY) { BODY.refreshing = false; redraw(); }
      flash("העדכון נכשל, ומוצגים הנתונים הקודמים.");
    }).then(function () { pending = null; });
    return pending;
  }
  /* a redraw nobody asked for keeps the reader where they were: the scroll, and the search box's caret */
  function redraw() {
    var scroller = document.getElementById("content");
    var top = scroller ? scroller.scrollTop : 0, winTop = window.scrollY;
    var q = document.getElementById("fin-q"), typing = q && document.activeElement === q;
    var caret = typing ? q.selectionStart : null;
    if (typing) { state.query = q.value; }
    render();
    if (scroller) { scroller.scrollTop = top; }
    window.scrollTo(0, winTop);
    if (typing) { q = document.getElementById("fin-q"); q.focus(); q.setSelectionRange(caret, caret); }
  }
  function load(fresh) {
    var p = fresh ? deps.post("/api/desk/refresh") : deps.api("/api/desk");
    return p.then(function (body) {
      /* D2: an answer without a view never replaces the one on the screen (takeNewer); with none on it, it is said */
      if (body.view || !VIEW) { took(body); VIEW = body.view || null; index(VIEW); }
      else if (BODY) { BODY.refreshing = false; }
      render();
      if (fresh && VIEW && body.last_attempt && body.last_attempt.ok === false) { flash("הרענון נכשל, ומוצגים הנתונים הקודמים."); }
      else if (fresh && VIEW && !body.view && body.view_error) { flash(VIEW_ERROR_KEPT); }
      /* the desk on the screen for the first time this visit: how long it took, and from which board */
      if (!USE.opened && VIEW) {
        USE.opened = true;
        use("open", { ms: Math.min(600000, Math.max(0, Math.round(Date.now() - USE.at))),
          board: deps.board === BOARD.b2 ? "b2" : deps.board === BOARD.sc ? "sc" : "", refreshing: !!body.refreshing });
      }
      followUp(body);
    }).catch(function (e) {
      use("load_failed", { code: useWord(e && e.error) || "unknown" });
      if (VIEW && e && e.error === "rate_limited") { notBefore(e); render(); flash(errorText(e)); return; }
      message(errorText(e));
    });
  }

  /* ---------- back on the page (round 3) ----------
     A reader who comes back is shown the fresh reading: the page asks for it through the refresh press's own route,
     quietly, and redraws in place - the scroll, the search and the open rows stay. "Back" is the view becoming visible
     again (another tab, another board), and - since slice 2 - its window taking the focus back: closing monday's item
     card over the view fires no visibilitychange, so a row edited in the card did not show (QA 11.11). Both ask through
     one rule: a reading on the screen older than RETURN_AFTER_MS, nothing on its way, and never before the server's
     Retry-After - its "not yet" (429) is kept until then. Since slice 2 a refresh reads only what changed (about a second)
     and the server's gap between two presses is as short as this rule (app/finance_api.py FINANCE_REFRESH_GAP_SECONDS), so an
     edit made in the card shows when the view is looked at again. No timer and no background thread: the page asks
     only on the reader's return, and the server reads inside that request (knowledge ADR-0087). */
  var RETURN_AFTER_MS = 5000, refreshNotBefore = 0;
  function notBefore(e) { refreshNotBefore = Date.now() + (num(e && e.retryAfter) || 5) * 1000; }
  function refetchDue(body, landedAt, now, notBeforeAt, busy) {
    return !!body && !busy && !body.refreshing && now >= notBeforeAt &&
      num(body.age_seconds) * 1000 + (now - landedAt) > RETURN_AFTER_MS;
  }
  function onVisible() {
    if (document.visibilityState === "visible") { askAgain("visible"); return; }
    /* out of sight (another board, another tab, monday closed): what waits is sent now, asked to outlive the page */
    use("hide");
    flushUse(true);
  }
  function onFocus() { askAgain("focus"); }
  function askAgain(how) {
    if (!VIEW || !refetchDue(BODY, bodyAt, Date.now(), refreshNotBefore, !!pending)) { return; }
    use("back", { how: how === "focus" ? "focus" : "visible" });
    BODY.refreshing = true; redraw();
    pending = deps.post("/api/desk/refresh").catch(function (e) {
      if (!e || e.error !== "rate_limited") { throw e; }
      /* "not yet" (429): a refresh was pressed inside the gap - by another reader, or by this one on the other charge
         board - so this instance already holds a reading that new. The plain open answers it without reading anything
         inside its span; it is taken only when it is newer than the one on the screen, and the Retry-After is kept
         (the hostile review of 11.11: the page used to stay on its older reading) */
      notBefore(e);
      var shown = BODY && BODY.taken_at;
      return deps.api("/api/desk").then(function (held) {
        if (!(Date.parse(held.taken_at) > Date.parse(shown))) { throw e; }
        return held;
      });
    }).then(function (next) {
      takeNewer(next);
      redraw();
    }).catch(function () {
      if (BODY) { BODY.refreshing = false; redraw(); }
    }).then(function () {
      pending = null;
      /* a held reading past its span comes marked `refreshing`: the page's own request reads the next, as on open */
      if (BODY && BODY.refreshing) { followUp(BODY); }
    });
  }
  function flash(text) {
    var t = document.createElement("div"); t.className = "fin-toast"; t.setAttribute("role", "alert"); t.textContent = text;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 6000);
  }

  /* ---------- events ---------- */
  function onClick(ev) {
    var el = ev.target.closest("[data-fintab],[data-open],[data-refresh],[data-notes],[data-legend],[data-unfilter],[data-bstate],[data-b2state],[data-b2view],[data-customer],[data-row],[data-export],[data-retry]," +
      "[data-act],[data-act-go],[data-act-close],[data-terms]," +
      "[data-id-copy],[data-id-retry],[data-bank-retry],[data-vat-retry],[data-ask]");
    if (!el || !root.contains(el)) { return; }
    /* the actions (round 3, slice 3): inside a row's details, so they never open or close the row */
    if (el.dataset.act) {
      var cut = el.dataset.act.lastIndexOf(":");
      startAction(el.dataset.act.slice(0, cut), el.dataset.act.slice(cut + 1)); return;
    }
    if (el.dataset.actGo) { confirmAction(); return; }
    if (el.dataset.actClose) {
      if (!busyAct()) {
        /* a plan shown and not written: she chose not to */
        if (ACT && ACT.phase === "confirm") { use("act", { action: ACT.action, phase: "cancel" }); }
        ACT = null; redraw();
      }
      return;
    }
    /* the ID number (16.11): inside a payment's details, read live as the person; a refund's bank account and a
       company's ח.פ the same way (D2) */
    if (el.dataset.idCopy) { copyId(el.dataset.idCopy); return; }
    if (el.dataset.idRetry) { delete IDS[el.dataset.idRetry]; redraw(); return; }
    if (el.dataset.bankRetry) { delete BANKS[el.dataset.bankRetry]; redraw(); return; }
    if (el.dataset.vatRetry) { delete VATS[el.dataset.vatRetry]; redraw(); return; }
    /* "חסר כאן משהו? פנייה לנדב": the link opens the request form as it did; this only counts it */
    if (el.dataset.ask) { use("ask"); flushUse(false); return; }
    if (el.dataset.terms) {
      /* "אין תנאי תשלום": the company's ledger, its billing details at the top (read only since D2) */
      state.b2.customer = el.dataset.terms; state.b2.state = ""; state.b2.view = "list"; state.b2.open = {};
      use("customer", { from: "terms" });
      render();
      var top = document.getElementById("content");
      if (top) { top.scrollTop = 0; }
      window.scrollTo(0, 0);
      return;
    }
    if (el.dataset.open) {
      /* how she leaves for monday: by the row's name, the quiet icon, or the details' button */
      use("open_item", { where: el.classList.contains("fin-name") ? "name" : el.classList.contains("fin-dx-open") ? "details" : "icon" });
      if (deps.monday) {
        ev.preventDefault();
        var href = el.getAttribute("href");
        try {
          Promise.resolve(deps.monday.execute("openItemCard", { itemId: Number(el.dataset.open) }))
            .catch(function () { window.open(href, "_blank", "noopener"); });
        } catch (e) { window.open(href, "_blank", "noopener"); }
      }
      return;
    }
    if (el.dataset.row) {
      if (ev.target.closest("a,button")) { return; }
      var opened = state.tab === "b2b" ? state.b2.open : state.sc.open;
      opened[el.dataset.row] = !opened[el.dataset.row];
      var row = rowByKey((state.tab === "b2b" ? "b2" : "sc") + ":" + el.dataset.row);
      use("row", { state: row ? row.state : "", open: !!opened[el.dataset.row] });
      redraw(); return;
    }
    if (el.dataset.retry) { el.disabled = true; el.textContent = "טוען..."; load(false); return; }
    if (el.dataset.export) { use("export", { list: el.dataset.export }); exportList(el.dataset.export); return; }
    if (el.dataset.unfilter) {
      unfilter();
      use("unfilter", { from: el.closest(".fin-empty") ? "empty" : "head", results: Math.min(searchCounts().results, 100000) });
      redraw(); return;
    }
    if (el.dataset.bstate !== undefined) { state.sc.state = el.dataset.bstate; state.sc.open = {}; use("state"); redraw(); return; }
    if (el.dataset.b2state !== undefined) {
      state.b2.state = el.dataset.b2state; state.b2.view = "list"; state.b2.open = {};
      use("state");
      redraw(); return;
    }
    if (el.dataset.b2view) {
      state.b2.view = el.dataset.b2view;
      if (el.dataset.b2view === "customers") { state.b2.customer = ""; }   /* "לפי לקוח" is every customer */
      use("view", { view: el.dataset.b2view === "customers" ? "customers" : "list" });
      redraw(); return;
    }
    if (el.dataset.customer !== undefined) {
      state.b2.customer = el.dataset.customer; state.b2.state = ""; state.b2.view = "list"; state.b2.open = {};
      use("customer", { from: el.dataset.customer ? "aging" : "all" });
      redraw(); return;
    }
    if (el.dataset.fintab) { state.tab = el.dataset.fintab; use("tab"); }
    else if (el.dataset.notes) { state.notes = !state.notes; use("notes", { open: !!state.notes }); render(); return; }
    else if (el.dataset.legend) { state.legend = !state.legend; use("legend", { open: !!state.legend }); render(); return; }
    /* "רענון" reads the boards again, and what the open details read live with them (the ID number, D2: a bank account,
       a ח.פ) */
    else if (el.dataset.refresh) { use("refresh"); liveStale(); el.disabled = true; el.textContent = "מרענן..."; load(true); return; }
    render();
    /* on a wide screen the app scrolls #content, not the window */
    var scroller = document.getElementById("content");
    if (scroller) { scroller.scrollTop = 0; }
    window.scrollTo(0, 0);
  }
  var typing = null;
  function onInput(ev) {
    if (ev.target.dataset && ev.target.dataset.actInput !== undefined) { DRAFT[ev.target.dataset.actInput] = ev.target.value; return; }
    if (ev.target.id !== "fin-q") { return; }
    clearTimeout(typing);
    typing = setTimeout(function () {
      state.query = ev.target.value; render();
      var q = document.getElementById("fin-q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
      searchSettling();
    }, 250);
  }
  function onKey(ev) {
    /* Enter in an action's box presses its button */
    if (ev.key === "Enter" && ev.target.dataset && ev.target.dataset.actInput !== undefined) {
      ev.preventDefault();
      var k = ev.target.dataset.actInput, rk = k.slice(0, k.lastIndexOf(":"));
      var btn = [].filter.call(root.querySelectorAll("[data-act]"), function (b) { return b.dataset.act.indexOf(rk + ":") === 0 && b.closest(".fin-act") === ev.target.closest(".fin-act"); })[0];
      if (btn && !btn.disabled) { btn.click(); }
      return;
    }
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches("[data-bstate],[data-b2state],[data-row],[data-customer]")) { ev.preventDefault(); ev.target.click(); }
  }
  function onChange(ev) {
    if (ev.target.id !== "fin-period") { return; }
    state.sc.period = ev.target.value; state.sc.open = {};
    use("period", { period: state.sc.period });
    redraw();
  }

  /* desk.js calls this once: deps = { api(path), post(path, payload, keep), monday (the SDK whenever monday supplied a
     context, else null), board (the charge board the view sits on, else "") } */
  function mount(el, given) {
    root = el; deps = given;
    /* this visit, for the usage events (16.11): a random id, and the page's own clock from now */
    USE.session = useSession(); USE.at = Date.now();
    window.addEventListener("pagehide", function () { flushUse(true); });
    /* on B2B Charge the desk opens on the companies tab, anywhere else on the students tab; both tabs stay a
       click away on both boards, for whoever is a member of both (the server answers each tab by its board) */
    state.tab = given.board === BOARD.b2 ? "b2b" : "b2c";
    root.addEventListener("click", onClick);
    root.addEventListener("input", onInput);
    root.addEventListener("keydown", onKey);
    root.addEventListener("change", onChange);
    document.addEventListener("visibilitychange", onVisible);
    /* the view's window taking the focus back: after monday's item card closes over it, the first click in the view */
    window.addEventListener("focus", onFocus);
    /* round 3, slice 3: whether this person may write from here (monday's context) - asked beside the first reading, never
       in its way: desk.js shows the desk when this promise settles, and monday may be slow to answer, or not answer */
    var first = load(false);
    readAbility();
    return first;
  }
  /* the rest is for the tests only (tests/test_desk_page.py here; analiza-metrics' tests/test_finance_js.py held the
     page's lists to its app/finance.py): its words, its return rule, and its HTML for a view and a state, drawn without
     a document */
  window.FinanceDesk = { mount: mount, _scFilter: scFilter, _b2Filter: b2Filter, _exportable: function (q) { return EXPORTABLE_Q.test(q); },
    _flags: function () { return { all: FLAG, on: FLAG_ON }; },
    _lastDayText: lastDayText, _refetchDue: refetchDue, _returnAfterMs: RETURN_AFTER_MS, _notes: function () { return { all: NOTE, loud: LOUD }; },
    /* "חיפוש ללא סינון" pressed: the filters it leaves (16.11) */
    _unfilter: function () { unfilter(); return { tab: state.tab, query: state.query, sc: state.sc, b2: state.b2 }; },
    _labels: { planned: PLANNED_LABEL, lastDay: LAST_DAY_LABEL },
    _columns: function () {
      var heads = {};
      Object.keys(COLS).forEach(function (k) { heads[k] = COLS[k].h; });
      return { heads: heads, sc: SC_COLS, b2: B2_COLS };
    },
    /* round 3, slice 3: the allowlist, the one write, the plan, and an action driven step by step */
    _writable: function () { return WRITABLE; }, _apiVersion: API_VERSION, _textMax: TEXT_MAX,
    _write: function (boardId, itemId, values) { return mondayWrite(boardId, itemId, values); },
    _refused: refusedColumns, _plan: planAction, _cleanInput: cleanInput, _mondayErr: mondayErr,
    _act: function (rk, key, typed) {
      Object.keys(typed || {}).forEach(function (c) { DRAFT[rk + ":" + c] = typed[c]; });
      startAction(rk, key);
    },
    _confirm: confirmAction, _actState: function () { return ACT; }, _can: function () { return CAN; },
    _show: function (st) {
      Object.keys(st || {}).forEach(function (k) {
        state[k] = st[k] && typeof st[k] === "object" ? Object.assign({}, state[k], st[k]) : st[k];
      });
      render();
      return root.innerHTML;
    },
    _render: function (body, st) {
      deps = deps || { api: function () { return new Promise(function () {}); }, post: function () { return new Promise(function () {}); } };
      if (st && st.can) { CAN = st.can; st = Object.assign({}, st); delete st.can; }
      if (st && st.act !== undefined) { ACT = st.act; st = Object.assign({}, st); delete st.act; }
      BODY = body; VIEW = body.view; index(VIEW); TOTALS = {};
      Object.keys(st || {}).forEach(function (k) {
        state[k] = st[k] && typeof st[k] === "object" ? Object.assign({}, state[k], st[k]) : st[k];
      });
      return overviewHtml() + healthHtml();
    } };
})();

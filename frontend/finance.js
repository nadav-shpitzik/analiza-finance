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
 * 4. a company's billing details were shown read only at the port: their edit went through analiza-metrics' server
 *    (its account route, which this service does not have). Since D2's item 4 below they are edited from the browser;
 * 5. the usage batches (POST /api/usage, not in this service yet) stop quietly at the first failure;
 * 6. three sentences that would no longer be true: a guest is told so (this gate refuses one by name), the ID number's
 *    "no access" no longer sends her to the Excel file (whose server reads no ID number), and the chip "אין תנאי תשלום"
 *    opens the company's ledger without promising an edit there (since D2's item 4, it promises one again to whoever
 *    may write).
 * In the comments below, app/finance.py and app/finance_api.py are analiza-metrics' modules: their port to this
 * service is under way, and each rule named there is the one the port keeps.
 *
 * What changed after the move: D2's list (Analiza-Monday docs/design/analiza-finance-desk-move-plan.md §1b, approved
 * 24.11), each item from the evidence of how Chava and Pini used the desk in 17-23.11, and why:
 * 1.1 "שליחת קבלה: יצאה" and "סימון תשלום הוחזר" sit on the row itself, for one row or several picked at once (the box
 *     at the row's start, up to BATCH_MAX): she made those two edits 30 times in monday, eight refunds within two
 *     minutes, and never pressed them in a row's details. One press reads the rows live as the person and shows ONE
 *     confirmation listing each row's write; a row monday changed since the desk's reading is not written. On "כתיבה"
 *     each row is read again right before its write and checked after, as a single action (confirmAction), and the
 *     result is said per row. One refresh after the batch, as after one action. The allowlist did not change; readRow
 *     became readRows of one row, so one request reads a batch.
 * 1.2 A search looks at every row of its tab, whatever tile, period or company is open, says so, and marks a row found
 *     outside the open list with its own tile: her four searches all found nothing in the open list, three would have
 *     found rows without its filters. Its Excel file and totals are asked with no tile, period or company, so the
 *     server's filters pick what the screen shows.
 * 2.1 "חיפוש ללא סינון" went out (pressed 0 times; 1.2 made it the default), with its helpers and its test hook.
 * 2.2 "חסר כאן משהו? פנייה לנדב" went out with its link: no request came through its form since the desk was built.
 * 4   A company's billing details are edited again, FROM THE BROWSER, AS THE PERSON (ADR-0188), where analiza-metrics'
 *     server wrote them: "עריכה" reads the four live, "המשך" checks what changed by that server's rules and shows
 *     before and after, "שמירה" reads them again, writes only when monday still holds what was shown, and checks the
 *     answer. The allowlist (WRITABLE, and the server's desk_writes alike) gained exactly the four Accounts columns,
 *     each by its rule, and mondayWrite writes Accounts only for a company the desk shows (ITEM_GUARD). MON-41: the old
 *     Update on the company is written by mondayUpdate - the page's second mutation - only when monday's context says
 *     the app holds updates:write for the person (CAN_UPDATE); it is never asked for, and otherwise the confirmation
 *     says monday's activity log keeps who wrote, the before and the after.
 * And from the adversarial review of 62fd993: a reading whose view could not be built (`view_error`) is said as the
 * server's fault, with no refresh offered as its fix; an item's link (href) goes through esc() like every other value.
 * From the adversarial review of a32395f: the visit's id for the usage events is sixteen letters, never a digit
 * (useSession; the server refuses any other).
 * From the QA of 24.11 (the desk inside monday, at the width Chava works at), each by its number in the QA report:
 * QA 1 Ticking a row's box moved the table about 58 pixels down - the bar of picked rows came in above it - so the click
 *      aimed at the next row's box unticked the first. The bar now holds its place wherever the list offers a box, at one
 *      height (its buttons the row's own size), quiet while nothing is picked and saying what the box is for.
 * QA 2 "ייצוא לאקסל" said nothing on the page. When the file's link opens, the page says the file is on its way and how
 *      many rows it holds: the rows of the list exported, counted as the screen shows them (exportedCount; the companies'
 *      list is one function, b2Rows, for the screen and the count). The file's name is the server's and is not said.
 * QA 3 A search with a row outside the open list kept the open list's columns, so a payment found from "החזר לביצוע"
 *      showed a refund's "-" where its own date, channel and receipt are. With any row outside the open list the search
 *      is drawn with the general columns, on both tabs; with every row inside, the open list's own stay (1.4). And a row
 *      found by a field its list does not show says which, and what it holds there ("נמצא לפי: הוחזרו 976.19 ₪",
 *      foundMark); the search's fields are one list (MATCH) for the rule and for the mark.
 * QA 4 At full width the students' "לטיפול" and "הכל" ran about 40 pixels past their frame, "פתיחה במאנדיי" cut to "פר":
 *      their widest cells could not wrap. A state's name and the totals' label may wrap in their cell now, and the date's
 *      "(לפי יום היצירה)" sits under the date (finance.css), so every column fits at monday's view widths of 1280 and 1400
 *      with rows as long as real ones (a students' list's narrowest width went from about 1210 to about 1070 pixels, and
 *      to about 1090 with QA 6's chevron).
 * QA 5 An empty state's pill disappeared ("ביקש החזר, טרם אושר"), while an empty task's tile stays with its ✓. Every
 *      state's pill stays, an empty one muted with its 0 and a grey dot, on both tabs (no comment had recorded the hiding
 *      as a decision: it came with analiza-metrics' first import, 10.11).
 * QA 6 Nothing said a row opens its details on a click (there is no "פרטים" button). A row of a list that opens details
 *      carries a chevron at its start (after the box where it has one), lit while the row is pointed at and turned while
 *      its details are open, beside the pointer and the hover it had: a narrow column, and every list still fits (QA 4).
 * QA 7 With a company open, its name was only in the list's heading, far below its figures, its "פרטי חיוב" and its
 *      tiles - all its own. Its name heads the tab now, with the way back to every company ("כל החברות") beside it.
 * QA 8 "כל החברות" went to "הכל" and "רשימה", whatever she came from ("לטיפול" and "לפי לקוח"). A company opened from a
 *      list or from "לפי לקוח" keeps where she was - the tile, the view, the page's height (state.b2.back) - and the way
 *      back returns there; a company opens at its top, where its name is.
 * QA 9 The table scrolled inside a frame of 78% of the view (about six rows inside monday) in a page that scrolls too. A
 *      table that fits its frame now scrolls with the page - one scroll, its header held at the top of the view (the
 *      panel clips without being a scroll box) - and only one wider than its frame, at a narrow width, scrolls in a frame
 *      as tall as the view, its sideways scrollbar in reach (fitFrames, after every drawing and on a resize).
 * QA 10 The page's words: a refresh on its way is "מתעדכן" on the button pressed as beside the reading's time (it was
 *       "מרענן" there, REFRESHING); a date not typed says "(לפי יום היצירה)" (CREATED_BY) in the list's cell, the details
 *       and the row's situation alike; an empty value is "-" in every column ("תאריך החזר", "מועד פירעון", "עסקה" and
 *       the rest were empty cells); "ממתין לנתונים" shows no amount while none is typed (its "₪0" said there was nothing
 *       to bill). The "(MON-43)" the billing form shows is the server's sentence (account_form.mon43), drawn as sent.
 * QA 11 The totals: the total row of "לפי לקוח" has the count of "לטיפול" (the companies' counts shown, added: a count, no
 *       money). The heading of "הכל" has its sum where the server made one: on the students' tab the period's "התקבלו"
 *       (the view's), on the companies' tab - the view has none - the server's total of that very list, once it lands.
 * QA 12 No change here: the QA could not read the console of the view's iframe, so tests/test_desk_browser.py holds that
 *       the page logs no console error and throws nothing while it loads, opens details, confirms an action, refreshes.
 * And with the server's QA 6 (24.11, Nadav's go): a payment of "ללא תקבול" whose "שליחת קבלה" is "יצאה" is in "חריג,
 *      לבדיקה" with the flag receipt_no_money. The page draws its chip in the server's words, red (a contradiction
 *      between two columns), at the server's place in FLAG_ORDER, and says it in the row's details (WARN); until then the
 *      row sat in "חריג" with no chip.
 * And with the server's QA 1 (24.11): in "החזר בוצע" the column of the refund counted (monday's "סכום החזר", or what was
 *      paid for a full refund without it, the asterisk) was headed "סכום החזר" as if it were monday's column; it is
 *      "הוחזרו", the desk's word for it on the tab's figure and in the Excel file. A refund's details keep "סכום החזר" for
 *      monday's own value. The mark of what found a row reads "נמצא לפי: <field> <value>" with it.
 * From the QA's second pass of 24.11 (QA2, the live desk at ef9d817), each by its number there:
 * QA2 1 While "מתעדכן" showed, a click on a tile was not taken and text typed into the search box was erased. A press is a
 *      click only when what was pressed is still there at its release, and a drawing between the two - the newer reading
 *      landing, or monday's frame giving the view the focus back, which fires on the press itself - built the tile anew.
 *      The search box was read back only while it had the focus, and what is typed reaches state.query only when the search
 *      settles (250ms after the last key), so a drawing in between drew it over; "רענון" drew its answer with render(),
 *      dropping the box's focus too. Now every drawing keeps what the box holds, focused or not, with its focus and caret
 *      when it had them (render, focusKey); every answer is drawn as one, "רענון"'s too (load); and a drawing that is not
 *      the person's own act - an answer, a return, the search settling - waits while a pointer is down on the desk and is
 *      drawn once after the click (landed; PRESS_MAX_MS at most, for a release the page never hears).
 * QA2 2 After "שליחת קבלה: יצאה" was written from here, "פתיחה במאנדיי" still showed "טרם" - closed and opened again, after
 *      "רענון" too - and only reloading the page showed "יצאה" (the item's page in a new tab showed it at once). The write
 *      was right; monday's card in that tab keeps what it read before it. Opening over the view the card of a row written
 *      in this visit (WROTE, kept for the visit as WRITTEN is not) says so, once for that row, in a flash that outlasts
 *      the card closing (cardNote, CARD_NOTE_MS); never with the write itself, nor outside monday, where the link opens
 *      the item's page in a new tab.
 * QA2 3 "The confirmation screen pushes the table down, and the mouse stays over the button of another row." A batch's box
 *      (a row's own button, or the bar's) took the bar's place above the table, taller than it: every row 69 pixels lower
 *      at 1280x748, and with the list scrolled down the focus on it scrolled the page up to it, about 550 pixels - QA 1's bar
 *      held one height only while nothing was confirmed. Now the bar keeps its place (barHtml; held, empty, while the
 *      batch's rows offer no box any more), and the batch - its reading, its one confirmation, its progress, its report -
 *      floats in the view beside what was pressed (fin-pop, batchBox): above the row, or the bar, where the view has room,
 *      below it otherwise, never over it, so the pointer that pressed is never on "כתיבה"; it follows a scroll and a resize
 *      (placePop). The focus still goes to its box, never to "כתיבה", and scrolls nothing. A row's details' own plan is as
 *      it was: under its button, which does not move.
 * D3 (25.11, Analiza-Monday backlog 0301 D3; the desk move plan §1a.2): what the books (Hashavshevet) say that monday does
 * not, read only - every word, number and list from the server (app/desk/books.py), drawn here, nothing computed:
 * D3 1 Three chips of the books on a Student Charge row, from A1's own judgement of it: "מספר החשבונית שבשורה לא נמצא
 *      בחשבשבת כמסמך תקף: ...", "בשורה X, בחשבשבת Y" and "בחשבשבת חשבונית מס/קבלה N, והסטטוס ...". Red (BAD: the row
 *      contradicts the books), in FLAG_ORDER right after the contradictions between two columns, their words filled on the
 *      server (flagText takes them from the row's `books`), and said again in the row's details, in a sentence (WARN). A
 *      mark moves no row: its state, its tile and every sum stay the server's, as before.
 * D3 2 "לטיפול" on the students' tab holds a marked row from 25.10.2026 on (`books_task`, the same rule as the server's
 *      filter_b2c) beside the rows of its task states, and the tile counts what the server counted (each period's
 *      `tasks`). A row's marks sit under its state wherever a list shows "שיבוץ" ("לטיפול", "הכל" and the rest), and in
 *      "מה לבדוק" where a list has it: no column was added, so QA 4's widths hold. An older marked row keeps its mark.
 * D3 3 Under "לטיפול", on each tab, "מסמכים בחשבשבת בלי חיוב במאנדיי" (booksHtml): on the students' tab each document
 *      with its candidates by name - rows of Student Charge named like the card, under "מועמדת לפי שם", each opening its
 *      card in monday, never drawn as a match; on the companies' tab with the company whose key the card is, and the line
 *      of the vouchers' card. The documents of a card whose side is not clear come only to a member of both charge
 *      boards, and are drawn under both tabs. A search stays on the rows.
 * D3 4 The books' state: "לפי הספרים נכון ל-..." above the lists and in the footer, or why not, in the server's sentence
 *      (`view.books.said`). The totals of "לטיפול" are asked by the books' part on the screen (`books`), as by its reading,
 *      and the Excel link carries it too, so a file built from another part says so.
 * D3 5 A number a row cites that is just past the end of its series is not yet in the copy (read every hour): the server
 *      gives no mark for it, and the row's details say "עוד לא בהעתק של הספרים" (`books_note`, booksNote), quietly.
 * D4 (26.11, "רישום מחשבשבת"): what the books propose to fill on a Student Charge row - FR-C2's invoice number and A1's
 *      receipt date, of the desk's era, computed on the server (app/desk/books.py `proposals`) - written by the person.
 *      A chip "בחשבשבת: N" (or "תאריך מחשבשבת: D" for the date alone) under the row's state and in "מה לבדוק", its
 *      sentence and its button in the row's details; on the document's line in the books' list, "הצעה: לרשום בשורה של
 *      ..." and the button; above that list, "לרשום את כל ההצעות (N)". Each is the batch of D2's 1.1 (one request reads the
 *      rows, one confirmation, each row read again right before its write, the report row by row, one refresh), floating
 *      beside what was pressed, so no row moves. A cell is written only empty, only with what the books propose (if_empty;
 *      planAction), and a row whose cells are not what the desk read is skipped and said, never written over (fillMoved).
 *      WRITABLE gained "תאריך תקבול" as a date, as the server's desk_writes did. A written date is shown on the row at once
 *      (applyWritten), and the document leaves the list with the next reading.
 * From the hostile review of D4 (26.11), by its numbers:
 * 1  A newer reading that landed while a confirmation of the books' proposals was open (a refresh, or askAgain when the view
 *      takes the focus back from monday's item card) left the batch on the rows it was pressed on, and "כתיבה" wrote a
 *      proposal the reading on the screen had withdrawn. A proposal is now always the reading's on the screen: taken off the
 *      current rows when the confirmation is made (settleFills), held to each newer reading while it is open (recheckBatch:
 *      one withdrawn or changed leaves for "לא ייכתב" with why, the count follows, and with none left the confirmation
 *      closes, said), and checked again right before each row's write (confirmBatch).
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
    /* a receipt that went out on a payment that brought no money in: in "חריג, לבדיקה" (QA 24.11, the server's rule 6) */
    receipt_no_money: "שליחת קבלה: יצאה, בסטטוס {status}",
    /* D3: what the books contradict on the row - the server fills each "{...}" (the row's `books`; booksMark) */
    books_ref_invalid: "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: {why}",
    books_amount_differs: "בשורה {row}, בחשבשבת {books}",
    books_receipt_unpaid: "בחשבשבת חשבונית מס/קבלה {doc}, והסטטוס {status}",
    /* D4: what the books propose to fill on the row - the server fills each "{...}" (the row's `books`; booksMark) */
    books_ref_fill: "בחשבשבת: {number}", books_date_fill: "תאריך מחשבשבת: {date}",
    invoice_late: "מועד דרישת התשלום עבר", no_contract: "בהרצה ללא חוזה", over_deal_value: "מעל שווי עסקה",
    billed_no_due: "חויב בלי מועד דרישת תשלום", billed_before_demand: "חויב, ומועד דרישת התשלום עוד לא הגיע",
    partial_amount_unknown: "סכום ששולם (כולל מע\"מ) לא הוקלד", billed_no_invoice: "חסרה אסמכתת חשבונית",
    paid_not_billed: "\"שולם\" לפני \"חויב\"", payer_mismatch: "החברה אינה Account משלם בעסקה",
    terms_missing: "אין תנאי תשלום", status_unknown: "תווית שהמסך לא מכיר"
  };
  /* the same flag on the other board, where its column has another name */
  var FLAG_ON = { b2: { amount_missing: "סכום לחיוב (ללא מע\"מ) לא הוקלד" } };
  var BAD = { refund_overdue: 1, refund_last_day_passed: 1, refund_exceeds_paid: 1, invoice_late: 1, no_contract: 1,
    over_deal_value: 1, status_unknown: 1, credit_on_status: 1, refund_amount_on_status: 1, refund_date_on_status: 1,
    receipt_no_money: 1, books_ref_invalid: 1, books_amount_differs: 1, books_receipt_unpaid: 1 };
  var TASK = { refund_last_day_unknown: 1, invoice_missing: 1, receipt_pending: 1, credit_ref_missing: 1,
    refund_amount_missing: 1, refund_amount_zero: 1, billed_no_due: 1, billed_before_demand: 1, partial_amount_unknown: 1,
    billed_no_invoice: 1, terms_missing: 1, books_ref_fill: 1, books_date_fill: 1 };
  var FLAG_ORDER = ["status_unknown", "credit_on_status", "refund_amount_on_status", "refund_date_on_status",
    "receipt_no_money", "books_ref_invalid", "books_amount_differs", "books_receipt_unpaid", "refund_last_day_passed",
    "refund_overdue", "refund_exceeds_paid", "invoice_late", "no_contract", "over_deal_value",
    "refund_last_day_unknown", "invoice_missing", "books_ref_fill", "books_date_fill", "billed_no_invoice",
    "receipt_pending", "credit_ref_missing",
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
    sc: { state: "tasks", period: "", open: {}, view: "list", cohorts: {}, unpriced: false },
    /* the companies list: a state, a customer (account id), the list or the customers, the charges opened */
    b2: { state: "tasks", customer: "", view: "list", open: {}, back: null } };

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
  var MORE = "<span class=\"fin-more\" title=\"פרטים\"><svg viewBox=\"0 0 16 16\" width=\"14\" height=\"14\" aria-hidden=\"true\" " +
    "focusable=\"false\"><path d=\"M4 6l4 4 4-4\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" " +
    "stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></span>";
  function openLink(r) {
    return "<a class=\"fin-open\" data-open=\"" + esc(r.id) + "\" href=\"" + esc(itemUrl(r.board, r.id)) + "\" target=\"_blank\" rel=\"noopener\"" +
      " aria-label=\"פתיחה במאנדיי\" title=\"פתיחה במאנדיי\">" + OPEN_ICON + "</a>";
  }
  /* the row's name opens its monday card over the view (openItemCard, in onClick), and outside monday its page in a new
     tab; a click anywhere else on the row still opens or closes its details */
  function nameLink(r, inner) {
    return "<a class=\"fin-name\" data-open=\"" + esc(r.id) + "\" href=\"" + esc(itemUrl(r.board, r.id)) + "\" target=\"_blank\" rel=\"noopener\">" +
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
  /* D3: a mark of the books on the row, its words filled on the server (app/desk/books.py): its chip and its sentence */
  function booksMark(r, k) { return (r.books || []).filter(function (m) { return m.kind === k; })[0] || null; }
  /* the row's marks as chips, in FLAG_ORDER: where a list shows "שיבוץ" (every general list, "לטיפול" among them) they sit
     under the state; a list with "מה לבדוק" says them there, among the row's other chips */
  function booksChips(r) {
    var keys = FLAG_ORDER.filter(function (k) { return !!booksMark(r, k); });
    return keys.length ? "<div class=\"fin-chips fin-books-chips\">" + keys.map(function (k) {
      return "<span class=\"" + chipClass(k) + "\">" + esc(booksMark(r, k).chip) + "</span>";
    }).join("") + "</div>" : "";
  }
  function flagText(k, r) {
    var m = booksMark(r, k);
    return m ? m.chip : (((FLAG_ON[r.board] || {})[k]) || FLAG[k]).replace("{status}", statusWord(r));
  }
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
    student: { h: "סטודנט/ית", c: function (r) { return nameLink(r, esc(r.name)) + writtenMark(r) + whereMark(r) + foundMark(r); } },
    /* Student Charge's "נציג מכירות" (17.11): the Owner of the payment's lead, read with the lead (app/finance.py lead_rep) */
    rep: { h: "נציג מכירות", xs: 1, c: function (r) { return r.rep ? esc(r.rep) : NOT_SET; } },
    cohort: { h: "מחזור", xs: 1, c: function (r) { return r.cohort_code ? "<span title=\"" + esc(r.cohort_title) + "\">" + ltr(r.cohort_code) + "</span>" : "<span class=\"fin-subtle\">-</span>"; } },
    /* D3: beside its state, the row's marks of the books - red, in the server's words; the mark moves no row */
    state: { h: "שיבוץ", c: function (r) { return stateChip(r.state) + booksChips(r); } },
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
    /* QA 10 (24.11): an empty value is "-" in every column, as monday shows it - "תאריך החזר" was an empty cell */
    refund_date: { h: "תאריך החזר", c: function (r) { return r.refund_date ? d(r.refund_date) : NOT_SET; } },
    /* the refund counted (app/desk/view.py `refunded`): monday's "סכום החזר", or what was paid for a full refund without
       it, said by the asterisk under the list. Its word is the desk's "הוחזרו", as the tab's figure and the Excel file
       call it (QA 24.11): under "סכום החזר" it read as monday's column, which the details show as it is */
    refunded: { h: "הוחזרו", n: 1, t: "refunded", c: function (r) {
      return refundGuessed(r) ? "<span class=\"fin-subtle\" title=\"סכום החזר ריק או 0: מוצג כל סכום ששולם\">" + money2(r.refunded) + "*</span>"
        : money2(r.refunded);
    } },
    account: { h: "חברה", c: function (r) { return r.ctx.account ? esc(r.ctx.account) : NOT_SET; } },
    deal: { h: "עסקה", xs: 1, c: function (r) { return r.ctx.deal ? esc(r.ctx.deal) : NOT_SET; } },
    charge: { h: "חיוב", c: function (r) { return nameLink(r, label(r.name)) + writtenMark(r) + whereMark(r) + foundMark(r); } },
    course: { h: "קוד קורס", xs: 1, c: function (r) { return ltr(r.course) || NOT_SET; } },
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
      if (!r.due) { return NOT_SET; }
      return d(r.due) + (r.flags.indexOf("terms_missing") !== -1 ? " " + noTerms(r.account_id) : r.account_terms ? " <bdi class=\"fin-subtle\">" + esc(r.account_terms) + "</bdi>" : "");
    } },
    b2state: { h: "שיבוץ", c: function (r) { return stateChip(r.state, VIEW.b2b.states, B2_COLOR); } },
    paid_so_far: { h: "סכום ששולם (כולל מע\"מ)", n: 1, t: "paid_so_far", c: function (r) { return r.paid_so_far != null ? money2(r.paid_so_far) : "<span class=\"fin-badtext\">לא הוקלד</span>"; } },
    balance: { h: "יתרה לגבייה (כולל מע\"מ)", n: 1, t: "balance", c: function (r) { return r.balance != null ? money2(r.balance) : "-"; } },
    late: { h: "ימים בפיגור", n: 1, opt: function (r) { return r.days_late != null; }, c: function (r) { return r.days_late != null ? NF.format(r.days_late) : NOT_SET; } },
    notes: { h: "מה לבדוק", c: function (r) { return rowChips(r); } },
    /* QA 6 (24.11): a row that opens its details by a click says so - a chevron at its start, turned while they are open
       (finance.css), beside the pointer and the hover it had; a narrow column, so no list grows past its frame (QA 4) */
    more: { h: "", c: function () { return MORE; } },
    open: { h: "פתיחה במאנדיי", c: function (r) { return openLink(r); } },
    /* 1.1 (D2): the box that picks a row for a batch, under a box that picks every row it can, and the row's own button -
       only on the rows that offer one of the two actions, where the person may write (pickable) */
    pick: { h: "", opt: function (r) { return pickable(r); }, hh: function (rows) {
      return "<input type=\"checkbox\" class=\"fin-pick\" data-pick-all=\"1\"" + (allPicked(rows) ? " checked" : "") + (busy() ? " disabled" : "") +
        " aria-label=\"" + esc("בחירת כל השורות") + "\" title=\"" + esc("בחירת כל השורות") + "\">";
    }, c: function (r) {
      if (!pickable(r)) { return ""; }
      var k = rowKey(r);
      return "<input type=\"checkbox\" class=\"fin-pick\" data-pick=\"" + esc(k) + "\"" + (PICKED[k] ? " checked" : "") +
        (busy() ? " disabled" : "") + " aria-label=\"" + esc("בחירת השורה " + r.name) + "\">";
    } },
    rowact: { h: "פעולות", opt: function (r) { return pickable(r); }, c: function (r) {
      var a = pickable(r) ? rowAction(r) : null;
      if (!a) { return ""; }
      return "<button type=\"button\" class=\"fin-btn fin-rowact\" data-row-act=\"" + esc(rowKey(r) + ":" + a.key) + "\"" +
        (busy() ? " disabled" : "") + ">" + esc(a.button) + "</button>";
    } }
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
  /* QA 10 (24.11): "יום היצירה" alone beside a date did not read without the legend, so it is said "(לפי יום היצירה)",
     in the list's cell (on a line of its own under the date, finance.css), the details and the row's situation alike */
  var CREATED_BY = "(לפי " + CREATED_WORD + ")";
  function paidDate(r) {
    return r.date_from === "paid" ? d(r.date) : d(r.date) + " <span class=\"fin-subtle\">" + CREATED_BY + "</span>";
  }
  function paidDateSaid(r) {
    return r.date_from === "paid" ? d(r.date) : d(r.date) + " <span class=\"fin-subtle\">" + CREATED_BY + "</span>";
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
    deps.api("/api/desk/totals?" + qs).then(function (body) { TOTALS[key].body = body; landed(); })
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
      if (!cell && first && k !== "pick" && k !== "more") { first = false; cell = "<b>סה\"כ</b> <span class=\"fin-subtle fin-nw\">" + plural(t.count, what[0], what[1]) + "</span>"; }
      return "<td" + cls(k) + ">" + cell + "</td>";
    }).join("") + "</tr></tfoot>";
  }
  function table(rows, cols, detail, opened, totals) {
    cols = cols.filter(function (k) { return !COLS[k].opt || rows.some(COLS[k].opt); });
    if (detail) { var at = cols[0] === "pick" ? 1 : 0; cols = cols.slice(0, at).concat(["more"], cols.slice(at)); }
    /* a searched list's columns, while its rows are drawn: what a row shows, for the mark of what found it (QA 3) */
    DRAWN = null;
    if (searching()) { DRAWN = {}; cols.forEach(function (k) { DRAWN[k] = true; }); }
    var html = "<div class=\"fin-tw\" data-list=\"" + esc(listKey()) + "\"><table class=\"fin-t\"><thead><tr>" + cols.map(function (k) {
      return "<th" + cls(k) + ">" + (COLS[k].hh ? COLS[k].hh(rows) : esc(COLS[k].h)) + "</th>";
    }).join("") + "</tr></thead><tbody>" + rows.map(function (r) {
      var open = detail && (opened || state.sc.open)[r.id];
      var attrs = detail ? " class=\"fin-rowbtn" + (open ? " open" : "") + "\" data-row=\"" + esc(r.id) + "\" tabindex=\"0\" aria-expanded=\"" + (open ? "true" : "false") + "\"" : "";
      return "<tr" + attrs + ">" + cols.map(function (k) { return "<td" + cls(k) + ">" + COLS[k].c(r) + "</td>"; }).join("") + "</tr>" +
        (open ? "<tr class=\"fin-detail\"><td colspan=\"" + cols.length + "\">" + detail(r) + "</td></tr>" : "");
    }).join("") + "</tbody>" + (totals ? totalsRow(cols, totals.body, totals.what) : "") + "</table></div>";
    DRAWN = null;
    return html;
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
  /* the search box's fields, by board: an amount within a shekel (in agorot) of any of `nums`, or the text inside `texts`
     joined - app/finance.py b2c_matches and b2b_matches. One list for the rule and for the mark that says which field
     found a row (QA 3, foundMark) */
  var MATCH = {
    sc: { nums: ["money_in", "refunded", "credit_approved"], texts: ["name", "cohort_code", "cohort_title", "invoice", "credit_ref"] },
    b2: { nums: ["amount", "paid_so_far", "balance"], texts: ["account", "deal", "course", "invoice", "name"] } };
  function amountNear(a, n) { var x = a == null ? null : agorot(a); return !!x && Math.abs(x - n) < 100; }
  /* the fields that find a row for a search (trimmed, lowercased), or null: its amounts near the number searched, else
     its texts that hold the search - or, for a search the joined text holds across two of them, the ones it spans */
  function matchedBy(r, q, m) {
    var numText = q.replace(/[,₪\s]/g, "");
    if (/^[0-9]+(\.[0-9]{1,2})?$/.test(numText)) {
      var n = agorot(numText), near = m.nums.filter(function (f) { return amountNear(r[f], n); });
      if (near.length) { return near; }
    }
    var parts = m.texts.map(function (f) { return r[f] == null ? "" : String(r[f]); });
    var at = parts.join(" ").toLowerCase().indexOf(q);
    if (at === -1) { return null; }
    var whole = m.texts.filter(function (f, i) { return parts[i].toLowerCase().indexOf(q) !== -1; });
    if (whole.length) { return whole; }
    var start = 0, spans = [];
    parts.forEach(function (p, i) {
      if (at < start + p.length && at + q.length > start) { spans.push(m.texts[i]); }
      start += p.length + 1;
    });
    return spans.length ? spans : m.texts;
  }
  function scMatch(r, q) {
    q = String(q || "").trim().toLowerCase();
    return !q || !!matchedBy(r, q, MATCH.sc);
  }
  function scFilter(rows, b2c, st, periodKey, q) {
    var months = {}, tasks = {};
    (periodKey ? periodKey.split("+") : []).forEach(function (m) { months[m] = 1; });
    b2c.states.forEach(function (x) { if (x.task) { tasks[x.key] = 1; } });
    var out = rows.filter(function (r) {
      /* D3: "לטיפול" holds a row the books mark from 25.10.2026 on, in its own state (`books_task`) */
      var keep = st === "tasks" ? !!tasks[r.state] || !!r.books_task : (!st || st === r.state);
      return keep && (!periodKey || !!months[r.month]) && scMatch(r, q);
    });
    var one = st && st !== "tasks" ? st : "";
    if (one === "refund_to_execute") { return out.slice().sort(byRefundKey); }
    return sortRows(out, SC_SORT[one] || "-date");
  }
  /* D2, 1.2 (the desk move plan §1b, 24.11): a search looks at every row of its tab, whatever tile, period or company is
     open - Chava's four searches of 17-23.11 all found nothing in the list that was open, three would have found rows
     unfiltered, and "חיפוש ללא סינון" was pressed 0 times. A row found outside the open list carries its own tile beside
     its name (whereMark), and the list's head says the search went over every row. The Excel file and the totals of a
     search are asked with no tile, period or company, so they hold what the screen shows (the server's filter_b2c and
     filter_b2b, held to the page's by tests/test_desk_parity_js.py). */
  function searching() { return !!state.query.trim(); }
  function scRows() {
    scPeriod();
    return searching() ? scFilter(ROWS.sc, VIEW.b2c, "", "", state.query)
      : scFilter(ROWS.sc, VIEW.b2c, state.sc.state, state.sc.period, state.query);
  }
  /* the open list's rows, by id: what the tiles, the period and the company would show without the search */
  function idsOf(rows) { var out = {}; rows.forEach(function (r) { out[r.id] = true; }); return out; }
  /* while a searched list is drawn: its rows outside the open list, by id (null when no search is drawn) */
  var OUTSIDE = null;
  function whereMark(r) {
    if (!OUTSIDE || !OUTSIDE[r.id]) { return ""; }
    return " <span class=\"fin-where\" title=\"" + esc("מחוץ לרשימה הפתוחה") + "\">" +
      (r.board === "b2" ? stateChip(r.state, VIEW.b2b.states, B2_COLOR) : stateChip(r.state)) + "</span>";
  }
  /* QA 3 (24.11): a row a search found by a field its list does not show says which, and what that field holds - "976.19"
     found a row by its refund while the row showed only "סכום ששולם" (₪1,000). The field is named by its column's
     header; a field no column shows (a cohort's title, shown only on hover) by the column it belongs to */
  var FOUND_COL = {
    sc: { money_in: "received", refunded: "refunded", credit_approved: "to_refund", name: "student", cohort_code: "cohort",
      cohort_title: "cohort", invoice: "invoice", credit_ref: "credit_ref" },
    b2: { amount: "amount", paid_so_far: "paid_so_far", balance: "balance", account: "account", deal: "deal", course: "course",
      invoice: "invoice", name: "charge" } };
  var FOUND_HIDDEN = { cohort_title: 1 };       /* in a column that does not show it */
  var FOUND_LTR = { cohort_code: 1, invoice: 1, credit_ref: 1, course: 1 };
  var DRAWN = null;                              /* while a searched list's rows are drawn: its columns, by key */
  function foundMark(r) {
    var q = state.query.trim().toLowerCase(), m = MATCH[r.board], cols = FOUND_COL[r.board];
    if (!DRAWN || !q || !m) { return ""; }
    var by = matchedBy(r, q, m) || [];
    if (!by.length || by.some(function (f) { return !FOUND_HIDDEN[f] && DRAWN[cols[f]]; })) { return ""; }
    return " <span class=\"fin-found\">נמצא לפי: " + by.map(function (f) {
      var v = m.nums.indexOf(f) !== -1 ? money2(r[f]) : FOUND_LTR[f] ? ltr(r[f]) : esc(r[f]);
      return esc(COLS[cols[f]].h) + " " + v;
    }).join(" · ") + "</span>";
  }
  /* the head of a searched list: how many it found, and how many of them are outside the open list, named */
  function searchHead(rows, words, openName) {
    var out = rows.filter(function (r) { return OUTSIDE && OUTSIDE[r.id]; }).length;
    return {
      title: "חיפוש בכל " + words[2],
      line: plural(rows.length, words[0], words[1]) + " · חיפוש: " + esc(state.query) +
        (out ? " · " + (out === 1 ? "אחד מהם" : NF.format(out) + " מהם") + " מחוץ לרשימה הפתוחה" : ""),
      about: "<p class=\"fin-about\">" + esc("החיפוש עובר על כל " + words[2] + ", לא רק על הרשימה הפתוחה (" + openName +
        "). ליד שורה מחוץ לה כתוב השיבוץ שלה.") + "</p>"
    };
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
    /* D3: the server counts "לטיפול" itself (its task states and the rows the books mark from 25.10.2026 on) */
    var open = p.tasks != null ? p.tasks : tasks.reduce(function (a, s) { return a + p.states[s.key].count; }, 0);
    return "<div class=\"fin-states\" role=\"group\" aria-label=\"שיבוץ התקבולים\"><div class=\"fin-stategroup\">" +
      tile("tasks", "לטיפול", open, null, true) +
      tasks.map(function (s) { var x = p.states[s.key]; return tile(s.key, s.label, x.count, money(x.money), true); }).join("") +
      /* QA 5 (24.11): every state, an empty one with its 0 - as a task's tile is - so it is clear the state exists and
         holds nothing ("ביקש החזר, טרם אושר" went missing when empty) */
      "</div><div class=\"fin-stategroup rest\">" +
      rest.map(function (s) {
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
    /* a receipt sent on a payment whose status brought no money in: the status or the receipt is wrong */
    receipt_no_money: function (r) {
      return "שליחת קבלה \"יצאה\", וסטטוס תשלום " + statusWord(r) + ": קבלה יצאה על כסף שלא נכנס";
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
    refund_amount_missing: "סטטוס התשלום אומר שהוחזר, וסכום החזר לא הוקלד", unlinked: "התקבול לא מקושר להרשמה או לליד מקור",
    /* D3: the server's sentence of each mark of the books (which number and why, the two sums, the document) */
    books_ref_invalid: function (r) { return booksText(r, "books_ref_invalid"); },
    books_amount_differs: function (r) { return booksText(r, "books_amount_differs"); },
    books_receipt_unpaid: function (r) { return booksText(r, "books_receipt_unpaid"); }
    /* D4: a proposal of the books is no warning - its sentence sits under its button, in the actions (fillSaid) */
  };
  function booksText(r, k) { var m = booksMark(r, k); return m ? m.text : FLAG[k]; }
  /* D3 fix 2: a number the row cites that is just past the end of its series is not yet in the copy of the books (read
     every hour) - no mark, and the details say so, in the server's words, quietly */
  function booksNote(r) { return r.books_note ? "<p class=\"fin-dx-books\">" + esc(r.books_note) + "</p>" : ""; }
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
      "<a class=\"fin-btn fin-dx-open\" data-open=\"" + esc(r.id) + "\" href=\"" + esc(itemUrl(r.board, r.id)) + "\" target=\"_blank\" rel=\"noopener\">פתיחה במאנדיי</a></div>" +
      body + "</div>";
  }
  var NONE = "<span class=\"fin-subtle\">טרם נרשמה</span>";

  function scSituation(r) {
    var paid = "שולם " + money2(r.money_in) + " ב-" + d(r.date) + (r.date_from === "paid" ? "" : " " + CREATED_BY) +
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
      ["סכום ששולם", COLS.received.c(r)], ["תאריך תקבול", paidDateSaid(r)],
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
    return dx(stateChip(r.state), scSituation(r), actionsBox(r) + refund + payment + linked + warnings(r) + booksNote(r) +
      (others.length ? "<div class=\"fin-dx-more\"><span class=\"fin-subtle\">תקבולים נוספים של אותו סטודנט/ית:</span> " + others.map(function (x) {
        return "<span class=\"fin-nw\">" + d(x.date) + " " + money2(x.money_in) + " " + stateChip(x.state) + "</span>";
      }).join(" · ") + "</div>" : ""), r);
  }

  function b2cHtml() {
    if (state.sc.view === "cohorts" && cohortsPart()) { return cohortsHtml(); }   /* D4: the students by cohort */
    var v = VIEW.b2c, p = scPeriod(), rows = scRows(), q = searching();
    var key = state.sc.state, one = key && key !== "tasks" ? key : "";
    OUTSIDE = null;
    if (q) {
      var open = idsOf(scFilter(ROWS.sc, v, key, state.sc.period, ""));
      OUTSIDE = idsOf(rows.filter(function (r) { return !open[r.id]; }));
    }
    /* the open list's columns stay with a search while every row it found is in that list: what is shown where stays
       (§1b, 1.4). QA 3 (24.11): a row of another tile under a refund's columns showed "-" where its payment date, its
       channel and its receipt are, so a search with a row outside the open list is drawn with the general columns, as a
       search from "לטיפול" is. The box and the row's button of 1.1 at its edges, where a row offers one of the two */
    var outside = q && rows.some(function (r) { return OUTSIDE[r.id]; });
    var cols = withRowActions(SC_COLS[outside ? "" : one] || SC_COLS[""]);
    var what = key === "tasks" ? "לטיפול" : key ? stateLabel(v.states, key) : "כל התקבולים";
    /* a state's own money beside its count; QA 11 (24.11): "הכל" too, its period's "התקבלו" - a sum of the view, never
       one made here. "לטיפול" mixes money in and refunds to make, so it has none */
    var sum = q ? "" : one ? " · " + money(p.states[one].money) : !key ? " · " + money(p.money_in) : "";
    var found = q ? searchHead(rows, ["תקבול אחד", "תקבולים", "התקבולים"], what + " · " + p.label) : null;
    var head = "<div class=\"fin-listhead\"><div><h2>" + (found ? esc(found.title) : esc(what) + " <span class=\"fin-subtle\">· " + esc(p.label) + "</span>") + "</h2>" +
      "<div class=\"fin-subtle\">" + (found ? found.line : plural(rows.length, "תקבול אחד", "תקבולים") + sum) +
      "</div>" + (found ? found.about : aboutHtml("b2c", key)) + "</div><div class=\"fin-actions\">" + scViews() +
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
    /* a search's totals are of every payment it found, as its Excel file (exportList); D3: of the books' part on the screen */
    var tp = { tab: "b2c", state: q ? "" : key, period: q ? "" : state.sc.period, q: state.query.trim() };
    if (VIEW.books && VIEW.books.key) { tp.books = VIEW.books.key; }
    var tot = { body: totalsFor(tp), what: ["תקבול אחד", "תקבולים"] };
    var list = rows.length ? table(rows, cols, detailHtml, null, exportable() ? tot : null) + listNotes([
      guessed && "* סכום החזר ריק או 0, ולכן מוצג כל סכום ששולם (סטטוס תשלום \"תשלום הוחזר\")"])
      : empty(q ? "אין תקבולים שמתאימים לחיפוש." : key === "tasks" ? "אין תקבולים לטיפול." : "אין כאן תקבולים.");
    OUTSIDE = null;
    /* the books' list first: a batch pressed there floats in its panel, and the list's own box then draws none (D4) */
    POP_IN_BOOKS = false;
    var books = key === "tasks" && !q ? booksHtml("b2c") : "";
    return figures + statesHtml(p) + panel(head + batchHtml(rows) + list) + books;
  }
  /* ---------- D4: the students by cohort ("לפי מחזור", knowledge backlog 0313; Chava 26.11) ----------
     A view of the students' tab beside its list, as "לפי לקוח" is of the companies': for each cohort with a price, what its
     students should pay ("צפי תשלום"), what came in ("שולם בפועל") and the balance in Chava's parts by the voucher status,
     "אושר - ממתין לתשלום" - money that should already have come in - its figure first and its column the one tinted; a
     cohort opens to its students, each balance in its part's column, so a column of students sums to its cohort's cell.
     Every word, number and column is the server's (app/desk/cohorts.py, view.b2c.cohorts); the totals of the cohorts a
     search leaves are GET /api/desk/totals's (the list b2c_cohorts) - nothing is added up here. */
  var COHORTS_WORD = "לפי מחזור";
  /* where a cohort and a student (an enrollment) open in monday: their boards, Cohorts and Enrollments */
  var ITEM_BOARD = { co: "5089806632", en: "5092151346" };
  function cohortsPart() { return (VIEW && VIEW.b2c && VIEW.b2c.cohorts) || null; }
  /* the search on the view: the same rule as app/desk/cohorts.py cohort_matches - its code, its name, a student's name */
  function cohortMatch(x, q) {
    q = String(q || "").trim().toLowerCase();
    if (!q) { return true; }
    return [x.code || "", x.title || "", x.name || ""].concat((x.students_list || []).map(function (s) { return s.name; }))
      .join(" ").toLowerCase().indexOf(q) !== -1;
  }
  function cohortsShown() { var c = cohortsPart(); return c ? c.cohorts.filter(function (x) { return cohortMatch(x, state.query); }) : []; }
  function unpricedShown() { var c = cohortsPart(); return c ? c.unpriced.filter(function (x) { return cohortMatch(x, state.query); }) : []; }
  /* the table's columns: the server's, an optional part only when a cohort shown has money in it (cohorts.py columns_for);
     the price and the students counted are said under the cohort's name (`folded`), so the table fits monday's widths */
  function cohortCols(c, shown) {
    return c.columns.filter(function (col) {
      return !col.folded && (!col.shown_when || shown.some(function (x) { return num(x["part_" + col.shown_when]); }));
    });
  }
  function header(c, key) { return (c.columns.filter(function (col) { return col.key === key; })[0] || {}).header || ""; }
  /* an amount as it is: in whole shekels when it has no agorot, with them when it has - never rounded (the cohorts' sums
     are six figures long, and agorot on every one of them pushed the table past monday's width) */
  function cash(v) { var n = num(v); return Math.round(n * 100) % 100 === 0 ? money(n) : money2(n); }
  function scViews() {
    var on = state.sc.view === "cohorts";
    return cohortsPart() ? "<div class=\"fin-seg\" role=\"group\" aria-label=\"תצוגה\">" +
      "<button type=\"button\" class=\"fin-btn" + (on ? "" : " on") + "\" aria-pressed=\"" + !on + "\" data-scview=\"list\">רשימה</button>" +
      "<button type=\"button\" class=\"fin-btn" + (on ? " on" : "") + "\" aria-pressed=\"" + on + "\" data-scview=\"cohorts\">" +
      esc(COHORTS_WORD) + "</button></div>" : "";
  }
  function itemLink(board, id, cls, inner, label) {
    return "<a class=\"" + cls + "\" data-open=\"" + esc(id) + "\" href=\"" + esc(ACCOUNT_URL + "/boards/" + ITEM_BOARD[board] + "/pulses/" + id) +
      "\" target=\"_blank\" rel=\"noopener\"" + (label ? " aria-label=\"" + esc(label) + "\" title=\"" + esc(label) + "\"" : "") + ">" + inner + "</a>";
  }
  function hi(col) { return col.key === "part_approved" ? " fin-hi" : ""; }
  function cohortCell(col, x) {
    var k = col.key;
    if (k === "cohort") {
      var c = cohortsPart();
      return itemLink("co", x.id, "fin-name", "<bdi class=\"fin-nw\">" + ltr(x.code) + "</bdi>") + " " + esc(x.title) +
        "<span class=\"fin-sub-said\">" +
        esc(header(c, "price")) + " " + cash(x.price) + " · " + NF.format(x.students) + " " + esc(header(c, "students")) +
        (x.status ? " · " + esc(x.status) : "") + "</span>";
    }
    return num(x[k]) ? cash(x[k]) : NOT_SET;
  }
  /* a student under its cohort: each balance in its own part's column; what a waived or an out student paid struck through,
     as "סכום מחוק" is (shown, in no sum of the line) */
  function studentCell(col, s) {
    var k = col.key, counted = s.standing === "counted";
    if (k === "cohort") {
      return itemLink("en", s.id, "fin-name", esc(s.name)) + "<span class=\"fin-sub-said\">" + esc(s.said || s.voucher) + "</span>" +
        (s.notes.length ? "<div class=\"fin-chips\">" + s.notes.map(function (t) { return "<span class=\"fin-chip\">" + esc(t) + "</span>"; }).join("") + "</div>" : "");
    }
    if (k === "expected") { return counted ? cash(s.price) : NOT_SET; }
    if (k === "paid") {
      if (!num(s.paid)) { return NOT_SET; }
      return counted ? cash(s.paid) : "<s class=\"fin-subtle\" title=\"" + esc(s.said + ": לא נספר") + "\">" + cash(s.paid) + "</s>";
    }
    if (k === "balance") { return counted && num(s.balance) ? cash(s.balance) : NOT_SET; }
    return counted && k === "part_" + s.part && num(s.balance) ? cash(s.balance) : NOT_SET;
  }
  /* what a cohort's line holds besides its students, said under them when it is open: the waived and the out with what
     they paid, what was paid over the price, the payments no student holds, and its link to its enrollments where monday
     disagrees with itself */
  function cohortNotes(x) {
    var c = cohortsPart(), w = c.words || {}, out = [];
    if (x.waived) { out.push(w.waived + ": " + plural(x.waived, "סטודנט/ית אחד/ת", "סטודנטים") + (num(x.waived_paid) ? ", שילמו " + amountText(x.waived_paid) : "")); }
    if (x.out) { out.push(w.out + ": " + plural(x.out, "סטודנט/ית אחד/ת", "סטודנטים") + (num(x.out_paid) ? ", שילמו " + amountText(x.out_paid) : "")); }
    if (num(x.overpaid)) { out.push(w.overpaid + ": " + amountText(x.overpaid)); }
    if (x.unlinked.count) { out.push(w.unlinked + ": " + plural(x.unlinked.count, "תקבול אחד", "תקבולים") + ", " + amountText(x.unlinked.money)); }
    if (x.shared.count) { out.push(w.shared + ": " + plural(x.shared.count, "תקבול אחד", "תקבולים") + ", " + amountText(x.shared.money)); }
    if (x.roster_missing) { out.push(plural(x.roster_missing, "הרשמה אחת", "הרשמות") + " שהמחזור מקושר אליהן, ומאנדיי לא החזיר אותן"); }
    if (x.roster_elsewhere) { out.push(plural(x.roster_elsewhere, "הרשמה אחת", "הרשמות") + " שהמחזור מקושר אליהן, וההרשמה עצמה מקושרת למחזור אחר"); }
    return out;
  }
  function cohortsTable(c, shown) {
    /* no column "פתיחה במאנדיי" here: a cohort's code and a student's name open them in monday, and the narrower table fits
       monday's widths with sums as long as real ones */
    var cols = cohortCols(c, shown), span = cols.length + 1;
    var head = "<tr><th class=\"k-more\"></th>" + cols.map(function (col) {
      return "<th class=\"k-" + esc(col.key) + (col.kind === "text" ? "" : " n") + hi(col) + "\">" + esc(col.header) + "</th>";
    }).join("") + "</tr>";
    var body = shown.map(function (x) {
      var open = !!state.sc.cohorts[x.id];
      var row = "<tr class=\"fin-rowbtn" + (open ? " open" : "") + "\" data-cohort=\"" + esc(x.id) + "\" tabindex=\"0\" aria-expanded=\"" + open + "\">" +
        "<td class=\"k-more\">" + MORE + "</td>" + cols.map(function (col) {
          return "<td class=\"k-" + esc(col.key) + (col.kind === "text" ? "" : " n") + hi(col) + "\">" + cohortCell(col, x) + "</td>";
        }).join("") + "</tr>";
      if (!open) { return row; }
      var notes = cohortNotes(x);
      return row + (x.students_list.length ? x.students_list.map(function (s) {
        return "<tr class=\"fin-subrow" + (s.standing === "counted" ? "" : " fin-subrow-off") + "\"><td class=\"k-more\"></td>" + cols.map(function (col) {
          return "<td class=\"k-" + esc(col.key) + (col.kind === "text" ? "" : " n") + hi(col) + "\">" + studentCell(col, s) + "</td>";
        }).join("") + "</tr>";
      }).join("") : "<tr class=\"fin-subrow\"><td colspan=\"" + span + "\" class=\"fin-subnote\">אין סטודנטים במחזור.</td></tr>") +
        (notes.length ? "<tr class=\"fin-subrow\"><td colspan=\"" + span + "\" class=\"fin-subnote\">" + notes.map(esc).join(" · ") + "</td></tr>" : "");
    }).join("");
    /* the totals of the cohorts shown: the server's (GET /api/desk/totals, the list b2c_cohorts), of this reading only */
    var t = exportable() ? totalsFor({ tab: "b2c_cohorts", q: state.query.trim() }) : null;
    var foot = t && !t.failed && !t.other_reading && t.sums ? "<tfoot><tr class=\"fin-total\"><td class=\"k-more\"></td>" + cols.map(function (col) {
      var k = col.key, v = t.sums[k];
      if (k === "cohort") {
        return "<td class=\"k-cohort\"><b>סה\"כ</b> <span class=\"fin-subtle fin-nw\">" + plural(t.count, "מחזור אחד", "מחזורים") +
          " · " + NF.format(num(t.sums.students)) + " " + esc(header(c, "students")) + "</span></td>";
      }
      return "<td class=\"k-" + esc(k) + " n" + hi(col) + "\">" + (num(v) ? cash(v) : NOT_SET) + "</td>";
    }).join("") + "</tr></tfoot>" : "";
    return "<div class=\"fin-tw\" data-list=\"" + esc(listKey()) + "\"><table class=\"fin-t fin-cohorts-t\"><thead>" + head + "</thead><tbody>" + body +
      "</tbody>" + foot + "</table></div>";
  }
  /* the cohorts with no price, or cancelled: listed with why, no money - under the table, opened on demand */
  function unpricedHtml(c, list) {
    if (!c.unpriced.length) { return ""; }
    var open = state.sc.unpriced;
    var why = {};
    (c.unpriced_why || []).forEach(function (x) { why[x.key] = x.label; });
    return "<div class=\"fin-unpriced\"><span>" + esc(c.words.unpriced) + ": " + NF.format(list.length) + "</span> " +
      "<button class=\"fin-linkbtn\" type=\"button\" data-unpriced=\"1\" aria-expanded=\"" + open + "\">" + (open ? "הסתרה" : "הצגה") + "</button>" +
      (open && list.length ? "<ul>" + list.map(function (x) {
        return "<li><bdi class=\"fin-nw\">" + ltr(x.code) + "</bdi> " + esc(x.title) + (x.status ? " · " + esc(x.status) : "") + " · " +
          esc(why[x.why] || x.why_text) + (x.why === "unreadable" ? " <bdi class=\"fin-subtle\">(\"" + esc(x.price_text) + "\")</bdi>" : "") + "</li>";
      }).join("") + "</ul>" : "") + "</div>";
  }
  function cohortsHtml() {
    var c = cohortsPart(), shown = cohortsShown(), t = c.totals, w = c.words || {};
    var ok = VIEW.checks.b2c_ok && c.checks.ties;
    /* "אושר - ממתין לתשלום" first among the figures, and the only one tinted (Chava 26.11: "זה הכי חשוב לי") */
    var figures = "<div class=\"fin-figs\"><dl>" + [[c.parts[0].label, money(t.part_approved), true], [w.balance, money(t.balance)],
      [w.paid, money(t.paid)], [w.expected, money(t.expected)]].map(function (x) {
      return "<div" + (x[2] ? " class=\"fin-hi\"" : "") + "><dt>" + esc(x[0]) + "</dt><dd>" + x[1] + "</dd></div>";
    }).join("") + "</dl><span class=\"fin-figs-note\">" + esc("הסכומים כולל מע\"מ, בכל המחזורים עם מחיר") + "</span></div>";
    var head = "<div class=\"fin-listhead\"><div><h2>" + esc(COHORTS_WORD) + "</h2><div class=\"fin-subtle\">" +
      plural(shown.length, "מחזור אחד", "מחזורים") + " עם מחיר" + (searching() ? " מתאימים לחיפוש" : "") + "</div>" +
      "<p class=\"fin-about\">" + esc(c.about) + "</p>" +
      (c.checks.ties ? "" : "<p class=\"fin-badtext\">" + esc(w.not_tied) + "</p>") + "</div><div class=\"fin-actions\">" + scViews() +
      "<button class=\"fin-btn\" type=\"button\" data-export=\"b2c_cohorts\"" + (shown.length && ok && exportable() ? "" : " disabled") +
      exportTitle(ok) + ">ייצוא לאקסל</button></div></div>";
    var list = shown.length ? cohortsTable(c, shown) : empty(searching() ? "אין מחזור עם מחיר שמתאים לחיפוש." : "אין מחזור עם מחיר.");
    return figures + panel(head + list + unpricedHtml(c, unpricedShown()));
  }
  function onCohortClick(ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest("[data-scview],[data-cohort],[data-unpriced]") : null;
    if (!el || !root.contains(el)) { return; }
    if (el.dataset.scview) {
      if (state.sc.view !== el.dataset.scview) {
        state.sc.view = el.dataset.scview;
        use("view", { view: state.sc.view === "cohorts" ? "cohorts" : "list" });
        redraw();
      }
      return;
    }
    if (el.dataset.cohort) {
      if (ev.target.closest("a,button,input,label")) { return; }
      var open = state.sc.cohorts[el.dataset.cohort] = !state.sc.cohorts[el.dataset.cohort];
      use("row", { state: "cohort", open: !!open });
      redraw(); return;
    }
    if (el.dataset.unpriced) { state.sc.unpriced = !state.sc.unpriced; redraw(); }
  }
  function onCohortKey(ev) {
    if ((ev.key === "Enter" || ev.key === " ") && ev.target && ev.target.matches && ev.target.matches("[data-cohort]") &&
        root.contains(ev.target)) { ev.preventDefault(); ev.target.click(); }
  }

  /* QA 2 (24.11): the file said nothing on the page - no word that it was on its way, nor how many rows it holds. The
     count is the list the reader sees when the file is asked (the server's filter picks the same rows: the parity tests) */
  var EXPORT_WHAT = { b2c: ["תקבול אחד", "תקבולים"], b2b: ["חיוב אחד", "חיובים"], b2b_customers: ["חברה אחת", "חברות"],
    b2c_cohorts: ["מחזור אחד", "מחזורים"] };
  function exportedCount(tab) {
    if (tab === "b2c_cohorts") { return cohortsShown().length; }   /* D4 */
    return tab === "b2b_customers" ? customersShown(VIEW.b2b).length : tab === "b2b" ? b2Rows().length : scRows().length;
  }
  function exportSaid(tab, n) {
    var what = EXPORT_WHAT[tab] || EXPORT_WHAT.b2c;
    return "קובץ האקסל יורד: " + plural(n, what[0], what[1]) + ".";
  }
  function exportList(tab) {
    var count = 0;
    /* a newer reading on its way is waited for first, so the file is built from the list the reader then sees */
    return Promise.resolve(pending).then(function () {
      count = exportedCount(tab);
      var b2 = tab === "b2b" || tab === "b2b_customers", f = b2 ? state.b2 : state.sc;
      /* a search's file holds every row it found, as the screen shows them: no tile, period or company (1.2) */
      var all = searching() && tab !== "b2b_customers";
      /* D4: the students by cohort take the search box only, like "לפי לקוח" */
      var whole = tab === "b2b_customers" || tab === "b2c_cohorts";
      var body = { tab: whole ? tab : b2 ? "b2b" : "b2c", state: whole || all ? "" : f.state,
        period: b2 || all || whole ? "" : state.sc.period, customer: tab === "b2b" && !all ? state.b2.customer : "",
        q: state.query.trim(), taken: (BODY && BODY.taken_at) || "" };
      /* D3: and the books' part on the screen, so a file built from another says so (as the totals are asked by it) */
      if (VIEW.books && VIEW.books.key) { body.books = VIEW.books.key; }
      return deps.post("/api/desk/export-link", body);
    }).then(function (res) {
      var url = new URL(res.url, window.location.href).href;
      var said = function () { flash(exportSaid(tab, count)); };
      /* monday may sandbox the view against downloads: a new tab escapes it, and the file names itself */
      if (deps.monday) {
        return Promise.resolve(deps.monday.execute("openLinkInTab", { url: url })).catch(function () { window.open(url, "_blank", "noopener"); })
          .then(said);
      }
      var a = document.createElement("a"); a.href = url; a.rel = "noopener"; document.body.appendChild(a); a.click(); a.remove();
      said();
    }).catch(function (e) { flash(errorText(e)); });
  }

  /* ---------- D3: what the books say that monday does not (app/desk/books.py) ----------
     Under "לטיפול", a list of its own on each tab: the books' documents of the era that no charge row cites. Everything is
     the server's - the documents, their candidates, the sentences; nothing is counted or matched here. A candidate by name
     is said as one, never as a match: its name opens its own card in monday, and nothing links or fills anything */
  var BOOKS_AS_OF = "לפי הספרים";
  var BOOKS_HEADS = { number: "מספר מסמך", type: "סוג מסמך", date: "תאריך מסמך", card: "שם בכרטיס",
    incl: "סכום בחשבשבת (כולל מע\"מ)", excl: "סכום בחשבשבת (ללא מע\"מ)", candidates: "מועמדת לפי שם", accounts: "חברה",
    why: "למה לא ברור" };
  var BOOKS_COLS = { b2c: ["number", "type", "date", "incl", "card", "candidates"],
    b2b: ["number", "type", "date", "excl", "incl", "card", "accounts"],
    unclear: ["number", "type", "date", "excl", "incl", "card", "why"] };
  /* a time as the page writes it: the reading's (asOf), and the books' copy's */
  function stamp(iso) {
    return iso ? new Date(iso).toLocaleString("he-IL", { timeZone: "Asia/Jerusalem", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
  }
  /* "לפי הספרים נכון ל-..." and, when what is shown is not fresh or there is none, why (the server's sentence) */
  function booksSaid() {
    var b = VIEW.books;
    if (!b) { return ""; }
    var at = stamp(b.as_of);
    return (at ? BOOKS_AS_OF + " נכון ל-" + at + "." : "") + (b.said ? (at ? " " : "") + b.said : "");
  }
  function booksCalm() { var b = VIEW.books; return !b || b.state === "ok" || b.state === "off"; }
  function candidatesHtml(e) {
    var c = e.candidates || [];
    if (!c.length) { return NOT_SET + proposalHtml(e); }
    return c.map(function (x) {
      return "<div class=\"fin-cand\">" + nameLink({ board: "sc", id: x.id }, esc(x.name)) + " " + stateChip(x.state) +
        " <span class=\"fin-subtle\">" + esc(x.status || "ריק") + "</span></div>";
    }).join("") + (e.candidates_more ? "<div class=\"fin-subtle\">ועוד " + NF.format(e.candidates_more) + "</div>" : "") +
      proposalHtml(e);
  }
  /* D4: under a document's candidates, the row the books propose to write its number in (the server's `proposal`: one
     document, one row, of the era) and the button that writes it, as the person - or that it was just written */
  var PROPOSAL_SAID = "הצעה: לרשום בשורה של ";
  function proposalHtml(e) {
    var p = e.proposal, r = p ? rowByKey("sc:" + p.item_id) : null, a = r ? rowFill(r) : null;
    if (!p) { return ""; }
    return "<div class=\"fin-fill\">" + esc(PROPOSAL_SAID + p.name) + (r && r.written ? writtenMark(r)
      : a && fillable(r) ? " <button type=\"button\" class=\"fin-btn fin-fill-btn\" data-books-fill=\"" + esc(rowKey(r)) +
        "\" data-books-from=\"list\"" + (busy() ? " disabled" : "") + ">" + esc(a.button) + "</button>" : "") + "</div>";
  }
  /* above the students' list: every proposal at once, its count (the documents' and the dates alone, allFills), and under
     the list how many of them are a date alone, whose rows already cite their number and are not on it */
  var FILL_ALL = "לרשום את כל ההצעות";
  function fillAllHtml(fills) {
    return fills.length ? "<div class=\"fin-actions\"><button type=\"button\" class=\"fin-btn\" data-books-all=\"1\"" +
      (busy() ? " disabled" : "") + ">" + esc(FILL_ALL) + " <span class=\"fin-subtle\">(" + NF.format(fills.length) +
      ")</span></button></div>" : "";
  }
  function datesAloneSaid(fills) {
    var n = fills.filter(function (r) { return !(r.books_fill || {}).number; }).length;
    return !n ? "" : "<p class=\"fin-listnote\">" + esc(n === 1
      ? "ועוד הצעה אחת לתאריך תקבול בלבד, בתקבול שכבר יש בו אסמכתת חשבונית: היא ליד השורה שלו, ו\"" + FILL_ALL + "\" כותב גם אותה."
      : "ועוד " + NF.format(n) + " הצעות לתאריך תקבול בלבד, בתקבולים שכבר יש בהם אסמכתת חשבונית: הן ליד השורות שלהם, ו\"" +
        FILL_ALL + "\" כותב גם אותן.") + "</p>";
  }
  function booksCell(k, e) {
    if (k === "number") { return ltr(e.number) || NOT_SET; }
    if (k === "type") { return esc(e.type_name); }
    if (k === "date") { return e.date ? d(e.date) : NOT_SET; }
    if (k === "incl") { return e.total_incl != null ? money2(e.total_incl) : NOT_SET; }
    if (k === "excl") { return e.total_excl != null ? money2(e.total_excl) : NOT_SET; }
    if (k === "card") { return e.card ? esc(e.card) : NOT_SET; }
    if (k === "accounts") { return (e.accounts || []).length ? e.accounts.map(esc).join(" · ") : NOT_SET; }
    if (k === "why") { return esc(e.why_text || ""); }
    return candidatesHtml(e);
  }
  function booksTable(kind, docs) {
    var cols = BOOKS_COLS[kind], at = function (k) { return " class=\"b-" + k + (k === "incl" || k === "excl" ? " n" : "") + "\""; };
    return "<div class=\"fin-tw\" data-list=\"" + esc(listKey() + "|books|" + kind) + "\"><table class=\"fin-t fin-books-t\"><thead><tr>" +
      cols.map(function (k) { return "<th" + at(k) + ">" + esc(BOOKS_HEADS[k]) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      docs.map(function (e) {
        return "<tr>" + cols.map(function (k) { return "<td" + at(k) + ">" + booksCell(k, e) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }
  /* one list: its title, how many, its sentence and the books' state; its table, or that it is empty; the vouchers' line */
  function booksPanel(kind, part) {
    var docs = part.documents || [], none = VIEW.books.state === "none";
    var said = booksSaid(), fills = kind === "b2c" ? allFills() : [];
    var head = "<div class=\"fin-listhead\"><div><h2>" + esc(part.title) + "</h2>" +
      (none ? "" : "<div class=\"fin-subtle\">" + plural(docs.length, "מסמך אחד", "מסמכים") + "</div>") +
      "<p class=\"fin-about\">" + esc(part.about) + "</p>" +
      (said ? "<p class=\"fin-books-asof" + (booksCalm() ? "" : " fin-badtext") + "\">" + esc(said) + "</p>" : "") + "</div>" +
      fillAllHtml(fills) + "</div>";
    var body = docs.length ? booksTable(kind, docs) : none ? "" : "<div class=\"fin-empty\"><p>אין מסמכים כאלה.</p></div>";
    /* D4: a batch pressed in this list floats here, beside what was pressed */
    var pop = kind === "b2c" && popInBooks(BATCH) ? batchBox(BATCH) : "";
    if (pop) { POP_IN_BOOKS = true; }
    return "<div class=\"fin-panel fin-books\" data-books=\"" + kind + "\">" + head + body + datesAloneSaid(fills) +
      (part.vouchers_said ? "<p class=\"fin-listnote\">" + esc(part.vouchers_said) + "</p>" : "") + pop + "</div>";
  }
  /* under "לטיפול" of a tab: its list, and the unclear documents when the server sent them (a member of both boards) */
  function booksHtml(tab) {
    var part = VIEW[tab] && VIEW[tab].books;
    if (!VIEW.books || VIEW.books.state === "off" || !part) { return ""; }
    var unclear = VIEW.books_unclear;
    return booksPanel(tab, part) + (unclear && (unclear.documents || []).length ? booksPanel("unclear", unclear) : "");
  }

  /* ---------- companies: one list by state, and the customers' aging ---------- */
  function b2Match(r, q) {
    q = String(q || "").trim().toLowerCase();
    return !q || !!matchedBy(r, q, MATCH.b2);
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
  /* the companies' list on the screen: a search over every charge (1.2), or the tile and the company chosen */
  function b2Rows() {
    var b = VIEW.b2b, f = state.b2;
    return searching() ? b2Filter(ROWS.b2, b, "", "", state.query) : b2Filter(ROWS.b2, b, f.state, f.customer, state.query);
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
      /* "בוטל" is counted in no sum; "ממתין לנתונים" sums a typed amount (app/desk/view.py B2B_STATES), and with none typed
         its 0 said "nothing to bill" of charges whose amount is not known yet (QA 10, 24.11): no amount then */
      var x = N[s.key], m = s.key === "cancelled" || (s.key === "pending" && !num(x.money)) ? null : money(x.money);
      if (s.key === "partial" && x.count) {
        m += " <small>סכום לחיוב (ללא מע\"מ)</small>" + (x.balance_unknown ? "<br><span class=\"fin-badtext\">סכום ששולם (כולל מע\"מ) לא הוקלד " +
          (x.balance_unknown === 1 ? "בחיוב אחד" : "ב-" + NF.format(x.balance_unknown) + " חיובים") + "</span>"
          : "<br>יתרה לגבייה " + money(x.balance) + " <small>כולל מע\"מ</small>");
      }
      return tile(s.key, s.label, x.count, m, s.task, "data-b2state", cur, B2_COLOR);
    }
    return "<div class=\"fin-states\" role=\"group\" aria-label=\"שיבוץ החיובים\"><div class=\"fin-stategroup\">" +
      tile("tasks", "לטיפול", open, null, true, "data-b2state", cur, B2_COLOR) + tasks.map(t).join("") +
      "</div><div class=\"fin-stategroup rest\">" + rest.map(t).join("") +            /* an empty state too (QA 5) */
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
      if (k === "terms") { return "<td></td>"; }
      /* QA 11 (24.11): "לטיפול" is a count, so its total is the companies' counts, shown, added - no money */
      if (k === "tasks") { return "<td class=\"n\">" + NF.format(shown.reduce(function (a, c) { return a + num(c.tasks); }, 0)) + "</td>"; }
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
    var q = searching();
    var rows = b2Rows();
    var key = f.state === "tasks" ? "" : f.state;
    var what = f.state === "tasks" ? "לטיפול" : f.state ? stateLabel(b.states, f.state) : "כל החיובים";
    var views = "<div class=\"fin-seg\" role=\"group\" aria-label=\"תצוגה\">" +
      "<button type=\"button\" class=\"fin-btn" + (f.view === "list" ? " on" : "") + "\" aria-pressed=\"" + (f.view === "list") + "\" data-b2view=\"list\">רשימה</button>" +
      "<button type=\"button\" class=\"fin-btn" + (f.view === "customers" ? " on" : "") + "\" aria-pressed=\"" + (f.view === "customers") + "\" data-b2view=\"customers\">לפי לקוח</button></div>";
    var mine = f.customer && b.by_customer[f.customer];
    /* QA 7 (24.11): with a company open, its name was only in the list's heading, far below its figures, its "פרטי חיוב"
       and its tiles - all of them its own. It heads the tab now, with the way back to every company */
    var company = f.customer ? "<div class=\"fin-co\"><h2 class=\"fin-co-name\">" + esc((mine && mine.name) || who || "") + "</h2>" +
      "<button class=\"fin-linkbtn\" type=\"button\" data-customer=\"\">כל החברות</button></div>" : "";
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
    OUTSIDE = null;
    if (q) {
      var open = idsOf(b2Filter(ROWS.b2, b, f.state, f.customer, ""));
      OUTSIDE = idsOf(rows.filter(function (r) { return !open[r.id]; }));
    }
    var found = q ? searchHead(rows, ["חיוב אחד", "חיובים", "החיובים"], what + (who ? " · " + who : "")) : null;
    var btot = { body: totalsFor({ tab: "b2b", state: q ? "" : f.state === "tasks" ? "tasks" : f.state, customer: q ? "" : f.customer,
      q: state.query.trim() }), what: ["חיוב אחד", "חיובים"] };
    /* a state's own money beside its count (the view's); QA 11 (24.11): "הכל" too - the view has no sum of every charge,
       so it is the server's total of this very list (GET /api/desk/totals, its "סכום לחיוב (ללא מע"מ)" in the table's last
       row), said once it lands, of this reading only. Never a sum made here */
    var all = btot.body && !btot.body.failed && !btot.body.other_reading && btot.body.sums ? btot.body.sums.amount : null;
    var sum = f.customer ? "" : f.state && f.state !== "tasks" && f.state !== "cancelled" ? money(b.numbers[f.state].money)
      : !f.state && all != null ? money(all) : "";
    var head = "<div class=\"fin-listhead\"><div><h2>" + (found ? esc(found.title) : esc(what) + (who ? " <span class=\"fin-subtle\">· " + esc(who) + "</span>" +
      (mine && (mine.terms || mine.terms_missing) ? termsMark(mine.terms) : "") : "")) + "</h2>" +
      "<div class=\"fin-subtle\">" + (found ? found.line : plural(rows.length, "חיוב אחד", "חיובים") +
      (sum ? " · " + sum + " ללא מע\"מ" : "")) +
      (who ? " · <button class=\"fin-linkbtn\" type=\"button\" data-customer=\"\">כל החברות</button>" : "") + "</div>" +
      (found ? found.about : aboutHtml("b2b", f.state)) + "</div><div class=\"fin-actions\">" + views +
      "<button class=\"fin-btn\" type=\"button\" data-export=\"b2b\"" + (rows.length && VIEW.checks.b2b_ok && exportable() ? "" : " disabled") +
      exportTitle(VIEW.checks.b2b_ok) + ">ייצוא לאקסל</button></div></div>";
    /* the ledger: oldest first, with the open money owed up to each charge. A search in a ledger finds every company's
       charges, so it is drawn as the list of every charge, with its company and its tile */
    var ledger = f.customer && !f.state && !q;
    /* QA 3: as on the students' tab, a search with a charge outside the open list is drawn with the general columns */
    var outside = q && rows.some(function (r) { return OUTSIDE[r.id]; });
    var bcols = ledger ? ["demand", "deal", "charge", "amount", "b2state", "due", "ledger", "invoice", "open"]
      : (B2_COLS[outside ? "" : key] || B2_COLS[""]);
    var list = rows.length ? table(rows, bcols, b2Detail, state.b2.open, exportable() ? btot : null)
      : empty(q ? "אין חיובים שמתאימים לחיפוש." : f.state === "tasks" && !f.customer ? "אין חיובים לטיפול." : "אין כאן חיובים.",
        /* no search, one company in one state: that company's every charge, its ledger */
        !q && f.customer && f.state !== "" ? "<button class=\"fin-linkbtn\" type=\"button\" data-b2state=\"\">כל החיובים של החברה</button>" : "");
    OUTSIDE = null;
    return company + figures + b2Tiles(b) + panel(head + list) + (f.state === "tasks" && !f.customer && !q ? booksHtml("b2b") : "");
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
     naming another index went out). Held to app/desk/view.py desk_writes() by a test; a write outside it is refused here,
     before anything is sent. Since D2 (ADR-0188, the desk move plan §1b item 4) also a customer's four billing details on
     Accounts, written as the person: each text by its own rule (shapeOk), "תנאי תשלום" by the dropdown ids the form offers -
     and only on an account the desk shows (ITEM_GUARD). */
  var WRITABLE = {
    "5093448677": { color_mm1kxpvt: [4, 5], numeric_mm62za1f: "number", text_mm8fjzfj: "text", text_mm6d6s97: "text",
      color_mm4cq1hw: [1], date_mm8yh4bn: "date" },
    "5097721228": { color_mm3zx2n7: [6], text_mm3y6snz: "text", date_mm4a6y8y: "date", color_mm4bhvta: [0, 1],
      numeric_mm8zqhag: "number" },
    "5089697343": { text_mm3y3gm7: "vat_id", text_mm3yprqa: "hash_key", text_mm3yfrew: "invoice_name",
      dropdown_mm3yd36d: { dropdown: [1, 2, 3, 4, 5] } }
  };
  /* an allowlist entry's kind: a list of indexes is a status, { dropdown: ids } a dropdown, any other its own word */
  function kindOf(entry) {
    return Array.isArray(entry) ? "status" : entry && typeof entry === "object" && Array.isArray(entry.dropdown) ? "dropdown" : entry;
  }
  /* the billing details' rules (analiza-metrics app/account_writer.py `clean` at da671b2): a ח.פ is nine digits, the check
     digit not enforced; a Hashavshevet key up to 15 letters, digits or "-"; a name on an invoice up to 120 characters on one
     line. Each may be empty (a key typed in error is cleared, MON-43); "תנאי תשלום" never is */
  var VAT_ID_RE = /^[0-9]{9}$/, HASH_KEY_RE = /^[0-9A-Za-z-]{1,15}$/, INVOICE_NAME_MAX = 120;
  /* a board whose items the page writes only when the desk shows them: Accounts, a company with a charge on the desk */
  var ITEM_GUARD = { "5089697343": function (id) { return deskShowsAccount(id); } };
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
  /* the page's one other write (D2, ADR-0188): an Update on an item, a billing edit's before and after (MON-41) - sent by
     mondayUpdate only */
  var WRITE_UPDATE = "mutation ($i: ID!, $body: String!) { create_update(item_id: $i, body: $body) { id } }";
  /* a written column on the row the page shows: its label and index, or its value (the row is patched until a reading
     shows the same) */
  var ROW_FIELD = { color_mm1kxpvt: ["status", "status_idx"], color_mm4cq1hw: ["receipt", "receipt_idx"],
    numeric_mm62za1f: ["refund_typed"], text_mm8fjzfj: ["credit_ref"], text_mm6d6s97: ["invoice"], date_mm8yh4bn: ["paid_date"],
    color_mm3zx2n7: ["billing", "billing_idx"], color_mm4bhvta: ["payment", "payment_idx"], text_mm3y6snz: ["invoice"],
    date_mm4a6y8y: ["demand"], numeric_mm8zqhag: ["paid_so_far"] };
  var NUMERIC_FIELD = { refund_typed: 1, paid_so_far: 1 };
  /* whether this person may write from here: "yes", "no" (with why), or "unknown" - monday did not say, so the page tries
     and learns from the answer. From monday's context (permissions.approvedScopes, user.isViewOnly), read at mount. */
  var CAN = { state: "unknown", why: "" };
  /* D2 (ADR-0188): whether monday's context says the app holds updates:write for this person - only then is a billing
     edit's Update written - and the person's monday user id, for its words (the context has no name) */
  var CAN_UPDATE = false, USER_ID = "";
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
  /* QA2 2 (24.11): the items written from here in this visit, by item id: "written", and "told" once the note on monday's
     card was said for it (cardNote). Unlike WRITTEN, kept for the visit: monday's card in this tab stays behind after a
     reading has caught up */
  var WROTE = {};

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
    if (kind === "vat_id") { return typeof v === "string" && (v === "" || VAT_ID_RE.test(v)); }
    if (kind === "hash_key") { return typeof v === "string" && (v === "" || HASH_KEY_RE.test(v)); }
    if (kind === "invoice_name") {
      return typeof v === "string" && v.length <= INVOICE_NAME_MAX && !CONTROL.test(v) && v === v.trim();
    }
    if (kind === "dropdown") {
      return keys.length === 1 && Array.isArray(v.ids) && v.ids.length === 1 && typeof v.ids[0] === "number" &&
        entry.dropdown.indexOf(v.ids[0]) !== -1;
    }
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
    var refused = refusedColumns(boardId, values), guard = ITEM_GUARD[String(boardId)];
    if (refused.length || !/^[0-9]+$/.test(String(itemId)) || (guard && !guard(String(itemId)))) {
      return Promise.reject({ code: "write_refused", text: refused.join(", ") });
    }
    return mondayCall(WRITE_ROW, { b: String(boardId), i: String(itemId), v: JSON.stringify(values), c: Object.keys(values) })
      .then(function (data) { return cellsOf((data.change_multiple_column_values || {}).column_values); });
  }
  /* The Update door: an Update only on an item of a board listed here, only on an item the desk shows (ITEM_GUARD), and
     only when monday's context says the app holds updates:write for this person (CAN_UPDATE) - otherwise it is never
     tried, and no scope is ever asked for. Its body is HTML whose every value was escaped (billUpdateBody). */
  var UPDATE_BOARDS = { "5089697343": true }, UPDATE_MAX = 2000;
  function mondayUpdate(boardId, itemId, body) {
    var guard = ITEM_GUARD[String(boardId)];
    if (!UPDATE_BOARDS[String(boardId)] || !CAN_UPDATE || !/^[0-9]+$/.test(String(itemId)) || (guard && !guard(String(itemId))) ||
        typeof body !== "string" || !body || body.length > UPDATE_MAX || CONTROL.test(body)) {
      return Promise.reject({ code: "write_refused", text: "update" });
    }
    return mondayCall(WRITE_UPDATE, { i: String(itemId), body: body }).then(function (data) {
      if (!(data.create_update && data.create_update.id)) { throw { code: "unverified", text: "update" }; }
      return String(data.create_update.id);
    });
  }
  /* rows as monday holds them now, read as the person, in one request: every column there on the board they should be
     on. {id: {name, cells, columns}} for the items monday answered from that board; an item it did not answer, or answered
     from another board, is not in it (a row's batch, 1.1, reads its rows so; BATCH_MAX keeps them in one answer) */
  function readRows(boardId, itemIds, cols) {
    return mondayCall(READ_ROW, { i: itemIds.map(String), c: cols, b: [String(boardId)] }).then(function (data) {
      var columns = {};
      ((((data.boards || [])[0]) || {}).columns || []).forEach(function (c) { if (c && c.id) { columns[c.id] = c; } });
      var items = {};
      (data.items || []).forEach(function (item) {
        if (!item || String((item.board || {}).id) !== String(boardId)) { return; }
        items[String(item.id)] = { name: item.name || "", cells: cellsOf(item.column_values), columns: columns };
      });
      var missing = cols.filter(function (c) { return !columns[c]; });
      if (missing.length && Object.keys(items).length) { throw { code: "columns_changed", text: missing.join(", ") }; }
      return items;
    });
  }
  /* the row as monday holds it now, read as the person: on the board it should be on, every column there */
  function readRow(boardId, itemId, cols) {
    return readRows(boardId, [itemId], cols).then(function (items) {
      var item = items[String(itemId)];
      if (!item) { throw { code: "not_found", text: "" }; }
      return item;
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
        } else if (w.books) {
          /* D4: what the books propose for the row (the server's `books_fill`, given here in `typed` by column) - never
             typed by the person, never today: not proposed, not written; an empty cell only (if_empty) */
          var proposed = typed[w.column];
          if (proposed === undefined || proposed === "") { return; }
          if (w.if_empty && cell.text) { line.kept = true; lines.push(line); return; }
          values[w.column] = w.kind === "date" ? { date: proposed } : proposed;
          line.to = proposed;
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
    var id = rk.slice(rk.indexOf(":") + 1);
    WROTE[id] = WROTE[id] || "written";
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
      /* D4: a "תאריך תקבול" written from here is the day the row is shown by, as the next reading will file it */
      if (w.fields.paid_date && row.paid_date === w.fields.paid_date) { row.date = row.paid_date; row.date_from = "paid"; }
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
      landed();
      pending = deps.post("/api/desk/refresh").then(function (next) {
        takeNewer(next);
        landed();
        if (!settled() && tries < 2) { later(function () { refreshAfterWrite(tries + 1); }, 5000); }
      }, function (e) {
        if (BODY) { BODY.refreshing = false; }
        landed();
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
    landed();
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
    /* the plan is read out where it appears: focus on the box itself, never on "כתיבה" (a key held down must not write) */
    landed(function () {
      var box = root && root.querySelector ? root.querySelector(".fin-act-plan") : null;
      if (box && box.focus) { box.focus(); }
    });
    return true;
  }
  function startAction(rk, key) {
    var r = rowByKey(rk);
    var a = r && rowActions(r).filter(function (x) { return x.key === key; })[0];
    if (!a || CAN.state === "no" || busy()) { return; }
    if (BATCH && !busyBatch()) { BATCH = null; }          /* a batch's confirmation, not being written, gives way */
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
      landed();
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
    var off = CAN.state === "no" || busy() || (!!act && act.phase === "confirm");
    return "<section class=\"fin-dx-block fin-acts\" aria-label=\"פעולות\"><h3 class=\"fin-dx-h\">פעולות <span class=\"fin-dx-name\">" +
      "נכתבות במאנדיי בשמך</span></h3>" + (CAN.state === "no" ? "<p class=\"fin-act-why\">" + esc(ABILITY[CAN.why] || ABILITY.no_scope) + "</p>" : "") +
      "<div class=\"fin-act-row\">" + list.map(function (a) {
        return fillAction(a) ? fillControl(r, a, off) : actionControl(r, a, off);
      }).join("") + "</div>" + fillSaid(r) + (act ? actStatus(act) : "") + "</section>";
  }
  /* D4: what the books propose for the row, in the server's sentence (the document, its day and total, what is written),
     quietly under the actions: a proposal, never a warning */
  function fillSaid(r) {
    var m = booksMark(r, "books_ref_fill") || booksMark(r, "books_date_fill");
    return m && rowFill(r) ? "<p class=\"fin-dx-books fin-fill-said\">" + esc(m.text) + "</p>" : "";
  }
  /* D4: "רישום מחשבשבת" in a row's details - nothing to type: the batch of one row, floating beside this button */
  function fillControl(r, a, off) {
    return "<div class=\"fin-act\"><button type=\"button\" class=\"fin-btn\" data-books-fill=\"" + esc(rowKey(r)) +
      "\" data-books-from=\"detail\"" + (off || !fillable(r) ? " disabled" : "") +
      (CAN.state === "no" ? " title=\"" + esc(ABILITY[CAN.why] || ABILITY.no_scope) + "\"" : "") + ">" + esc(a.button) + "</button></div>";
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
      CAN_UPDATE = u.isViewOnly !== true && !!p && Array.isArray(p.approvedScopes) && has(p.approvedScopes, "updates:write");
      USER_ID = String(u.id == null ? "" : u.id).replace(/[^0-9]/g, "").slice(0, 20);
    }, function () { CAN = { state: "unknown", why: "" }; }).then(function () { if (VIEW) { landed(); } });
  }

  /* ---------- 1.1 (D2, the desk move plan §1b): two actions on the row itself, for one row or several at once ----------
     In 17-24.11 Chava made 36 edits on Student Charge, all in monday: 21 "שליחת קבלה" to "יצאה" (receipt_sent) and 9
     "סטטוס תשלום" to "תשלום הוחזר" (refund_done), eight of these within two minutes on 22.11 - and pressed no action of
     the desk, where they sat in a row's details behind a confirm step. So these two sit on the row itself: its button is
     one press to them, and a box at the row's start picks it for several at once, one press for them all. Either way
     ONE confirmation lists what will be written in each row, from the rows read live as the person (one request); a row
     whose value changed in monday since the desk's reading is not written, and the confirmation says so. On "כתיבה" each
     row is read again right before its write, written through mondayWrite (the allowlist as it is) and checked against
     monday's answer - exactly as a single action (confirmAction) - one row after another; a row that changed since the
     confirmation was shown is not written. The result is said per row: written, not written and why, failed and why.
     One refresh of the reading after the batch, as after one action (until the stored reading lands, a forced reading
     reads every board, about 9 seconds). The buttons sit only on rows whose state offers the action (the server's
     per-row `actions`), and only where the person may write. */
  var ROW_ACTIONS = { receipt_sent: 1, refund_done: 1 };
  /* at most this many rows in one batch: its rows are read in one request, and monday's items(ids:) answers 25 at most */
  var BATCH_MAX = 25;
  var PICKED = {};     /* the rows picked for a batch, by "sc:123"; only the rows of the list on the screen count */
  /* the bar with nothing picked (QA 1): where the picks will show, and what the box at a row's start does */
  var PICK_HINT = "לא נבחרו שורות. אפשר לבחור כמה בתיבה שבתחילת השורה, ולסמן בכולן יחד.";
  var BATCH = null;    /* the batch on the screen: { action, a, items: [{ key, row, name, verdict, why, live, plan, result }],
                          phase: "reading" | "confirm" | "nothing" | "writing" | "done" | "failed", at, total, error } */
  function busyBatch() { return !!BATCH && (BATCH.phase === "reading" || BATCH.phase === "writing"); }
  function busy() { return busyAct() || busyBatch(); }
  /* the row's one action of the two, as the server offers it: one step that sets one status, nothing typed */
  function rowAction(r) {
    return rowActions(r).filter(function (a) {
      return ROW_ACTIONS[a.key] && a.steps.length === 1 && a.steps[0].length === 1 && a.steps[0][0].kind === "status" &&
        !a.steps[0][0].input;
    })[0] || null;
  }
  /* a row that carries the button and the box: it offers one of the two, the person may write, and it was not just
     written from here (its new value waits for a reading) */
  function pickable(r) { return CAN.state !== "no" && !r.written && !!rowAction(r); }
  function pickedIn(rows) { return rows.filter(function (r) { return PICKED[rowKey(r)] && pickable(r); }); }

  /* ---------- D4 (26.11): "רישום מחשבשבת" - what the books propose for a row, written as the person ----------
     The server proposes (app/desk/books.py `proposals`: FR-C2's number and A1's date, the desk's era only) and sends, on
     the row, its values (`books_fill`) and the action that writes them (DESK_ACTIONS `books_fill`: each write is the
     proposal's value under its `books` key, only into an empty cell, if_empty). The page proposes nothing. From the row's
     details, from its document in the books' list ("הצעה: לרשום בשורה של ..."), or every proposal at once ("לרשום את כל
     ההצעות", above the list), it is the batch of 1.1: the rows read live as the person in one request, one confirmation
     of what each row will be written, each row read again right before its write and checked after, the result said row
     by row, one refresh after. A row whose cells are not what the desk read is skipped and said, never written over - but
     a cell written since with the very value the books propose, which is not written again - and its box floats beside
     what was pressed (QA2 3), so no row moves. */
  function fillAction(a) {
    var ws = a ? stepWrites(a) : [];
    return !!ws.length && a.steps.length === 1 && ws.every(function (w) { return !!w.books && !w.input; });
  }
  function rowFill(r) { return rowActions(r).filter(fillAction)[0] || null; }
  /* a row whose proposal may be written from here now: the person may write, it was not just written, the books propose */
  function fillable(r) { return CAN.state !== "no" && !r.written && !!r.books_fill && !!rowFill(r); }
  /* the books' proposal for a row, by column: what the batch writes in place of what a box would hold */
  function proposalOf(r, a) {
    var out = {};
    stepWrites(a).forEach(function (w) { var v = (r.books_fill || {})[w.books]; if (v) { out[w.column] = String(v); } });
    return out;
  }
  /* a cell's text as the desk's reading holds it and as monday holds it now: a date by its day */
  function cellText(kind, text) { text = String(text == null ? "" : text); return kind === "date" ? text.slice(0, 10) : text; }
  function shownField(r, w) { var f = (ROW_FIELD[w.column] || [])[0]; return cellText(w.kind, f ? r[f] : ""); }
  /* why a row is not planned (""; when it may be): a cell that is not what the desk read - unless it holds now the very
     value the books propose - or a cell the books propose a value for that is not empty (a mark a right-to-left editor
     leaves reads as empty to the rules, never here) */
  function fillMoved(a, r, live, typed) {
    var why = "";
    stepWrites(a).forEach(function (w) {
      var cell = live.cells[w.column] || { text: "", index: null, label: "" }, now = cellText(w.kind, cell.text);
      if (why || (typed[w.column] !== undefined && now === typed[w.column])) { return; }
      if (now !== shownField(r, w)) { why = "השתנתה במאנדיי מאז הקריאה, ועכשיו " + w.title + " " + shownCell(w.kind, cell); }
      else if (typed[w.column] !== undefined && now !== "") { why = "יש כבר ערך ב" + w.title + " במאנדיי: " + shownCell(w.kind, cell); }
    });
    return why;
  }
  /* The hostile review of D4 (26.11): a batch kept the rows of the reading it was pressed on, so a newer reading that landed
     while its confirmation was open - a refresh, or the view taking the focus back from monday's item card (askAgain) -
     withdrew a proposal on the screen and "כתיבה" still wrote it. A proposal is now always the reading's on the screen: read
     off the current rows by id when the confirmation is made (settleFills), checked again when a newer reading lands while
     it is open (recheckBatch), and once more right before each row's write (confirmBatch). A row whose proposal left or
     changed is not written, and the confirmation and the report say so in one of these closed words */
  var FILL_GONE = "בקריאה החדשה של הלוחות כבר אין הצעה לשורה הזאת";
  var FILL_CHANGED = "ההצעה השתנתה מאז שנפתח האישור, בקריאה החדשה של הלוחות";
  var FILL_CLOSED = "קריאה חדשה של הלוחות נחתה בזמן שהאישור היה פתוח, ואין בה עוד מה לכתוב מההצעות שבו. האישור נסגר.";
  /* the row on the screen now offers a proposal: the server's values, and the action that writes them */
  function offersFill(r) { return !!r && !!r.books_fill && !!rowFill(r); }
  /* why a row's confirmed proposal may no longer be written ("" when it may): the reading on the screen holds none for it,
     or another one */
  function proposalMoved(a, it) {
    var r = rowByKey(it.key);
    if (!offersFill(r)) { return FILL_GONE; }
    return JSON.stringify(proposalOf(r, a)) === JSON.stringify(it.typed || {}) ? "" : FILL_CHANGED;
  }
  function settleFills(batch, got) {
    batch.items.forEach(function (it) {
      var live = got[String(it.row.id)];
      it.live = live || null;
      if (!live) { it.verdict = "skip"; it.why = WRITE_ERRORS.not_found; return; }
      it.name = live.name || it.name;
      /* the row as the reading on the screen holds it now: a newer one may have landed while the rows were read */
      var r = rowByKey(it.key);
      if (!offersFill(r)) { it.verdict = "skip"; it.why = FILL_GONE; return; }
      it.row = r;
      it.typed = proposalOf(r, batch.a);
      var why = fillMoved(batch.a, it.row, live, it.typed), plan = planAction(batch.a, live, it.typed, israelToday(), {});
      if (why) { it.verdict = "skip"; it.why = why; return; }
      if (!plan.steps.length) { it.verdict = "skip"; it.why = "כבר כך במאנדיי"; return; }
      it.verdict = "write";
      it.plan = plan;
    });
  }
  /* a newer reading landed (index) while a confirmation of the books' proposals is open: each row it would write is held
     to the reading now on the screen. A proposal that left or changed moves to "לא ייכתב" with why, the count follows, and
     what waits for the next press is counted again; with nothing left to write, the confirmation closes and says why */
  function recheckBatch() {
    var b = BATCH;
    if (!b || b.phase !== "confirm" || !fillAction(b.a)) { return; }
    b.items.forEach(function (it) {
      if (it.verdict !== "write") { return; }
      var why = proposalMoved(b.a, it);
      if (why) { it.verdict = "skip"; it.why = why; it.plan = null; } else { it.row = rowByKey(it.key); }
    });
    if (b.from === "all") {
      var held = {};
      b.items.forEach(function (it) { held[it.key] = true; });
      b.left = allFills().filter(function (r) { return !held[rowKey(r)]; }).length;
    }
    if (!b.items.some(function (it) { return it.verdict === "write"; })) { b.phase = "nothing"; b.notice = FILL_CLOSED; }
  }
  /* the rows "לרשום את כל ההצעות" takes: each document's of the books' list in its order, then the dates alone, newest first */
  function allFills() {
    var seen = {}, out = [];
    (((VIEW.b2c || {}).books || {}).documents || []).forEach(function (e) {
      var r = e.proposal ? rowByKey("sc:" + e.proposal.item_id) : null;
      if (r && !seen[r.id] && fillable(r)) { seen[r.id] = true; out.push(r); }
    });
    return out.concat(sortRows(ROWS.sc.filter(function (r) { return !seen[r.id] && fillable(r); }), "-date"));
  }
  /* the batch's action for a row: one of the two on the row itself (pickable), or what the books propose (fillable) */
  function batchAction(r, key) {
    var a = ROW_ACTIONS[key] ? (pickable(r) ? rowAction(r) : null) : fillable(r) ? rowFill(r) : null;
    return a && a.key === key ? a : null;
  }
  var BATCH_FROM = { row: 1, bar: 1, detail: 1, list: 1, all: 1 };
  /* `from`: what was pressed - "row", the row's own button, or "bar", the bar's; D4: "detail", the row's details', "list",
     its document's in the books' list, or "all", above that list - beside which the batch floats (QA2 3) */
  function startBatch(actionKey, keys, from) {
    if (CAN.state === "no" || busy()) { return; }
    var items = [], a = null, left = 0;
    keys.forEach(function (k) {
      var r = rowByKey(k), x = r ? batchAction(r, actionKey) : null;
      if (!x) { return; }
      if (items.length >= BATCH_MAX) { left += 1; return; }
      a = a || x;
      items.push({ key: k, row: r, name: r.name });
    });
    if (!items.length) { return; }
    /* one thing on the screen at a time: a single action's plan, not being written, gives way */
    if (ACT && !busyAct()) { ACT = null; }
    var books = fillAction(a);
    /* `bar`: the list's bar was there when it was pressed, so its place is held while the batch is on the screen */
    var batch = BATCH = { action: actionKey, a: a, items: items, phase: "reading", at: 0, total: 0, error: "",
      from: BATCH_FROM[from] ? from : "bar", left: left, bar: !books || scRows().some(pickable) };
    use("act", { action: actionKey, phase: "batch_start" });
    if (books) { use("books_fill", { from: from === "all" ? "all" : from === "list" ? "list" : "detail", rows: Math.min(items.length, 100) }); }
    redraw();
    readRows(boardIdOf(items[0].row), items.map(function (it) { return it.row.id; }), stepWrites(a).map(function (w) { return w.column; }))
      .then(function (got) {
        if (BATCH === batch) { settleBatch(batch, got); }
      }, function (e) { if (BATCH === batch) { failBatch(batch, e); } });
  }
  /* a status cell as the desk's reading shows it: its index, "" when empty */
  function shownIndex(r, column) {
    var f = ROW_FIELD[column];
    return f && f.length === 2 ? String(r[f[1]] == null ? "" : r[f[1]]) : "";
  }
  /* the two on the row itself: one status each. Whether a label moved (the whole batch then fails, before any write) */
  function settleStatuses(batch, got) {
    var w = batch.a.steps[0][0], labelsMoved = false;
    batch.items.forEach(function (it) {
      var live = got[String(it.row.id)];
      it.live = live || null;
      if (!live) { it.verdict = "skip"; it.why = WRITE_ERRORS.not_found; return; }
      it.name = live.name || it.name;
      var cell = live.cells[w.column] || { text: "", index: null, label: "" }, held = cell.index == null ? "" : String(cell.index);
      var plan = planAction(batch.a, live, {}, israelToday(), {});
      if (plan.problems.length) { labelsMoved = true; return; }
      if (held === String(w.index)) { it.verdict = "skip"; it.why = "כבר " + shownCell("status", cell) + " במאנדיי"; return; }
      /* the desk showed another value: monday changed since the reading, and what is there now is not written over */
      if (held !== shownIndex(it.row, w.column)) {
        it.verdict = "skip";
        it.why = "השתנתה במאנדיי מאז הקריאה, ועכשיו " + w.title + " " + shownCell("status", cell);
        return;
      }
      it.verdict = "write";
      it.plan = plan;
    });
    return labelsMoved;
  }
  function settleBatch(batch, got) {
    var w = batch.a.steps[0][0];
    /* D4: what the books propose has its own rules (settleFills) */
    if (fillAction(batch.a)) { settleFills(batch, got); }
    else if (settleStatuses(batch, got)) { failBatch(batch, { code: "label_moved", text: w.title + " " + w.label }); return; }
    var n = batch.items.filter(function (it) { return it.verdict === "write"; }).length;
    batch.phase = n ? "confirm" : "nothing";
    use("act", { action: batch.action, phase: n ? "batch_shown" : "batch_nothing" });
    /* the confirmation is read out where it appears: focus on the box, never on "כתיבה" (a key held down must not write).
       It floats in the view (QA2 3), so the focus scrolls nothing */
    landed(function () {
      var box = root && root.querySelector ? root.querySelector(".fin-batch-plan") : null;
      if (box && box.focus) { box.focus({ preventScroll: true }); }
    });
  }
  function failBatch(batch, e) {
    if (e && e.code === "no_scope") { CAN = { state: "no", why: "no_scope" }; }
    use("act", { action: batch.action, phase: "batch_failed", code: useWord(e && e.code) || "monday" });
    batch.phase = "failed";
    batch.error = errorWords(e);
    batch.detail = (e && e.text) || "";
    landed();
    flash(batch.error);
  }
  function confirmBatch() {
    var batch = BATCH;
    if (!batch || batch.phase !== "confirm" || busyAct()) { return; }
    var w = batch.a.steps[0][0], boardId = boardIdOf(batch.items[0].row), stop = "", maybe = false;
    var writes = stepWrites(batch.a), cols = writes.map(function (x) { return x.column; });
    var todo = batch.items.filter(function (it) { return it.verdict === "write"; });
    batch.phase = "writing"; batch.at = 0; batch.total = todo.length;
    /* the picks are the batch's now: whatever happens, the report says it row by row (D4: a proposal of the books takes
       no pick, and leaves them as they are) */
    if (!fillAction(batch.a)) { batch.items.forEach(function (it) { delete PICKED[it.key]; }); }
    use("act", { action: batch.action, phase: "batch_confirm" });
    redraw();
    todo.reduce(function (p, it) {
      return p.then(function () {
        if (stop) { it.result = "failed"; it.why = stop + ". לא נשלח."; return null; }
        batch.at += 1;
        landed();
        /* the row again, as the person, right before its write: written only if it still holds what the confirmation
           showed (and its label still stands for the index), as a single action's confirmAction */
        return readRow(boardId, it.row.id, cols).then(function (live) {
          var now = planAction(batch.a, live, it.typed || {}, israelToday(), {});
          if (changedSince(it.live, live, cols) || now.problems.length ||
              JSON.stringify(now.steps) !== JSON.stringify(it.plan.steps)) {
            /* the first cell that moved is said, as monday holds it now */
            var x = writes.filter(function (c) { return changedSince(it.live, live, [c.column]); })[0] || w;
            it.result = "skipped";
            it.why = "השתנתה במאנדיי מאז שהתוכנית הוצגה, ועכשיו " + x.title + " " +
              shownCell(x.kind, live.cells[x.column] || { index: null, text: "" });
            return null;
          }
          /* D4's review: what the books propose is the reading's on the screen, right before the write - a newer reading
             may have landed since the confirmation was shown, or while the rows before this one were written */
          var moved = fillAction(batch.a) ? proposalMoved(batch.a, it) : "";
          if (moved) { it.result = "skipped"; it.why = moved; return null; }
          return runSteps(boardId, it.row.id, it.plan.steps).then(function (cells) {
            noteWritten(it.key, cells, it.live);
            it.result = "written";
          });
        }).catch(function (e) {
          e = e || {};
          if (e.cells && Object.keys(e.cells).length) { noteWritten(it.key, e.cells, it.live); }
          /* something may be in monday that the screen does not show: a write monday kept otherwise, or one it did not
             answer in time */
          if ((e.landed && e.landed.length) || e.code === "unverified" || (e.code === "timeout" && e.landed)) { maybe = true; }
          it.result = "failed";
          it.why = errorWords(e);
          /* what fails every row alike stops the rest: the app's permission, or monday asking to slow down */
          if (e.code === "no_scope") { CAN = { state: "no", why: "no_scope" }; stop = WRITE_ERRORS.no_scope; }
          if (e.code === "rate_limited") { stop = WRITE_ERRORS.rate_limited; }
        });
      });
    }, Promise.resolve()).then(function () {
      var written = todo.filter(function (it) { return it.result === "written"; }).length;
      batch.phase = "done";
      use("act", { action: batch.action, phase: "batch_done",
        code: written === todo.length ? "" : written ? "partial" : "none" });
      landed();
      flash(written ? "נכתב במאנדיי בשמך ב" + (written === 1 ? "שורה אחת" : "-" + NF.format(written) + " שורות") +
        (written < todo.length ? ", ובשאר לא: הפירוט ברשימה" : "") + ". המסך מתעדכן." : "לא נכתב כלום: הפירוט ברשימה.");
      /* one refresh after the batch, as after one action (refreshAfterWrite asks again at most twice while a written row
         is not shown yet) */
      if (written || maybe) { refreshAfterWrite(0); }
    });
  }
  function batchLine(it, words) {
    return "<li><b>" + esc(it.name) + "</b> · " + esc(words) + "</li>";
  }
  /* QA2 3 (24.11): "the confirmation screen pushes the table down, and the mouse stays over the button of another row". The
     batch took the bar's place above the table, taller than it (every row 69 pixels lower), and with the list scrolled down
     the focus on its box scrolled the page up to it (about 550 pixels). Now the bar keeps its place, and the batch - its
     reading, its one confirmation, its progress, its report - floats beside what was pressed (fin-pop, placePop): above the
     row, or the bar, where the view has room, below it otherwise, never over it. No row moves when it opens or closes */
  function batchHtml(rows) {
    return barHtml(rows) + (BATCH && !POP_IN_BOOKS ? batchBox(BATCH) : "");
  }
  /* D4: a batch pressed in the books' list floats in that list's panel, beside what was pressed there, while the list is
     drawn (booksPanel sets it); anywhere else, in the list's own (batchHtml) */
  var POP_IN_BOOKS = false;
  function popInBooks(b) { return !!b && (b.from === "list" || b.from === "all"); }
  /* a row's plan as the confirmation says it, each write: now, and what will be written - or that it stays */
  function planWords(p) {
    return p.lines.map(function (l) {
      return l.title + ": " + (l.same || l.kept ? "כבר " + shownCell(l.kind, l.now) + ", לא נכתב"
        : "עכשיו " + shownCell(l.kind, l.now) + ", ייכתב " + shownValue(l.kind === "status" ? "text" : l.kind, l.to));
    }).join(" · ");
  }
  /* what was written in a row, write by write (D4: each row its own values) */
  function writtenWords(p) {
    return p.lines.filter(function (l) { return !l.same && !l.kept; }).map(function (l) {
      return l.title + " " + shownValue(l.kind === "status" ? "text" : l.kind, l.to);
    }).join(", ");
  }
  function batchBox(b) {
    var close = "<button type=\"button\" class=\"fin-linkbtn\" data-batch-close=\"1\">סגירה</button>";
    var w = b.a.steps[0][0], books = fillAction(b.a);
    if (b.phase === "reading") {
      return "<div class=\"fin-batch fin-pop\"><p class=\"fin-act-msg\" role=\"status\">קורא במאנדיי " +
        (b.items.length === 1 ? "את השורה" : "את " + NF.format(b.items.length) + " השורות") + "...</p></div>";
    }
    if (b.phase === "failed") {
      return "<div class=\"fin-batch fin-pop\"><p class=\"fin-act-msg bad\" role=\"alert\">" + esc(b.error) + " " + close + "</p>" +
        (b.detail ? "<p class=\"fin-subtle fin-act-detail\" dir=\"auto\">מאנדיי: " + esc(b.detail) + "</p>" : "") + "</div>";
    }
    var writes = b.items.filter(function (it) { return it.verdict === "write"; });
    var skips = b.items.filter(function (it) { return it.verdict === "skip"; });
    var notWritten = skips.length ? "<p>לא ייכתב:</p><ul>" + skips.map(function (it) { return batchLine(it, it.why); }).join("") + "</ul>" : "";
    if (b.phase === "nothing") {
      /* D4's review: a confirmation a newer reading left with nothing to write says so first (recheckBatch) */
      return "<div class=\"fin-batch fin-act-plan fin-batch-plan fin-pop\" role=\"group\" aria-label=\"מה ייכתב\" tabindex=\"-1\">" +
        (b.notice ? "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(b.notice) + "</p>" : "") +
        "<p>אין מה לכתוב במאנדיי:</p><ul>" + skips.map(function (it) { return batchLine(it, it.why); }).join("") + "</ul>" +
        "<div class=\"fin-act-go\">" + close + "</div></div>";
    }
    if (b.phase === "confirm") {
      return "<div class=\"fin-batch fin-act-plan fin-batch-plan fin-pop\" role=\"group\" aria-label=\"מה ייכתב\" tabindex=\"-1\">" +
        "<p>" + (writes.length === 1 ? "ייכתב במאנדיי בשורה " + esc("\"" + writes[0].name + "\"") + ", בשמך:"
          : "ייכתב במאנדיי בשמך, ב-" + NF.format(writes.length) + " שורות:") + "</p><ul>" + writes.map(function (it) {
          return batchLine(it, planWords(it.plan));
        }).join("") + "</ul>" + notWritten +
        (b.a.after ? "<p class=\"fin-subtle\">" + esc(b.a.after) + "</p>" : "") +
        /* D4: past one batch's worth, the rest of the proposals wait for the next press */
        (b.left ? "<p class=\"fin-subtle\">" + esc((b.left === 1 ? "ועוד הצעה אחת" : "ועוד " + NF.format(b.left) + " הצעות") +
          ": בלחיצה נוספת אחרי הכתיבה, עד " + BATCH_MAX + " שורות בכתיבה אחת.") + "</p>" : "") +
        "<div class=\"fin-act-go\"><button type=\"button\" class=\"fin-btn fin-primary\" data-batch-go=\"1\">כתיבה</button>" +
        "<button type=\"button\" class=\"fin-btn\" data-batch-close=\"1\">ביטול</button></div></div>";
    }
    if (b.phase === "writing") {
      return "<div class=\"fin-batch fin-pop\"><p class=\"fin-act-msg\" role=\"status\">כותב במאנדיי" +
        (b.total > 1 ? ", שורה " + NF.format(b.at) + " מתוך " + NF.format(b.total) : "") + "...</p></div>";
    }
    /* done: every row of the batch, by what happened to it */
    var done = writes.filter(function (it) { return it.result === "written"; });
    var failed = writes.filter(function (it) { return it.result === "failed"; });
    var later = writes.filter(function (it) { return it.result === "skipped"; });
    /* D4: a proposal written moves no row; its document leaves the books' list with the next reading */
    var head = done.length ? "נכתב במאנדיי בשמך ב" + (done.length === 1 ? "שורה אחת" : "-" + NF.format(done.length) + " שורות") +
      (books ? ". המסך מתעדכן." : ". המסך מתעדכן, והשורות יעברו לשיבוץ שלהן.") : "לא נכתב כלום.";
    return "<div class=\"fin-batch fin-batch-done fin-pop\"><p class=\"fin-act-msg " + (failed.length ? "bad\" role=\"alert\">" : "ok\" role=\"status\">") +
      esc(head) + " " + close + "</p>" +
      (done.length && books ? "<p>נכתב:</p><ul>" + done.map(function (it) { return batchLine(it, writtenWords(it.plan)); }).join("") +
        "</ul>" : "") +
      (done.length && !books ? "<p>נכתב " + esc(w.title + " " + shownValue("text", w.label)) + ":</p><ul>" +
        done.map(function (it) { return "<li><b>" + esc(it.name) + "</b></li>"; }).join("") + "</ul>" : "") +
      (failed.length ? "<p>נכשל:</p><ul>" + failed.map(function (it) { return batchLine(it, it.why); }).join("") + "</ul>" : "") +
      (later.length || skips.length ? "<p>לא נכתב:</p><ul>" + later.concat(skips).map(function (it) { return batchLine(it, it.why); }).join("") +
        "</ul>" : "") + "</div>";
  }
  /* the bar: the rows picked in this list, and one button per action they offer. QA 1 (24.11): wherever the list offers a
     box, the bar holds its place - a quiet line when nothing is picked, which also says what the box is for - so a pick
     never moves the rows under the next click (the second row's box was unticking the first). While a batch is on the
     screen the bar's place is held even where no row offers a box any more (its rows written, the person no longer
     allowed), empty, so the list does not move under the batch's report (QA2 3) */
  function barHtml(rows) {
    if (!rows.some(pickable)) {
      /* D4: held only where it was there when the batch was pressed (`bar`): a proposal of the books, pressed where the list
         had no box, adds none */
      return BATCH && BATCH.bar ? "<div class=\"fin-batch fin-batchbar idle fin-hold\" aria-hidden=\"true\"></div>" : "";
    }
    var picked = pickedIn(rows);
    if (!picked.length) {
      return "<div class=\"fin-batch fin-batchbar idle\" role=\"group\" aria-label=\"השורות שנבחרו\"><span>" + esc(PICK_HINT) +
        "</span></div>";
    }
    var by = {};
    picked.forEach(function (r) { var a = rowAction(r); (by[a.key] = by[a.key] || { a: a, n: 0 }).n += 1; });
    return "<div class=\"fin-batch fin-batchbar\" role=\"group\" aria-label=\"השורות שנבחרו\"><span>" +
      (picked.length === 1 ? "נבחרה שורה אחת" : "נבחרו " + NF.format(picked.length) + " שורות") + "</span>" +
      Object.keys(by).map(function (k) {
        return "<button type=\"button\" class=\"fin-btn\" data-batch=\"" + esc(k) + "\"" + (busy() ? " disabled" : "") + ">" +
          esc(by[k].a.button) + " <span class=\"fin-subtle\">(" + NF.format(by[k].n) + ")</span></button>";
      }).join("") + "<button type=\"button\" class=\"fin-linkbtn\" data-pick-clear=\"1\">ניקוי הבחירה</button></div>";
  }
  /* the list's columns with the two at the edges: the box first, the row's button before "פתיחה במאנדיי" */
  function withRowActions(cols) {
    return ["pick"].concat(cols.filter(function (k) { return k !== "open"; }), ["rowact"], cols.indexOf("open") !== -1 ? ["open"] : []);
  }
  /* "בחירת כל השורות": the rows of the list that carry a box, in its order, up to one batch's worth (BATCH_MAX); when as
     many as it can hold are picked, none */
  function allPicked(rows) {
    var can = rows.filter(pickable), n = pickedIn(rows).length;
    return !!can.length && n >= Math.min(can.length, BATCH_MAX);
  }
  function pickAll(rows) {
    var can = rows.filter(pickable);
    if (allPicked(rows)) { can.forEach(function (r) { delete PICKED[rowKey(r)]; }); return; }
    var n = pickedIn(rows).length;
    can.forEach(function (r) { if (!PICKED[rowKey(r)] && n < BATCH_MAX) { PICKED[rowKey(r)] = true; n += 1; } });
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
  /* the visit's id: sixteen random letters, never a digit (app/desk/usage.py SESSION: with digits, an ID number could pass
     as one; D2's review) */
  function useSession() {
    var s = "", i;
    try {
      var a = new Uint8Array(16);
      window.crypto.getRandomValues(a);
      s = [].map.call(a, function (b) { return String.fromCharCode(97 + b % 26); }).join("");
    } catch (e) { s = ""; }
    if (!s) { for (i = 0; i < 16; i++) { s += String.fromCharCode(97 + Math.floor(Math.random() * 26)); } }
    return s;
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
  /* how many rows the search finds in the open list (`results`), and in the whole tab (`unfiltered`): since D2's 1.2 the
     screen shows the second, the first says how many of them the open list holds */
  function searchCounts() {
    var q = state.query;
    if (state.tab === "b2b") {
      if (!VIEW.b2b) { return { results: 0, unfiltered: 0 }; }
      if (state.b2.view === "customers") { var n = customersShown(VIEW.b2b).length; return { results: n, unfiltered: n }; }
      return { results: b2Filter(ROWS.b2, VIEW.b2b, state.b2.state, state.b2.customer, q).length,
        unfiltered: b2Filter(ROWS.b2, VIEW.b2b, "", "", q).length };
    }
    if (!VIEW.b2c) { return { results: 0, unfiltered: 0 }; }
    if (state.sc.view === "cohorts" && cohortsPart()) { var c = cohortsShown().length; return { results: c, unfiltered: c }; }
    return { results: scFilter(ROWS.sc, VIEW.b2c, state.sc.state, state.sc.period, q).length,
      unfiltered: scFilter(ROWS.sc, VIEW.b2c, "", "", q).length };
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
        if (store[key] === mine && VIEW) { landed(); }
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

  /* ---------- the customer's billing details, in the ledger (round 3, slice 3; D2, ADR-0188) ----------
     Four columns of Accounts, under the server's form contract (view.b2b.account_form): its fields and their monday titles,
     in its order. analiza-metrics' server wrote them (its account route), when Chava had no CRM seat; she has one since
     16.11, and since ADR-0188 they are written FROM THE BROWSER, AS THE PERSON, like every other write of the page: monday's
     own permissions apply, and the board's activity log keeps who wrote, what was there and what was written (MON-41). By
     a member of B2B Charge (the companies' tab is answered only to one) who may write here (CAN: not view-only, inside
     monday), and only for a company the desk shows (ITEM_GUARD). "עריכה" reads the four live first, as the person (the
     ח.פ too: the server never reads it); "המשך" checks what changed by the old server's rules (analiza-metrics
     app/account_writer.py `clean` at da671b2: shapeOk's rules, the terms never cleared) and shows before and after;
     "שמירה" reads them again right before the write, writes only when monday still holds what was shown, through
     mondayWrite (the four columns are on the allowlist), and checks monday's answer. The old app's Update on the company
     ("עדכון פרטי חיוב מתוך שולחן הכספים", before and after) is written only when monday's context says the app holds
     updates:write for this person (CAN_UPDATE); otherwise it is not tried, and the confirmation says the activity log keeps
     the before and the after. One refresh of the reading after a write, as after one action: the terms move due dates.
     The ח.פ read or written stays in this page's memory (VATS, BILL) and goes to monday only - never to the server, a
     usage event or the console. */
  var BILL = null;   /* { aid, phase: "reading" | "edit" | "confirm" | "saving" | "done" | "failed", live: { name, values,
                        drift }, draft, touched, error, detail, plan, outcome, focus } */
  var ACCOUNT_ERRORS = {
    bad_vat_id: "ח.פ: 9 ספרות בדיוק, בלי מקפים ובלי רווחים.",
    bad_hash_key: "מפתח חשבשבת: עד 15 אותיות באנגלית או ספרות.",
    bad_invoice_name: "חשבונית על שם: עד 120 תווים, בשורה אחת.",
    bad_terms: "תנאי תשלום: לבחור אחד מהרשימה.",
    nothing: "אין שינוי: כל הפרטים כבר כך.",
    changed_since_read: "בינתיים פרטי החברה השתנו במאנדיי. הערכים שמוצגים עכשיו הם העדכניים: לבדוק, ולשמור שוב.",
    labels_drift: "התוויות של תנאי תשלום השתנו במאנדיי, ולכן לא נכתב כלום. נדב צריך לבדוק."
  };
  /* why a company's details cannot be edited from here, by CAN's reason */
  var BILL_WHY = {
    outside: "עריכת פרטי החיוב נכתבת במאנדיי בשמך, ולכן היא זמינה רק כשהתצוגה פתוחה מתוך מאנדיי.",
    view_only: "במאנדיי יש לך הרשאת צפייה בלבד, ולכן אי אפשר לערוך מכאן.",
    approve: ABILITY.approve.replace("עד אז אין כאן פעולות.", "עד אז אין כאן עריכה."),
    no_scope: "לאפליקציה עוד אין הרשאת כתיבה ללוחות במאנדיי, ולכן אי אפשר לערוך מכאן."
  };
  /* a read or a write of the details that failed, in words: what monday refused, by mondayErr's code */
  var BILL_ERRORS = {
    denied: "מאנדיי לא מרשה לך לכתוב בלוח החברות", no_scope: "לאפליקציה אין הרשאה לכתוב בלוח החברות",
    rate_limited: "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה", not_found: "החברה לא נמצאה בלוח החברות (אולי נמחקה)",
    columns_changed: "עמודה של פרטי החיוב לא נמצאה בלוח החברות; נדב צריך לבדוק", timeout: "מאנדיי לא ענתה בזמן",
    write_refused: "הכתיבה הזאת מחוץ לעמודות שהמסך רשאי לכתוב, ולכן לא נשלחה", monday: "מאנדיי לא קיבלה את הכתיבה"
  };
  var BILL_READ_ERRORS = { denied: "אין לך גישה ללוח החברות במאנדיי", no_scope: "לאפליקציה אין הרשאה לקרוא את לוח החברות",
    monday: "פרטי החברה לא נקראו ממאנדיי" };
  var CONTROL_ALL = new RegExp(CONTROL.source, "g");
  function billForm() { return (VIEW.b2b && VIEW.b2b.account_form) || null; }
  function termsLabel(id) { var t = (billForm().terms || []).filter(function (x) { return x.id === id; })[0]; return t ? t.label : ""; }
  function termsId(label) { var t = (billForm().terms || []).filter(function (x) { return x.label === label; })[0]; return t ? t.id : ""; }
  /* a company the desk shows: one with a charge on B2B Charge, whose billing details the reading carries (never "none",
     the charges linked to no account) */
  function deskShowsAccount(id) {
    var mine = VIEW && VIEW.b2b && VIEW.b2b.by_customer ? VIEW.b2b.by_customer[String(id)] : null;
    return String(id) !== "none" && !!(mine && mine.billing);
  }
  function canBill() { return CAN.state !== "no"; }
  /* a dropdown's labels by id, as monday holds them now ({"2": "שוטף+30"}); a status' shape is read too */
  function dropdownLabels(column) {
    var out = {};
    try {
      var l = (JSON.parse((column && column.settings_str) || "{}") || {}).labels;
      if (Array.isArray(l)) { l.forEach(function (x) { if (x && x.id != null) { out[String(x.id)] = String(x.name == null ? "" : x.name); } }); }
      else if (l && typeof l === "object") { Object.keys(l).forEach(function (k) { out[k] = String(l[k] == null ? "" : l[k]); }); }
    } catch (e) { return {}; }
    return out;
  }
  /* the four as monday holds them now, read as the person: the terms as their label's text, and the terms' ids whose label
     is no longer the form's (`drift`: an id would then write another label) */
  function readBilling(aid) {
    var form = billForm();
    return readRow(ACCOUNTS_BOARD, aid, form.fields.map(function (f) { return f.column; })).then(function (live) {
      var values = {}, terms = form.fields.filter(function (f) { return f.key === "terms"; })[0];
      form.fields.forEach(function (f) { values[f.key] = (live.cells[f.column] || {}).text || ""; });
      var labels = terms ? dropdownLabels(live.columns[terms.column]) : {};
      return { name: live.name, values: values,
        drift: form.terms.filter(function (t) { return labels[t.id] !== t.label; }).map(function (t) { return t.id; }) };
    });
  }
  function startBill(aid, focus) {
    var mine = VIEW.b2b && VIEW.b2b.by_customer[aid];
    if (!mine || !billForm() || !canBill() || !deskShowsAccount(aid) || (BILL && (BILL.phase === "saving" || BILL.phase === "reading"))) {
      return;
    }
    var bill = BILL = { aid: aid, phase: "reading", error: "", focus: focus || "vat_id", touched: {}, draft: {} };
    use("bill", { phase: "edit" });
    redraw();
    readBilling(aid).then(function (live) {
      if (BILL !== bill) { return; }
      bill.live = live;
      VATS[aid] = { phase: "ok", value: live.values.vat_id };     /* the ח.פ just read, as the details show it */
      bill.phase = "edit";
      landed(function () {
        var box = document.getElementById("fin-bill-" + bill.focus);
        if (box && box.focus) { box.focus(); }
      });
    }, function (e) {
      if (BILL !== bill) { return; }
      bill.phase = "failed";
      bill.error = "פרטי החברה לא נקראו ממאנדיי: " + (BILL_READ_ERRORS[e && e.code] || BILL_ERRORS[e && e.code] || BILL_READ_ERRORS.monday) +
        ". לא נכתב כלום.";
      bill.detail = (e && e.text) || "";
      use("bill", { phase: "failed", code: useWord(e && e.code) || "monday" });
      landed();
    });
  }
  /* a box's value: what she typed, or - a box she did not touch - what monday holds now (review 11.11: an untouched box
     that kept the value it opened with wrote it back over a newer one after "changed since read") */
  function billDraft(b, key) {
    if (b.touched[key]) { return b.draft[key]; }
    return key === "terms" ? termsId(b.live.values.terms) : b.live.values[key];
  }
  function billTyped(key, value) {
    if (!BILL || BILL.phase !== "edit") { return; }
    BILL.draft[key] = value;
    BILL.touched[key] = true;
  }
  /* a typed detail the rules refuse, in their words; "" when it may be written */
  function billError(key, v) {
    var kind = kindOf(WRITABLE[ACCOUNTS_BOARD][(billForm().fields.filter(function (f) { return f.key === key; })[0] || {}).column]);
    if (key === "terms") { return termsLabel(v) ? "" : ACCOUNT_ERRORS.bad_terms; }
    if (kind === "vat_id" || kind === "hash_key" || kind === "invoice_name") {
      return shapeOk(kind, v) ? "" : ACCOUNT_ERRORS["bad_" + kind];
    }
    return ACCOUNT_ERRORS.bad_terms;
  }
  function quotedOrEmpty(t) { return t ? "\"" + t + "\"" : "ריק"; }
  /* what differs from what monday holds, by the rules: { fields: { key: { value, to, now, column, title } }, lines } or
     { error } */
  function billPlan(b) {
    var fields = {}, lines = [], error = "";
    billForm().fields.forEach(function (f) {
      var raw = String(billDraft(b, f.key) == null ? "" : billDraft(b, f.key)), v = f.key === "terms" ? raw : raw.trim();
      var now = b.live.values[f.key] || "", to = f.key === "terms" ? termsLabel(v) : v;
      if (f.key === "terms" && !v) { return; }            /* the terms are never cleared from here */
      if (to === now) { return; }
      /* only a detail that changes is checked: a value monday holds that the rules would not take typed (a ח.פ with
         dashes, a name with a mark) never blocks setting the terms beside it (review 11.11) */
      error = error || billError(f.key, v);
      fields[f.key] = { value: v, to: to, now: now, column: f.column, title: f.title };
      lines.push("<li><b>" + esc(f.title) + "</b>: עכשיו " + esc(quotedOrEmpty(now)) + ", ייכתב " + esc(quotedOrEmpty(to)) + "</li>");
    });
    if (!error && fields.terms && b.live.drift.length) { error = ACCOUNT_ERRORS.labels_drift; }
    return error ? { error: error } : { fields: fields, lines: lines };
  }
  function billGo() {
    var b = BILL;
    if (!b || b.phase !== "edit") { return; }
    var plan = billPlan(b);
    if (plan.error) { use("bill", { phase: "invalid" }); b.error = plan.error; redraw(); return; }
    if (!plan.lines.length) { use("bill", { phase: "nothing" }); b.error = ACCOUNT_ERRORS.nothing; redraw(); return; }
    b.plan = plan;
    b.error = "";
    b.phase = "confirm";
    use("bill", { phase: "confirm", fields: Object.keys(plan.fields).length });
    redraw();
    var box = root && root.querySelector ? root.querySelector(".fin-billing .fin-act-plan") : null;
    if (box && box.focus) { box.focus(); }
  }
  /* MON-41: the old app's Update on the company, in its words - who (the update is the person's own: monday shows them as
     its writer; the context gives their id, not their name), and each detail with what was there and what was written.
     HTML: every value escaped, and stripped of the marks the rules refuse */
  function billUpdateBody(plan) {
    var clean = function (t) { return esc(quotedOrEmpty(String(t || "").replace(CONTROL_ALL, ""))); };
    return ["עדכון פרטי חיוב מתוך שולחן הכספים, בשם מי שכתב את העדכון הזה" + (USER_ID ? " (משתמש " + USER_ID + ")" : "") + "."]
      .concat(Object.keys(plan.fields).map(function (k) {
        var x = plan.fields[k];
        return esc(x.title) + ": היה " + clean(x.now) + ", נכתב " + clean(x.to) + ".";
      }), ["הכתיבה נעשתה מהדפדפן בשם מי שכתב, ויומן הפעילות של לוח החברות רושם אותה גם הוא."]).join("<br>");
  }
  /* what the write answered, in the page's memory: the details the ledger shows, the company's terms, its ח.פ (VATS) */
  function rememberBilling(aid, plan, after) {
    var mine = VIEW.b2b && VIEW.b2b.by_customer[aid];
    Object.keys(plan.fields).forEach(function (k) {
      var x = plan.fields[k], text = (after[x.column] || { text: x.to }).text;
      if (k === "vat_id") { VATS[aid] = { phase: "ok", value: text }; return; }
      if (mine && mine.billing) { mine.billing[k] = text; }
      if (k === "terms" && mine) { mine.terms = text; mine.terms_missing = !text; }
    });
  }
  function billSave() {
    var b = BILL;
    if (!b || b.phase !== "confirm") { return; }
    b.phase = "saving";
    b.error = ""; b.detail = "";
    redraw();
    var sent = false;
    /* the company again, as the person, right before the write: written only when monday still holds what was shown */
    readBilling(b.aid).then(function (live) {
      if (BILL !== b) { return "gone"; }
      var moved = Object.keys(b.plan.fields).filter(function (k) { return (live.values[k] || "") !== b.plan.fields[k].now; });
      if (moved.length) {
        b.live = live;
        VATS[b.aid] = { phase: "ok", value: live.values.vat_id };
        return "moved";
      }
      if (b.plan.fields.terms && live.drift.length) { throw { code: "labels_drift" }; }
      var payload = {};
      Object.keys(b.plan.fields).forEach(function (k) {
        var x = b.plan.fields[k];
        payload[x.column] = k === "terms" ? { ids: [Number(x.value)] } : x.value;
      });
      sent = true;
      return mondayWrite(ACCOUNTS_BOARD, b.aid, payload).then(function (after) {
        rememberBilling(b.aid, b.plan, after);
        /* monday may take a write and keep another value: read back from its own answer */
        var wrong = Object.keys(b.plan.fields).filter(function (k) {
          var x = b.plan.fields[k];
          return ((after[x.column] || {}).text || "") !== x.to;
        });
        if (wrong.length) { throw { code: "unverified", text: wrong.map(function (k) { return b.plan.fields[k].column; }).join(", ") }; }
        if (!CAN_UPDATE) { return "no_update"; }
        return mondayUpdate(ACCOUNTS_BOARD, b.aid, billUpdateBody(b.plan)).then(function () { return "posted"; },
          function (e) { b.detail = (e && e.text) || ""; return "update_failed"; });
      });
    }).then(function (outcome) {
      if (outcome === "gone") { return; }
      if (outcome === "moved") {
        b.phase = "edit";
        b.error = ACCOUNT_ERRORS.changed_since_read;
        use("bill", { phase: "failed", code: "changed_since_read" });
        landed();
        return;
      }
      b.phase = "done";
      b.outcome = outcome;
      use("bill", { phase: "done", fields: Math.min(Object.keys(b.plan.fields).length, 10), code: outcome === "posted" ? "" : outcome });
      landed();
      flash(outcome === "posted" ? "פרטי החיוב נכתבו בלוח החברות, ועל החברה נכתב עדכון."
        : outcome === "update_failed" ? "פרטי החיוב נכתבו בלוח החברות, אבל העדכון על החברה לא נכתב."
          : "פרטי החיוב נכתבו בלוח החברות בשמך.");
      refreshAfterWrite(0);
    }, function (e) {
      e = e || {};
      if (BILL !== b) { return; }
      if (e.code === "no_scope") { CAN = { state: "no", why: "no_scope" }; }
      b.phase = "failed";
      /* a write monday answered with another value, or did not answer in time: something may be in monday, so never
         "nothing was written" (review 11.11), and what it holds is read again */
      var maybe = e.code === "unverified" || (sent && e.code === "timeout");
      b.error = e.code === "labels_drift" ? ACCOUNT_ERRORS.labels_drift
        : e.code === "unverified" ? "מאנדיי ענתה, אבל הערך שנשמר אינו מה שנשלח. לבדוק את החברה במאנדיי."
          : maybe ? "מאנדיי לא ענתה על הכתיבה בזמן, ולכן לא ידוע אם נכתב. לבדוק את החברה במאנדיי לפני שמנסים שוב."
            : (BILL_ERRORS[e.code] || BILL_ERRORS.monday) + ". לא נכתב כלום.";
      b.detail = e.text || "";
      use("bill", { phase: "failed", code: useWord(e.code) || "monday" });
      landed();
      flash(b.error);
      if (maybe) { refreshAfterWrite(0); }
    });
  }
  function billStatus(b, mine) {
    var close = "<button type=\"button\" class=\"fin-linkbtn\" data-bill-close=\"1\">סגירה</button>";
    if (b.phase === "reading") { return "<p class=\"fin-act-msg\" role=\"status\">קורא את פרטי החברה במאנדיי...</p>"; }
    if (b.phase === "confirm") {
      return "<div class=\"fin-act-plan\" role=\"group\" aria-label=\"מה ייכתב\" tabindex=\"-1\"><p>ייכתב בלוח החברות, בחברה " +
        esc("\"" + (b.live.name || mine.name || "") + "\"") + ", בשמך:</p><ul>" + b.plan.lines.join("") + "</ul><p class=\"fin-subtle\">" +
        esc(CAN_UPDATE ? "על החברה ייכתב גם עדכון בשמך: מה היה ומה נכתב."
          : "יומן הפעילות של לוח החברות שומר מי כתב, מה היה ומה נכתב. עדכון על החברה לא ייכתב: לאפליקציה אין הרשאה לכתוב עדכונים.") +
        "</p><div class=\"fin-act-go\"><button type=\"button\" class=\"fin-btn fin-primary\" data-bill-save=\"1\">שמירה</button>" +
        "<button type=\"button\" class=\"fin-btn\" data-bill-back=\"1\">חזרה לעריכה</button></div></div>";
    }
    if (b.phase === "saving") { return "<p class=\"fin-act-msg\" role=\"status\">שומר בלוח החברות...</p>"; }
    if (b.phase === "done") {
      var ok = b.outcome !== "update_failed";
      return "<p class=\"fin-act-msg " + (ok ? "ok\" role=\"status\">" : "bad\" role=\"alert\">") + esc(
        b.outcome === "posted" ? "נכתב בלוח החברות בשמך, ועל החברה נכתב עדכון: מה היה ומה נכתב."
          : b.outcome === "update_failed" ? "נכתב בלוח החברות בשמך, אבל העדכון על החברה לא נכתב. יומן הפעילות של הלוח שומר מה היה ומה נכתב."
            : "נכתב בלוח החברות בשמך. יומן הפעילות של הלוח שומר מה היה ומה נכתב.") + " " + close + "</p>" +
        (!ok && b.detail ? "<p class=\"fin-subtle fin-act-detail\" dir=\"auto\">מאנדיי: " + esc(b.detail) + "</p>" : "");
    }
    if (b.phase === "failed") {
      return "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(b.error) + " " + close + "</p>" +
        (b.detail ? "<p class=\"fin-subtle fin-act-detail\" dir=\"auto\">מאנדיי: " + esc(b.detail) + "</p>" : "");
    }
    if (b.phase === "edit" && b.error) { return "<p class=\"fin-act-msg bad\" role=\"alert\">" + esc(b.error) + "</p>"; }
    return "";
  }
  function billingPanel(aid, mine) {
    var form = billForm();
    if (!form || !mine || !mine.billing) { return ""; }
    var cur = mine.billing, b = BILL && BILL.aid === aid ? BILL : null;
    var editing = !!b && (b.phase === "edit" || b.phase === "confirm" || b.phase === "saving");
    var can = canBill() && deskShowsAccount(aid);
    var head = "<div class=\"fin-billing-head\"><h2>פרטי חיוב</h2><span class=\"fin-subtle\">של החברה, בלוח החברות</span>" +
      (editing || !can || (b && b.phase === "reading") ? "" : "<button type=\"button\" class=\"fin-btn\" data-bill-edit=\"" + esc(aid) +
        "\">עריכה</button>") + "</div>";
    var body;
    if (!editing) {
      body = facts(form.fields.map(function (f) {
        var v = cur[f.key];
        /* whatever the answer carries under this key, the ח.פ shown is the one monday gives the person now */
        if (f.key === "vat_id") { return [f.title, vatCell(aid)]; }
        if (f.key === "terms") { return [f.title, v ? "<bdi>" + esc(v) + "</bdi>" : mine.terms_missing ? NO_TERMS : NOT_SET]; }
        return [f.title, v ? (f.key === "invoice_name" ? esc(v) : ltr(v)) : NOT_SET];
      })) + (canBill() ? "" : "<p class=\"fin-act-why\">" + esc(BILL_WHY[CAN.why] || BILL_WHY.no_scope) + "</p>");
    } else {
      var off = b.phase !== "edit" ? " disabled" : "";
      body = "<div class=\"fin-billing-form\">" + form.fields.map(function (f) {
        var id = "fin-bill-" + f.key, v = billDraft(b, f.key) == null ? "" : billDraft(b, f.key), held = b.live.values[f.key];
        var control = f.key === "terms"
          ? "<select id=\"" + id + "\" class=\"fin-select\" data-bill=\"terms\"" + off + "><option value=\"\"" + (v ? "" : " selected") + ">" +
            esc(held && !termsId(held) ? "\"" + held + "\" (לא ברשימה)" : "בחירה") + "</option>" + form.terms.map(function (t) {
              return "<option value=\"" + esc(t.id) + "\"" + (t.id === v ? " selected" : "") + ">" + esc(t.label) + "</option>";
            }).join("") + "</select>"
          : "<input id=\"" + id + "\" class=\"fin-input\" type=\"text\" data-bill=\"" + esc(f.key) + "\" dir=\"" + (f.key === "invoice_name" ? "auto" : "ltr") + "\"" +
            (f.key === "vat_id" ? " inputmode=\"numeric\" maxlength=\"9\"" : f.key === "hash_key" ? " maxlength=\"15\"" : " maxlength=\"" + INVOICE_NAME_MAX + "\"") +
            " value=\"" + esc(v) + "\"" + off + ">";
        /* what monday holds now, beside each box: after "changed since read" it is the new value, and she sees it */
        var now = held ? (f.key === "invoice_name" || f.key === "terms" ? "<bdi>" + esc(held) + "</bdi>" : ltr(held)) : "ריק";
        return "<label class=\"fin-act-field\" for=\"" + id + "\"><span>" + esc(f.title) + "</span>" + control +
          "<small class=\"fin-subtle\">במאנדיי עכשיו: " + now + (f.hint ? " · " + esc(f.hint) : "") + "</small>" +
          (f.key === "hash_key" && form.mon43 ? "<small class=\"fin-billing-note\">" + esc(form.mon43) + "</small>" : "") + "</label>";
      }).join("") + "</div>" + (b.phase === "edit" ? "<div class=\"fin-act-go\"><button type=\"button\" class=\"fin-btn fin-primary\" data-bill-go=\"1\">המשך</button>" +
        "<button type=\"button\" class=\"fin-btn\" data-bill-close=\"1\">ביטול</button></div>" : "");
    }
    return "<section class=\"fin-panel fin-billing\" aria-label=\"פרטי חיוב\">" + head + body + (b ? billStatus(b, mine) : "") + "</section>";
  }
  /* "אין תנאי תשלום" beside a company that has an account: a way to its ledger, where its billing details show - and,
     for one who may edit them, their form, on the terms */
  function noTerms(aid) {
    var mine = aid && VIEW.b2b && VIEW.b2b.by_customer[aid];
    if (!mine || !mine.billing || !billForm()) { return NO_TERMS; }
    return "<button type=\"button\" class=\"fin-chip fin-chiplink\" data-terms=\"" + esc(aid) + "\" title=\"" +
      esc(canBill() ? "פתיחת הכרטסת של החברה, לעריכת תנאי תשלום" : "פתיחת הכרטסת של החברה") + "\">" + esc(FLAG.terms_missing) + "</button>";
  }

  /* ---------- frame ---------- */
  /* the legend ("מקרא", round 3): the words this desk computes, from the server, for the tab on the screen. monday's own
     columns and labels have no entry - they are called by their monday names */
  function legendHtml() {
    var parts = ((VIEW[state.tab] && VIEW[state.tab].legend) || []).concat(state.tab === "b2c" && cohortsPart() ? [cohortsPart().legend] : []);
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
    /* D3: which copy of the books the marks and the lists are of, or why there is none */
    var books = booksSaid();
    if (books) { line += " <span class=\"" + (booksCalm() ? "fin-subtle" : "fin-badtext") + "\">" + esc(books) + "</span>"; }
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
  function asOf() { return stamp((BODY && BODY.taken_at) || VIEW.read_at); }
  var KEEP = ["fintab", "bstate", "b2state", "b2view", "customer", "row", "export", "notes", "refresh", "legend", "scview", "cohort", "unpriced",
    "actInput", "act", "terms", "idCopy", "pick", "pickAll", "pickClear", "rowAct", "batch", "batchGo", "batchClose",
    "bill", "billEdit", "billGo", "billSave", "billBack", "billClose"];
  function focusKey() {
    var a = document.activeElement;
    if (!a || a === root || !root.contains(a) || !a.dataset) { return ""; }
    if (a.id === "fin-q") { return "#fin-q"; }          /* the search box keeps its focus and caret too (QA 24.11) */
    var k = KEEP.filter(function (x) { return a.dataset[x] !== undefined; })[0];
    /* a dataset name in camelCase is a data- attribute in kebab-case */
    return k ? "[data-" + k.replace(/[A-Z]/g, function (m) { return "-" + m.toLowerCase(); }) + "=\"" +
      a.dataset[k].replace(/["\\]/g, "\\$&") + "\"]" : "";
  }
  /* which list a table shows: the same key after a redraw is the same list, so it keeps its own scroll */
  function listKey() {
    return JSON.stringify([state.tab, state.sc.state, state.sc.period, state.sc.view, state.b2.state, state.b2.view, state.b2.customer, state.query]);
  }
  /* A table wider than its frame scrolls inside it (QA 2.8; since QA 9 only such a table, fitFrames), and a redraw builds it
     anew: opening a row's details, or a newer reading landing, must not throw the reader back to the box's top. Each list
     keeps its scroll across a redraw; another list starts at its top. */
  function tableScrolls() {
    var out = {};
    [].forEach.call(root.querySelectorAll ? root.querySelectorAll(".fin-tw[data-list]") : [], function (el) {
      out[el.getAttribute("data-list")] = [el.scrollTop, el.scrollLeft];
    });
    return out;
  }
  /* QA 9 (24.11): one scroll. A table that fits its frame scrolls with the page, its header held at the top of the view;
     one wider than its frame (a narrow view) gets the frame that scrolls (fin-tw-x, as tall as the view), so its sideways
     scrollbar is in reach. Measured after every drawing, and again when the view's width changes */
  function fitFrames() {
    [].forEach.call(root && root.querySelectorAll ? root.querySelectorAll(".fin-tw") : [], function (el) {
      var t = el.querySelector("table");
      el.classList.remove("fin-tw-x");
      if (t && t.offsetWidth > el.clientWidth + 1) { el.classList.add("fin-tw-x"); }
    });
  }
  var refit = false;
  function onResize() {
    if (refit || !VIEW) { return; }
    refit = true;
    var again = function () { refit = false; fitFrames(); placePop(); };
    if (window.requestAnimationFrame) { window.requestAnimationFrame(again); } else { setTimeout(again, 16); }
  }
  /* QA2 3 (24.11): a batch's box (fin-pop) floats in the view beside what was pressed - the row whose button it was, or the
     bar - and follows it: placed after every drawing, on a scroll (the page's, or a table frame's) and on a resize. Above
     it where the view has room, below it otherwise; where neither side holds it, on the side with more, cut to that
     height (its own scroll). Never over what was pressed, so the pointer that pressed is never on "כתיבה". When the row
     has left the list (a reading moved it), beside the bar */
  var POP_GAP = 6, POP_EDGE = 8;
  function popAnchor(pop) {
    var panel = pop.closest ? pop.closest(".fin-panel") : null, b = BATCH, v = null, h = null;
    if (!panel || !b) { return null; }
    var quote = function (s) { return String(s).replace(/["\\]/g, "\\$&"); };
    if (b.from === "row") {
      v = panel.querySelector("tr[data-row=\"" + quote(b.items[0].row.id) + "\"]");
      h = panel.querySelector("[data-row-act=\"" + quote(b.items[0].key + ":" + b.action) + "\"]");
    } else if (b.from === "detail" || b.from === "list" || b.from === "all") {
      /* D4: the button pressed - in the row's details, on its document's line in the books' list (beside which the
         document's row is never covered), or above that list */
      h = panel.querySelector(b.from === "all" ? "[data-books-all]" : "[data-books-fill=\"" + quote(b.items[0].key) + "\"]");
      v = h && b.from === "list" && h.closest ? h.closest("tr") : h;
    }
    if (!v) {
      v = panel.querySelector(".fin-batchbar");
      h = v && b.from === "bar" ? v.querySelector("[data-batch=\"" + quote(b.action) + "\"]") : null;
    }
    v = v || panel.querySelector(".fin-listhead");
    return v ? { v: v, h: h || v } : null;
  }
  function placePop() {
    var pop = root && root.querySelector ? root.querySelector(".fin-pop") : null;
    if (!pop || !pop.getBoundingClientRect) { return; }
    var at = popAnchor(pop), vh = window.innerHeight, vw = document.documentElement.clientWidth;
    pop.style.maxHeight = "";
    var h = pop.offsetHeight, w = pop.offsetWidth;
    if (!at) { pop.style.top = POP_EDGE + "px"; pop.style.left = Math.round((vw - w) / 2) + "px"; return; }
    var a = at.v.getBoundingClientRect(), x = at.h.getBoundingClientRect();
    var above = a.top - POP_GAP - POP_EDGE, below = vh - POP_EDGE - a.bottom - POP_GAP;
    var up = h <= above || (h > below && above >= below);
    if (h > (up ? above : below)) { h = Math.max(up ? above : below, 60); pop.style.maxHeight = h + "px"; }
    pop.style.top = Math.round(up ? a.top - POP_GAP - h : a.bottom + POP_GAP) + "px";
    pop.style.left = Math.round(Math.min(Math.max(POP_EDGE, x.left + x.width / 2 - w / 2), vw - w - POP_EDGE)) + "px";
  }
  var follow = false;
  function onScroll() {
    if (follow || !BATCH) { return; }
    follow = true;
    var again = function () { follow = false; placePop(); };
    if (window.requestAnimationFrame) { window.requestAnimationFrame(again); } else { setTimeout(again, 16); }
  }
  function render() {
    if (!VIEW) { noReading(); return; }        /* D2: nothing read on the server yet */
    /* QA 24.11: what the search box holds is what she typed, focused or not, its debounce (onInput, 250ms after the last
       key) run or not - text typed just before an answer landed, or while the focus was in monday's frame, was drawn over
       from state.query, which only the debounce and the focused box fed. No drawing draws over the box now, and state.query
       changes nowhere else */
    var box = document.getElementById("fin-q");
    if (box && box.value !== state.query) { state.query = box.value; }
    var keep = focusKey(), scrolls = tableScrolls(), a = document.activeElement;
    /* an input being typed into keeps its caret: a redraw (a total landing, a newer reading) builds it anew */
    var caret = keep && a && a.tagName === "INPUT" && a.selectionStart != null ? [a.selectionStart, a.selectionEnd] : null;
    paint();
    fitFrames();
    [].forEach.call(root.querySelectorAll ? root.querySelectorAll(".fin-tw[data-list]") : [], function (el) {
      var at = scrolls[el.getAttribute("data-list")];
      if (at) { el.scrollTop = at[0]; el.scrollLeft = at[1]; }
    });
    placePop();
    var back = keep && root.querySelector(keep);
    if (back) {
      back.focus({ preventScroll: true });
      if (caret && back.setSelectionRange) { try { back.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text box */ } }
    }
  }
  /* a newer reading on its way: one word on the refresh button pressed and beside the reading's time (QA 10, 24.11: the
     button said "מרענן" and the header "מתעדכן" of the same thing) */
  var REFRESHING = "מתעדכן";
  var SEARCH_ICON = "<svg class=\"fin-searchicon\" viewBox=\"0 0 16 16\" width=\"16\" height=\"16\" aria-hidden=\"true\" focusable=\"false\">" +
    "<circle cx=\"7\" cy=\"7\" r=\"4.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\"/><path d=\"M10.5 10.5 13.5 13.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\"/></svg>";
  function paint() {
    root.innerHTML = "<div class=\"fin-wrap\"><div class=\"fin-top\"><h1>כספים</h1><span class=\"fin-asof\">נכון ל-" + esc(asOf()) +
      (BODY && BODY.refreshing ? " <span class=\"fin-refreshing\">" + REFRESHING + "</span>" : "") + "</span>" +
      "<span class=\"fin-spacer\"></span><span class=\"fin-searchbox\">" + SEARCH_ICON +
      "<input class=\"fin-search\" id=\"fin-q\" type=\"search\" aria-label=\"חיפוש\" placeholder=\"חיפוש: שם, סכום, אסמכתא\" value=\"" +
      esc(state.query) + "\"></span><button class=\"fin-btn\" type=\"button\" data-refresh=\"1\">רענון</button></div>" +
      overviewHtml() + "<div class=\"fin-foot\">" + healthHtml() +
      "</div></div>";
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
    /* D4's review: a confirmation of the books' proposals on the screen is held to this reading */
    recheckBatch();
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
      landed();
    }).catch(function () {
      if (BODY) { BODY.refreshing = false; landed(); }
      flash("העדכון נכשל, ומוצגים הנתונים הקודמים.");
    }).then(function () { pending = null; });
    return pending;
  }
  /* a redraw nobody asked for keeps the reader where they were: the scroll, and (render) the search box - what is in it,
     and its focus and caret when it had them. `then` (landed): what follows the drawing, such as a plan taking the focus */
  function redraw() {
    var scroller = document.getElementById("content");
    var top = scroller ? scroller.scrollTop : 0, winTop = window.scrollY;
    var then = press.then;
    press.owed = false; press.then = null; clearTimeout(press.timer);
    render();
    if (scroller) { scroller.scrollTop = top; }
    window.scrollTo(0, winTop);
    placePop();                                 /* where the page is now held (QA2 3) */
    if (then) { then(); }
  }
  /* QA 24.11: "a click on a tile was not taken" while the screen updated. A press is a click only when what was pressed is
     still there when it is released, and a drawing between the two builds it anew: monday's frame giving the view the focus
     back fires on the press itself (onFocus, a return), and an answer may land inside it. So a drawing that is not the
     person's own act - an answer landing, a return to the view, the search settling - waits while a pointer is down on the
     desk, and is drawn once, after the click the release makes; PRESS_MAX_MS at most, for a release the page never hears
     (a select's own list, a press dragged out of the view). The person's own acts (a click, a key, a choice) draw at once */
  var PRESS_MAX_MS = 1500;
  var press = { down: false, owed: false, then: null, timer: null };
  function landed(then) {
    if (then) { press.then = then; }
    if (!press.down) { redraw(); return; }
    if (!press.owed) {
      press.owed = true;
      press.timer = setTimeout(function () { press.down = false; drawOwed(); }, PRESS_MAX_MS);
    }
  }
  function drawOwed() { if (press.owed && !press.down) { redraw(); } }
  /* the main button only: it alone makes a click */
  function onPress(ev) { if (!ev.button) { press.down = true; } }
  function onRelease() {
    if (!press.down) { return; }
    press.down = false;
    /* the click comes after this event, in the same turn: a timer draws after it */
    if (press.owed) { setTimeout(drawOwed, 0); }
  }
  function load(fresh) {
    var p = fresh ? deps.post("/api/desk/refresh") : deps.api("/api/desk");
    return p.then(function (body) {
      /* a desk already on the screen ("רענון", "לנסות שוב") is redrawn as any answer is: what was typed and the scroll stay
         (QA 24.11); the first drawing is the desk itself */
      var drawn = !!VIEW;
      /* D2: an answer without a view never replaces the one on the screen (takeNewer); with none on it, it is said */
      if (body.view || !VIEW) { took(body); VIEW = body.view || null; index(VIEW); }
      else if (BODY) { BODY.refreshing = false; }
      if (drawn) { landed(); } else { render(); }
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
      if (VIEW && e && e.error === "rate_limited") { notBefore(e); landed(); flash(errorText(e)); return; }
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
    BODY.refreshing = true; landed();
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
      landed();
    }).catch(function () {
      if (BODY) { BODY.refreshing = false; landed(); }
    }).then(function () {
      pending = null;
      /* a held reading past its span comes marked `refreshing`: the page's own request reads the next, as on open */
      if (BODY && BODY.refreshing) { followUp(BODY); }
    });
  }
  function flash(text, ms) {
    var t = document.createElement("div"); t.className = "fin-toast"; t.setAttribute("role", "alert"); t.textContent = text;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ms || 6000);
  }
  /* QA2 2 (24.11): "פתיחה במאנדיי" after "שליחת קבלה: יצאה" was written from here showed "טרם" - closed and opened again, and
     after "רענון" too - and only reloading the page showed "יצאה"; the item's page in a new tab showed it at once. monday's
     card in this tab keeps what it read before the write, and the write was right (read back from monday's answer), so
     whoever checks the card would think it failed. Opening over the view the card of a row written in this visit says so,
     once for that row, never with the write itself; for long enough to be read once the card over the view is closed */
  var CARD_NOTE = "נכתב במאנדיי, כפי שמוצג כאן. כרטיס הפריט בלשונית הזאת עשוי להציג את הערך שלפני הכתיבה, עד שהדף ייטען " +
    "מחדש בדפדפן.";
  var CARD_NOTE_MS = 12000;
  function cardNote(id) {
    if (WROTE[id] !== "written") { return; }
    WROTE[id] = "told";
    flash(CARD_NOTE, CARD_NOTE_MS);
  }

  /* ---------- events ---------- */
  function onClick(ev) {
    var el = ev.target.closest("[data-fintab],[data-open],[data-refresh],[data-notes],[data-legend],[data-bstate],[data-b2state],[data-b2view],[data-customer],[data-row],[data-export],[data-retry]," +
      "[data-act],[data-act-go],[data-act-close],[data-terms]," +
      "[data-pick],[data-pick-all],[data-pick-clear],[data-row-act],[data-batch],[data-batch-go],[data-batch-close]," +
      "[data-books-fill],[data-books-all]," +
      "[data-bill-edit],[data-bill-go],[data-bill-save],[data-bill-back],[data-bill-close]," +
      "[data-id-copy],[data-id-retry],[data-bank-retry],[data-vat-retry]");
    if (!el || !root.contains(el)) { return; }
    /* 1.1: a row picked for a batch, every row, none; the row's own button; the batch's button, its "כתיבה", its close */
    if (el.dataset.pick || el.dataset.pickAll || el.dataset.pickClear) {
      if (busy()) { redraw(); return; }
      /* another pick: a batch's confirmation or report on the screen gives way to the bar of what is picked now */
      BATCH = null;
      if (el.dataset.pickAll) { pickAll(scRows()); }
      else if (el.dataset.pickClear) { PICKED = {}; }
      else if (PICKED[el.dataset.pick]) { delete PICKED[el.dataset.pick]; }
      else if (pickedIn(scRows()).length < BATCH_MAX) { PICKED[el.dataset.pick] = true; }
      else { flash("אפשר לבחור עד " + BATCH_MAX + " שורות לכתיבה אחת."); }
      redraw(); return;
    }
    if (el.dataset.rowAct) {
      var at = el.dataset.rowAct.lastIndexOf(":");
      startBatch(el.dataset.rowAct.slice(at + 1), [el.dataset.rowAct.slice(0, at)], "row"); return;
    }
    if (el.dataset.batch) {
      startBatch(el.dataset.batch, pickedIn(scRows()).map(rowKey), "bar"); return;
    }
    /* D4: what the books propose - one row's, from its details or its document in the books' list, or every one */
    if (el.dataset.booksFill) {
      var fillRow = rowByKey(el.dataset.booksFill), fillA = fillRow ? rowFill(fillRow) : null;
      if (fillA) { startBatch(fillA.key, [el.dataset.booksFill], el.dataset.booksFrom === "list" ? "list" : "detail"); }
      return;
    }
    if (el.dataset.booksAll) {
      var fills = allFills();
      if (fills.length) { startBatch(rowFill(fills[0]).key, fills.map(rowKey), "all"); }
      return;
    }
    if (el.dataset.batchGo) { confirmBatch(); return; }
    if (el.dataset.batchClose) {
      if (!busyBatch()) {
        /* a confirmation shown and not written: she chose not to */
        if (BATCH && BATCH.phase === "confirm") { use("act", { action: BATCH.action, phase: "batch_cancel" }); }
        BATCH = null; redraw();
      }
      return;
    }
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
    /* a company's billing details (D2, ADR-0188): their edit, written as the person */
    if (el.dataset.billEdit) { startBill(el.dataset.billEdit); return; }
    if (el.dataset.billGo) { billGo(); return; }
    if (el.dataset.billSave) { billSave(); return; }
    if (el.dataset.billBack) { if (BILL && BILL.phase === "confirm") { BILL.phase = "edit"; redraw(); } return; }
    if (el.dataset.billClose) {
      if (BILL && BILL.phase !== "saving" && BILL.phase !== "reading") {
        if (BILL.phase === "edit" || BILL.phase === "confirm") { use("bill", { phase: "cancel" }); }
        BILL = null; redraw();
      }
      return;
    }
    if (el.dataset.terms) {
      /* "אין תנאי תשלום": the company's ledger, its billing details at the top, and for one who may edit them, their form
         on the terms (D2: read live first) */
      openCompany(el.dataset.terms);
      use("customer", { from: "terms" });
      render();
      if (canBill()) { startBill(el.dataset.terms, "terms"); }
      scrollPageTo(0);
      return;
    }
    if (el.dataset.open) {
      /* how she leaves for monday: by the row's name, the quiet icon, or the details' button */
      use("open_item", { where: el.classList.contains("fin-name") ? "name" : el.classList.contains("fin-dx-open") ? "details" : "icon" });
      if (deps.monday) {
        ev.preventDefault();
        var href = el.getAttribute("href");
        /* the card over the view may be behind a write from here (QA2 2); a page in a new tab is not */
        cardNote(el.dataset.open);
        try {
          Promise.resolve(deps.monday.execute("openItemCard", { itemId: Number(el.dataset.open) }))
            .catch(function () { window.open(href, "_blank", "noopener"); });
        } catch (e) { window.open(href, "_blank", "noopener"); }
      }
      return;
    }
    if (el.dataset.row) {
      if (ev.target.closest("a,button,input,label")) { return; }
      var opened = state.tab === "b2b" ? state.b2.open : state.sc.open;
      opened[el.dataset.row] = !opened[el.dataset.row];
      var row = rowByKey((state.tab === "b2b" ? "b2" : "sc") + ":" + el.dataset.row);
      use("row", { state: row ? row.state : "", open: !!opened[el.dataset.row] });
      redraw(); return;
    }
    if (el.dataset.retry) { el.disabled = true; el.textContent = "טוען..."; load(false); return; }
    if (el.dataset.export) { use("export", { list: el.dataset.export }); exportList(el.dataset.export); return; }
    if (el.dataset.bstate !== undefined) { state.sc.state = el.dataset.bstate; state.sc.open = {}; use("state"); redraw(); return; }
    if (el.dataset.b2state !== undefined) {
      state.b2.state = el.dataset.b2state; state.b2.view = "list"; state.b2.open = {};
      use("state");
      redraw(); return;
    }
    if (el.dataset.b2view) {
      state.b2.view = el.dataset.b2view;
      /* "לפי לקוח" is every customer: a company left this way leaves no way back to keep */
      if (el.dataset.b2view === "customers") { state.b2.customer = ""; state.b2.back = null; }
      use("view", { view: el.dataset.b2view === "customers" ? "customers" : "list" });
      redraw(); return;
    }
    if (el.dataset.customer !== undefined) {
      /* a company opens at its top, where its name is (QA 7); "כל החברות" goes back to where she was (QA 8) */
      var y = 0;
      if (el.dataset.customer) { openCompany(el.dataset.customer); } else { y = leaveCompany(); }
      use("customer", { from: el.dataset.customer ? "aging" : "all" });
      render(); scrollPageTo(y); return;
    }
    if (el.dataset.fintab) { state.tab = el.dataset.fintab; use("tab"); }
    else if (el.dataset.notes) { state.notes = !state.notes; use("notes", { open: !!state.notes }); render(); return; }
    else if (el.dataset.legend) { state.legend = !state.legend; use("legend", { open: !!state.legend }); render(); return; }
    /* "רענון" reads the boards again, and what the open details read live with them (the ID number, D2: a bank account,
       a ח.פ) */
    else if (el.dataset.refresh) { use("refresh"); liveStale(); el.disabled = true; el.textContent = REFRESHING + "..."; load(true); return; }
    render();
    /* on a wide screen the app scrolls #content, not the window */
    var scroller = document.getElementById("content");
    if (scroller) { scroller.scrollTop = 0; }
    window.scrollTo(0, 0);
  }
  /* QA 8 (24.11): "כל החברות" went to "הכל" and "רשימה", whatever she came from ("לטיפול" and "לפי לקוח"). A company
     opened from a list or from "לפי לקוח" keeps where she was - the tile, the view and how far down the page - and the
     way back returns there; a tile chosen inside the company does not change it */
  function openCompany(aid) {
    var f = state.b2;
    if (!f.customer) { f.back = { state: f.state, view: f.view, y: window.scrollY || 0 }; }
    f.customer = aid; f.state = ""; f.view = "list"; f.open = {};
  }
  function leaveCompany() {
    var f = state.b2, back = f.back || { state: "", view: "list", y: 0 };
    f.customer = ""; f.state = back.state; f.view = back.view; f.open = {}; f.back = null;
    return back.y;
  }
  /* the page at a height: the window, or on a wide screen the app's #content (analiza-metrics' frame) */
  function scrollPageTo(y) {
    var scroller = document.getElementById("content");
    if (scroller) { scroller.scrollTop = y; }
    window.scrollTo(0, y);
  }
  var typing = null;
  function onInput(ev) {
    if (ev.target.dataset && ev.target.dataset.actInput !== undefined) { DRAFT[ev.target.dataset.actInput] = ev.target.value; return; }
    if (ev.target.dataset && ev.target.dataset.bill !== undefined) { billTyped(ev.target.dataset.bill, ev.target.value); return; }
    if (ev.target.id !== "fin-q") { return; }
    clearTimeout(typing);
    /* the search settles 250ms after the last key, drawn as any drawing she did not ask for this moment (QA 24.11): it
       waits for a press on the desk, and keeps the box's focus and caret only when it has them */
    typing = setTimeout(function () {
      state.query = ev.target.value;
      landed();
      searchSettling();
    }, 250);
  }
  function onKey(ev) {
    /* Enter in a billing detail's box: "המשך" */
    if (ev.key === "Enter" && ev.target.dataset && ev.target.dataset.bill !== undefined) { ev.preventDefault(); billGo(); return; }
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
    if (ev.target.dataset && ev.target.dataset.bill !== undefined) { billTyped(ev.target.dataset.bill, ev.target.value); return; }
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
    /* a press on the desk, and its release wherever it happens: a drawing that lands between them waits (QA 24.11) */
    root.addEventListener("pointerdown", onPress);
    window.addEventListener("pointerup", onRelease, true);
    window.addEventListener("pointercancel", onRelease, true);
    root.addEventListener("input", onInput);
    root.addEventListener("keydown", onKey);
    root.addEventListener("change", onChange);
    document.addEventListener("visibilitychange", onVisible);
    /* D4: the students by cohort's own clicks and keys, beside the desk's (onCohortClick: inside the desk only) */
    document.addEventListener("click", onCohortClick);
    document.addEventListener("keydown", onCohortKey);
    /* the view's window taking the focus back: after monday's item card closes over it, the first click in the view */
    window.addEventListener("focus", onFocus);
    /* monday's sidebar opened or closed, the window resized: whether each table still fits its frame (QA 9) */
    window.addEventListener("resize", onResize);
    /* a scroll of the page or of a table's frame: a batch's floating box follows what was pressed (QA2 3) */
    window.addEventListener("scroll", onScroll, true);
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
    /* 1.1 (D2): a batch, and the rows picked for one */
    _batchState: function () { return BATCH; }, _picked: function () { return PICKED; }, _batchMax: BATCH_MAX,
    /* D2 (ADR-0188): a company's billing edit, the Update's door and whether monday's context lets the app write one */
    _billState: function () { return BILL; }, _canUpdate: function () { return CAN_UPDATE; },
    _update: function (boardId, itemId, body) { return mondayUpdate(boardId, itemId, body); },
    _show: function (st) {
      Object.keys(st || {}).forEach(function (k) {
        state[k] = st[k] && typeof st[k] === "object" ? Object.assign({}, state[k], st[k]) : st[k];
      });
      /* a query given is one typed into the box: every drawing keeps what the box holds (QA 24.11) */
      var box = document.getElementById("fin-q");
      if (box && st && st.query !== undefined) { box.value = st.query; }
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

# נתוני שימוש - דסק הכספים

נדב, 16.11: "בוא נוודא שיש לנו גם נתוני שימוש שלה שם, כדי שנוכל לשפר את זה עם הזמן". מאז, המסך "כספים" רושם מה נעשה
בו - בלי תוכן - בטבלה `desk_events` במסד של השירות. עד D2 (בקלוג 0301) הטבלה ישבה ב-analiza-metrics; מאז היא כאן
(`migrations/005_desk_events.sql`, אותה טבלה בדיוק), וב-D5 ההיסטוריה משם מיובאת אליה (תוכנית המעבר §4.4 בריפו התיעוד).

- הבדיקה של כל שדה: `app/desk/usage.py`. הנתיב: `POST /api/usage` ב-`app/desk/routes.py`. הכתיבה: `app/desk/store.py`.
- המקור: `docs/usage-data.md` של analiza-metrics ב-`da671b2`, עם תיקון אחד: שאילתות 5-9 ספרו שם את כל המשתמשים יחד
  (הערת הסשן של 24.11 בריפו התיעוד), וכאן כל שאילתה אחרי הראשונה היא של משתמש אחד.

## 1. מה נרשם

שורה לכל פעולה במסך: מתי (`at`, שעון השרת), מי (`user_id`, מזהה המשתמש במאנדיי - אותו מזהה ששורת הביקורת של כל קריאה
כבר רושמת, ותמיד מה-sessionToken המאומת, אף פעם לא מגוף הבקשה), איזה ביקור (`session`, מזהה אקראי שהמסך יוצר כשהוא נפתח),
הלשונית (`tab`: ‏`b2c` סטודנטים, ‏`b2b` לקוחות עסקיים), הרשימה שהיתה פתוחה (`state`: ‏`tasks` לטיפול, ריק = הכל, או שם
המשבצת), כמה זמן המסך היה פתוח (`page_ms`), ומה קרה (`kind`) עם הפרטים שלו (`detail`):

| `kind` | מתי | `detail` |
|---|---|---|
| `open` | המסך הציג קריאה ראשונה | `ms` כמה זמן לקח, `board` (‏`sc` / ‏`b2`), `refreshing` (הוצגה הקריאה השמורה) |
| `load_failed` | המסך לא הצליח להציג נתונים | `code` מילת השגיאה של השרת |
| `tab` | מעבר לשונית | - |
| `state` | לחיצה על משבצת | - (המשבצת ב-`state`) |
| `period` | בחירת תקופה | `period` (‏`2026-10`, ‏`2026-09+2026-10`, או ריק) |
| `view` | "רשימה" / "לפי לקוח" / "לפי מחזור" (D4) | `view` (‏`list`, ‏`customers`, ‏`cohorts`) |
| `customer` | כרטסת של חברה נפתחה או נסגרה | `from`: ‏`aging` (מ"לפי לקוח"), ‏`terms` (מ"אין תנאי תשלום"), ‏`all` (חזרה לכל החברות) |
| `search` | חיפוש שהתייצב (שנייה וחצי בלי הקלדה) | `len` כמה תווים, `kind` סוג (‏`digits`, ‏`amount`, ‏`hebrew`, ‏`latin`, ‏`mixed`, ‏`other`), `results` כמה נמצאו ברשימה, `unfiltered` כמה ללא סינון |
| `unfilter` | "חיפוש ללא סינון" | `from` (‏`empty` מתחת לרשימה ריקה, ‏`head` מראש הרשימה), `results` |
| `row` | פרטים של שורה נפתחו או נסגרו | `state` המשבצת של השורה (‏`cohort`: מחזור ב"לפי מחזור" נפתח לסטודנטים שלו), `open` |
| `open_item` | שורה נפתחה במאנדיי | `where`: ‏`name` (השם), ‏`icon`, ‏`details` (הכפתור בפרטים) |
| `act` | פעולה (סימון "תשלום הוחזר", "יצאה", "חויב"...) | `action`, `phase` (‏`start`, ‏`shown`, ‏`confirm`, ‏`done`, ‏`failed`, ‏`nothing`, ‏`invalid`, ‏`cancel`), `code` למה נכשלה |
| `books_fill` | "רישום מחשבשבת" נלחץ (D4, ‏26.11); מה שקורה אחר כך הוא `act` עם `action` ‏`books_fill` | `from`: ‏`detail` (בפרטים של שורה), ‏`list` (ליד מסמך ברשימה), ‏`all` ("לרשום את כל ההצעות"); `rows` כמה שורות באצווה |
| `bill` | פרטי חיוב של חברה. **לא נשלח מאז המעבר:** פרטי החיוב מוצגים לקריאה בלבד עד שהעריכה תחזור מהדפדפן (ADR-0188); נשאר ברשימה בשבילה, ובשביל ההיסטוריה שמיובאת | `phase` (‏`edit`, ‏`confirm`, ‏`done`, ‏`failed`, ‏`nothing`, ‏`invalid`, ‏`cancel`), `code`, `fields` כמה פרטים |
| `export` | ייצוא לאקסל | `list` (‏`b2c`, ‏`b2b`, ‏`b2b_customers`, ‏`b2c_cohorts`: "לפי מחזור", D4) |
| `legend` / `notes` | "מקרא" / "פרטים על הנתונים" | `open` |
| `refresh` | "רענון" | - |
| `back` | המסך קרא שוב כשחזרו אליו | `how`: ‏`visible` (לשונית אחרת), ‏`focus` (כרטיס של מאנדיי נסגר) |
| `hide` | המסך יצא מהעין | - |
| `id_shown` / `id_empty` / `id_failed` | ת.ז נקראה בפרטים / אין בליד / לא נקראה | `code` למה לא נקראה |
| `id_copied` | כפתור ההעתקה ליד ת.ז | `code` (ריק = הועתק, ‏`failed`) |
| `ask` | "חסר כאן משהו? פנייה לנדב" (יצא ב-D2, וחזר ב-QA של 26.11) | - |

כל מי שמחובר לחשבון של אנליזה יכול לשלוח את השימוש **שלו**, גם מי שהשולחן לא נפתח לו (אורח, מי שלא חבר באף לוח חיובים):
הסירוב עצמו נרשם, כ-`load_failed` עם המילה של השרת.

## 2. מה לא נרשם אף פעם

שם, סכום, טקסט של חיפוש, ת.ז, פרטי בנק, מזהה של שורה או של חברה. חיפוש נרשם רק כצורה: כמה תווים, איזה סוג, כמה נמצאו.
השרת דוחה כל ערך אחר - מספר בטווח של השדה שלו, כן/לא, מפתח תקופה, או מילה שמתחילה באות לטינית קטנה - כך שגם מסך שינסה
לשלוח שם או מספר לא יכתוב אותו. כדאי שחוה, וכל מי שמשתמש במסך, תדע שהמסך רושם מה נלחץ ומתי, בלי תוכן.

## 3. איך קוראים

ב-Replit, בפאנל Database של **מסד הפריסה** (Production), לא של סביבת העבודה (Development, ‏`helium` - עליו רץ
`python3 -m app.migrate`). אם לא ברור על איזה מסד עומדים:

```sql
SELECT count(*) AS events, max(at) AS last FROM desk_events;
```

במסד הפריסה `last` הוא הפתיחה האחרונה של השולחן; במסד הפיתוח אין שורות, או רק מבדיקות.

מזהה המשתמש של חוה: בפרופיל שלה במאנדיי, המספר בכתובת (‏`/users/<מספר>`), או השאילתה הראשונה למטה. בכל שאילתה אחריה
מחליפים `12345678` במזהה של מי שקוראים את השימוש שלו - חוה, או משתמש אחר שהשאילתה הראשונה מראה.

**1. מי משתמש, כמה ביקורים וכמה ימים**

```sql
SELECT user_id, count(DISTINCT session) AS visits,
       count(DISTINCT (at AT TIME ZONE 'Asia/Jerusalem')::date) AS days, min(at) AS first, max(at) AS last
FROM desk_events WHERE desk = 'finance'
GROUP BY user_id ORDER BY visits DESC;
```

**2. הביקורים שלה בשבועיים האחרונים: מתי, כמה זמן, כמה פעולות**

```sql
SELECT session, min(at) AT TIME ZONE 'Asia/Jerusalem' AS started, max(at) - min(at) AS length, count(*) AS events
FROM desk_events WHERE desk = 'finance' AND user_id = 12345678 AND at > now() - interval '14 days'
GROUP BY session ORDER BY started DESC;
```

**3. מה היא עושה, לפי סוג**

```sql
SELECT kind, count(*) FROM desk_events
WHERE user_id = 12345678 AND at > now() - interval '30 days'
GROUP BY kind ORDER BY count(*) DESC;
```

**4. איזו רשימה היא פותחת**

```sql
SELECT tab, state, count(*) FROM desk_events
WHERE kind IN ('state', 'row') AND user_id = 12345678 AND at > now() - interval '30 days'
GROUP BY tab, state ORDER BY count(*) DESC;
```

**5. חיפושים שלא מצאו כלום** - השאילתה הכי שימושית לשיפור החיפוש. ‏`digits` באורך 9 הוא כמעט תמיד חיפוש לפי ת.ז,
והחיפוש לא מחפש בת.ז; ‏`hidden_by_a_filter` הוא חיפוש שמצא רק אחרי "חיפוש ללא סינון".

```sql
SELECT detail->>'kind' AS kind, (detail->>'len')::int AS len, count(*) AS searches,
       count(*) FILTER (WHERE (detail->>'results')::int = 0) AS found_nothing,
       count(*) FILTER (WHERE (detail->>'results')::int = 0 AND (detail->>'unfiltered')::int > 0) AS hidden_by_a_filter
FROM desk_events WHERE kind = 'search' AND user_id = 12345678 AND at > now() - interval '30 days'
GROUP BY 1, 2 ORDER BY searches DESC;
```

**6. פעולות: כמה התחילו, כמה נכתבו, כמה נכשלו ולמה**

```sql
SELECT detail->>'action' AS action, detail->>'phase' AS phase, detail->>'code' AS code, count(*)
FROM desk_events WHERE kind = 'act' AND user_id = 12345678 AND at > now() - interval '30 days'
GROUP BY 1, 2, 3 ORDER BY 1, 2;
```

**7. מתי היא יוצאת למאנדיי** - מה שהמסך לא נותן לה, היא הולכת לחפש שם

```sql
SELECT tab, state, detail->>'where' AS how, count(*) FROM desk_events
WHERE kind = 'open_item' AND user_id = 12345678 AND at > now() - interval '30 days'
GROUP BY 1, 2, 3 ORDER BY count(*) DESC;
```

**8. ת.ז בפרטים: הוצגה, הועתקה, לא נקראה ולמה**

```sql
SELECT kind, detail->>'code' AS code, count(*) FROM desk_events
WHERE kind IN ('id_shown', 'id_empty', 'id_failed', 'id_copied') AND user_id = 12345678
  AND at > now() - interval '30 days'
GROUP BY 1, 2 ORDER BY 1;
```

**9. כמה זמן לוקח למסך להיפתח, לפי יום**

```sql
SELECT (at AT TIME ZONE 'Asia/Jerusalem')::date AS day, count(*) AS opens,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (detail->>'ms')::int) AS median_ms, max((detail->>'ms')::int) AS max_ms
FROM desk_events WHERE kind = 'open' AND user_id = 12345678
GROUP BY 1 ORDER BY 1 DESC;
```

## 4. אחרי הפריסה

זה שינוי סכימה (`migrations/005_desk_events.sql`), אז הוא עובר לפי הסדר ב-`CLAUDE.md` ("A change that touches
migrations/"), כל צעד בנפרד:

1. נחיתה ב-`main`.
2. ב-**Shell** של ה-Repl:
   `bash scripts/sync-from-github.sh && pip install -q -r requirements.txt && python3 -m app.migrate`.
   מצפים ל-`Now at: <7 התווים של main>`, אחריו `database: helium/...` (מסד הפיתוח), ‏`applied  005_desk_events.sql`,
   ובסוף שורת `ok:` שמונה גם את `desk_events`.
3. **Republish בממשק**, וקריאת ה-SQL שהוא מציג: רק היצירה של `desk_events` - ‏`CREATE TABLE desk_events`, הרצף
   `desk_events_id_seq`, המפתח `desk_events_pkey`, והאינדקסים `desk_events_at` ו-`desk_events_user_at`. אם הוא כותב את
   הרצף ואת המפתח כ-`ALTER` על `desk_events` עצמה (כך Postgres מתאר טבלה עם `bigserial`), זה חלק מהיצירה. כל `DROP`, וכל
   שינוי של טבלה אחרת - ‏Cancel.
4. ב-`/health` (נקרא לכל היותר פעם ב-15 שניות):
   1. ‏`build.sha` הוא של `main`, ‏`store.tables.desk_events` הוא `"present"`, ו-`store.missing_columns` רשימה ריקה.
   2. אחרי פתיחה אחת של המסך (ויציאה ממנו, או 30 שניות): ‏`desk.usage.stored` גדול מ-0, ו-`desk.usage.refused` הוא 0.
      אירוע שנדחה הוא מסך ששולח משהו שהשרת לא מכיר - לבדוק. ‏`desk.usage.last_error` אומר למה אצווה לא נשמרה: ‏`store_schema`
      (אין טבלה: צעד 2 או 3 לא קרה), ‏`no_store` (אין `DATABASE_URL`), ‏`bad_usage` (אצווה בצורה אחרת), או סוג תקלה אחר
      של המסד. ‏`/health` סופר אצוות של המופע שעונה, ואף פעם לא אדם או אירוע.
5. בפאנל Database של מסד הפריסה:

   ```sql
   SELECT kind, count(*) FROM desk_events WHERE at > now() - interval '1 hour' GROUP BY kind;
   ```

## 5. כמה זמן שומרים

אין מחיקה אוטומטית. כמה זמן שומרים - החלטה לריפו התיעוד (תוכנית המעבר קוראת לטבלה "ההיסטוריה של חוה" ומייבאת אותה
לפני כל `DROP` במדדים); מחיקה של שורות ישנות בטוחה (שום דבר לא קורא אותן חוץ מהשאילתות כאן), למשל שנה:

```sql
DELETE FROM desk_events WHERE at < now() - interval '365 days';
```

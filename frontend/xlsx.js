/* The desk's own .xlsx writer (D4, 26.11; Analiza-Monday backlog 0301). The students' Excel file with the ID number is
   written in the person's browser (finance.js, "ייצוא לאקסל עם ת.ז"): this service's server never reads an ID number
   (app/monday/client.py NEVER_READ), so it cannot write that file. No library and no network: a zip of a few XML parts,
   stored as they are (the zip format's method 0), each with its CRC-32 - some 260 lines, read whole in a review.

   The file is the server's own. app/desk/export.py describes it as data (`b2c_table`, the description its openpyxl writer
   `write` writes the Excel file from) and this writes that description the way the server's file is written: every sheet
   right to left; a list's header bold on its fill, frozen, with a filter on it; dates as dates (dd/mm/yyyy), amounts as
   numbers (#,##0.00), every text a string - never a formula: a name that opens with "=", "+", "-" or "@" is a string here,
   as it is in the server's file (an inline string, as openpyxl 3.1 writes one), and a reference like 00123 keeps its
   zeros; under the list, the total that follows the filter (SUBTOTAL 109); "פרטים" a key in bold and its value. What
   XML cannot carry (a control character, half of a surrogate pair) is dropped, as openpyxl drops it. It keeps nothing:
   the bytes it returns are its caller's.

   A book: { sheets: [sheet, ...] }. A sheet is a list - { title, columns: [{head, kind, width}], rows: [[cell, ...]],
   total: the label of the row under it, lines: texts under it } - or pairs - { title, pairs: [[key, value, kind]],
   widths } - as "פרטים" is. A cell by its column's kind: "text" a string; "money" and "amount" a number as a decimal
   string (an amount round-trips exactly); "date" "YYYY-MM-DD"; "int" a number; null an empty cell. A number or a date
   that is not one is an error, never an empty cell: the caller says the file was not made. */
(function () {
  "use strict";

  var MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  var NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  var REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  var PKG = "http://schemas.openxmlformats.org/package/2006/relationships";
  var HEAD = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n";
  /* the cell styles (xl/styles.xml's cellXfs), the server's file's: its header, its number formats ("@" for a text), and
     the bold of the total and of "פרטים"'s keys */
  var S = { plain: 0, head: 1, money: 2, date: 3, int: 4, text: 5, totalMoney: 6, bold: 7 };
  var KIND_STYLE = { money: S.money, amount: S.money, date: S.date, int: S.int, text: S.text };
  var XF = "fillId=\"0\" borderId=\"0\" xfId=\"0\"";
  var STYLES = HEAD + "<styleSheet xmlns=\"" + NS + "\">" +
    "<numFmts count=\"1\"><numFmt numFmtId=\"164\" formatCode=\"dd/mm/yyyy\"/></numFmts>" +
    "<fonts count=\"2\"><font><sz val=\"11\"/><name val=\"Calibri\"/><family val=\"2\"/></font>" +
    "<font><b/><sz val=\"11\"/><name val=\"Calibri\"/><family val=\"2\"/></font></fonts>" +
    "<fills count=\"3\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill>" +
    "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FFE8ECF4\"/><bgColor indexed=\"64\"/></patternFill></fill></fills>" +
    "<borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>" +
    "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>" +
    "<cellXfs count=\"8\">" +
    "<xf numFmtId=\"0\" fontId=\"0\" " + XF + "/>" +
    "<xf numFmtId=\"0\" fontId=\"1\" fillId=\"2\" borderId=\"0\" xfId=\"0\" applyFont=\"1\" applyFill=\"1\" applyAlignment=\"1\">" +
    "<alignment horizontal=\"right\" vertical=\"center\" wrapText=\"1\"/></xf>" +
    "<xf numFmtId=\"4\" fontId=\"0\" " + XF + " applyNumberFormat=\"1\"/>" +
    "<xf numFmtId=\"164\" fontId=\"0\" " + XF + " applyNumberFormat=\"1\"/>" +
    "<xf numFmtId=\"1\" fontId=\"0\" " + XF + " applyNumberFormat=\"1\"/>" +
    "<xf numFmtId=\"49\" fontId=\"0\" " + XF + " applyNumberFormat=\"1\"/>" +
    "<xf numFmtId=\"4\" fontId=\"1\" " + XF + " applyNumberFormat=\"1\" applyFont=\"1\"/>" +
    "<xf numFmtId=\"0\" fontId=\"1\" " + XF + " applyFont=\"1\"/>" +
    "</cellXfs><cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles></styleSheet>";

  /* ---- a cell ---- */
  /* the text without what XML 1.0 cannot hold: the control characters openpyxl drops (all below a space but the tab, the
     line feed and the carriage return), half of a surrogate pair alone, U+FFFE and U+FFFF */
  function clean(v) {
    var s = v == null ? "" : String(v), out = "";
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0xD800 && c <= 0xDBFF) {
        var d = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
        if (d >= 0xDC00 && d <= 0xDFFF) { out += s.charAt(i) + s.charAt(i + 1); i += 1; }
        continue;
      }
      if ((c >= 0xDC00 && c <= 0xDFFF) || c === 0xFFFE || c === 0xFFFF) { continue; }
      if (c < 0x20 && c !== 0x09 && c !== 0x0A && c !== 0x0D) { continue; }
      out += s.charAt(i);
    }
    return out;
  }
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  /* a string cell (t="inlineStr"): never read as a formula, an error or a number; no cell for an empty text */
  function textCell(ref, v, style) {
    var s = clean(v);
    if (!s) { return ""; }
    var keep = /^\s|\s$|[\n\t]/.test(s) ? " xml:space=\"preserve\"" : "";
    return "<c r=\"" + ref + "\" s=\"" + style + "\" t=\"inlineStr\"><is><t" + keep + ">" + esc(s) + "</t></is></c>";
  }
  function fail(what) { var e = new Error("xlsx: " + what); e.code = "file"; throw e; }
  /* a number as the sheet's XML holds one: a decimal string as it came (a Decimal's exponent form too, "1E+3"), a count
     as written */
  function numberText(v) {
    if (typeof v === "number") { return isFinite(v) ? String(v) : fail("a number that is not one"); }
    var s = String(v);
    return /^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$/.test(s) ? s : fail("a number that is not one");
  }
  /* a day as Excel's serial number: the 1900 system, days since 30.12.1899 */
  function serial(v) {
    var m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(v));
    var t = m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : NaN;
    var back = isFinite(t) ? new Date(t).toISOString().slice(0, 10) : "";
    return back === String(v) ? String(Math.round(t / 86400000) + 25569) : fail("a date that is not one");
  }
  function cell(ref, kind, v) {
    if (kind !== "money" && kind !== "amount" && kind !== "date" && kind !== "int") { return textCell(ref, v, S.text); }
    var style = KIND_STYLE[kind];
    /* an empty amount or date keeps its column's format, as in the server's file */
    if (v == null || v === "") { return "<c r=\"" + ref + "\" s=\"" + style + "\"/>"; }
    return "<c r=\"" + ref + "\" s=\"" + style + "\"><v>" + (kind === "date" ? serial(v) : numberText(v)) + "</v></c>";
  }
  /* a column's letters: 1 is A, 27 is AA */
  function col(n) {
    var s = "";
    while (n > 0) { var m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
    return s;
  }
  function row(r, cells) { return cells.length ? "<row r=\"" + r + "\">" + cells.join("") + "</row>" : ""; }

  /* ---- a sheet ---- */
  function sheetView(first, frozen) {
    return "<sheetViews><sheetView rightToLeft=\"1\"" + (first ? " tabSelected=\"1\"" : "") + " workbookViewId=\"0\">" +
      (frozen ? "<pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/>" +
        "<selection pane=\"bottomLeft\" activeCell=\"A2\" sqref=\"A2\"/>" : "") + "</sheetView></sheetViews>";
  }
  function widths(list) {
    return list.length ? "<cols>" + list.map(function (w, i) {
      return "<col min=\"" + (i + 1) + "\" max=\"" + (i + 1) + "\" width=\"" + Number(w) + "\" customWidth=\"1\"/>";
    }).join("") + "</cols>" : "";
  }
  function worksheet(parts) {
    return HEAD + "<worksheet xmlns=\"" + NS + "\" xmlns:r=\"" + REL + "\">" + parts.join("") + "</worksheet>";
  }
  /* a list: its header, its rows, and under them its total or its lines. {xml, filter: the range its filter is on} */
  function listSheet(t, first) {
    var cols = t.columns || [], rows = t.rows || [], out = [];
    out.push(row(1, cols.map(function (c, i) { return textCell(col(i + 1) + "1", c.head, S.head); })));
    rows.forEach(function (cells, k) {
      var r = k + 2;
      out.push(row(r, cols.map(function (c, i) { return cell(col(i + 1) + r, c.kind, (cells || [])[i]); })));
    });
    var last = rows.length + 1, under = rows.length + 3;
    if (t.total) {
      /* the label in the second column, and a SUBTOTAL under each amount that has a total, as the server's file writes
         them (a formula there would take the label's place, as it does there) */
      var total = [];
      for (var i = 0; i < Math.max(cols.length, 2); i++) {
        var letter = col(i + 1);
        if (cols[i] && cols[i].kind === "money" && rows.length) {
          total.push("<c r=\"" + letter + under + "\" s=\"" + S.totalMoney + "\"><f>SUBTOTAL(109," + letter + "2:" + letter +
            last + ")</f></c>");
        } else if (i === 1) {
          total.push(textCell(letter + under, t.total, S.bold));
        }
      }
      out.push(row(under, total));
      under += 1;
    }
    (t.lines || []).forEach(function (text, k) { out.push(row(under + k, [textCell("A" + (under + k), text, S.text)])); });
    var ref = "A1:" + col(Math.max(cols.length, 1)) + last;
    return { xml: worksheet([sheetView(first, true), "<sheetFormatPr defaultRowHeight=\"15\"/>",
      widths(cols.map(function (c) { return c.width || 10; })), "<sheetData>" + out.join("") + "</sheetData>",
      "<autoFilter ref=\"" + ref + "\"/>"]), filter: ref };
  }
  /* "פרטים": a key in bold beside its value - a text as a string, a count as a number, a sum as an amount */
  function pairsSheet(t, first) {
    var out = (t.pairs || []).map(function (p, k) {
      var r = k + 1, kind = p[2] || "text", value;
      if (kind === "money") { value = cell("B" + r, "money", p[1]); }
      else if (kind === "int") { value = p[1] == null ? "" : "<c r=\"B" + r + "\"><v>" + numberText(p[1]) + "</v></c>"; }
      else { value = textCell("B" + r, p[1], S.text); }
      return row(r, [textCell("A" + r, p[0], S.bold), value]);
    });
    return { xml: worksheet([sheetView(first, false), "<sheetFormatPr defaultRowHeight=\"15\"/>", widths(t.widths || []),
      "<sheetData>" + out.join("") + "</sheetData>"]), filter: "" };
  }

  /* ---- the workbook's parts ---- */
  function sheetName(t, i) {
    /* Excel's own rules for a sheet's name: at most 31 characters, none of []:*?/\ */
    return clean(t).replace(/[\[\]:*?\/\\]/g, "-").slice(0, 31) || "Sheet" + (i + 1);
  }
  function parts(book) {
    var sheets = (book && book.sheets) || [];
    if (!sheets.length) { fail("a book without a sheet"); }
    var names = sheets.map(function (t, i) { return sheetName(t.title, i); });
    var built = sheets.map(function (t, i) { return t.pairs ? pairsSheet(t, i === 0) : listSheet(t, i === 0); });
    var files = [];
    files.push(["[Content_Types].xml", HEAD + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" +
      "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>" +
      "<Default Extension=\"xml\" ContentType=\"application/xml\"/>" +
      "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>" +
      "<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>" +
      built.map(function (_b, i) {
        return "<Override PartName=\"/xl/worksheets/sheet" + (i + 1) + ".xml\" " +
          "ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>";
      }).join("") + "</Types>"]);
    files.push(["_rels/.rels", HEAD + "<Relationships xmlns=\"" + PKG + "\"><Relationship Id=\"rId1\" " +
      "Type=\"" + REL + "/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>"]);
    /* a filter's range by its sheet's name, as Excel names it */
    var defined = built.map(function (b, i) {
      if (!b.filter) { return ""; }
      return "<definedName name=\"_xlnm._FilterDatabase\" localSheetId=\"" + i + "\" hidden=\"1\">" +
        esc("'" + names[i].replace(/'/g, "''") + "'!" + b.filter.replace(/([A-Z]+)([0-9]+)/g, "$$$1$$$2")) + "</definedName>";
    }).join("");
    files.push(["xl/workbook.xml", HEAD + "<workbook xmlns=\"" + NS + "\" xmlns:r=\"" + REL + "\">" +
      "<bookViews><workbookView activeTab=\"0\"/></bookViews><sheets>" + names.map(function (n, i) {
        return "<sheet name=\"" + esc(n) + "\" sheetId=\"" + (i + 1) + "\" r:id=\"rId" + (i + 1) + "\"/>";
      }).join("") + "</sheets>" + (defined ? "<definedNames>" + defined + "</definedNames>" : "") +
      "<calcPr calcId=\"124519\" fullCalcOnLoad=\"1\"/></workbook>"]);
    files.push(["xl/_rels/workbook.xml.rels", HEAD + "<Relationships xmlns=\"" + PKG + "\">" + built.map(function (_b, i) {
      return "<Relationship Id=\"rId" + (i + 1) + "\" Type=\"" + REL + "/worksheet\" Target=\"worksheets/sheet" + (i + 1) + ".xml\"/>";
    }).join("") + "<Relationship Id=\"rId" + (built.length + 1) + "\" Type=\"" + REL + "/styles\" Target=\"styles.xml\"/>" +
      "</Relationships>"]);
    files.push(["xl/styles.xml", STYLES]);
    built.forEach(function (b, i) { files.push(["xl/worksheets/sheet" + (i + 1) + ".xml", b.xml]); });
    return files;
  }

  /* ---- the zip: each part stored as it is (method 0) with its CRC-32, then the central directory ---- */
  var CRC_TABLE = (function () {
    var t = [];
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) { c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; }
      t.push(c >>> 0);
    }
    return t;
  })();
  function crc32(bytes) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) { c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8); }
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  function utf8(s) { return new TextEncoder().encode(s); }
  /* every part dated 1.1.1980 00:00, the zip format's first day: a file's parts carry no time of their own */
  var DOS_TIME = 0, DOS_DATE = (1 << 5) | 1;
  function le(n, size) {
    var b = new Uint8Array(size);
    for (var i = 0; i < size; i++) { b[i] = (n >>> (8 * i)) & 0xFF; }
    return b;
  }
  function zip(files) {
    var chunks = [], central = [], offset = 0;
    files.forEach(function (f) {
      var name = utf8(f[0]), data = typeof f[1] === "string" ? utf8(f[1]) : f[1], crc = crc32(data);
      /* version 2.0, no flag, stored, the time, the CRC, both sizes, the name's length, no extra field */
      var common = [le(20, 2), le(0, 2), le(0, 2), le(DOS_TIME, 2), le(DOS_DATE, 2), le(crc, 4), le(data.length, 4),
        le(data.length, 4), le(name.length, 2), le(0, 2)];
      [le(0x04034b50, 4)].concat(common, [name, data]).forEach(function (b) { chunks.push(b); });
      /* made by 2.0; no comment, disk 0, no attributes; where its local header starts */
      central.push([le(0x02014b50, 4), le(20, 2)].concat(common, [le(0, 2), le(0, 2), le(0, 2), le(0, 4), le(offset, 4), name]));
      offset += 30 + name.length + data.length;
    });
    var size = 0;
    central.forEach(function (entry) { entry.forEach(function (b) { chunks.push(b); size += b.length; }); });
    [le(0x06054b50, 4), le(0, 2), le(0, 2), le(files.length, 2), le(files.length, 2), le(size, 4), le(offset, 4), le(0, 2)]
      .forEach(function (b) { chunks.push(b); });
    var total = 0;
    chunks.forEach(function (b) { total += b.length; });
    var out = new Uint8Array(total), at = 0;
    chunks.forEach(function (b) { out.set(b, at); at += b.length; });
    return out;
  }

  /* the bytes of the .xlsx of a book */
  function write(book) { return zip(parts(book)); }

  window.DeskXlsx = { MIME: MIME, write: write, _parts: parts, _crc32: crc32, _serial: serial, _col: col, _clean: clean };
})();

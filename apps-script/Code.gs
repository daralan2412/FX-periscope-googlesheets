/**
 * Periscope Ramp Ops (REP-1901) -> Google Sheets Web App
 *
 * THIS IS A NEW, INDEPENDENT PROJECT. It does not touch, share properties
 * with, or redeploy the old "Periscope to Sheets WebApp" project used by
 * daralan2412/periscope-to-sheets (that pipeline feeds a different sheet/tab
 * from a different Periscope report and is left completely alone).
 *
 * Source:  https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35
 *          Sisense report "REP-1901" — FedEx Ramp Team ops/task data.
 * Target:  Google Sheet "2026_RAW_MISSIONS"
 *          (1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU), tab "DATA" (gid=1584481054).
 *          That tab's header row already matches the HEADERS array below —
 *          confirmed live on 2026-08-30.
 *
 * doGet   -> tells the scraper the next date it should pull (D0 buffer —
 *            same-day pull allowed, computed on Asia/Taipei's calendar since
 *            the station is TPE).
 * doPost  -> receives that date's rows (JSON) and appends them to the DATA tab.
 *
 * Both endpoints require ?token=<AUTH_TOKEN>, checked against the AUTH_TOKEN
 * Script Property (Project Settings > Script Properties) — set your own
 * random value there, then put the same value in the GitHub secret
 * WEBAPP_TOKEN. Nothing here reuses the old pipeline's token.
 */

var SHEET_ID = '1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU';
var TAB_NAME = 'DATA';
var TIMEZONE = 'Asia/Taipei';   // station TPE — "today" and the pull cutoff are computed on TPE's calendar day
var PULL_BUFFER_DAYS = 0;       // D0 (same-day pull allowed), changed from D-1 per explicit instruction on 2026-08-30

var HEADERS = [
  'mission_sas_id', 'date', 'station', 'airline_code', 'tail_number', 'vessel_description',
  'job_name', 'mission_name', 'arrival_flight_number', 'departure_flight_number',
  'org_city', 'dest_city', 'arr_time', 'dep_time', 'disp_name', 'agent_name', 'mission_notes',
  'assigned_date', 'start_time', 'finish_time',
  'task_1', 'task_2', 'task_3', 'task_4', 'task_5', 'task_6', 'task_7', 'task_8', 'task_9', 'task_10',
  'task_11', 'task_12', 'task_13', 'task_14', 'task_15'
];

function doGet(e) {
  if (!checkToken_(e)) return jsonOut_({ success: false, error: 'unauthorized' });

  var props = PropertiesService.getScriptProperties();
  var last = props.getProperty('lastProcessedDate'); // 'YYYY-MM-DD' — seed this manually before first run

  if (!last) {
    return jsonOut_({
      success: false,
      error: 'lastProcessedDate not set. Set it as a Script Property (the day BEFORE ' +
        'you want the first pull to happen) before the first scheduled/manual run.'
    });
  }

  var todayTpe = Utilities.formatDate(new Date(), TIMEZONE, 'yyyy-MM-dd');
  var nextDate = addDays_(last, 1);
  var cutoff = addDays_(todayTpe, -PULL_BUFFER_DAYS);

  if (nextDate <= cutoff) {
    return jsonOut_({ success: true, next_date: nextDate });
  }
  return jsonOut_({ success: true, next_date: null });
}

function doPost(e) {
  if (!checkToken_(e)) return jsonOut_({ success: false, error: 'unauthorized' });

  var body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonOut_({ success: false, error: 'invalid JSON body: ' + err });
  }

  var date = body.date;        // 'YYYY-MM-DD'
  var rows = body.rows || [];  // array of arrays, each row matching HEADERS order/length

  if (!date) return jsonOut_({ success: false, error: 'missing "date"' });

  // Everything below can throw (bad sheet state, a malformed row, a Sheets
  // API hiccup) — always return JSON, even on failure, so the caller never
  // has to parse an HTML error page. A failure here does NOT advance
  // lastProcessedDate, so the same date is safely retried next run.
  try {
    var ss = SpreadsheetApp.openById(SHEET_ID);
    var sheet = ss.getSheetByName(TAB_NAME);
    if (!sheet) return jsonOut_({ success: false, error: 'tab "' + TAB_NAME + '" not found' });

    if (sheet.getLastRow() === 0) {
      sheet.appendRow(HEADERS); // safety net only — the tab already has this header row as of setup
    }

    if (rows.length > 0) {
      // Defensive pad/truncate: never let a stray column-count mismatch in
      // one row (a trailing blank, an upstream schema tweak) crash the
      // whole batch — setValues() requires every row to match exactly.
      var fixedRows = rows.map(function (row) {
        row = row || [];
        if (row.length < HEADERS.length) {
          return row.concat(new Array(HEADERS.length - row.length).fill(''));
        }
        if (row.length > HEADERS.length) {
          return row.slice(0, HEADERS.length);
        }
        return row;
      });
      sheet.getRange(sheet.getLastRow() + 1, 1, fixedRows.length, HEADERS.length).setValues(fixedRows);
    }

    PropertiesService.getScriptProperties().setProperty('lastProcessedDate', date);

    return jsonOut_({ success: true, date: date, rows_written: rows.length });
  } catch (err) {
    return jsonOut_({ success: false, error: 'doPost failed: ' + err, date: date });
  }
}

function checkToken_(e) {
  var token = PropertiesService.getScriptProperties().getProperty('AUTH_TOKEN');
  return !!token && e.parameter && e.parameter.token === token;
}

function addDays_(yyyyMmDd, n) {
  var parts = yyyyMmDd.split('-').map(Number);
  var d = new Date(parts[0], parts[1] - 1, parts[2]);
  d.setDate(d.getDate() + n);
  return Utilities.formatDate(d, TIMEZONE, 'yyyy-MM-dd');
}

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

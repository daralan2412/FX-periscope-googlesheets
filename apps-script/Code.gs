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
 * Rebuilt 2026-08-30 (v3): switched from a date-watermark pull (D-1, then
 * D0 - see version history below) to the scraper pulling Periscope's
 * "Current Week" filter every run, twice a day (7am/7pm Asia/Taipei - see
 * .github/workflows/run.yml). Current Week necessarily overlaps what's
 * already in the sheet on every run (and a mission's own fields - times,
 * task columns - can fill in after it was first logged), so there is no
 * lastProcessedDate watermark anymore. doPost now appends every posted row
 * and then dedupes the whole DATA tab by mission_sas_id (column A), keeping
 * the LAST (most recently posted = freshest) row per mission and deleting
 * earlier duplicates - see dedupeByMissionId_().
 *
 * doGet   -> lightweight token/connectivity health check only. It no longer
 *            decides what the scraper should pull (there's nothing to
 *            decide - every run just re-syncs the current week).
 * doPost  -> appends the posted rows to DATA, then dedupes by mission_sas_id.
 *
 * Both endpoints require ?token=<AUTH_TOKEN>, checked against the AUTH_TOKEN
 * Script Property (Project Settings > Script Properties) — set your own
 * random value there, then put the same value in the GitHub secret
 * WEBAPP_TOKEN. Nothing here reuses the old pipeline's token.
 *
 * Version history:
 *   v1 (2026-08-30 early) - D-1 pull buffer, one date per run, tracked via
 *       a lastProcessedDate Script Property.
 *   v2 (2026-08-30 later) - switched the buffer to D0 (same-day pulls
 *       allowed), still one date per run via lastProcessedDate.
 *   v3 (2026-08-30, this version) - replaced the whole watermark/buffer
 *       model with "Current Week" + dedup by mission_sas_id, per explicit
 *       instruction. lastProcessedDate is no longer read or written; any
 *       leftover Script Property by that name is simply unused now.
 */

var SHEET_ID = '1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU';
var TAB_NAME = 'DATA';
var MISSION_ID_COL = 1; // column A = mission_sas_id (1-based)

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
  return jsonOut_({ success: true, message: 'ok' });
}

function doPost(e) {
  if (!checkToken_(e)) return jsonOut_({ success: false, error: 'unauthorized' });

  var body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonOut_({ success: false, error: 'invalid JSON body: ' + err });
  }

  var rows = body.rows || []; // array of arrays, each row matching HEADERS order/length

  // Everything below can throw (bad sheet state, a malformed row, a Sheets
  // API hiccup) — always return JSON, even on failure, so the caller never
  // has to parse an HTML error page.
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

    var duplicatesRemoved = dedupeByMissionId_(sheet);

    return jsonOut_({
      success: true,
      rows_received: rows.length,
      duplicates_removed: duplicatesRemoved,
      total_rows: sheet.getLastRow() - 1
    });
  } catch (err) {
    return jsonOut_({ success: false, error: 'doPost failed: ' + err });
  }
}

// Keeps the LAST occurrence of each non-blank mission_sas_id (column A) in
// the DATA tab and deletes earlier duplicate rows. Rows appended later in
// the same batch (or by a later run) reflect the freshest scrape of that
// mission - Periscope can fill in a mission's own fields (times, task
// columns) after it was first logged - so "last wins". Blank IDs are left
// alone (never treated as duplicates of each other). Returns the number of
// rows deleted.
function dedupeByMissionId_(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 3) return 0; // header + at most 1 data row - nothing to dedupe

  var ids = sheet.getRange(2, MISSION_ID_COL, lastRow - 1, 1).getValues();
  var lastIndexById = {};
  for (var i = 0; i < ids.length; i++) {
    var id = String(ids[i][0]).trim();
    if (!id) continue;
    lastIndexById[id] = i; // later occurrences overwrite earlier ones in this map
  }

  var rowsToDelete = [];
  for (var j = 0; j < ids.length; j++) {
    var idJ = String(ids[j][0]).trim();
    if (!idJ) continue;
    if (lastIndexById[idJ] !== j) {
      rowsToDelete.push(j + 2); // +2: back to 1-based sheet row number (data starts at row 2)
    }
  }

  // Delete from the bottom up so earlier row numbers stay valid as we go.
  rowsToDelete.sort(function (a, b) { return b - a; });
  rowsToDelete.forEach(function (rowNum) { sheet.deleteRow(rowNum); });

  return rowsToDelete.length;
}

function checkToken_(e) {
  var token = PropertiesService.getScriptProperties().getProperty('AUTH_TOKEN');
  return !!token && e.parameter && e.parameter.token === token;
}

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

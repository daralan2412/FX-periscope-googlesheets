// Periscope Ramp Ops (REP-1901) -> Google Sheets Web App
// NEW, independent project. Does not touch the old Periscope to Sheets
// WebApp project or its target sheet/tab.
// Source: https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35
// Target: Google Sheet 2026_RAW_MISSIONS (1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU), tab DATA (gid=1584481054)
// doGet returns the next date to pull (D-1 buffer, Asia/Taipei). doPost appends that date rows to DATA.
// Both endpoints require token param matching the AUTH_TOKEN Script Property.
// This file mirrors the deployed Apps Script project fx-periscope-sheets.

var SHEET_ID = '1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU';
var TAB_NAME = 'DATA';
var TIMEZONE = 'Asia/Taipei';
var PULL_BUFFER_DAYS = 1;

var HEADERS = ['mission_sas_id','date','station','airline_code','tail_number','vessel_description','job_name','mission_name','arrival_flight_number','departure_flight_number','org_city','dest_city','arr_time','dep_time','disp_name','agent_name','mission_notes','assigned_date','start_time','finish_time','task_1','task_2','task_3','task_4','task_5','task_6','task_7','task_8','task_9','task_10','task_11','task_12','task_13','task_14','task_15'];

function doGet(e) {
if (!checkToken_(e)) return jsonOut_({success: false, error: 'unauthorized'});
var props = PropertiesService.getScriptProperties();
var last = props.getProperty('lastProcessedDate');
if (!last) {
return jsonOut_({success: false, error: 'lastProcessedDate not set. Set it as a Script Property before the first run.'});
}
var todayTpe = Utilities.formatDate(new Date(), TIMEZONE, 'yyyy-MM-dd');
var nextDate = addDays_(last, 1);
var cutoff = addDays_(todayTpe, -PULL_BUFFER_DAYS);
if (nextDate <= cutoff) {
return jsonOut_({success: true, next_date: nextDate});
}
return jsonOut_({success: true, next_date: null});
}

function doPost(e) {
if (!checkToken_(e)) return jsonOut_({success: false, error: 'unauthorized'});
var body;
try {
body = JSON.parse(e.postData.contents);
} catch (err) {
return jsonOut_({success: false, error: 'invalid JSON body: ' + err});
}
var date = body.date;
var rows = body.rows || [];
if (!date) return jsonOut_({success: false, error: 'missing date'});
var ss = SpreadsheetApp.openById(SHEET_ID);
var sheet = ss.getSheetByName(TAB_NAME);
if (!sheet) return jsonOut_({success: false, error: 'tab not found'});
if (sheet.getLastRow() === 0) {
sheet.appendRow(HEADERS);
}
if (rows.length > 0) {
sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, HEADERS.length).setValues(rows);
}
PropertiesService.getScriptProperties().setProperty('lastProcessedDate', date);
return jsonOut_({success: true, date: date, rows_written: rows.length});
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

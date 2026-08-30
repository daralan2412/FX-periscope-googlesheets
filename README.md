# FX-periscope-googlesheets

New, independent pipeline. It does not read, write, or redeploy anything in the old daralan2412/periscope-to-sheets repo or its Apps Script project(s) - those keep running exactly as before.

Source: Periscope shared report REP-1901
https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35

Target: Google Sheet "2026_RAW_MISSIONS" (1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU), tab "DATA" (gid=1584481054)

Schedule: daily, D-1 pull buffer, Asia/Taipei calendar. Cron runs at 23:00 UTC = 07:00 Asia/Taipei.

## How it works

1. GitHub Actions runs scrape_and_upload.py on a daily cron (see .github/workflows/run.yml), or on demand via workflow_dispatch.
2. The script calls the deployed Apps Script Web App (GET) to find the next date due, based on the D-1 buffer and the lastProcessedDate Script Property.
3. If a date is due, it scrapes REP-1901 for that single day with Playwright and POSTs the rows back to the Web App.
4. The Web App appends the rows to the DATA tab and advances lastProcessedDate.

## Status

- Apps Script project "fx-periscope-sheets" is created and deployed as a Web App.
- Script Properties AUTH_TOKEN and lastProcessedDate are set.
- This repo has Code.gs, appsscript.json, scrape_and_upload.py, requirements.txt, and the workflow file.
- Still needed: add the SHEETS_WEBAPP_URL and WEBAPP_TOKEN repo secrets (Settings > Secrets and variables > Actions), matching the deployed Web App /exec URL and its AUTH_TOKEN, then run workflow_dispatch once and confirm rows land in DATA before trusting the daily cron.

## What was not verified against full daily volume

The Start Date / End Date filters and the Data table were confirmed live against a small sample only. Whether the table virtualizes/lazy-loads at a full day's row count (the old pipeline saw 400-2,400 rows/day) was not exercised - the scraper scrolls and re-collects to handle that, but run workflow_dispatch once and sanity-check the row count in Periscope's UI against what lands in the sheet before trusting the unattended daily run.

## Files

- apps-script/Code.gs - the Web App: doGet returns the next date to pull (D-1 buffer, Asia/Taipei calendar); doPost appends that date's rows to the DATA tab and advances lastProcessedDate.
- apps-script/appsscript.json - Apps Script project manifest.
- scrape_and_upload.py - calls the Web App, scrapes REP-1901 for the target date via Playwright, posts the rows back.
- .github/workflows/run.yml - daily cron + manual trigger.
- requirements.txt - requests, playwright.

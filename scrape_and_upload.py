#!/usr/bin/env python3
"""
scrape_and_upload.py - NEW pipeline, independent of the old
daralan2412/periscope-to-sheets repo/script (which is left untouched).

Source: Periscope/Sisense shared report "REP-1901"
        https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35
Target: Google Sheet "2026_RAW_MISSIONS", tab "DATA"
        via the Apps Script Web App (SHEETS_WEBAPP_URL / WEBAPP_TOKEN secrets).

Flow:
  1. GET  {SHEETS_WEBAPP_URL}?token=...  -> {"success": true, "next_date": "YYYY-MM-DD" | null}
  2. If next_date is null, the pull buffer isn't satisfied yet -> exit 0, no-op.
  3. Open REP-1901, set the Date Range filter to a single day (next_date to
     next_date) via the "Custom Range" option, then use the widget's own
     "Download Data" CSV export (NOT DOM scraping - the Data table is a
     virtualized Sisense grid, so most rows/columns are never in the DOM at
     once; the CSV export is generated server-side and is complete).
  4. POST {"date": next_date, "rows": [[...35 cols...], ...]} to the Web App,
     which appends the rows and advances lastProcessedDate.

Rebuilt 2026-08-30 after the first live run failed: the original version
DOM-scraped a "table tbody tr" selector that does not exist on this page
(the Data widget is a div-based Sisense "ninja-grid", not an HTML table),
so it silently scraped an unrelated table and posted malformed rows, which
crashed the Apps Script doPost() (setValues() column-count mismatch) and
came back as a non-JSON error page. The CSV export path used here reads
the exact same 35 columns, in the same order, as the target sheet's
header row - confirmed live against the deployed report.
"""

import csv
import io
import json
import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

PERISCOPE_URL = "https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35"
WEBAPP_URL = os.environ["SHEETS_WEBAPP_URL"]
WEBAPP_TOKEN = os.environ["WEBAPP_TOKEN"]

HEADERS = [
    "mission_sas_id", "date", "station", "airline_code", "tail_number", "vessel_description",
    "job_name", "mission_name", "arrival_flight_number", "departure_flight_number",
    "org_city", "dest_city", "arr_time", "dep_time", "disp_name", "agent_name", "mission_notes",
    "assigned_date", "start_time", "finish_time",
    "task_1", "task_2", "task_3", "task_4", "task_5", "task_6", "task_7", "task_8", "task_9",
    "task_10", "task_11", "task_12", "task_13", "task_14", "task_15",
]


def get_next_date():
    resp = requests.get(WEBAPP_URL, params={"token": WEBAPP_TOKEN}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Web App GET failed: {data}")
    return data.get("next_date")


def scrape_day_csv(target_date: str):
    """Filter REP-1901's Data widget to a single day and pull its CSV export.

    Uses the widget's built-in "Download Data" export instead of scraping the
    DOM: the Data widget is a virtualized grid (rows AND columns are only
    rendered near the viewport), so a DOM scrape would silently miss most of
    a real day's rows/columns. The CSV export is generated server-side and
    is complete regardless of what happened to be scrolled into view.

    Returns the CSV text, or None if the widget shows "Query returned no
    matching rows" for target_date (this happens for "today" under a D0
    pull buffer, early in the day before any ops have been logged yet -
    Sisense doesn't even offer a "Download Data" menu item when there's
    nothing to export, so this has to be checked for explicitly rather than
    treated as a scrape failure). Caller decides what a None means (skip
    posting, don't advance lastProcessedDate, retry next run).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # Capture the export URL from the first matching request, then poll
        # it ourselves rather than relying on the page's own retry behavior.
        # Registered before any interaction so we can't race the request.
        export_url = {"value": None}

        def on_response(resp):
            if export_url["value"] is None and "/download_csv/" in resp.url:
                export_url["value"] = resp.url

        page.on("response", on_response)

        try:
            page.goto(PERISCOPE_URL, wait_until="networkidle", timeout=60_000)

            # Make sure the dashboard has actually rendered its widgets (not
            # just "network idle") before touching anything.
            page.wait_for_selector(".filters-bar-label", timeout=30_000)
            page.wait_for_selector(".ninja-grid", timeout=30_000)

            # Open the report-level filters panel.
            page.locator(".filters-bar-label").first.click()
            page.wait_for_selector(".custom-date-option", timeout=10_000)
            page.wait_for_timeout(300)

            # Date Range column: select "Custom Range" to reveal Start/End Date.
            page.locator(".custom-date-option").first.click()
            page.wait_for_selector("input[placeholder='Start Date']", timeout=10_000)
            page.wait_for_timeout(300)

            # A radio-button element in the same panel intermittently overlaps
            # these inputs (visually settled, but still "receives pointer
            # events" per Playwright's actionability check), so a plain
            # .click() can retry for the full 30s timeout and fail. force=True
            # skips that receives-events check and fills directly - safe here
            # since we already waited for the input to exist and be enabled.
            start_input = page.get_by_placeholder("Start Date")
            end_input = page.get_by_placeholder("End Date")
            start_input.fill(target_date, force=True)
            end_input.fill(target_date, force=True)

            # Apply the filter and let the widget refresh.
            apply_button = page.locator(".apply-button")
            apply_button.click(force=True)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            # Find the "Data" widget specifically (report may gain more
            # widgets later).
            widget = page.locator(".widget-container", has=page.locator(".widget-title", has_text="Data")).first
            widget.scroll_into_view_if_needed()

            # No rows for this date yet (common for "today" under D0, before
            # ops have logged anything) - Sisense shows this in place of the
            # grid and doesn't offer a "Download Data" menu item at all, so
            # check for it up front instead of timing out waiting for a menu
            # that will never appear.
            if widget.locator(".error-message", has_text="no matching rows").count() > 0:
                browser.close()
                return None

            # Open the per-widget menu.
            widget.hover()
            page.wait_for_timeout(500)
            widget.locator(".controls .expand.button").click(force=True)
            page.wait_for_selector("text=Download Data", timeout=10_000)
            page.get_by_text("Download Data", exact=True).click()

            deadline = time.time() + 20
            while export_url["value"] is None and time.time() < deadline:
                page.wait_for_timeout(250)
            if export_url["value"] is None:
                raise RuntimeError("Did not observe a download_csv request after clicking Download Data")
        except Exception:
            try:
                page.screenshot(path="debug_failure.png", full_page=True)
                with open("debug_failure.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception as diag_err:
                print(f"(could not capture debug artifacts: {diag_err})", file=sys.stderr)
            browser.close()
            raise

        csv_text = None
        deadline = time.time() + 90
        while time.time() < deadline:
            resp = page.context.request.get(export_url["value"])
            if resp.status == 200:
                csv_text = resp.text()
                break
            page.wait_for_timeout(2000)

        browser.close()

        if csv_text is None:
            raise RuntimeError("Timed out waiting for the CSV export to become ready")
        return csv_text


def parse_csv_rows(csv_text: str):
    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return []

    if [h.strip() for h in header] != HEADERS:
        print(
            "WARNING: CSV header does not match the expected 35-column schema. "
            f"Got: {header}",
            file=sys.stderr,
        )

    rows = []
    for row in reader:
        # Defensive pad/truncate: never let a stray column-count mismatch
        # (a trailing blank line, a schema tweak upstream, etc.) crash the
        # whole batch - Apps Script's setValues() requires a fixed width.
        if len(row) < len(HEADERS):
            row = row + [""] * (len(HEADERS) - len(row))
        elif len(row) > len(HEADERS):
            row = row[: len(HEADERS)]
        rows.append(row)
    return rows


def post_rows(target_date: str, rows: list):
    resp = requests.post(
        WEBAPP_URL,
        params={"token": WEBAPP_TOKEN},
        data=json.dumps({"date": target_date, "rows": rows}),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Web App POST failed: {data}")
    return data


def main():
    next_date = get_next_date()
    if not next_date:
        print("Nothing due yet (pull buffer not satisfied, Asia/Taipei calendar). Exiting.")
        return

    print(f"Pulling Periscope REP-1901 data for {next_date} (Asia/Taipei)...")
    csv_text = scrape_day_csv(next_date)
    if csv_text is None:
        # No rows logged for this date yet (e.g. "today" under D0, early in
        # the day). Don't post, don't advance lastProcessedDate - exit 0 so
        # this isn't treated as a failure, and the same date is retried next
        # run once real data exists.
        print(f"No rows yet for {next_date} - nothing to pull. Will retry next run.")
        return

    rows = parse_csv_rows(csv_text)
    print(f"Scraped {len(rows)} rows for {next_date}.")

    result = post_rows(next_date, rows)
    print(f"Wrote {result.get('rows_written')} rows for {next_date}. lastProcessedDate advanced.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

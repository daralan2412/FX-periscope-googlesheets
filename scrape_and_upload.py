#!/usr/bin/env python3
"""
scrape_and_upload.py - NEW pipeline, independent of the old
daralan2412/periscope-to-sheets repo (left untouched).

Source: Periscope report REP-1901
        https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35
Target: Google Sheet 2026_RAW_MISSIONS, tab DATA, via the Apps Script Web App.

NOTE: the Start Date/End Date filter and Data table were confirmed live at
build time against a small sample only. Table virtualization at full daily
volume (hundreds of rows) was not exercised - verify before trusting the
first scheduled run.
"""

import json
import os
import sys

import requests
from playwright.sync_api import sync_playwright

PERISCOPE_URL = "https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35"
WEBAPP_URL = os.environ["SHEETS_WEBAPP_URL"]
WEBAPP_TOKEN = os.environ["WEBAPP_TOKEN"]

def get_next_date():
    resp = requests.get(WEBAPP_URL, params={"token": WEBAPP_TOKEN}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError("Web App GET failed: " + str(data))
    return data.get("next_date")

def scrape_day(target_date):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(PERISCOPE_URL, wait_until="networkidle", timeout=60000)

        page.get_by_placeholder("Start Date").fill(target_date)
        page.get_by_placeholder("End Date").fill(target_date)
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        rows = []
        seen_ids = set()
        stagnant_rounds = 0
        rounds = 0
        while stagnant_rounds < 3 and rounds < 200:
            rounds += 1
            for r in page.query_selector_all("table tbody tr"):
                cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
                if not cells:
                    continue
                key = cells[0]
                if key and key not in seen_ids:
                    seen_ids.add(key)
                    rows.append(cells)
            before = len(seen_ids)
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(500)
            if len(seen_ids) == before:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0

        browser.close()
        return rows

def post_rows(target_date, rows):
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
        raise RuntimeError("Web App POST failed: " + str(data))
    return data

def main():
    next_date = get_next_date()
    if not next_date:
        print("Nothing due yet (D-1 buffer not satisfied). Exiting.")
        return

    print("Pulling Periscope REP-1901 data for " + next_date + " (Asia/Taipei)...")
    rows = scrape_day(next_date)
    print("Scraped " + str(len(rows)) + " rows for " + next_date + ".")

    result = post_rows(next_date, rows)
    print("Wrote " + str(result.get("rows_written")) + " rows for " + next_date + ".")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAILED: " + str(exc), file=sys.stderr)
        sys.exit(1)

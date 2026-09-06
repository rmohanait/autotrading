#!/usr/bin/env python3
"""
capture_bigshort.py — forward-capture entry point (Phase 1 remedy).

BigShort has no API/export and its historical values look-ahead, so the only
sound dataset is captured live every 5 minutes. This script wires the capture
loop (bstrader_research.capture) to a value `reader`.

Two reader modes are provided:

  --mode manual   : prompts you to type the on-screen values each interval.
                    Good for a low-cost pilot / spot checks. Blocks on input.

  --mode playwright : SKELETON for scraping app.bigshort.com's rendered values
                    with a logged-in browser session. You must fill in the DOM
                    selectors for your layout (they are not published and change
                    with the UI). Read BigShort's Terms of Service first — this is
                    for your own research use of your own subscription; do not
                    redistribute captured data.

Both modes stamp capture-time == read-time, so there is no look-ahead. Re-reads
of a past bucket are versioned so you can later prove whether BigShort repaints
(capture.analyze_repaint) — the single most important integrity check before you
trust any historical-looking BigShort backtest.

Usage:
  python research/capture_bigshort.py --symbol SPY --mode manual
  python research/capture_bigshort.py --symbol SPY --mode playwright   # after editing selectors
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from bstrader_research.capture import CaptureWriter, run_capture_loop, FEATURE_KEYS


def manual_reader(symbol: str) -> dict:
    """Prompt the operator for current values. Enter blank to skip a field."""
    print(f"\n[{symbol}] enter current BigShort values (blank = skip):")
    vals = {}
    raw = input("  price: ").strip()
    if raw:
        vals["price"] = float(raw)
    for k in FEATURE_KEYS:
        raw = input(f"  {k}: ").strip()
        if raw:
            try:
                vals[k] = float(raw)
            except ValueError:
                vals[k] = raw   # e.g. gex_regime as a label
    return vals


def make_playwright_reader(url: str = "https://app.bigshort.com/"):
    """
    Returns a reader() that scrapes rendered values from a logged-in browser.

    THIS IS A SKELETON. BigShort's DOM/canvas structure is not documented and you
    must supply selectors for your own chart layout. Values rendered on a <canvas>
    cannot be read from the DOM at all and would need the on-page numeric readouts
    or an OCR step — verify what your layout exposes as text.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    # Reuse a persistent profile so your BigShort login is remembered.
    profile_dir = os.path.join(os.path.dirname(__file__), ".bs_profile")
    browser = pw.chromium.launch_persistent_context(
        profile_dir,
        headless=False,  # keep visible so you can log in the first time
        executable_path=os.getenv("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium") or None,
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto(url)
    print("Log in to BigShort in the opened browser, load your SPY/QQQ 5-min "
          "layout with the indicators visible, then leave it running.")

    # Map each feature to the selector that holds its numeric readout in YOUR layout.
    # Fill these in; leave a feature out to store null for it.
    SELECTORS: dict[str, str] = {
        # "nof": "[data-testid='nof-readout']",
        # "gex": "#gex-value",
        # ...
    }

    def reader(symbol: str) -> dict:
        vals: dict = {}
        try:
            price_sel = SELECTORS.get("price")
            if price_sel:
                vals["price"] = float(page.inner_text(price_sel).replace(",", ""))
            for key, sel in SELECTORS.items():
                if key == "price":
                    continue
                txt = page.inner_text(sel).strip()
                try:
                    vals[key] = float(txt.replace(",", ""))
                except ValueError:
                    vals[key] = txt
        except Exception as e:
            print(f"[warn] read failed: {e}")
        return vals

    return reader


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--mode", default="manual", choices=["manual", "playwright"])
    ap.add_argument("--max", type=int, default=None, help="stop after N snapshots")
    args = ap.parse_args()

    if args.mode == "manual":
        reader = manual_reader
    else:
        reader = make_playwright_reader()

    writer = CaptureWriter(args.symbol)
    print(f"Capturing {args.symbol} every 5 minutes -> {writer.csv_path}")
    print("Ctrl-C to stop. Run capture.compact_to_parquet() when you have data.")
    run_capture_loop(args.symbol, reader, writer=writer, max_snapshots=args.max)


if __name__ == "__main__":
    main()

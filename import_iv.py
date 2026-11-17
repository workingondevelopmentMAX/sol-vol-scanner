"""Import a daily SOL implied-volatility series you got from elsewhere.

  python import_iv.py my_file.csv

The CSV needs a header row with:
  date    - YYYY-MM-DD (or DD/MM/YYYY)
  atm_iv  - 30-day at-the-money implied vol, as 65.2 or 0.652  (a column named iv or close also works)

Imported values beat trade-backfill estimates but never replace the scanner's own daily readings.
"""
import csv
import sys
from datetime import datetime

import vol_gauge


def parse_date(s: str) -> str:
    s = s.strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"unrecognised date {s!r}")


def main(path: str) -> int:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("File is empty.")
        return 1
    cols = {c.lower().strip(): c for c in rows[0]}
    dcol = cols.get("date") or cols.get("day") or cols.get("timestamp")
    vcol = cols.get("atm_iv") or cols.get("atm_iv_30d") or cols.get("iv") or cols.get("close")
    if not dcol or not vcol:
        print(f"Need a 'date' column and an 'atm_iv' (or 'iv'/'close') column. Found: {list(rows[0])}")
        return 1
    incoming = {}
    for r in rows:
        try:
            iv = vol_gauge.normalize_iv(r[vcol])
            if iv:
                incoming[parse_date(r[dcol])] = iv
        except (ValueError, KeyError):
            continue
    hist = {r["date"]: r for r in vol_gauge.load_history()}
    added = 0
    for d, iv in incoming.items():
        r = hist.setdefault(d, {"date": d})
        if r.get("iv_source") == "snapshot":
            continue
        r["atm_iv_30d"], r["iv_source"] = iv, "imported"
        added += 1
    vol_gauge.save_history(list(hist.values()))
    print(f"Imported {added} day(s) of IV ({len(incoming) - added} skipped because the scanner already has its own reading).")
    print("Run run_scan.bat to refresh the dashboard.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))

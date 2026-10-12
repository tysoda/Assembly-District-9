"""Snapshot Optiq ballot-return data for California Assembly District 9.

Saves two files per run into data/optiq_ad9/:
  raw_<UTC stamp>.json   - the API response, untouched
  flat_<UTC stamp>.csv   - one row per record, with snapshot_utc added
"""
import csv
import datetime
import json
import pathlib
import sys
import time
import urllib.request

URL = "https://abev.optiqdata.com/api/ballot-returns/STATE%20ASSEMBLY%209"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "optiq_ad9"


def fetch(url, attempts=3):
    for i in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ad9-snapshot/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # network or JSON error
            print(f"attempt {i} failed: {e}", file=sys.stderr)
            if i == attempts:
                raise
            time.sleep(10 * i)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    payload = fetch(URL)

    (OUT / f"raw_{stamp}.json").write_text(json.dumps(payload))

    rows = payload.get("ballotReturns") or []
    if not rows:
        print("WARNING: no ballotReturns records in response", file=sys.stderr)
        return

    fields = ["snapshot_utc"] + sorted({k for row in rows for k in row})
    with open(OUT / f"flat_{stamp}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({"snapshot_utc": stamp, **row})

    print(f"{len(rows)} records saved at {stamp}")


if __name__ == "__main__":
    main()

"""Snapshot Optiq ballot-return data for California Assembly District 9.

Saves per run into data/optiq_ad9/:
  raw_<UTC stamp>.json         - district ballot returns (party x age x gender x ethnicity x return date), untouched
  flat_<UTC stamp>.csv         - the same, one row per record, with snapshot_utc added
  raw_county_<UTC stamp>.json  - returns by county (registered and returned by party), untouched
  county_<UTC stamp>.csv       - the same, one row per county, with snapshot_utc added
The county endpoint has no per-day breakdown, so these snapshots are its only time series.
Each endpoint is fetched independently: a failure in one doesn't lose the other.
"""
import csv
import datetime
import json
import pathlib
import sys
import time
import urllib.request

URL = "https://abev.optiqdata.com/api/ballot-returns/STATE%20ASSEMBLY%209"
COUNTY_URL = "https://abev.optiqdata.com/api/geography-breakdown/STATE%20ASSEMBLY%209?level=county_name"
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


def write_csv(path, rows, stamp):
    fields = ["snapshot_utc"] + sorted({k for row in rows for k in row})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({"snapshot_utc": stamp, **row})


def district(stamp):
    payload = fetch(URL)
    (OUT / f"raw_{stamp}.json").write_text(json.dumps(payload))
    rows = payload.get("ballotReturns") or []
    if not rows:
        raise ValueError("no ballotReturns records in response")
    write_csv(OUT / f"flat_{stamp}.csv", rows, stamp)
    print(f"district: {len(rows)} records saved at {stamp}")


def by_county(stamp):
    payload = fetch(COUNTY_URL)
    (OUT / f"raw_county_{stamp}.json").write_text(json.dumps(payload))
    rows = payload if isinstance(payload, list) else payload.get("data") or []
    if not rows:
        raise ValueError("no county records in response")
    write_csv(OUT / f"county_{stamp}.csv", rows, stamp)
    print(f"county: {len(rows)} counties saved at {stamp}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    failed = []
    for name, job in (("district", district), ("county", by_county)):
        try:
            job(stamp)
        except Exception as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)
            failed.append(name)
    if len(failed) == 2:
        sys.exit(1)     # nothing saved: fail the run so it shows up in Actions


if __name__ == "__main__":
    main()

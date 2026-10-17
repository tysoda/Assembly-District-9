"""Snapshot Civiq (formerly Optiq) ballot-return data for AD9 and Stanislaus Supervisor District 1.

Saves per run into data/optiq_ad9/ and data/optiq_d1/:
  raw_<UTC stamp>.json         - district ballot returns (party x age x gender x ethnicity x return date), untouched
  flat_<UTC stamp>.csv         - the same, one row per record, with snapshot_utc added
  raw_county_<UTC stamp>.json  - returns by county (registered and returned by party), untouched
  county_<UTC stamp>.csv       - the same, one row per county, with snapshot_utc added
The county endpoint has party figures only: the age, gender and ethnicity breakdown is
district-wide, in the ballot-returns feed. The county endpoint has no per-day breakdown,
so these snapshots are its only time series. Precinct figures come from
scripts/civiq_precincts.py. Each endpoint is fetched independently: a failure in one
doesn't lose the others.
"""
import csv
import datetime
import json
import os
import pathlib
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://abev.civiq.vote/api"
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
DISTRICTS = {                      # output folder: Civiq district name
    "optiq_ad9": "STATE ASSEMBLY 9",
    "optiq_d1": "STANISLAUS COUNTY SUPERVISORIAL 1",
}
_CA = "/root/.ccr/ca-bundle.crt"
CTX = ssl.create_default_context(cafile=_CA if os.path.exists(_CA) else None)


def fetch(url, attempts=3):
    for i in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ad9-snapshot/1.0"})
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
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


def district(out, slug, stamp):
    payload = fetch(f"{BASE}/ballot-returns/{urllib.parse.quote(slug)}")
    (out / f"raw_{stamp}.json").write_text(json.dumps(payload))
    rows = payload.get("ballotReturns") or []
    if not rows:
        raise ValueError("no ballotReturns records in response")
    write_csv(out / f"flat_{stamp}.csv", rows, stamp)
    print(f"{slug} district: {len(rows)} records saved at {stamp}")


def by_county(out, slug, stamp):
    payload = fetch(f"{BASE}/geography-breakdown/{urllib.parse.quote(slug)}?level=county_name")
    (out / f"raw_county_{stamp}.json").write_text(json.dumps(payload))
    rows = payload if isinstance(payload, list) else payload.get("data") or []
    if not rows:
        raise ValueError("no county records in response")
    write_csv(out / f"county_{stamp}.csv", rows, stamp)
    print(f"{slug} county: {len(rows)} counties saved at {stamp}")


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    jobs = failed = 0
    for folder, slug in DISTRICTS.items():
        out = DATA / folder
        out.mkdir(parents=True, exist_ok=True)
        for name, job in (("district", district), ("county", by_county)):
            jobs += 1
            try:
                job(out, slug, stamp)
            except Exception as e:
                print(f"ERROR {slug} {name}: {e}", file=sys.stderr)
                failed += 1
    if failed == jobs:
        sys.exit(1)     # nothing saved: fail the run so it shows up in Actions


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
sos_results.py
==============
Logs California Secretary of State results for Assembly District 9 as they are
published, to measure how quickly each county reports.

Polls the SOS results API (api.sos.ca.gov) for AD9 districtwide and for each of
its five counties. Every poll is logged; results are logged only when they change,
stamped with the time this script first saw them.

Output (data/sos_results/):
  polls.csv    one row per URL per poll: poll time, HTTP status, the feed's own
               ReportingTime and "Reporting" line, Last-Modified/ETag headers, and
               whether the content changed since the previous poll
  results.csv  one row per candidate each time a geography's figures change:
               first_seen_utc, ReportingTime, precincts reported/total, votes, percent
  raw/<geography>_<UTC stamp>.json   the response, untouched, whenever it changed

Usage
  python scripts/sos_results.py                       # one poll
  python scripts/sos_results.py --until 2026-11-04T15:00Z --interval 120 --max-minutes 340 --commit
      # poll every 2 minutes until the deadline (or 340 minutes), committing and
      # pushing after each change so nothing is lost if the run is cut short
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "sos_results"
BASE = "https://api.sos.ca.gov/returns/state-assembly/district/9"
GEOGRAPHIES = {"district": BASE} | {
    c: f"{BASE}/county/{c}" for c in ("amador", "calaveras", "sacramento", "san-joaquin", "stanislaus")}
_CA = "/root/.ccr/ca-bundle.crt"
CTX = ssl.create_default_context(cafile=_CA if os.path.exists(_CA) else None)
POLL_FIELDS = ["poll_utc", "geography", "http_status", "error", "reporting_time", "reporting",
               "last_modified", "etag", "content_hash", "changed"]
RESULT_FIELDS = ["first_seen_utc", "geography", "reporting_time", "reporting", "precincts_reported",
                 "precincts_total", "race_title", "candidate", "party", "incumbent", "votes", "percent"]


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def fetch(url: str) -> tuple[int, bytes, dict, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "AD9 results logger (github.com/tysoda)",
                                               "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            return r.status, r.read(), dict(r.headers), ""
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(e.headers or {}), f"HTTP {e.code}"
    except Exception as e:
        return 0, b"", {}, str(e)[:200]


def contest(payload) -> dict:
    """The feed wraps the contest in a list in some versions; return the contest object."""
    if isinstance(payload, list):
        payload = next((p for p in payload if isinstance(p, dict)), {})
    return payload if isinstance(payload, dict) else {}


def precincts(reporting: str) -> tuple[str, str]:
    """'45.0% (120 of 267) precincts reporting' → ('120', '267')."""
    m = re.search(r"\(([\d,]+)\s+of\s+([\d,]+)\)", reporting or "")
    return (m.group(1).replace(",", ""), m.group(2).replace(",", "")) if m else ("", "")


def append(path: Path, fields: list, rows: list) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def last_hashes() -> dict:
    """Content hash of the last successful poll per geography (so restarts don't re-log)."""
    out = {}
    p = OUT / "polls.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            if r["content_hash"]:
                out[r["geography"]] = r["content_hash"]
    return out


def poll(seen: dict) -> int:
    t = now()
    stamp = t.strftime("%Y-%m-%dT%H%M%SZ")
    polls, results, changes = [], [], 0
    for geo, url in GEOGRAPHIES.items():
        status, body, headers, err = fetch(url)
        c, h, changed = {}, "", False
        if body:
            try:
                payload = json.loads(body)
                c = contest(payload)
                # Hash the parsed content so whitespace/key-order noise doesn't count as a change
                h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
            except ValueError:
                err = err or f"not JSON: {body[:80]!r}"
        if h and h != seen.get(geo):
            changed, seen[geo] = True, h
            changes += 1
            (OUT / "raw").mkdir(parents=True, exist_ok=True)
            (OUT / "raw" / f"{geo}_{stamp}.json").write_bytes(body)
            rep = c.get("Reporting", "")
            pr, pt = precincts(rep)
            for cand in c.get("candidates") or []:
                results.append({"first_seen_utc": t.isoformat(timespec="seconds"), "geography": geo,
                                "reporting_time": c.get("ReportingTime", ""), "reporting": rep,
                                "precincts_reported": pr, "precincts_total": pt,
                                "race_title": c.get("raceTitle", ""), "candidate": cand.get("Name", ""),
                                "party": cand.get("Party", ""), "incumbent": cand.get("incumbent", ""),
                                "votes": str(cand.get("Votes", "")).replace(",", ""),
                                "percent": cand.get("Percent", "")})
        polls.append({"poll_utc": t.isoformat(timespec="seconds"), "geography": geo, "http_status": status,
                      "error": err, "reporting_time": c.get("ReportingTime", ""), "reporting": c.get("Reporting", ""),
                      "last_modified": headers.get("Last-Modified", ""), "etag": headers.get("ETag", ""),
                      "content_hash": h, "changed": changed})
        time.sleep(1)
    append(OUT / "polls.csv", POLL_FIELDS, polls)
    if results:
        append(OUT / "results.csv", RESULT_FIELDS, results)
    d = next((p for p in polls if p["geography"] == "district"), {})
    print(f"{t:%H:%M:%S}Z  {changes} changed  district: {d.get('reporting') or d.get('error') or '—'}"
          f" ({d.get('reporting_time', '')})", flush=True)
    return changes


def git_push(message: str) -> None:
    cmds = [["git", "add", "data/sos_results"], ["git", "commit", "-q", "-m", message],
            ["git", "pull", "-q", "--rebase", "--autostash"], ["git", "push", "-q"]]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode and cmd[1] != "commit":
            print(f"  ! {' '.join(cmd)}: {r.stderr.strip()[:200]}", file=sys.stderr)
            return
        if r.returncode:            # nothing to commit
            return


def main():
    ap = argparse.ArgumentParser(description="Log SOS results for AD9 as they are published.")
    ap.add_argument("--until", help="Keep polling until this UTC time (e.g. 2026-11-04T15:00Z).")
    ap.add_argument("--max-minutes", type=float, default=340, help="Stop after this long (GitHub jobs end at 360).")
    ap.add_argument("--interval", type=int, default=120, help="Seconds between polls.")
    ap.add_argument("--commit", action="store_true", help="Commit and push after each poll that changed something.")
    args = ap.parse_args()

    seen = last_hashes()
    start = last_commit = now()
    stop = start + dt.timedelta(minutes=args.max_minutes)
    if args.until:
        stop = min(stop, dt.datetime.fromisoformat(args.until.replace("Z", "+00:00")))
    while True:
        changes = poll(seen)
        # Push on every change; otherwise every 15 minutes (the poll log alone)
        if args.commit and (changes or now() - last_commit >= dt.timedelta(minutes=15) or not args.until):
            last_commit = now()
            git_push(f"SOS AD9 results {now():%Y-%m-%dT%H%MZ}" + (f" ({changes} changed)" if changes else ""))
        if not args.until or now() + dt.timedelta(seconds=args.interval) >= stop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

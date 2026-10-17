#!/usr/bin/env python3
"""
civiq_precincts.py
==================
Statewide precinct ballot returns from Civiq's map tiles, plus the AD9 and
Stanislaus Supervisor District 1 subsets.

Civiq's precinct map (abev.civiq.vote) is served as Mapbox vector tiles:
  https://abev.civiq.vote/api/tiles/{district}/{z}/{x}/{y}.pbf
Each tile has a "precincts" layer with, per precinct: precinct_shapefile,
county_name, {dem,rep,other,total}_{registered,returned}, margin_pct. A tile
holds only the precincts of the district in its URL (for a split county, only
the part inside the district).
Adapted from fetch_optiq_precincts.py in tysoda/2026-Ca-SD4-forecast-V2.

Each run:
  1. County totals: Civiq's county-level feed for each of the 58 "<NAME> COUNTY"
     districts, saved to data/civiq/state/county_totals.csv. These are the check
     on the statewide precinct sums.
  2. Statewide precincts: tiles hold only the precincts of the district named in
     the URL, so each county is fetched under its own "<NAME> COUNTY" address
     (finer zooms tried until its total matches), pieces cut by tile edges
     merged. Saved to
     data/civiq/state/precincts_<stamp>.csv.gz, with per-county coverage in
     data/civiq/state/coverage.csv.
  3. Districts (AD9, D1): precincts whose centre falls inside the district
     boundary (data/boundaries/*.geojson), checked against the district's own
     county feed. If the district total is off, its own tiles are tried too and
     the closer match is kept. Saved to data/civiq/<district>/precincts.csv
     (time series), coverage.csv and precincts.geojson (written when the
     precinct set changes).

The county feed has party figures only (no age, gender or ethnicity); the
demographic breakdown exists only district-wide, in the ballot-returns feed that
scripts/optiq_snapshot.py saves.

Usage
  python scripts/civiq_precincts.py              # fetch, validate, write
  python scripts/civiq_precincts.py --dry-run    # fetch and validate only
  python scripts/civiq_precincts.py --offline DIR
      # test with saved inputs: DIR/tiles/{z}-{x}-{y}.pbf and DIR/feeds/<slug>.json
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import io
import json
import math
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import mapbox_vector_tile as mvt
import pandas as pd
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "civiq"
BOUNDARIES = REPO / "data" / "boundaries"
BASE = "https://abev.civiq.vote/api"
PROBE_ZOOM = 5                       # coarse pass to find each county's extent
CA_BBOX = (-124.5, 32.5, -114.1, 42.05)
ZOOMS = (8, 9)
MATCH_TOLERANCE = 0.02               # |precinct sum − Civiq county total| / total
MIN_COVERAGE = 0.95                  # share of statewide registration in matching counties needed to save
FIELDS = ["dem_registered", "rep_registered", "other_registered", "total_registered",
          "dem_returned", "rep_returned", "other_returned", "total_returned"]
CA_COUNTIES = [
    "ALAMEDA", "ALPINE", "AMADOR", "BUTTE", "CALAVERAS", "COLUSA", "CONTRA COSTA", "DEL NORTE",
    "EL DORADO", "FRESNO", "GLENN", "HUMBOLDT", "IMPERIAL", "INYO", "KERN", "KINGS", "LAKE",
    "LASSEN", "LOS ANGELES", "MADERA", "MARIN", "MARIPOSA", "MENDOCINO", "MERCED", "MODOC",
    "MONO", "MONTEREY", "NAPA", "NEVADA", "ORANGE", "PLACER", "PLUMAS", "RIVERSIDE",
    "SACRAMENTO", "SAN BENITO", "SAN BERNARDINO", "SAN DIEGO", "SAN FRANCISCO", "SAN JOAQUIN",
    "SAN LUIS OBISPO", "SAN MATEO", "SANTA BARBARA", "SANTA CLARA", "SANTA CRUZ", "SHASTA",
    "SIERRA", "SISKIYOU", "SOLANO", "SONOMA", "STANISLAUS", "SUTTER", "TEHAMA", "TRINITY",
    "TULARE", "TUOLUMNE", "VENTURA", "YOLO", "YUBA"]
DISTRICTS = {
    "ad9": {"slug": "STATE ASSEMBLY 9", "boundary": "ad9.geojson",
            "counties": {"AMADOR", "CALAVERAS", "SACRAMENTO", "SAN JOAQUIN", "STANISLAUS"}},
    "d1": {"slug": "STANISLAUS COUNTY SUPERVISORIAL 1", "boundary": "d1.geojson",
           "counties": {"STANISLAUS"}},
}
# Census TIGER/Line state legislative districts (lower chamber), current lines; tried in order
TIGER_SLDL = [f"https://www2.census.gov/geo/tiger/TIGER{y}/SLDL/tl_{y}_06_sldl.zip" for y in (2025, 2024, 2023)]


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Source:
    """Fetches tiles and feeds, or reads them from an --offline directory."""

    def __init__(self, offline: Path | None = None):
        self.offline = offline
        ca = "/root/.ccr/ca-bundle.crt"
        self.ctx = ssl.create_default_context(cafile=ca if os.path.exists(ca) else None)
        self.requests = 0

    def get(self, url: str, attempts: int = 3) -> bytes:
        for i in range(1, attempts + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AD9/D1 ballot tracker)"})
                with urllib.request.urlopen(req, timeout=60, context=self.ctx) as r:
                    self.requests += 1
                    return r.read()
            except Exception as e:
                if getattr(e, "code", None) in (204, 404) or i == attempts:
                    raise
                time.sleep(5 * i)

    def tile(self, slug: str, z: int, x: int, y: int) -> bytes | None:
        if self.offline:
            p = self.offline / "tiles" / f"{z}-{x}-{y}.pbf"
            return p.read_bytes() if p.exists() else None
        try:
            data = self.get(f"{BASE}/tiles/{urllib.parse.quote(slug)}/{z}/{x}/{y}.pbf")
        except Exception as e:
            if getattr(e, "code", None) in (204, 404):
                return None
            raise
        time.sleep(0.25)                 # be gentle with the server
        return data

    def county_feed(self, slug: str) -> list | None:
        """Civiq's county breakdown for a district: one row per county (or part of one)."""
        if self.offline:
            p = self.offline / "feeds" / f"{slug}.json"
            return json.loads(p.read_text()) if p.exists() else None
        try:
            rows = json.loads(self.get(f"{BASE}/geography-breakdown/{urllib.parse.quote(slug)}?level=county_name"))
        except Exception as e:
            print(f"  ! county feed for {slug} failed: {e}", file=sys.stderr)
            return None
        return rows if isinstance(rows, list) else rows.get("data")


def feed_totals(rows: list | None) -> dict:
    """{COUNTY: {total_registered, dem_registered, …}} in the precinct field names."""
    out = {}
    for r in rows or []:
        o = {}
        for party, key in (("dem", "dem"), ("rep", "rep"), ("other", "npp"), ("total", "total")):
            o[f"{party}_registered"] = int(r.get(f"{key}Registered") or 0)
            o[f"{party}_returned"] = int(r.get(f"{key}Returned") or 0)
        o["latest_return_date"] = r.get("latestReturnDate") or ""
        out[str(r["geography"]).upper().strip()] = o
    return out


# ── Tiles ────────────────────────────────────────────────────────────────────
def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_for_bbox(bbox, z: int) -> list[tuple[int, int, int]]:
    lon0, lat0, lon1, lat1 = bbox
    x0, y0 = tile_xy(lon0, lat1, z)
    x1, y1 = tile_xy(lon1, lat0, z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def to_lonlat(px, py, z, x, y, extent):
    n = 2 ** z
    lon = (x + px / extent) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n))))
    return round(lon, 6), round(lat, 6)


def decode_tiles(src: Source, slug: str, tiles, stats: dict) -> dict:
    """{(COUNTY, precinct): {"props": {...}, "polygons": [...]}}

    A precinct cut by a tile edge appears in each tile with identical figures, so
    pieces are merged on (county, precinct id, figures). Different precincts that
    share an id keep separate entries, labelled id, id#2, ….
    """
    stats.update(tiles=len(tiles), features=0, no_county=0, no_id=0, keys=set())
    groups: dict = {}
    for z, x, y in tiles:
        data = src.tile(slug, z, x, y)
        if not data:
            continue
        layer = mvt.decode(data, default_options={"y_coord_down": True}).get("precincts")
        if not layer:
            continue
        ext = layer.get("extent", 4096)
        seen_here: dict = {}
        for f in layer["features"]:
            stats["features"] += 1
            p = f["properties"]
            stats["keys"].update(p)
            county = str(p.get("county_name") or "").upper().strip()
            pid = str(p.get("precinct_shapefile") or "").strip()
            if not county:
                stats["no_county"] += 1
            if not pid:
                stats["no_id"] += 1
                pid = "(no id)"
            sig = tuple(int(p.get(k) or 0) for k in FIELDS)
            n = seen_here.get((county, pid, sig), 0)
            seen_here[(county, pid, sig)] = n + 1
            sig = sig + (n,)
            g = f["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            polys = [[[to_lonlat(px, py, z, x, y, ext) for px, py in ring] for ring in poly] for poly in polys]
            rec = groups.setdefault((county, pid), {}).setdefault(sig, {"props": p, "polygons": []})
            rec["polygons"].extend(polys)
    out = {}
    for (county, pid), by_sig in groups.items():
        for i, sig in enumerate(sorted(by_sig, key=lambda s: (-s[3], s[-1]))):
            out[(county, pid if i == 0 else f"{pid}#{i + 1}")] = by_sig[sig]
    return out


def centroid(polygons: list) -> tuple[float, float]:
    """Area-weighted centre of the outer rings (falls back to the mean of points)."""
    ax = ay = a_tot = 0.0
    for poly in polygons:
        if not poly or not poly[0]:
            continue
        ring = poly[0]
        a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            cr = x0 * y1 - x1 * y0
            a += cr; cx += (x0 + x1) * cr; cy += (y0 + y1) * cr
        if abs(a) > 1e-12:
            ax += cx / 3; ay += cy / 3; a_tot += a
    if abs(a_tot) > 1e-12:
        return ax / a_tot, ay / a_tot
    pts = [pt for poly in polygons if poly for pt in poly[0]]
    if not pts:
        return float("nan"), float("nan")
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def to_frame(precincts: dict, stats: dict) -> pd.DataFrame:
    """One row per California precinct. Unlabelled precincts take the county of the
    nearest labelled one; anything outside California is dropped."""
    centres = {k: centroid(r["polygons"]) for k, r in precincts.items()}
    labelled = [(c, lon, lat) for (c, _), (lon, lat) in centres.items() if c]
    assigned: dict = {}
    for (county, pid), (lon, lat) in list(centres.items()):
        if county or not labelled:
            continue
        k = math.cos(math.radians(lat))
        near = min(labelled, key=lambda r: ((r[1] - lon) * k) ** 2 + (r[2] - lat) ** 2)[0]
        assigned[near] = assigned.get(near, 0) + 1
        rec, c = precincts.pop((county, pid)), centres.pop((county, pid))
        new = (near, pid)
        if new in precincts and precincts[new]["props"] == rec["props"]:
            precincts[new]["polygons"].extend(rec["polygons"])
            continue
        while new in precincts:
            new = (near, new[1] + "+")
        precincts[new], centres[new] = rec, c
    stats["assigned"] = assigned
    extra = sorted(k for k in stats["keys"] if k not in FIELDS and k not in ("county_name", "precinct_shapefile"))
    stats["extra_keys"] = extra
    rows = []
    for (county, pid), rec in precincts.items():
        if county not in CA_COUNTIES:
            continue
        lon, lat = centres[(county, pid)]
        p = rec["props"]
        rows.append({"county": county, "precinct": pid, "lon": round(lon, 5), "lat": round(lat, 5),
                     **{k: int(p.get(k) or 0) for k in FIELDS},
                     # anything else the tiles carry (margin_pct today) is kept as-is
                     **{k: p.get(k) for k in extra}})
    return pd.DataFrame(rows, columns=["county", "precinct", "lon", "lat"] + FIELDS + extra)


# ── Validation ───────────────────────────────────────────────────────────────
def compare(df: pd.DataFrame, feed: dict, counties=None) -> pd.DataFrame:
    """Per-county precinct sums vs Civiq's county feed."""
    g = df.groupby("county")[FIELDS].sum() if len(df) else pd.DataFrame(columns=FIELDS)
    rows = []
    for c in sorted(counties or feed):
        o = feed.get(c)
        reg = int(g.loc[c, "total_registered"]) if c in g.index else 0
        ret = int(g.loc[c, "total_returned"]) if c in g.index else 0
        f_reg = o["total_registered"] if o else None
        rows.append({"county": c, "precincts": int((df["county"] == c).sum()) if len(df) else 0,
                     "reg_precincts": reg, "reg_feed": f_reg, "ret_precincts": ret,
                     "ret_feed": o["total_returned"] if o else None,
                     "reg_diff": round((reg - f_reg) / f_reg, 4) if f_reg else None,
                     "ok": bool(o) and f_reg is not None and (f_reg == 0 and reg == 0 or
                                                               f_reg > 0 and abs(reg - f_reg) / f_reg <= MATCH_TOLERANCE),
                     **{f"gap_{k}": (o[k] - (int(g.loc[c, k]) if c in g.index else 0)) if o else None
                        for k in FIELDS if not k.startswith("total")}})
    return pd.DataFrame(rows)


def coverage(report: pd.DataFrame) -> float:
    """Share of registration (per Civiq's county feeds) that the precincts account for.
    A county's surplus doesn't make up for another's shortfall."""
    have = report[report["reg_feed"].notna()]
    tot = have["reg_feed"].sum()
    return float(have[["reg_precincts", "reg_feed"]].min(axis=1).sum() / tot) if tot else 0.0


def show(report: pd.DataFrame, only_bad: bool = False) -> None:
    cols = ["county", "precincts", "reg_precincts", "reg_feed", "reg_diff", "ret_precincts", "ret_feed", "ok"]
    r = report[~report["ok"]] if only_bad else report
    if len(r):
        print(r[cols].to_string(index=False))


# ── Statewide ────────────────────────────────────────────────────────────────
def statewide(src: Source, feed: dict, zooms) -> tuple:
    """Tiles hold only the precincts of the district named in the URL (the SD4
    address gives SD4's counties, and only SD4's part of a split county), so each
    county is fetched under its own "<NAME> COUNTY" address: a coarse pass to find
    where the county is, then finer zooms until its total matches Civiq's."""
    frames, precincts, zoom_used, keys = [], {}, {}, set()
    for c in CA_COUNTIES:
        slug = f"{c} COUNTY"
        stats: dict = {}
        probe = to_frame(decode_tiles(src, slug, tiles_for_bbox(CA_BBOX, PROBE_ZOOM), stats), stats)
        probe = probe[probe["county"] == c]
        if probe.empty:
            print(f"  {c.title()}: no precincts in its tiles", file=sys.stderr)
            continue
        bbox = (probe.lon.min() - 0.15, probe.lat.min() - 0.15, probe.lon.max() + 0.15, probe.lat.max() + 0.15)
        best = None
        for z in zooms:
            stats = {}
            p = decode_tiles(src, slug, tiles_for_bbox(bbox, z), stats)
            d = to_frame(p, stats)
            d = d[d["county"] == c]
            keys |= stats["keys"]
            r = compare(d, feed, [c]).iloc[0]
            gap = abs(r["reg_diff"]) if r["reg_diff"] is not None else -len(d)
            if best is None or gap < best[0]:
                best = (gap, z, d, p, r)
            if r["ok"]:
                break
        gap, z, d, p, r = best
        frames.append(d)
        precincts |= {k: v for k, v in p.items() if k[0] == c}
        zoom_used[c] = z
        print(f"  {c.title()}: zoom {z}, {len(d):,} precincts, {int(d['total_registered'].sum()):,} registered"
              + (f" ({r['reg_diff']:+.2%} vs Civiq)" if r["reg_diff"] is not None else " (no Civiq total)"))
    print(f"  tile fields: {', '.join(sorted(keys))}")
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["county", "precinct", "lon", "lat"] + FIELDS)
    rep = compare(df, feed, CA_COUNTIES)
    rep["zoom"] = rep["county"].map(zoom_used)
    z = max(zoom_used.values()) if zoom_used else zooms[-1]
    return coverage(rep), z, df, precincts, rep


# ── Districts ────────────────────────────────────────────────────────────────
def ensure_boundary(name: str, src: Source) -> Path | None:
    path = BOUNDARIES / DISTRICTS[name]["boundary"]
    if path.exists() or name != "ad9" or src.offline:
        return path if path.exists() else None
    import shapefile  # pyshp
    for url in TIGER_SLDL:
        try:
            z = zipfile.ZipFile(io.BytesIO(src.get(url)))
        except Exception as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            continue
        stem = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
        r = shapefile.Reader(shp=io.BytesIO(z.read(stem + ".shp")), dbf=io.BytesIO(z.read(stem + ".dbf")),
                             shx=io.BytesIO(z.read(stem + ".shx")))
        fields = [f[0] for f in r.fields[1:]]
        for sr in r.iterShapeRecords():
            rec = dict(zip(fields, sr.record))
            if str(rec.get("SLDLST")) == "009":
                geom = shape(sr.shape.__geo_interface__).simplify(0.0002, preserve_topology=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
                    "type": "Feature", "properties": {"district": "California Assembly District 9",
                                                      "source": url, "NAMELSAD": rec.get("NAMELSAD")},
                    "geometry": mapping(geom)}]}, separators=(",", ":")))
                print(f"  ✓ AD9 boundary from {url} → {path.relative_to(REPO)}")
                return path
    print("  ! no AD9 boundary: TIGER download failed", file=sys.stderr)
    return None


def select(df: pd.DataFrame, area, counties: set) -> pd.DataFrame:
    p = prep(area)
    m = df["county"].isin(counties)
    inside = [p.contains(Point(lon, lat)) for lon, lat in zip(df.loc[m, "lon"], df.loc[m, "lat"])]
    return df[m][inside].copy()


def district(name: str, src: Source, state_df: pd.DataFrame, state_precincts: dict, z: int) -> tuple | None:
    cfg = DISTRICTS[name]
    path = ensure_boundary(name, src)
    if not path:
        print(f"{name.upper()}: no boundary file, skipped", file=sys.stderr)
        return None
    area = unary_union([shape(f["geometry"]) for f in json.loads(path.read_text())["features"]])
    feed = feed_totals(src.county_feed(cfg["slug"]))
    total = sum(o["total_registered"] for o in feed.values())

    def score(d):
        rep = compare(d, feed, cfg["counties"])
        reg = int(d["total_registered"].sum())
        return (abs(reg - total) / total if total else float("inf")), rep

    df = select(state_df, area, cfg["counties"])
    diff, rep = score(df)
    used, precincts = "statewide tiles", state_precincts
    if diff > MATCH_TOLERANCE:
        # Try the district's own tiles in case statewide tiles carry other districts' figures
        minx, miny, maxx, maxy = area.bounds
        stats = {}
        alt_p = decode_tiles(src, cfg["slug"], tiles_for_bbox((minx - .02, miny - .02, maxx + .02, maxy + .02), z), stats)
        alt = select(to_frame(alt_p, stats), area, cfg["counties"])
        a_diff, a_rep = score(alt)
        if a_diff < diff:
            df, diff, rep, used, precincts = alt, a_diff, a_rep, f"{cfg['slug']} tiles", alt_p
    reg = int(df["total_registered"].sum())
    print(f"{name.upper()}: {len(df):,} precincts from {used}; registered {reg:,} vs Civiq {total:,} "
          f"({(reg - total) / total if total else 0:+.2%}); returned {int(df['total_returned'].sum()):,} "
          f"vs {sum(o['total_returned'] for o in feed.values()):,}")
    show(rep)
    return df, precincts, rep, diff, total


# ── Output ───────────────────────────────────────────────────────────────────
SIMPLIFY = 0.0001


def append_csv(path: Path, new: pd.DataFrame, key: str, stamp: str) -> int:
    if path.exists():
        old = pd.read_csv(path, dtype={"precinct": str})
        new = pd.concat([old[old[key] != stamp], new], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False)
    return len(new)


def write_geojson(path: Path, df: pd.DataFrame, precincts: dict) -> None:
    feats = []
    for r in df.itertuples():
        rec = precincts.get((r.county, r.precinct))
        if not rec:
            continue
        geom = unary_union([shape({"type": "Polygon", "coordinates": poly}).buffer(0) for poly in rec["polygons"]])
        geom = geom.simplify(SIMPLIFY, preserve_topology=True)
        feats.append({"type": "Feature", "id": f"{r.county.title()}|{r.precinct}",
                      "properties": {"county": r.county.title(), "precinct": r.precinct, "lon": r.lon, "lat": r.lat},
                      "geometry": json.loads(json.dumps(mapping(geom), default=float))})
    if path.exists():
        old = json.loads(path.read_text())
        if {f["id"] for f in old["features"]} == {f["id"] for f in feats}:
            print(f"  · {path.relative_to(REPO)} unchanged (same precinct set)")
            return
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print(f"  ✓ {len(feats)} shapes → {path.relative_to(REPO)}")


def tidy(df: pd.DataFrame, stamp: str) -> pd.DataFrame:
    out = df.assign(snapshot_utc=stamp, county=df["county"].str.title())
    return out[["snapshot_utc", "county", "precinct"] + [c for c in df.columns if c not in ("county", "precinct")]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--zoom", type=int, help="Use only this zoom level.")
    ap.add_argument("--offline", type=Path, help="Read tiles and feeds from this directory (testing).")
    ap.add_argument("--stamp", help="Snapshot label (default: now, UTC, as YYYY-MM-DDTHHMMZ).")
    args = ap.parse_args()
    src = Source(args.offline)
    stamp = args.stamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    t0 = time.time()

    # 1. County totals, statewide
    feed: dict = {}
    for c in CA_COUNTIES:
        feed.update({k: v for k, v in feed_totals(src.county_feed(f"{c} COUNTY")).items() if k == c})
    print(f"County feeds: {len(feed)}/58 counties, {sum(o['total_registered'] for o in feed.values()):,} registered, "
          f"{sum(o['total_returned'] for o in feed.values()):,} returned")
    if feed and not args.dry_run:
        rows = pd.DataFrame([{"snapshot_utc": stamp, "county": c.title(), **o} for c, o in sorted(feed.items())])
        append_csv(OUT / "state" / "county_totals.csv", rows, "snapshot_utc", stamp)

    # 2. Statewide precincts
    cov, z, df, precincts, rep = statewide(src, feed, [args.zoom] if args.zoom else ZOOMS)
    print(f"Statewide: {len(df):,} precincts, {int(df['total_registered'].sum()):,} registered, "
          f"{int(df['total_returned'].sum()):,} returned; coverage {cov:.2%}")
    show(rep, only_bad=True)
    ok_state = bool(cov >= MIN_COVERAGE and len(df))
    if not args.dry_run:
        # Always recorded, so a run that isn't saved can be diagnosed from the repo
        append_csv(OUT / "state" / "coverage.csv",
                   rep.assign(snapshot_utc=stamp, county=rep["county"].str.title(), saved=ok_state,
                              coverage=round(cov, 4)), "snapshot_utc", stamp)
    if not ok_state:
        print(f"ERROR: statewide coverage {cov:.1%} is below {MIN_COVERAGE:.0%}; statewide snapshot not written.",
              file=sys.stderr)
    elif not args.dry_run:
        p = OUT / "state" / f"precincts_{stamp}.csv.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", newline="") as f:
            tidy(df, stamp).sort_values(["county", "precinct"]).to_csv(f, index=False)
        print(f"  ✓ {len(df):,} precincts → {p.relative_to(REPO)} ({p.stat().st_size / 1e6:.1f} MB)")

    # 3. Districts
    failed = []
    for name in DISTRICTS:
        try:
            res = district(name, src, df, precincts, z)
        except Exception as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)
            res = None
        if res is None:
            failed.append(name)
            continue
        d, d_precincts, d_rep, diff, total = res
        if diff > MATCH_TOLERANCE:
            print(f"ERROR {name}: district total is {diff:.1%} off Civiq; not written.", file=sys.stderr)
            failed.append(name)
            continue
        if args.dry_run:
            continue
        n = append_csv(OUT / name / "precincts.csv", tidy(d.drop(columns=["lon", "lat"]), stamp), "snapshot_utc", stamp)
        append_csv(OUT / name / "coverage.csv",
                   d_rep.assign(snapshot_utc=stamp, zoom=z, county=d_rep["county"].str.title()), "snapshot_utc", stamp)
        print(f"  ✓ {len(d):,} precincts → data/civiq/{name}/precincts.csv ({n:,} rows)")
        write_geojson(OUT / name / "precincts.geojson", d, d_precincts)

    print(f"Done in {time.time() - t0:.0f}s, {src.requests} requests.")
    if not ok_state and len(failed) == len(DISTRICTS):
        sys.exit(1)


if __name__ == "__main__":
    main()

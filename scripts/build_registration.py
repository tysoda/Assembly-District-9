"""Cut statewide SWDB registration-by-SR-precinct files to AD9.

    python scripts/build_registration.py path/to/state_{e}_registration_by_{e}_srprec.csv [...]

Writes, for each election e:
  data/raw/c5_{e}_reg.csv    every precinct in the five AD9 counties (raw counts)
  data/raw/ad9_{e}_reg.csv   AD9 only. 2022+: precincts in ad9_{e}_sov.csv (ADDIST == 9).
                             Before 2022: counts x AD9_SHARE from ad9_{e}_precinct_share.csv
                             (run scripts/rebuild_old_lines.py first).
"""
import re
import sys
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
AD9_FIPS = {"06005", "06009", "06067", "06077", "06099"}
KEYS = {"COUNTY", "FIPS", "SRPREC", "SRPREC_KEY", "ELECTION", "GEO_TYPE", "TYPE"}


def build(path: Path) -> pd.DataFrame:
    e = re.search(r"_([pg]\d\d)_", path.name).group(1)
    d = pd.read_csv(path, dtype={"FIPS": str, "SRPREC": str, "SRPREC_KEY": str}, low_memory=False)
    d["FIPS"] = d["FIPS"].str.zfill(5)
    d = d[d["FIPS"].isin(AD9_FIPS)].copy()
    d.to_csv(RAW / f"c5_{e}_reg.csv", index=False)

    if int(e[1:]) >= 22:
        keys = set(pd.read_csv(RAW / f"ad9_{e}_sov.csv", dtype={"SRPREC_KEY": str})["SRPREC_KEY"])
        out = d[d["SRPREC_KEY"].isin(keys)].copy()
        missing = len(keys - set(out["SRPREC_KEY"]))
        if missing:
            print(f"  {e}: {missing} AD9 results precincts have no registration row")
    else:
        share = pd.read_csv(RAW / f"ad9_{e}_precinct_share.csv", dtype={"SRPREC_KEY": str})
        out = d.merge(share, on="SRPREC_KEY", how="inner")
        out = out[out["AD9_SHARE"] > 0].copy()
        num = [c for c in out.columns if c not in KEYS and c != "AD9_SHARE"
               and pd.api.types.is_numeric_dtype(out[c])]
        out[num] = out[num].mul(out["AD9_SHARE"], axis=0)
    out.to_csv(RAW / f"ad9_{e}_reg.csv", index=False)
    return e, out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        e, o = build(Path(p))
        t = o["TOTREG_R"].sum()
        print(f"ad9_{e}_reg.csv: {len(o)} precincts, {t:,.0f} registered | "
              f"Dem {o.DEM.sum() / t:.1%}  Rep {o.REP.sum() / t:.1%}  NPP {o.DCL.sum() / t:.1%}  "
              f"other {(t - o.DEM.sum() - o.REP.sum() - o.DCL.sum()) / t:.1%}  | "
              f"Hispanic-surname {(o.HISPDEM + o.HISPREP + o.HISPDCL + o.HISPOTH).sum() / t:.1%}")

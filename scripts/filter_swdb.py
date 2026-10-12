"""Cut statewide SWDB statement-of-vote files (by SR precinct) down to AD9.

    python scripts/filter_swdb.py path/to/state_g24_sov_data_by_g24_srprec.csv [...]

2022 and later (current district lines): writes data/raw/ad9_{e}_sov.csv, rows with ADDIST == 9.
Before 2022 (old lines): writes data/raw/c5_{e}_sov.csv, every precinct in the five AD9 counties,
ready to be moved onto current lines with that election's precinct-to-block conversion file.

SWDB masks contest votes as '***' in very small precincts; these become blank.
Contest columns that are zero throughout the output are dropped.
"""
import re
import sys
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
AD9_FIPS = {"06005", "06009", "06067", "06077", "06099"}   # Amador, Calaveras, Sacramento, San Joaquin, Stanislaus
KEYS = {"COUNTY", "FIPS", "SRPREC", "SRPREC_KEY", "ELECTION", "GEO_TYPE",
        "ADDIST", "CDDIST", "SDDIST", "BEDIST"}
FIRST_CONTEST = "PRCVOTE"   # columns up to and including ABSVOTE are registration / turnout


def election_code(path: Path) -> str:
    m = re.search(r"_([pg]\d\d)_", path.name)
    if not m:
        raise ValueError(f"No election code (e.g. g24) in {path.name}")
    return m.group(1)


def filter_file(path: Path) -> tuple[str, pd.DataFrame]:
    e = election_code(path)
    d = pd.read_csv(path, dtype=str, low_memory=False)
    d["FIPS"] = d["FIPS"].str.strip().str.zfill(5).where(d["FIPS"].str.len() >= 4,
                                                          "06" + d["FIPS"].str.strip().str.zfill(3))
    d = d[d["FIPS"].isin(AD9_FIPS)].copy()
    for c in d.columns:
        if c not in KEYS:
            d[c] = pd.to_numeric(d[c].str.replace("*", "", regex=False), errors="coerce")
    d["ADDIST"] = pd.to_numeric(d["ADDIST"], errors="coerce")

    current_lines = int(e[1:]) >= 22
    out = d[d["ADDIST"] == 9] if current_lines else d
    cols = list(out.columns)
    cut = cols.index(FIRST_CONTEST) if FIRST_CONTEST in cols else 0
    keep = cols[:cut] + [c for c in cols[cut:] if out[c].fillna(0).ne(0).any()]
    out = out[keep]

    name = f"ad9_{e}_sov.csv" if current_lines else f"c5_{e}_sov.csv"
    out.to_csv(RAW / name, index=False)
    return name, out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        name, df = filter_file(Path(p))
        reg, vote = df["TOTREG"].sum(), df["TOTVOTE"].sum()
        print(f"{name}: {len(df)} precincts, {reg:,.0f} registered, {vote:,.0f} ballots ({vote / reg:.1%})")

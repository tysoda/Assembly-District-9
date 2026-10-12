"""Statewide Democratic and Republican totals for each partisan statewide contest.

    python scripts/state_totals.py path/to/state_{e}_sov_data_by_{e}_srprec.csv [...]

Writes data/raw/state_contest_totals.csv with one row per election x contest
(PRS president, GOV governor, USS US Senate): dem, rep (all candidates of each party summed)
and the number of candidates of each party. Contests without both a Democrat and a Republican
(e.g. the 2016 and 2018 Senate generals) are kept but have dem or rep = 0.
"""
import re
import sys
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "state_contest_totals.csv"
CONTESTS = ("PRS", "GOV", "USS")


def totals(path: Path) -> list[dict]:
    e = re.search(r"_([pg]\d\d)_", path.name).group(1)
    d = pd.read_csv(path, dtype=str, low_memory=False)
    rows = []
    for c in CONTESTS:
        dem = [x for x in d.columns if x.startswith(c + "DEM")]
        rep = [x for x in d.columns if x.startswith(c + "REP")]
        if not dem and not rep:
            continue
        num = lambda cols: sum(pd.to_numeric(d[x].str.replace("*", "", regex=False),
                                             errors="coerce").sum() for x in cols)
        rows.append({"election": e, "contest": c, "dem": num(dem), "rep": num(rep),
                     "n_dem": len(dem), "n_rep": len(rep)})
    return rows


if __name__ == "__main__":
    new = pd.DataFrame([r for p in sys.argv[1:] for r in totals(Path(p))])
    if OUT.exists():
        old = pd.read_csv(OUT)
        new = pd.concat([old[~old.election.isin(new.election)], new])
    order = lambda s: s.str[1:].astype(int) * 10 + (s.str[0] == "g")
    new = new.sort_values(["election", "contest"], key=lambda s: order(s) if s.name == "election" else s)
    new.to_csv(OUT, index=False)
    new["d2p"] = new.dem / (new.dem + new.rep)
    print(new.to_string(index=False))

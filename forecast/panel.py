"""Block-group panel: every election's results and registration on fixed 2020 census block groups.

    python -m forecast.panel            (from the repo root)

Precinct boundaries change every election, so each election is moved onto 2020 block groups
(the first 12 digits of a 2020 block GEOID), which stay fixed. For each election a weight table
gives, for every SR precinct, the share of its registered voters estimated to live in each
block group *and* inside the district:

  2020-block elections (p22 onward):  precinct -> 2020 block   (that election's block map, BLKREG)
  2010-block elections (p12 to g20):  precinct -> 2010 block   (that election's block map, BLKREG)
                                      2010 block -> 2020 blocks (Census relationship file; pieces weighted by
                                                                 p24 registrants x land-area share, as in
                                                                 scripts/rebuild_old_lines.py)
  every 2020 block x its in-district share (p24 precinct districts; see block20_share)

Outputs (data/processed/):
  bg_panel.csv        block group x election: registered, ballots, party registration, Democratic and
                      Republican votes in the partisan statewide contests, Assembly contest votes
  district_series.csv election totals for the district, with the statewide totals and the lean
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
CONTESTS = ("PRS", "GOV", "USS")
REG_COLS = ["TOTREG_R", "DEM", "REP", "DCL", "AIP", "HISPDEM", "HISPREP", "HISPDCL", "HISPOTH"]


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text())


def elections(cfg: dict) -> list[str]:
    return cfg["data"]["elections"]


def is_2020_blocks(e: str) -> bool:
    return int(e[1:]) >= 22


# ── geography ────────────────────────────────────────────────────────────────
def block20_share(cfg: dict) -> pd.Series:
    """Share of each 2020 block's p24 registrants who sit in in-district p24 precincts."""
    m = pd.read_csv(RAW / "c5_p24_sr_blk_map.csv", dtype=str)
    m["BLKREG"] = pd.to_numeric(m.BLKREG)
    inside = set(pd.read_csv(RAW / "ad9_p24_sov.csv", dtype=str).SRPREC_KEY)
    m["inside"] = m.SRPREC_KEY.isin(inside)
    g = m.groupby("BLOCK_KEY")
    reg = g.BLKREG.sum()
    share = (m.BLKREG * m.inside).groupby(m.BLOCK_KEY).sum() / reg
    share = share.where(reg > 0, g.inside.mean())        # unpopulated blocks: precinct membership
    return share.rename("share20")


def block10_to_20(share20: pd.Series) -> pd.DataFrame:
    """2010 block -> 2020 block pieces with weights summing to 1 per 2010 block."""
    m = pd.read_csv(RAW / "c5_p24_sr_blk_map.csv", dtype=str)
    reg20 = pd.to_numeric(m.BLKREG).groupby(m.BLOCK_KEY).sum().rename("reg20")
    r = pd.read_csv(RAW / "c5_tab2010_tab2020.csv", dtype={"BLOCK10": str, "BLOCK20": str})
    r = r.join(reg20, on="BLOCK20")
    r["reg20"] = r.reg20.fillna(0.0)
    frac = (r.AREALAND_INT / r.AREALAND_2020.where(r.AREALAND_2020 > 0)).fillna(1.0)
    r["w_reg"] = r.reg20 * frac
    r["w_area"] = r.AREALAND_INT + r.AREAWATER_INT
    tot_reg = r.groupby("BLOCK10").w_reg.transform("sum")
    tot_area = r.groupby("BLOCK10").w_area.transform("sum")
    r["w"] = np.where(tot_reg > 0, r.w_reg / tot_reg.where(tot_reg > 0, 1),
                      r.w_area / tot_area.where(tot_area > 0, 1))
    return r[["BLOCK10", "BLOCK20", "w"]]


def precinct_bg_weights(e: str, share20: pd.Series, x10: pd.DataFrame) -> pd.DataFrame:
    """SRPREC_KEY, BG, w: share of the precinct's registrants in that block group and in the district."""
    m = pd.read_csv(RAW / f"c5_{e}_sr_blk_map.csv", dtype=str)
    m["BLKREG"] = pd.to_numeric(m.BLKREG)
    tot = m.groupby("SRPREC_KEY").BLKREG.transform("sum")
    n = m.groupby("SRPREC_KEY").BLKREG.transform("size")
    m["p"] = np.where(tot > 0, m.BLKREG / tot.where(tot > 0, 1), 1.0 / n)   # share of precinct in block
    if is_2020_blocks(e):
        b = m[["SRPREC_KEY", "BLOCK_KEY", "p"]].rename(columns={"BLOCK_KEY": "BLOCK20"})
    else:
        b = m[["SRPREC_KEY", "BLOCK_KEY", "p"]].merge(x10, left_on="BLOCK_KEY", right_on="BLOCK10")
        b["p"] = b.p * b.w
    b = b.join(share20, on="BLOCK20")
    b["w"] = b.p * b.share20.fillna(0.0)
    b["BG"] = b.BLOCK20.str[:12]
    w = b.groupby(["SRPREC_KEY", "BG"], as_index=False).w.sum()
    return w[w.w > 1e-9]


# ── per-election values ──────────────────────────────────────────────────────
def _results(e: str) -> pd.DataFrame:
    """Unscaled precinct results: 2022+ district precincts, earlier all five-county precincts."""
    f = RAW / (f"ad9_{e}_sov.csv" if is_2020_blocks(e) else f"c5_{e}_sov.csv")
    d = pd.read_csv(f, dtype={"SRPREC_KEY": str}, low_memory=False)
    out = pd.DataFrame({"SRPREC_KEY": d.SRPREC_KEY, "reg_sov": d.TOTREG, "ballots": d.TOTVOTE})
    for c in CONTESTS:
        dem = [x for x in d.columns if x.startswith(c + "DEM")]
        rep = [x for x in d.columns if x.startswith(c + "REP")]
        out[f"{c}_dem"] = d[dem].sum(axis=1) if dem else 0.0
        out[f"{c}_rep"] = d[rep].sum(axis=1) if rep else 0.0
    for party in ("DEM", "REP", "AIP"):
        cols = [x for x in d.columns if x.startswith("ASS" + party)]
        out[f"ASS_{party.lower()}"] = d[cols].sum(axis=1) if cols else 0.0
    return out


def _registration(e: str) -> pd.DataFrame:
    f = RAW / (f"ad9_{e}_reg.csv" if is_2020_blocks(e) else f"c5_{e}_reg.csv")
    d = pd.read_csv(f, dtype={"SRPREC_KEY": str}, low_memory=False)
    out = d[["SRPREC_KEY"] + REG_COLS].copy()
    out["HISP"] = out[["HISPDEM", "HISPREP", "HISPDCL", "HISPOTH"]].sum(axis=1)
    return out.drop(columns=["HISPDEM", "HISPREP", "HISPDCL", "HISPOTH"])


def build(cfg: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = cfg or load_config()
    share20 = block20_share(cfg)
    x10 = block10_to_20(share20)
    state = pd.read_csv(RAW / "state_contest_totals.csv")
    panels, series = [], []
    for e in elections(cfg):
        w = precinct_bg_weights(e, share20, x10)
        vals = _results(e).merge(_registration(e), on="SRPREC_KEY", how="outer").fillna(0.0)
        num = [c for c in vals.columns if c != "SRPREC_KEY"]
        bg = (w.merge(vals, on="SRPREC_KEY")
               .assign(**{c: lambda d, c=c: d[c] * d.w for c in num})
               .groupby("BG")[num].sum().reset_index())
        bg.insert(1, "election", e)
        panels.append(bg)

        row = {"election": e, "year": 2000 + int(e[1:]), "general": int(e[0] == "g"),
               "presidential": int(int(e[1:]) % 4 == 0)}
        tot = bg[num].sum()
        row.update(registered=tot.reg_sov, ballots=tot.ballots, turnout=tot.ballots / tot.reg_sov,
                   reg_dem=tot.DEM / tot.TOTREG_R, reg_rep=tot.REP / tot.TOTREG_R,
                   reg_npp=tot.DCL / tot.TOTREG_R, reg_hisp=tot.HISP / tot.TOTREG_R)
        leans = []
        for c in CONTESTS:
            st = state[(state.election == e) & (state.contest == c)]
            if st.empty or st.dem.iloc[0] == 0 or st.rep.iloc[0] == 0:
                continue                                       # no D-vs-R contest
            d2p = tot[f"{c}_dem"] / (tot[f"{c}_dem"] + tot[f"{c}_rep"])
            s2p = st.dem.iloc[0] / (st.dem.iloc[0] + st.rep.iloc[0])
            row[f"{c}_d2p"], row[f"{c}_state_d2p"] = d2p, s2p
            leans.append(d2p - s2p)
        row["lean"] = float(np.mean(leans)) if leans else np.nan
        row["n_contests"] = len(leans)
        series.append(row)

    panel = pd.concat(panels, ignore_index=True)
    series = pd.DataFrame(series)
    OUT.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT / "bg_panel.csv", index=False)
    series.to_csv(OUT / "district_series.csv", index=False)
    return panel, series


if __name__ == "__main__":
    p, s = build()
    print(f"bg_panel.csv: {p.BG.nunique()} block groups x {p.election.nunique()} elections")
    cols = ["election", "registered", "ballots", "turnout", "reg_dem", "reg_rep", "reg_hisp", "lean", "n_contests"]
    print(s[cols].to_string(index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 10 else f"{x:,.0f}"))

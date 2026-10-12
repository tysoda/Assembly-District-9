"""Put pre-2022 elections (2010 census blocks, old district lines) onto the current AD9.

    python scripts/rebuild_old_lines.py p16 g16 p18 g18 p20 g20

Chain, all weighted by registered voters:

  2020 block -> AD9 share   from the p24 block map (c5_p24_sr_blk_map.csv) and p24 precinct districts
                            (state p24 results, ADDIST): share of the block's p24 registrants in AD9 precincts
  2010 block -> AD9 share   via the Census 2010-2020 block relationship file. Each 2010 block is split into
                            the 2020 blocks it overlaps; each piece is weighted by the 2020 block's p24
                            registrants x the piece's share of that block's land area. Falls back to land
                            area where none of the pieces has registrants.
  old precinct -> AD9 share from that election's block map (c5_{e}_sr_blk_map.csv, BLKREG)

Every count column in c5_{e}_sov.csv is then multiplied by the precinct's AD9 share and the result is
written to data/raw/ad9_{e}_sov.csv (precincts with share > 0 only, AD9_SHARE column added).
Also writes data/raw/ad9_{e}_precinct_share.csv.
"""
import sys
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
KEYS = {"COUNTY", "FIPS", "SRPREC", "SRPREC_KEY", "ELECTION", "GEO_TYPE", "TYPE",
        "ADDIST", "CDDIST", "SDDIST", "BEDIST"}


def block20_share() -> pd.Series:
    m = pd.read_csv(RAW / "c5_p24_sr_blk_map.csv", dtype=str)
    m["BLKREG"] = pd.to_numeric(m["BLKREG"])
    ad9 = set(pd.read_csv(RAW / "ad9_p24_sov.csv", dtype=str)["SRPREC_KEY"])
    m["in9"] = m["SRPREC_KEY"].isin(ad9)
    g = m.groupby("BLOCK_KEY").apply(
        lambda d: (d.BLKREG * d.in9).sum() / d.BLKREG.sum() if d.BLKREG.sum() > 0 else d.in9.mean(),
        include_groups=False)
    reg = m.groupby("BLOCK_KEY").BLKREG.sum()
    return g.rename("share20"), reg.rename("reg20")


def block10_share(share20: pd.Series, reg20: pd.Series) -> pd.Series:
    r = pd.read_csv(RAW / "c5_tab2010_tab2020.csv", dtype={"BLOCK10": str, "BLOCK20": str})
    r = r.join(share20, on="BLOCK20").join(reg20, on="BLOCK20")
    r["share20"] = r["share20"].fillna(0.0)   # 2020 blocks outside the p24 map: no voters, not AD9 precincts
    r["reg20"] = r["reg20"].fillna(0.0)
    frac = (r.AREALAND_INT / r.AREALAND_2020.where(r.AREALAND_2020 > 0)).fillna(1.0)
    r["w_reg"] = r.reg20 * frac
    r["w_area"] = r.AREALAND_INT + r.AREAWATER_INT

    def one(d):
        w = d.w_reg if d.w_reg.sum() > 0 else d.w_area
        return (w * d.share20).sum() / w.sum() if w.sum() > 0 else d.share20.mean()
    return r.groupby("BLOCK10").apply(one, include_groups=False).rename("share10")


def precinct_share(e: str, share10: pd.Series) -> pd.DataFrame:
    m = pd.read_csv(RAW / f"c5_{e}_sr_blk_map.csv", dtype=str)
    m["BLKREG"] = pd.to_numeric(m["BLKREG"])
    m = m.join(share10, on="BLOCK_KEY")
    missing = m.share10.isna()
    if missing.any():
        print(f"  {e}: {missing.sum()} block rows ({m.BLKREG[missing].sum():,.0f} voters) not in the "
              f"relationship file, treated as outside AD9")
    m["share10"] = m.share10.fillna(0.0)
    p = m.assign(r9=m.BLKREG * m.share10).groupby("SRPREC_KEY").agg(reg=("BLKREG", "sum"), r9=("r9", "sum"),
                                                                     n=("share10", "size"),
                                                                     s=("share10", "mean"))
    p["AD9_SHARE"] = (p.r9 / p.reg).where(p.reg > 0, p.s)
    return p[["AD9_SHARE"]].reset_index()


def rebuild(e: str, share10: pd.Series) -> pd.DataFrame:
    ps = precinct_share(e, share10)
    ps.to_csv(RAW / f"ad9_{e}_precinct_share.csv", index=False)
    sov = pd.read_csv(RAW / f"c5_{e}_sov.csv", dtype={"SRPREC_KEY": str, "SRPREC": str, "FIPS": str})
    sov = sov.merge(ps, on="SRPREC_KEY", how="left")
    unmatched = sov.AD9_SHARE.isna()
    if unmatched.any():
        print(f"  {e}: {unmatched.sum()} precincts ({sov.TOTREG[unmatched].sum():,.0f} registered) have no "
              f"block-map entry, treated as outside AD9")
    sov["AD9_SHARE"] = sov.AD9_SHARE.fillna(0.0)
    out = sov[sov.AD9_SHARE > 0].copy()
    num = [c for c in out.columns if c not in KEYS and c != "AD9_SHARE"
           and pd.api.types.is_numeric_dtype(out[c])]
    out[num] = out[num].mul(out.AD9_SHARE, axis=0)
    out.to_csv(RAW / f"ad9_{e}_sov.csv", index=False)
    return out


if __name__ == "__main__":
    s20, r20 = block20_share()
    s10 = block10_share(s20, r20)
    print(f"2010 blocks with any AD9 share: {(s10 > 0).sum():,}; split (0 < share < 1): "
          f"{((s10 > 0) & (s10 < 1)).sum():,}")
    for e in sys.argv[1:] or ["p16", "g16", "p18", "g18", "p20", "g20"]:
        out = rebuild(e, s10)
        reg, vote = out.TOTREG.sum(), out.TOTVOTE.sum()
        part = ((out.AD9_SHARE > 0.02) & (out.AD9_SHARE < 0.98)).sum()
        print(f"ad9_{e}_sov.csv: {len(out)} precincts ({part} partly in AD9), "
              f"{reg:,.0f} registered, {vote:,.0f} ballots ({vote / reg:.1%})")

"""AD9 forecast components, estimated from the block-group panel (forecast/panel.py).

    state environment   statewide Democratic two-party share. SD4's regression features, refit on two-party
                        shares from SWDB statewide totals (SD4's own target mixes total-vote shares, which
                        understates Democrats by ~2 pts in presidential years). Spec chosen in config.
    district lean       AD9 two-party share minus the state's, per election (mean over the partisan
                        statewide contests). Projected by weighted mean, district linear trend, or
                        block-group linear trends weighted by projected block-group ballots.
    turnout             block-group panel OLS: block-group fixed effects + presidential + general + the
                        immediately preceding election's turnout (SD4's county spec, at block-group level).
    registration        linear trend through November registration.

Every uncertainty is a walk-forward (expanding-window) error, never an in-sample residual.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROC, RAW = ROOT / "data" / "processed", ROOT / "data" / "raw"
CONTESTS = ("PRS", "GOV", "USS")
# (code, year, month, presidential, general) for elections after the panel's last one
SCHEDULE = [("p26", 2026, 6, 0, 0), ("g26", 2026, 11, 0, 1), ("p28", 2028, 3, 1, 0), ("g28", 2028, 11, 1, 1)]


def order_key(e: str) -> int:
    return int(e[1:]) * 10 + (e[0] == "g")


def _ols(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    if w is not None:
        sw = np.sqrt(w)
        X, y = X * sw[:, None], y * sw
    return np.linalg.lstsq(X, y, rcond=None)[0]


# ── state environment ────────────────────────────────────────────────────────
def state_env_table(sd4_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(sd4_dir / "state_environment_history.csv")
    df["yr"] = df.election.str.extract(r"(\d{4})").astype(int)
    df["general"] = df.election.str.contains("General").astype(int)
    st = pd.read_csv(RAW / "state_contest_totals.csv")
    st = st[(st.dem > 0) & (st.rep > 0)].assign(d2p=lambda d: d.dem / (d.dem + d.rep))
    office = {"US Sen": "USS", "Presid": "PRS", "Govern": "GOV"}

    def two_party(r):
        o = next(v for k, v in office.items() if k in r.election)
        m = st[(st.election == f"{'g' if r.general else 'p'}{r.yr % 100:02d}") & (st.contest == o)]
        return m.d2p.iloc[0] if len(m) else np.nan
    df["d2p"] = df.apply(two_party, axis=1)
    df["target"] = df.d2p.fillna(df.dem_share)      # rows without SWDB totals (2026 primary) keep SD4's value
    # president's party during the campaign: +1 Democratic, -1 Republican
    df["dem_pres"] = df.yr.map(lambda y: 1 if y in (2012, 2014, 2016, 2022, 2024) else -1)
    df["signed_approval"] = (df.approval - 0.5) * df.dem_pres   # > 0 helps Democrats
    return df


SPECS = {
    "sd4": ["presidential", "inflation", "approval"],      # SD4 as-is: approval not signed by party
    "signed": ["presidential", "signed_approval"],         # approval signed by the president's party
    "mean": ["presidential"],
}


def state_environment(sd4_dir: Path, spec: str) -> dict:
    df = state_env_table(sd4_dir)
    out = {}
    for name, feats in SPECS.items():
        errs, errs20 = [], []
        for yr in sorted(df.loc[df.yr >= 2016, "yr"].unique()):
            tr, te = df[df.yr < yr], df[df.yr == yr]
            c = _ols(np.column_stack([np.ones(len(tr)), tr[feats]]), tr.target.values)
            e = list(te.target.values - (c[0] + te[feats].values @ c[1:]))
            errs.extend(e)
            if yr >= 2020:          # training includes a Republican-president year
                errs20.extend(e)
        c = _ols(np.column_stack([np.ones(len(df)), df[feats]]), df.target.values)
        out[name] = {"features": feats, "coefficients": dict(zip(["intercept"] + feats, c.tolist())),
                     "walk_forward_sd": float(np.std(errs, ddof=1)),
                     "walk_forward_mae": float(np.mean(np.abs(errs))),
                     "walk_forward_mae_from_2020": float(np.mean(np.abs(errs20))),
                     "walk_forward_sd_from_2020": float(np.std(errs20, ddof=1)), "n_obs": len(df)}
    out["chosen"] = spec
    return out


def predict_state(env: dict, presidential: int, inflation, approval, dem_pres: int = -1) -> np.ndarray:
    s = env[env["chosen"]]
    c = s["coefficients"]
    approval = np.asarray(approval)
    x = {"presidential": presidential, "inflation": np.asarray(inflation), "approval": approval,
         "signed_approval": (approval - 0.5) * dem_pres}
    return c["intercept"] + sum(c[f] * x[f] for f in s["features"])


# ── district lean ────────────────────────────────────────────────────────────
def bg_leans(panel: pd.DataFrame) -> pd.DataFrame:
    """Block group x election lean (mean over contests of block-group two-party share minus state's)."""
    st = pd.read_csv(RAW / "state_contest_totals.csv")
    st = st[(st.dem > 0) & (st.rep > 0)]
    st = st.assign(s2p=st.dem / (st.dem + st.rep)).set_index(["election", "contest"]).s2p
    parts = []
    for c in CONTESTS:
        d, r = panel[f"{c}_dem"], panel[f"{c}_rep"]
        s2p = [st.get((e, c), np.nan) for e in panel.election]
        parts.append(pd.Series(np.where(d + r > 0, d / (d + r).where(d + r > 0, 1), np.nan) - s2p,
                               index=panel.index))
    lean = pd.concat(parts, axis=1).mean(axis=1)
    votes = panel[[f"{c}_{p}" for c in CONTESTS for p in ("dem", "rep")]].sum(axis=1)
    return panel[["BG", "election"]].assign(lean=lean, votes=votes, reg=panel.reg_sov,
                                            ballots=panel.ballots)


def _trend(x, y, at):
    if len(x) < 2:
        return float(np.mean(y))
    b = np.polyfit(x, y, 1)
    return float(np.polyval(b, at))


def project_lean(series: pd.DataFrame, leans: pd.DataFrame, kind: str, target_year: float,
                 method: str, weights_bg: pd.Series | None = None, history: list[str] | None = None) -> float:
    """kind 'g' (generals) or 'p' (primaries); history limits to elections seen so far."""
    s = series[series.election.str[0].eq(kind) & series.lean.notna()]
    if history is not None:
        s = s[s.election.isin(history)]
    if method == "weighted":                     # mean of the last three
        return float(s.lean.tail(3).mean())
    if method == "linear":
        return _trend(s.year.values, s.lean.values, target_year)
    if method == "bg_linear":
        l = leans[leans.election.isin(s.election)].merge(s[["election", "year"]], on="election")
        l = l[l.lean.notna() & (l.votes >= 25)]
        proj = l.groupby("BG").apply(lambda d: _trend(d.year.values, d.lean.values, target_year)
                                     if d.year.nunique() >= 3 else np.nan, include_groups=False)
        w = weights_bg if weights_bg is not None else l[l.election == s.election.iloc[-1]].set_index("BG").votes
        w = w.reindex(proj.index).fillna(0)
        ok = proj.notna() & (w > 0)
        return float((proj[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan
    raise ValueError(method)


def lean_validation(series: pd.DataFrame, leans: pd.DataFrame, kind: str) -> dict:
    """Walk-forward: project each election from 2016 on from the ones before it, by every method."""
    s = series[series.election.str[0].eq(kind) & series.lean.notna()]
    out = {}
    for m in ("weighted", "linear", "bg_linear"):
        errs = []
        for i, row in s.iterrows():
            hist = s[s.year < row.year].election.tolist()
            if row.year < 2016 or len(hist) < 2:
                continue
            w = leans[leans.election == row.election].set_index("BG").votes   # actual turnout pattern
            pred = project_lean(series, leans, kind, row.year, m, weights_bg=w, history=hist)
            if np.isfinite(pred):                # bg_linear needs three earlier elections
                errs.append(row.lean - pred)
        out[m] = {"errors": [round(e, 4) for e in errs], "mae": float(np.mean(np.abs(errs))),
                  "sd": float(np.sqrt(np.mean(np.square(errs))))}
    return out


# ── turnout ──────────────────────────────────────────────────────────────────
def turnout_frame(panel: pd.DataFrame, series: pd.DataFrame, min_reg: float) -> pd.DataFrame:
    p = panel[["BG", "election", "reg_sov", "ballots"]].copy()
    keep = p.groupby("BG").reg_sov.min() >= min_reg
    p = p[p.BG.isin(keep[keep].index)]
    p["turnout"] = (p.ballots / p.reg_sov).clip(0, 1)
    p = p.merge(series[["election", "presidential", "general"]], on="election")
    p["ord"] = p.election.map(order_key)
    p = p.sort_values(["BG", "ord"])
    p["prev"] = p.groupby("BG").turnout.shift(1)
    return p


TURNOUT_FEATURES = {"sd4": ["presidential", "general", "prev"],
                    "interaction": ["presidential", "general", "prev", "pres_x_gen"]}


def _turnout_design(d: pd.DataFrame, bgs: list[str], spec: str = "sd4") -> np.ndarray:
    d = d.assign(pres_x_gen=d.presidential * d.general)
    dummies = pd.get_dummies(pd.Categorical(d.BG, categories=bgs), drop_first=True, dtype=float).values
    return np.column_stack([np.ones(len(d)), d[TURNOUT_FEATURES[spec]].values, dummies])


def turnout_model(panel: pd.DataFrame, series: pd.DataFrame, min_reg: float = 50, spec: str = "sd4",
                  wf_start: str = "p16") -> dict:
    t = turnout_frame(panel, series, min_reg)
    fit_rows = t[t.prev.notna()]
    bgs = sorted(t.BG.unique())
    coef = _ols(_turnout_design(fit_rows, bgs, spec), fit_rows.turnout.values, fit_rows.reg_sov.values)

    # walk-forward, district level (registration-weighted aggregate of block-group predictions)
    errs, rows = [], []
    for e in sorted(t.election.unique(), key=order_key):
        if order_key(e) < order_key(wf_start):
            continue
        tr = fit_rows[fit_rows.ord < order_key(e)]
        te = t[(t.election == e) & t.prev.notna()]
        c = _ols(_turnout_design(tr, bgs, spec), tr.turnout.values, tr.reg_sov.values)
        pred = np.clip(_turnout_design(te, bgs, spec) @ c, 0.02, 0.98)
        dp = float((pred * te.reg_sov).sum() / te.reg_sov.sum())
        da = float(te.ballots.sum() / te.reg_sov.sum())
        errs.append(da - dp)
        rows.append({"election": e, "actual": round(da, 4), "predicted": round(dp, 4)})
    return {"coef": coef, "bgs": bgs, "frame": t, "lag_coef": float(coef[3]), "spec": spec,
            "walk_forward": rows, "district_sd": float(np.sqrt(np.mean(np.square(errs)))),
            "district_mae": float(np.mean(np.abs(errs)))}


def project_turnout(tm: dict, upto: str, anchors: dict | None = None) -> dict:
    """Chain block-group turnout from the last observed election through SCHEDULE to `upto`.

    anchors: {election: (district turnout, sd)} for elections already held but not yet in the panel
    (e.g. the June 2026 primary). Block-group predictions are scaled to match, and the chain's
    uncertainty restarts from that anchor's sd.
    """
    t, coef, bgs, spec = tm["frame"], tm["coef"], tm["bgs"], tm["spec"]
    anchors = anchors or {}
    anchor_sd = None
    last_e = max(t.election.unique(), key=order_key)
    cur = t[t.election == last_e][["BG", "turnout", "reg_sov"]].rename(columns={"turnout": "prev"})
    steps, path = 0, []
    for code, yr, mon, pres, gen in SCHEDULE:
        d = cur.assign(presidential=pres, general=gen)
        pred = np.clip(_turnout_design(d, bgs, spec) @ coef, 0.02, 0.98)
        if code in anchors:
            target, anchor_sd = anchors[code]
            pred = np.clip(pred * target / float((pred * d.reg_sov).sum() / d.reg_sov.sum()), 0.02, 0.98)
            steps = 0
        else:
            steps += 1
        cur = cur.assign(prev=pred)
        path.append((code, float((pred * d.reg_sov).sum() / d.reg_sov.sum())))
        if code == upto:
            break
    chain = tm["district_sd"] ** 2 * sum(tm["lag_coef"] ** (2 * k) for k in range(steps))
    if anchor_sd is not None:
        chain += (anchor_sd * tm["lag_coef"] ** steps) ** 2
    sd = float(np.sqrt(chain))
    return {"bg_turnout": cur.set_index("BG").prev, "reg_weights": cur.set_index("BG").reg_sov,
            "district": path[-1][1], "sd": sd, "chain": path}


def registration_projection(series: pd.DataFrame, year: float) -> float:
    g = series[series.general == 1]
    b = np.polyfit(g.year, g.registered, 1)
    return float(np.polyval(b, year))


# ── incumbent edge ───────────────────────────────────────────────────────────
def assembly_edges(cfg: dict) -> list[dict]:
    """Assembly Republican two-party share minus top-of-ticket Republican share, in the listed races."""
    out = []
    for r in cfg["incumbent"]["races"]:
        d = pd.read_csv(RAW / r["file"], low_memory=False)
        d = d[d.ADDIST == r["district"]]
        top = r["top"]
        td = d[[c for c in d.columns if c.startswith(top + "DEM")]].sum().sum()
        trr = d[[c for c in d.columns if c.startswith(top + "REP")]].sum().sum()
        a = d.ASSREP01.sum() / (d.ASSREP01.sum() + d.ASSDEM01.sum())
        out.append({"race": r["label"], "assembly_r2p": round(a, 4), "top_r2p": round(trr / (td + trr), 4),
                    "edge": round(a - trr / (td + trr), 4)})
    return out

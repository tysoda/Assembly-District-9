"""Monte Carlo for AD9: the November 2026 general (Adams vs Flora) and the 2028 open seat.

2026 general (two candidates, midterm):
    AD9 top-of-ticket D share = state env 2026 + AD9 general lean (projected)
    Adams share               = that − Flora's edge over the top of the ticket + candidate noise

2028 open seat, with the 2026 lean draw carried forward so the two years are correlated:
    March primary (top two):  D combined share = state env 2028 + AD9 primary lean (2026 deviation carried)
                              split across n_dem / n_rep candidates (Dirichlet); does a Democrat finish top two?
    November general:         D share = state env 2028 + AD9 lean 2028 (= 2026 lean + two-year change)
                              + open-seat candidate noise
Results are also reported conditional on Flora winning in 2026.
"""
from __future__ import annotations

import numpy as np


def _q(a, ps=(10, 50, 90)):
    return {f"p{p}": float(np.percentile(a, p)) for p in ps}


def run(cfg: dict, comp: dict) -> dict:
    sc = cfg["scenario"]
    n, rng = int(sc["n_sims"]), np.random.default_rng(sc["seed"])
    N = lambda m, s: rng.normal(m, s, n) if s > 0 else np.full(n, float(m))
    env = comp["state_env"]
    from forecast.model import predict_state

    def state(year_cfg, presidential):
        infl = N(*year_cfg["inflation"])
        appr = np.clip(N(*year_cfg["approval"]), 0.2, 0.7)
        dem_pres = 1 if year_cfg.get("president_party", "R") == "D" else -1
        e = env[env["chosen"]]
        sd = e["walk_forward_sd_from_2020"] if cfg["state_env"].get("sd_from", 2016) == 2020 else e["walk_forward_sd"]
        return predict_state(env, presidential, infl, appr, dem_pres) + N(0, sd)

    # ── 2026 ────────────────────────────────────────────────────────────────
    st26 = state(cfg["state_env"]["2026"], 0)
    lean_g = comp["lean_general"]
    dev26 = N(0, lean_g["sd"])
    lean26 = lean_g["proj_2026"] + dev26
    top26 = np.clip(st26 + lean26, 0.05, 0.95)
    inc = comp["incumbent_edge"]
    edge = N(inc["mean"], inc["sd"])
    adams = np.clip(top26 - edge + N(0, cfg["incumbent"]["challenger_quality_sd"]), 0.01, 0.99)
    if comp.get("primary_2026", {}).get("use"):
        p = comp["primary_2026"]       # inverse-variance blend with the June 2026 primary signal
        w = p["sd"] ** -2 / (p["sd"] ** -2 + np.var(adams))
        adams = np.clip((1 - w) * adams + w * N(p["mean"], p["sd"]), 0.01, 0.99)
    t26 = comp["turnout"]["g26"]
    ballots26 = comp["registration"]["g26"] * np.clip(N(t26["district"], t26["sd"]), 0.2, 0.95)
    votes26 = ballots26 * cfg["votes"]["assembly_completion_g26"]
    flora_wins = adams < 0.5

    # ── 2028 primary ────────────────────────────────────────────────────────
    st28 = state(cfg["state_env"]["2028"], 1)
    lean_p = comp["lean_primary"]
    extra = np.sqrt(max(lean_p["sd"] ** 2 - lean_g["sd"] ** 2, 1e-4))
    lean_p28 = lean_p["proj_2028"] + dev26 + N(0, extra)
    st_pri = st28 + N(0, cfg["primary_2028"]["state_primary_vs_general_sd"])
    d_pri = np.clip(st_pri + lean_p28, 0.05, 0.95)
    pc = cfg["primary_2028"]
    nd, nr, a = int(pc["n_dem"]), int(pc["n_rep"]), float(pc["concentration"])
    d_split = rng.dirichlet(np.full(nd, a), n) * d_pri[:, None]
    r_split = rng.dirichlet(np.full(nr, a), n) * (1 - d_pri)[:, None]
    allc = np.concatenate([d_split, r_split], axis=1)
    top2 = np.argsort(-allc, axis=1)[:, :2]
    dem_in = (top2 < nd).any(axis=1)
    both_dem = (top2 < nd).all(axis=1)

    # ── 2028 general ────────────────────────────────────────────────────────
    lean28 = lean26 + N(lean_g["drift_2y_mean"], lean_g["drift_2y_sd"])
    top28 = np.clip(st28 + lean28, 0.05, 0.95)
    os_ = cfg["open_seat"]
    dem28 = np.clip(top28 + N(os_["edge_mean"], os_["candidate_quality_sd"]), 0.01, 0.99)
    dem_seat = both_dem | (dem_in & (dem28 > 0.5))
    t28 = comp["turnout"]["g28"]
    ballots28 = comp["registration"]["g28"] * np.clip(N(t28["district"], t28["sd"]), 0.2, 0.98)
    votes28 = ballots28 * cfg["votes"]["assembly_completion_g28"]

    def block(mask):
        m = mask if mask is not None else np.ones(n, bool)
        dr = m & dem_in & ~both_dem
        return {
            "p_dem_in_top_two": float(dem_in[m].mean()),
            "p_republican_lockout": float((~dem_in[m]).mean()),
            "p_dem_wins_if_dem_vs_rep": float((dem28[dr] > 0.5).mean()) if dr.any() else None,
            "p_dem_wins_seat": float(dem_seat[m].mean()),
            "dem_primary_share": _q(d_pri[m]),
            "dem_general_share_if_dem_vs_rep": _q(dem28[dr]) if dr.any() else None,
            "top_of_ticket_dem_share": _q(top28[m]),
            "shift_needed_for_even_odds": float(0.5 - np.median(dem28[dr])) if dr.any() else None,
            "votes_short_median": float(np.median((0.5 - dem28[dr]) * votes28[dr])) if dr.any() else None,
        }

    return {
        "g26": {
            "p_adams_wins": float((~flora_wins).mean()),
            "adams_share": _q(adams),
            "top_of_ticket_dem_share": _q(top26),
            "state_env_dem_share": _q(st26),
            "ballots": _q(ballots26),
            "assembly_votes": _q(votes26),
            "votes_to_win_median": float(np.median(votes26) / 2),
            "votes_short_median": float(np.median((0.5 - adams) * votes26)),
            "shift_needed_for_even_odds": float(0.5 - np.median(adams)),
        },
        "g28_open_seat": {
            "settings": {"n_dem": nd, "n_rep": nr, "concentration": a},
            "all_draws": block(None),
            "if_flora_wins_2026": block(flora_wins),
            "ballots": _q(ballots28),
            "assembly_votes": _q(votes28),
            "votes_to_win_median": float(np.median(votes28) / 2),
        },
        "lockout_by_field": lockout_table(rng, d_pri, a),
    }


def lockout_table(rng, d_pri, a, fields=((1, 2), (1, 3), (2, 2), (2, 3), (3, 3), (2, 4), (3, 4))):
    """P(no Democrat in the top two) for different field sizes, same primary-share draws."""
    out = []
    n = len(d_pri)
    for nd, nr in fields:
        d = rng.dirichlet(np.full(nd, a), n) * d_pri[:, None]
        r = rng.dirichlet(np.full(nr, a), n) * (1 - d_pri)[:, None]
        top2 = np.argsort(-np.concatenate([d, r], axis=1), axis=1)[:, :2]
        out.append({"n_dem": nd, "n_rep": nr, "p_lockout": float(1 - (top2 < nd).any(axis=1).mean())})
    return out

"""AD9 forecast: November 2026 general and the 2028 open seat.

    python -m forecast.panel        # rebuild the block-group panel after adding data
    python run_forecast.py          # forecast with config.json
    python run_forecast.py n_dem=1 n_rep=4 approval=0.45:0.05@2028 state_spec=mean

Overrides: any key in primary_2028, open_seat, incumbent, scenario; state_spec=sd4|mean;
lean_general=/lean_primary= weighted|linear|bg_linear|auto; inflation=/approval=MEAN:SD@YEAR.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from forecast import model as m
from forecast import simulate

ROOT = Path(__file__).parent


def apply_overrides(cfg: dict, pairs: list[str]) -> None:
    for pair in pairs:
        k, v = pair.split("=", 1)
        if k == "state_spec":
            cfg["state_env"]["spec"] = v
        elif k in ("lean_general", "lean_primary"):
            cfg["lean"][k.split("_")[1] + "_method"] = v
        elif k in ("inflation", "approval"):
            val, yr = v.split("@")
            cfg["state_env"][yr][k] = [float(x) for x in val.split(":")]
        else:
            for sec in ("primary_2028", "open_seat", "incumbent", "scenario"):
                if k in cfg[sec]:
                    cfg[sec][k] = type(cfg[sec][k])(float(v)) if not isinstance(cfg[sec][k], bool) \
                        else v.lower() == "true"
                    break
            else:
                raise SystemExit(f"Unknown setting {k}")


def choose(val: dict, method: str) -> str:
    if method != "auto":
        return method
    # compare on elections every method could predict
    k = min(len(v["errors"]) for v in val.values())
    return min(val, key=lambda mm: np.mean(np.abs(val[mm]["errors"][-k:])))


def components(cfg: dict) -> dict:
    panel = pd.read_csv(ROOT / "data/processed/bg_panel.csv", dtype={"BG": str})
    series = pd.read_csv(ROOT / "data/processed/district_series.csv")
    leans = m.bg_leans(panel)
    comp = {"state_env": m.state_environment(ROOT / cfg["data"]["sd4_dir"], cfg["state_env"]["spec"])}

    tc = cfg["turnout"]
    tm = m.turnout_model(panel, series, tc["min_reg"], tc["spec"], tc["wf_start"])
    anchors = {k: tuple(v[:2]) for k, v in tc["anchors"].items()}
    t26, t28 = m.project_turnout(tm, "g26", anchors), m.project_turnout(tm, "g28", anchors)
    comp["turnout"] = {"walk_forward": tm["walk_forward"], "district_mae": tm["district_mae"],
                       "g26": {k: t26[k] for k in ("district", "sd", "chain")},
                       "g28": {k: t28[k] for k in ("district", "sd", "chain")}}

    for kind, key, yr in (("g", "general", 2026.85), ("p", "primary", 2028.2)):
        val = m.lean_validation(series, leans, kind)
        meth = choose(val, cfg["lean"][f"{key}_method"])
        w = (t26 if kind == "g" else t28)["bg_turnout"] * (t26 if kind == "g" else t28)["reg_weights"]
        comp[f"lean_{key}"] = {
            "method": meth, "validation": val, "sd": val[meth]["sd"],
            f"proj_{2026 if kind == 'g' else 2028}": m.project_lean(series, leans, kind, yr, meth, w),
            "history": series[series.election.str[0] == kind][["election", "lean"]].round(4).values.tolist()}
    g = series[series.general == 1].lean.values
    comp["lean_general"]["drift_2y_mean"] = 0.0
    comp["lean_general"]["drift_2y_sd"] = float(np.std(np.diff(g), ddof=1))

    gen = series[series.general == 1].tail(cfg["registration"]["trend_generals"])
    b = np.polyfit(gen.year, gen.registered, 1)
    comp["registration"] = {"g26": float(np.polyval(b, 2026.85)), "g28": float(np.polyval(b, 2028.85)),
                            "per_year": float(b[0])}

    edges = m.assembly_edges(cfg)
    e = np.array([x["edge"] for x in edges])
    comp["incumbent_edge"] = {"races": edges, "mean": float(e.mean()),
                              "sd": float(max(e.std(ddof=1), cfg["incumbent"]["edge_sd_floor"]))}
    p26 = cfg["primary_2026"]
    comp["primary_2026"] = {"use": bool(p26["enabled"] and p26["dem_share"] is not None)}
    if comp["primary_2026"]["use"]:
        comp["primary_2026"].update(mean=p26["dem_share"] + p26["primary_to_general_shift"][0],
                                    sd=p26["primary_to_general_shift"][1])
    comp["bg"] = bg_table(panel, leans, t26)
    return comp


def bg_table(panel, leans, t26) -> pd.DataFrame:
    """Per block group, for targeting: registration, party mix, lean, projected 2026 turnout."""
    last = panel[panel.election == "g24"].set_index("BG")
    gl = leans[leans.election.isin(["g20", "g22", "g24"])].pivot(index="BG", columns="election", values="lean")
    out = pd.DataFrame({
        "registered_g24": last.reg_sov.round(0),
        "dem_reg": (last.DEM / last.TOTREG_R).round(3),
        "rep_reg": (last.REP / last.TOTREG_R).round(3),
        "npp_reg": (last.DCL / last.TOTREG_R).round(3),
        "hispanic_surname": (last.HISP / last.TOTREG_R).round(3),
        "lean_g20": gl.get("g20"), "lean_g22": gl.get("g22"), "lean_g24": gl.get("g24"),
        "turnout_g24": (last.ballots / last.reg_sov).round(3),
        "turnout_g26_proj": t26["bg_turnout"].round(3),
    })
    out["lean_change_g20_g24"] = (out.lean_g24 - out.lean_g20).round(4)
    return out.reset_index().rename(columns={"index": "BG"})


def main():
    cfg = json.loads((ROOT / "config.json").read_text())
    apply_overrides(cfg, sys.argv[1:])
    comp = components(cfg)
    res = simulate.run(cfg, comp)
    out_dir = ROOT / Path(cfg["output"]).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    comp.pop("bg").to_csv(out_dir / "bg_targets.csv", index=False)
    (ROOT / cfg["output"]).write_text(json.dumps({"components": comp, "results": res}, indent=2, default=float))
    report(comp, res)


def report(c: dict, r: dict) -> None:
    pct = lambda x: f"{x:.1%}"
    rng = lambda d: f"{pct(d['p50'])} [{pct(d['p10'])}–{pct(d['p90'])}]"
    se = c["state_env"]
    print("\nAD9 forecast\n" + "=" * 60)
    for k in ("sd4", "mean"):
        s = se[k]
        print(f"State env '{k}'{' (used)' if se['chosen'] == k else ''}: walk-forward MAE {pct(s['walk_forward_mae'])}, "
              f"sd {pct(s['walk_forward_sd'])}")
    for key in ("general", "primary"):
        L = c[f"lean_{key}"]
        mae = {k: round(v["mae"], 4) for k, v in L["validation"].items()}
        proj = L.get("proj_2026", L.get("proj_2028"))
        print(f"AD9 {key} lean vs state: {proj:+.3f} ± {L['sd']:.3f} ({L['method']}; walk-forward MAE {mae})")
    ie = c["incumbent_edge"]
    races = ", ".join(f"{x['race']} {x['edge']:+.3f}" for x in ie["races"])
    print(f"Flora edge over top of ticket: {ie['mean']:+.3f} ± {ie['sd']:.3f} ({races})")
    for y in ("g26", "g28"):
        t = c["turnout"][y]
        print(f"Turnout {y}: {pct(t['district'])} ± {pct(t['sd'])}  (chain {[(a, round(b, 3)) for a, b in t['chain']]})")
    print(f"Registration: {c['registration']['g26']:,.0f} (Nov 2026), {c['registration']['g28']:,.0f} (Nov 2028)")

    g = r["g26"]
    print("\nNovember 2026 — Adams (D) vs Flora (R, incumbent)")
    print(f"  P(Adams wins) {pct(g['p_adams_wins'])}   Adams share {rng(g['adams_share'])}")
    print(f"  Top of ticket D share in AD9 {rng(g['top_of_ticket_dem_share'])}  (state env {rng(g['state_env_dem_share'])})")
    print(f"  Assembly votes ~{g['assembly_votes']['p50']:,.0f}; Adams median {g['votes_short_median']:,.0f} votes short "
          f"({g['shift_needed_for_even_odds']:+.1%} needed for even odds)")
    o = r["g28_open_seat"]
    print(f"\n2028 open seat (field: {o['settings']['n_dem']} D, {o['settings']['n_rep']} R)")
    for label, b in (("all draws", o["all_draws"]), ("if Flora wins 2026", o["if_flora_wins_2026"])):
        print(f"  {label}: P(D in top two) {pct(b['p_dem_in_top_two'])}  "
              f"P(D wins | D vs R) {pct(b['p_dem_wins_if_dem_vs_rep'])}  P(D wins seat) {pct(b['p_dem_wins_seat'])}")
    b = o["if_flora_wins_2026"]
    print(f"  D primary share {rng(b['dem_primary_share'])}; D general share {rng(b['dem_general_share_if_dem_vs_rep'])}; "
          f"{b['shift_needed_for_even_odds']:+.1%} needed for even odds (~{b['votes_short_median']:,.0f} votes of "
          f"~{o['assembly_votes']['p50']:,.0f})")
    print("  P(Republican lockout) by field: " +
          ", ".join(f"{x['n_dem']}D/{x['n_rep']}R {pct(x['p_lockout'])}" for x in r["lockout_by_field"]))


if __name__ == "__main__":
    main()

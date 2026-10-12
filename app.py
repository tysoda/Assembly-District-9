"""
Streamlit front end for the AD9 forecast: November 2026 (Adams vs Flora) and the 2028 open seat.

    streamlit run app.py

Data comes from data/processed (built by `python -m forecast.panel`) and data/sd4, exactly as in the
CLI (run_forecast.py). The sidebar overrides config.json for this session only.
"""

import copy
import json

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from forecast import simulate
from run_forecast import ROOT, components

st.set_page_config(page_title="AD9 forecast", page_icon="🗳️", layout="wide")

# Validated palette (dataviz reference instance, light mode; app theme is pinned to light)
DEM, REP, NPP, OTHER = "#2a78d6", "#d03b3b", "#1baf7a", "#eda100"   # party identity
BLUE, ORANGE = "#2a78d6", "#eb6834"                                  # categorical slots 1–2
INK_MUTED = "#52514e"

BASE_CFG = json.loads((ROOT / "config.json").read_text())


def pct(x, d=1):
    return "—" if x is None else f"{x:.{d}%}"


def comp_key(cfg: dict) -> str:
    """Only the settings that change what components() estimates."""
    keys = ["state_env", "lean", "turnout", "registration", "incumbent", "primary_2026", "data"]
    return json.dumps({k: cfg[k] for k in keys}, sort_keys=True)


@st.cache_data(show_spinner="Estimating components from the block-group panel…")
def cached_components(key: str) -> dict:
    cfg = copy.deepcopy(BASE_CFG)
    cfg.update(json.loads(key))
    return components(cfg)


@st.cache_data(show_spinner="Running simulations…")
def cached_run(cfg_json: str, key: str) -> dict:
    return simulate.run(json.loads(cfg_json), cached_components(key), return_draws=True)


def hist(values: np.ndarray, color: str, title: str, threshold: float = 0.5, bins: int = 60):
    """Share histogram with the 50% line; tooltip per bar."""
    counts, edges = np.histogram(values, bins=bins, range=(max(0.0, values.min()), min(1.0, values.max())))
    df = pd.DataFrame({"lo": edges[:-1], "hi": edges[1:], "share": counts / counts.sum()})
    df["side"] = np.where(df.lo >= threshold, "wins", "loses")
    bars = alt.Chart(df).mark_bar(color=color, cornerRadiusTopLeft=2, cornerRadiusTopRight=2).encode(
        x=alt.X("lo:Q", title=title, axis=alt.Axis(format=".0%"), scale=alt.Scale(zero=False, nice=False)),
        x2="hi:Q",
        y=alt.Y("share:Q", title="Share of simulations", axis=alt.Axis(format=".0%", grid=True)),
        y2=alt.datum(0),
        opacity=alt.condition(alt.datum.side == "wins", alt.value(1.0), alt.value(0.55)),
        tooltip=[alt.Tooltip("lo:Q", title="From", format=".1%"), alt.Tooltip("hi:Q", title="To", format=".1%"),
                 alt.Tooltip("share:Q", title="Simulations", format=".1%")])
    rule = alt.Chart(pd.DataFrame({"x": [threshold]})).mark_rule(color=INK_MUTED, strokeDash=[4, 3]).encode(x="x:Q")
    label = alt.Chart(pd.DataFrame({"x": [threshold], "t": ["50%"]})).mark_text(
        align="left", dx=4, dy=-4, color=INK_MUTED, fontSize=11).encode(x="x:Q", y=alt.value(10), text="t:N")
    return (bars + rule + label).properties(height=300)


# ── sidebar ──────────────────────────────────────────────────────────────────
cfg = copy.deepcopy(BASE_CFG)
se, pc, os_, inc, sc = cfg["state_env"], cfg["primary_2028"], cfg["open_seat"], cfg["incumbent"], cfg["scenario"]

with st.sidebar:
    st.header("Assumptions")
    st.caption("Defaults come from config.json. Changes here apply to this session only.")

    with st.expander("State environment", expanded=True):
        specs = ["signed", "sd4", "mean"]
        se["spec"] = st.radio("Model", specs, index=specs.index(se["spec"]), horizontal=True,
                              help="signed: presidential year + approval signed by the president's party. "
                                   "sd4: SD4's original (unsigned approval + inflation). mean: presidential-year average.")
        se["sd_from"] = st.radio("Uncertainty from walk-forward since", [2020, 2016],
                                 index=[2020, 2016].index(se["sd_from"]), horizontal=True,
                                 help="2020 is the first year whose training data includes a Republican president.")
        for yr in ("2026", "2028"):
            st.markdown(f"**{yr}**")
            c1, c2 = st.columns(2)
            se[yr]["president_party"] = c1.selectbox("President", ["R", "D"], key=f"pp{yr}",
                                                     index=0 if se[yr]["president_party"] == "R" else 1)
            se[yr]["approval"][0] = c2.number_input("Approval", 0.2, 0.7, float(se[yr]["approval"][0]), 0.01,
                                                    key=f"ap{yr}")
            se[yr]["approval"][1] = st.slider("Approval uncertainty (sd)", 0.0, 0.10, float(se[yr]["approval"][1]),
                                              0.01, key=f"apsd{yr}")

    with st.expander("2026: Adams vs Flora"):
        inc["edge_sd_floor"] = st.slider("Minimum sd of Flora's edge", 0.0, 0.06, float(inc["edge_sd_floor"]), 0.005,
                                         format="%.3f")
        inc["challenger_quality_sd"] = st.slider("Candidate noise (sd)", 0.0, 0.08,
                                                 float(inc["challenger_quality_sd"]), 0.005, format="%.3f")
        p26 = cfg["primary_2026"]
        p26["enabled"] = st.toggle("Blend in the June 2026 primary", value=p26["enabled"],
                                   help="Democratic candidates' combined Assembly primary share, shifted by AD9's "
                                        "midterm primary-to-general change.")
        p26["dem_share"] = st.number_input("June 2026 Democratic primary share", 0.2, 0.6,
                                           float(p26["dem_share"]), 0.005, format="%.3f",
                                           disabled=not p26["enabled"])

    with st.expander("2028 open seat", expanded=True):
        c1, c2 = st.columns(2)
        pc["n_dem"] = c1.number_input("Democrats in primary", 1, 5, int(pc["n_dem"]))
        pc["n_rep"] = c2.number_input("Republicans in primary", 1, 6, int(pc["n_rep"]))
        pc["concentration"] = st.slider("Field evenness (Dirichlet concentration)", 0.5, 10.0,
                                        float(pc["concentration"]), 0.5,
                                        help="Low = one candidate dominates their party's vote; high = even split.")
        os_["edge_mean"] = st.slider("Democratic candidate vs top of ticket (pts)", -0.08, 0.08,
                                     float(os_["edge_mean"]), 0.005, format="%.3f",
                                     help="Prior: no D-vs-R open seat in the area to calibrate it.")
        os_["candidate_quality_sd"] = st.slider("Candidate noise (sd)", 0.0, 0.08,
                                                float(os_["candidate_quality_sd"]), 0.005, format="%.3f", key="osq")

    with st.expander("Simulation"):
        sc["n_sims"] = st.select_slider("Simulations", [5000, 10000, 20000, 50000], value=int(sc["n_sims"]))
        sc["seed"] = int(st.number_input("Seed", value=int(sc["seed"]), step=1))

key = comp_key(cfg)
comp = cached_components(key)
res = cached_run(json.dumps(cfg, sort_keys=True), key)
draws = res["draws"]

st.title("Assembly District 9")
st.caption("Forecast for November 2026 (Matthew Adams, D, vs Heath Flora, R, incumbent) and the 2028 open seat "
           "(Flora is termed out). Statewide environment + AD9's lean vs the state from 14 elections on fixed "
           "2020 block groups; all uncertainty from walk-forward errors.")

tabs = st.tabs(["2026 general", "2028 open seat", "History", "Model components", "Block groups", "Ballot returns"])

# ── 2026 ─────────────────────────────────────────────────────────────────────
with tabs[0]:
    g = res["g26"]
    c = st.columns(4)
    c[0].metric("P(Adams wins)", pct(g["p_adams_wins"]))
    c[1].metric("Adams share (median)", pct(g["adams_share"]["p50"]),
                help=f"80% range {pct(g['adams_share']['p10'])}–{pct(g['adams_share']['p90'])}")
    c[2].metric("Votes short (median)", f"{g['votes_short_median']:,.0f}",
                help=f"Of ~{g['assembly_votes']['p50']:,.0f} Assembly votes")
    c[3].metric("Swing needed for even odds", f"{g['shift_needed_for_even_odds']:+.1%}")
    st.altair_chart(hist(draws["adams"], DEM, "Adams two-party share"), width="stretch")
    st.caption(f"Top-of-ticket Democratic share in AD9 {pct(g['top_of_ticket_dem_share']['p50'])} "
               f"(statewide environment {pct(g['state_env_dem_share']['p50'])}), minus Flora's edge over the top of "
               f"the ticket ({comp['incumbent_edge']['mean']:+.1%} ± {comp['incumbent_edge']['sd']:.1%}). "
               f"Darker bars: Adams wins.")

# ── 2028 ─────────────────────────────────────────────────────────────────────
with tabs[1]:
    o = res["g28_open_seat"]
    cond = st.toggle("Only simulations where Flora wins in 2026", value=True)
    b = o["if_flora_wins_2026"] if cond else o["all_draws"]
    c = st.columns(4)
    c[0].metric("P(Democrat wins seat)", pct(b["p_dem_wins_seat"]))
    c[1].metric("P(Democrat in top two)", pct(b["p_dem_in_top_two"]),
                help=f"Republican lockout {pct(b['p_republican_lockout'])}")
    c[2].metric("P(Democrat wins | D vs R)", pct(b["p_dem_wins_if_dem_vs_rep"]))
    c[3].metric("Swing needed (D vs R)", f"{b['shift_needed_for_even_odds']:+.1%}",
                help=f"~{b['votes_short_median']:,.0f} votes of ~{o['assembly_votes']['p50']:,.0f}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Primary: chance no Democrat makes the top two")
        lk = pd.DataFrame(res["lockout_by_field"])
        lk["field"] = lk.n_dem.astype(str) + " D / " + lk.n_rep.astype(str) + " R"
        lk["current"] = (lk.n_dem == pc["n_dem"]) & (lk.n_rep == pc["n_rep"])
        bars = alt.Chart(lk).mark_bar(color=REP, cornerRadiusTopRight=3, cornerRadiusBottomRight=3, height=18).encode(
            y=alt.Y("field:N", sort=None, title=None), x=alt.X("p_lockout:Q", title="P(Republican lockout)",
                                                               axis=alt.Axis(format=".0%")),
            opacity=alt.condition(alt.datum.current, alt.value(1.0), alt.value(0.6)),
            tooltip=[alt.Tooltip("field:N", title="Field"), alt.Tooltip("p_lockout:Q", title="Lockout", format=".1%")])
        text = bars.mark_text(align="left", dx=4, color="#0b0b0b", fontSize=11).encode(
            text=alt.Text("p_lockout:Q", format=".0%"), opacity=alt.value(1))
        st.altair_chart((bars + text).properties(height=260), width="stretch")
        st.caption(f"Same primary-share draws for every field; concentration {pc['concentration']}. "
                   f"Democratic primary share {pct(b['dem_primary_share']['p50'])} "
                   f"[{pct(b['dem_primary_share']['p10'])}–{pct(b['dem_primary_share']['p90'])}]. "
                   "Darker bar: the field set in the sidebar. More Democrats raise the lockout risk, but a Democrat "
                   "who does get through tends to come from a strong-Democratic draw, so P(win | D vs R) rises "
                   "while the overall chance falls.")
    with right:
        st.markdown("#### General: Democratic share when it's D vs R")
        mask = draws["dem_vs_rep"] & (draws["flora_wins"] if cond else True)
        if mask.any():
            st.altair_chart(hist(draws["dem28"][mask], DEM, "Democratic two-party share, Nov 2028"), width="stretch")
        st.caption("Open seat: top-of-ticket Democratic share (state environment 2028 + AD9 lean, carried from the "
                   "2026 draw) + candidate noise.")

# ── history ──────────────────────────────────────────────────────────────────
series = pd.read_csv(ROOT / "data/processed/district_series.csv")
with tabs[2]:
    st.subheader("AD9 lean vs the state")
    lean = series[["election", "year", "lean"]].dropna().assign(
        type=lambda d: np.where(d.election.str[0] == "g", "General", "Primary"))
    base = alt.Chart(lean).encode(
        x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("lean:Q", title="AD9 − state, Democratic two-party share",
                                                 axis=alt.Axis(format="+.0%"), scale=alt.Scale(zero=False)),
        color=alt.Color("type:N", scale=alt.Scale(domain=["General", "Primary"], range=[BLUE, ORANGE]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("election:N"), alt.Tooltip("lean:Q", format="+.1%")])
    st.altair_chart((base.mark_line(strokeWidth=2) + base.mark_point(filled=True, size=70)).properties(height=280),
                    width="stretch")
    st.caption("Generals: mean over president/governor/Senate contests with a Democrat and a Republican. "
               "Primaries: all Democratic vs all Republican candidates.")

    st.subheader("Registration")
    reg = series.melt(id_vars=["election", "year"], value_vars=["reg_dem", "reg_rep", "reg_npp", "reg_hisp"],
                      var_name="group", value_name="share")
    names = {"reg_dem": "Democratic", "reg_rep": "Republican", "reg_npp": "No party preference",
             "reg_hisp": "Hispanic surname"}
    reg["group"] = reg.group.map(names)
    reg = reg[reg.election.str[0] == "g"]
    order = list(names.values())
    rbase = alt.Chart(reg).encode(
        x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("share:Q", title="Share of registered voters", axis=alt.Axis(format=".0%")),
        color=alt.Color("group:N", scale=alt.Scale(domain=order, range=[DEM, REP, NPP, OTHER]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("election:N"), alt.Tooltip("group:N"), alt.Tooltip("share:Q", format=".1%")])
    last = reg[reg.year == reg.year.max()]
    labels = alt.Chart(last).mark_text(align="left", dx=6, fontSize=11, color="#0b0b0b").encode(
        x="year:O", y="share:Q", text=alt.Text("share:Q", format=".0%"))
    st.altair_chart((rbase.mark_line(strokeWidth=2) + rbase.mark_point(filled=True, size=50) + labels)
                    .properties(height=280), width="stretch")
    with st.expander("Table"):
        cols = ["election", "registered", "ballots", "turnout", "reg_dem", "reg_rep", "reg_npp", "reg_hisp", "lean"]
        st.dataframe(series[cols].style.format({"registered": "{:,.0f}", "ballots": "{:,.0f}", "turnout": "{:.1%}",
                                                "reg_dem": "{:.1%}", "reg_rep": "{:.1%}", "reg_npp": "{:.1%}",
                                                "reg_hisp": "{:.1%}", "lean": "{:+.1%}"}, na_rep="—"),
                     hide_index=True, width="stretch")

# ── components ───────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("State environment")
    sev = comp["state_env"]
    st.dataframe(pd.DataFrame([{
        "spec": k + (" (used)" if sev["chosen"] == k else ""), "features": ", ".join(v["features"]),
        "MAE from 2016": v["walk_forward_mae"], "MAE from 2020": v["walk_forward_mae_from_2020"],
        "sd from 2016": v["walk_forward_sd"], "sd from 2020": v["walk_forward_sd_from_2020"]}
        for k, v in sev.items() if k != "chosen"]).style.format(
        {c: "{:.1%}" for c in ["MAE from 2016", "MAE from 2020", "sd from 2016", "sd from 2020"]}),
        hide_index=True, width="stretch")
    st.caption("Walk-forward: each year predicted from the years before it. Refit on two-party shares from SWDB "
               "statewide totals.")

    st.subheader("AD9 lean projection")
    rows = []
    for kind in ("general", "primary"):
        L = comp[f"lean_{kind}"]
        for mth, v in L["validation"].items():
            rows.append({"lean": kind, "method": mth + (" (used)" if L["method"] == mth else ""),
                         "walk-forward MAE": v["mae"], "errors": ", ".join(f"{e:+.3f}" for e in v["errors"])})
    st.dataframe(pd.DataFrame(rows).style.format({"walk-forward MAE": "{:.1%}"}), hide_index=True, width="stretch")
    c = st.columns(2)
    c[0].metric("General lean, Nov 2026", f"{comp['lean_general']['proj_2026']:+.1%}",
                help=f"± {comp['lean_general']['sd']:.1%}")
    c[1].metric("Primary lean, Mar 2028", f"{comp['lean_primary']['proj_2028']:+.1%}",
                help=f"± {comp['lean_primary']['sd']:.1%}")

    st.subheader("Flora's edge over the top of the ticket")
    st.dataframe(pd.DataFrame(comp["incumbent_edge"]["races"]).style.format(
        {"assembly_r2p": "{:.1%}", "top_r2p": "{:.1%}", "edge": "{:+.1%}"}), hide_index=True, width="stretch")

    st.subheader("Turnout and registration")
    t = comp["turnout"]
    c = st.columns(3)
    c[0].metric("Turnout Nov 2026", pct(t["g26"]["district"]), help=f"± {t['g26']['sd']:.1%}")
    c[1].metric("Turnout Nov 2028", pct(t["g28"]["district"]), help=f"± {t['g28']['sd']:.1%}")
    c[2].metric("Registered Nov 2026", f"{comp['registration']['g26']:,.0f}")
    st.dataframe(pd.DataFrame(t["walk_forward"]).assign(error=lambda d: d.actual - d.predicted).style.format(
        {"actual": "{:.1%}", "predicted": "{:.1%}", "error": "{:+.1%}"}), hide_index=True, width="stretch")
    st.caption("Block-group panel OLS (fixed effects + presidential + general + previous election's turnout), "
               "anchored to June 2026 ≈ 40%. Walk-forward district errors shown.")

# ── block groups ─────────────────────────────────────────────────────────────
with tabs[4]:
    bg = comp["bg"].copy()
    st.caption(f"{len(bg)} 2020 census block groups inside AD9. Lean = block group − state, Democratic two-party "
               "share. GEOID: state, county, tract, block group.")
    sc_df = bg.dropna(subset=["lean_change_g20_g24", "hispanic_surname"])
    sc_df = sc_df[sc_df.registered_g24 >= 100]
    pts = alt.Chart(sc_df).mark_circle(color=BLUE, opacity=0.65, stroke="#fcfcfb", strokeWidth=1).encode(
        x=alt.X("hispanic_surname:Q", title="Hispanic-surname share of registration (2024)", axis=alt.Axis(format=".0%")),
        y=alt.Y("lean_change_g20_g24:Q", title="Change in lean, 2020 → 2024", axis=alt.Axis(format="+.0%")),
        size=alt.Size("registered_g24:Q", title="Registered", scale=alt.Scale(range=[20, 300]),
                      legend=alt.Legend(orient="top")),
        tooltip=["BG:N", alt.Tooltip("registered_g24:Q", format=",.0f"),
                 alt.Tooltip("hispanic_surname:Q", format=".0%"), alt.Tooltip("lean_g24:Q", format="+.1%"),
                 alt.Tooltip("lean_change_g20_g24:Q", format="+.1%")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=INK_MUTED, strokeDash=[4, 3]).encode(y="y:Q")
    st.altair_chart((pts + zero).properties(height=340), width="stretch")
    st.caption("Below the line: the block group moved toward Republicans relative to the state between 2020 and "
               "2024. Block groups with fewer than 100 registered voters hidden.")
    st.dataframe(bg.sort_values("registered_g24", ascending=False).style.format(
        {"registered_g24": "{:,.0f}", **{c: "{:.1%}" for c in ["dem_reg", "rep_reg", "npp_reg", "hispanic_surname",
                                                               "turnout_g24", "turnout_g26_proj"]},
         **{c: "{:+.1%}" for c in ["lean_g20", "lean_g22", "lean_g24", "lean_change_g20_g24"]}}, na_rep="—"),
        hide_index=True, width="stretch")
    st.download_button("Download block-group table (CSV)", bg.to_csv(index=False), "ad9_block_groups.csv", "text/csv")

# ── ballot returns ───────────────────────────────────────────────────────────
with tabs[5]:
    snaps = sorted((ROOT / "data/optiq_ad9").glob("flat_*.csv"))
    if not snaps:
        st.info("No Optiq snapshots yet. The GitHub Action writes them to data/optiq_ad9/.")
    else:
        ob = pd.read_csv(snaps[-1], dtype={"party": str})
        stamp = snaps[-1].stem.replace("flat_", "")
        ob["party"] = ob.party.map({"D": "Democratic", "R": "Republican"}).fillna("Other / NPP")
        returned = ob[ob.returnDate.notna()].copy()
        total = ob.groupby("party").voters.sum()
        by_day = returned.groupby(["returnDate", "party"]).voters.sum().reset_index().sort_values("returnDate")
        by_day["cumulative"] = by_day.groupby("party").voters.cumsum()
        by_day["day"] = pd.to_datetime(by_day.returnDate).dt.strftime("%b %d")
        by_day = by_day.merge(total.rename("eligible").reset_index(), on="party")
        by_day["rate"] = by_day.cumulative / by_day.eligible
        st.caption(f"Latest Optiq snapshot: {stamp} (UTC). Records with no return date are ballots not yet "
                   f"returned. {len(snaps)} snapshot(s) in the repo.")
        c = st.columns(4)
        c[0].metric("Ballots returned", f"{returned.voters.sum():,.0f}")
        for i, ptyname in enumerate(["Democratic", "Republican", "Other / NPP"], start=1):
            r = returned[returned.party == ptyname].voters.sum()
            c[i].metric(f"{ptyname} return rate", pct(r / total.get(ptyname, np.nan)),
                        help=f"{r:,.0f} of {total.get(ptyname, 0):,.0f}")
        doms = ["Democratic", "Republican", "Other / NPP"]
        rb = alt.Chart(by_day).encode(
            x=alt.X("day:O", title="Ballot return date", sort=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("rate:Q", title="Cumulative return rate", axis=alt.Axis(format=".1%")),
            color=alt.Color("party:N", scale=alt.Scale(domain=doms, range=[DEM, REP, NPP]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("day:O", title="Date"), "party:N",
                     alt.Tooltip("cumulative:Q", format=",.0f", title="Returned"),
                     alt.Tooltip("rate:Q", format=".2%", title="Rate")])
        end = by_day[by_day.returnDate == by_day.returnDate.max()].sort_values("rate", ascending=False)
        lab = alt.layer(*[                              # stagger so near-equal lines don't collide
            alt.Chart(end.iloc[[i]]).mark_text(align="left", dx=8, dy=dy, fontSize=11, color="#0b0b0b").encode(
                x=alt.X("day:O", sort=None), y="rate:Q", text="party:N")
            for i, dy in zip(range(len(end)), (-8, 8, 8))])
        st.altair_chart((rb.mark_line(strokeWidth=2) + rb.mark_point(filled=True, size=50) + lab)
                        .properties(height=300), width="stretch")
        share = returned.groupby("party").voters.sum() / returned.voters.sum()
        st.caption("Party share of ballots returned so far: " +
                   ", ".join(f"{k} {v:.1%}" for k, v in share.reindex(doms).items()))
        with st.expander("Table"):
            st.dataframe(by_day.pivot(index="returnDate", columns="party", values="cumulative").fillna(0)
                         .astype(int), width="stretch")

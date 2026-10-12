# 2026 CA Assembly District 9

Data collection and analysis for California Assembly District 9 (Amador, Calaveras, and parts of Sacramento, San Joaquin and Stanislaus counties), modelled on the SD4 forecast work, for planning toward 2028.

## Automated collection

| Source | What | How | Where |
|---|---|---|---|
| Optiq (`abev.optiqdata.com`) | Ballot returns by party, age, gender, ethnicity and return date (district); registered and returned by party for each county | GitHub Action `optiq_ad9.yml`, daily plus every 3 hours in election week | `data/optiq_ad9/` |
| CA SOS results API, county registrars, SOS unprocessed ballots | Results and ballots-received counts | Claude scheduled tasks | Google Drive folder "AD9 2026 Snapshots" |

Each Optiq run writes `raw_<UTC>.json` (untouched) and `flat_<UTC>.csv` (one row per record, with `snapshot_utc`) for the district, and `raw_county_<UTC>.json` / `county_<UTC>.csv` for the county breakdown. The county endpoint has no per-day detail, so these snapshots are its only time series. To trigger a run by hand, open **Actions → Optiq AD9 snapshot → Run workflow**.

## Streamlit app

```
pip install -r requirements.txt
streamlit run app.py
```

Tabs: **2026 general** (Adams vs Flora: win probability, share distribution, votes short), **2028 open seat**
(primary lockout risk by field size, the general when it's D vs R, optionally only where Flora wins in 2026),
**History** (AD9 lean vs the state, registration by party and Hispanic surname), **Model components** (state
environment specs and their walk-forward errors, lean methods, Flora's edge, turnout), **Block groups** (the targeting
table with a lean-change vs Hispanic-share chart, CSV download), **Ballot returns** (latest Optiq snapshot: cumulative
return rate by party). The sidebar overrides config.json for the session. Deploys to Streamlit Community Cloud like
the SD4 app: point it at `app.py` (Python 3.11 is pinned in `runtime.txt`).

## Forecast model: November 2026 and the 2028 open seat

```
python -m forecast.panel     # block-group panel from data/raw (re-run after adding elections)
python run_forecast.py       # forecast with config.json; prints a report, writes output/
python run_forecast.py n_dem=1 n_rep=3 state_spec=mean approval=0.45:0.05@2028
```

**Geography.** Every election from p12 to g24 is moved onto 345 fixed 2020 census block groups inside AD9
(`forecast/panel.py`): precinct → census block (each election's SWDB block map) → 2010→2020 block crosswalk
where needed → block group, weighted by registered voters. Outputs: `data/processed/bg_panel.csv`,
`data/processed/district_series.csv`.

**Components** (`forecast/model.py`), each with walk-forward (expanding-window) validation:

| Component | Method | Walk-forward |
|---|---|---|
| State environment | presidential + approval signed by the president's party, refit on two-party shares (`state_spec=signed`, default); `sd4` and `mean` specs available | from 2020: signed MAE 2.9 pts, sd4 5.3, mean 2.7; uncertainty from the 2020+ walk-forward (`sd_from`) |
| AD9 general lean vs state | mean of last three generals (auto-chosen over linear and block-group trends) | MAE 2.2 pts |
| AD9 primary lean vs state | linear trend (auto-chosen) | MAE 3.0 pts |
| Turnout | block-group panel OLS: block-group fixed effects + presidential + general + previous election's turnout; anchored to June 2026 (≈40%) | district MAE 7.1 pts from 2016 (3.5 from 2020) |
| Flora's edge (2026) | his Assembly R share minus top-of-ticket R share: g18 +3.9, g20 +7.3, g22 +3.6 | — |

**Simulation** (`forecast/simulate.py`): 2026 Adams share = state env + AD9 lean − Flora edge + noise. 2028 carries the
2026 lean draw forward: a top-two March primary (Democratic and Republican fields split by a Dirichlet; does a
Democrat finish top two?) then a November open-seat general. 2028 results are also reported conditional on Flora
winning in 2026. The June 2026 primary can be blended in (`primary_2026.enabled`, off by default).

Outputs: `output/ad9_forecast.json` (all components and results), `output/bg_targets.csv` (per block group:
registration and party mix, Hispanic-surname share, lean 2020–2024 and its change, 2024 and projected 2026 turnout).

**State environment.** SD4's approval term isn't signed by the president's party, so an unpopular Republican president
lowered the predicted Democratic share. The default here signs it ((approval − 0.5) × +1 Democratic / −1 Republican
president) and drops inflation. It can only be tested fairly from 2020, the first year whose training data includes a
Republican president, so its uncertainty is measured from 2020 too. It over-predicts the closest analogue (2018) by about
2 pts and rests on two Republican-president cycles.

**Caveats.** Turnout uncertainty from the 2016 walk-forward start is
wide because the early years have little training data; it only affects vote counts, not win probabilities. The
open-seat candidate effect is a prior: there's no D-vs-R open-seat race in the area to calibrate it.

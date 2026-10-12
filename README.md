# 2026 CA Assembly District 9

Data collection and analysis for California Assembly District 9 (Amador, Calaveras, and parts of Sacramento, San Joaquin and Stanislaus counties), modelled on the SD4 forecast work, for planning toward 2028.

## Automated collection

| Source | What | How | Where |
|---|---|---|---|
| Optiq (`abev.optiqdata.com`) | Ballot returns by party, age, gender, ethnicity and return date | GitHub Action `optiq_ad9.yml`, daily plus every 3 hours in election week | `data/optiq_ad9/` |
| CA SOS results API, county registrars, SOS unprocessed ballots | Results and ballots-received counts | Claude scheduled tasks | Google Drive folder "AD9 2026 Snapshots" |

Each Optiq run writes `raw_<UTC>.json` (untouched) and `flat_<UTC>.csv` (one row per record, with `snapshot_utc`). To trigger a run by hand, open **Actions → Optiq AD9 snapshot → Run workflow**.

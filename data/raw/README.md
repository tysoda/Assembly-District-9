# data/raw — model inputs

Precinct-level files from the Statewide Database (statewidedatabase.org). Name each file
`c{FIPS}_{election}_{type}.csv`, for example `c077_g24_sov.csv`. A statewide file can be named
`state_{election}_{type}.csv`; the loader filters it to the five AD9 counties.

| County | FIPS | Part of county in AD9 |
|---|---|---|
| Amador | 005 | all |
| Calaveras | 009 | all |
| Sacramento | 067 | part |
| San Joaquin | 077 | part |
| Stanislaus | 099 | part |

Elections are coded `p` (primary) or `g` (general) plus a two-digit year: p22, g22, p24, g24, p26.

## Files needed

| Type | Elections | SWDB name | Status |
|---|---|---|---|
| `sov` | p16 g16 p18 g18 p20 g20 p22 g22 p24 g24 | statement of vote by SR precinct | **Done** (below). Add p26 when posted |
| `reg` | same | registration by SR precinct | **Done**: `ad9_{e}_reg.csv`, `c5_{e}_reg.csv` |
| `blk_map` | p16 g16 p18 g18 p20 g20 | SR precinct to census block | **Done** (2010 blocks) |
| relationship | once | Census 2010-to-2020 tabulation block relationship file, CA | **Done**: `c5_tab2010_tab2020.csv` |
| ~~`ad21_blocks.csv`~~ | — | 2021 Assembly district block assignment | Covered by `ad9_p24_sr_blk_map.csv` |

## Already here

### Statements of vote, filtered from the statewide SWDB srprec files

Made by `python scripts/filter_swdb.py <statewide files>`.

* `ad9_{p22,g22,p24,g24}_sov.csv`: current lines, rows with `ADDIST == 9`.
* `c5_{p16,g16,p18,g18,p20,g20}_sov.csv`: old lines, every precinct in the five AD9 counties. `ADDIST` here is the
  pre-2021 district (5, 6, 7, 8, 9, 11, 12, 13, 21), so these still need the block conversion to place them in
  the current AD9.

All use `SRPREC` / `SRPREC_KEY`. `COUNTY` is SWDB's county sequence number (3 Amador, 5 Calaveras, 34 Sacramento,
39 San Joaquin, 50 Stanislaus); `FIPS` is the five-digit county FIPS.

* **Suppressed cells:** SWDB masks contest votes as `***` in very small precincts (about 0.1% of ballots). These are
  blank in the filtered files.
* **No party registration:** `DEMREG`/`REPREG` are zero throughout, so party registration needs the
  registration-by-precinct files.
* Contest columns that are zero throughout the output are dropped.

| | p22 | g22 | p24 | g24 |
|---|---|---|---|---|
| Registered | 264,628 | 265,584 | 262,973 | 270,387 |
| Ballots cast | 92,665 | 145,448 | 103,752 | 205,545 |
| Turnout | 35.0% | 54.8% | 39.5% | 76.0% |
| Assembly | Flora (R) 65,647, unopposed | Flora 96,990, Dem 43,109 | Flora 65,939, AIP 21,945 | Flora 129,107, AIP 55,133 |
| Top of ticket, D two-party | Gov 36.9%\* | Gov 34.4% | Pres 33.8%\* | Pres 38.0% |

\* Primary: all Democratic vs all Republican candidates combined.

### AD9 census blocks (p24)

`ad9_p24_sr_blk_map.csv`: SWDB's statewide p24 precinct-to-block map, limited to blocks in AD9 precincts
(matched to `ad9_p24_sov.csv` on `SRPREC_KEY`). It holds 6,655 blocks and 261,808 registered voters, 99.6% of AD9's
p24 registration. SR precincts nest inside districts, so this list stands in for the 2021 Assembly block
assignment wherever voters live. Blocks with no registered voters are missing, which doesn't matter for
registration-weighted work. 51 blocks are split between AD9 and a neighbouring district; `PCTBLK` gives each
part's share.

Joined to the CVAP file (registration-weighted), AD9's citizen voting-age population (2015–19) is about 297,000:
27.7% Hispanic, 60.9% non-Hispanic white, 5.8% Asian, 2.4% Black.

### 2016–2020 on current AD9 lines

`python scripts/rebuild_old_lines.py` writes `ad9_{p16,g16,p18,g18,p20,g20}_sov.csv` (every count column multiplied
by the precinct's AD9 share; `AD9_SHARE` column added) and `ad9_{e}_precinct_share.csv`. The chain, all weighted by
registered voters: 2020 block → AD9 (p24 block map + p24 precinct districts, `c5_p24_sr_blk_map.csv`), then 2010 block
→ 2020 blocks (Census relationship file, pieces weighted by p24 registrants × land-area share), then old precinct →
2010 blocks (that election's block map). Unmatched blocks hold under 25 voters per election.

Precincts split by the new line get fractional votes, so totals are estimates near the edges (60–95 precincts per
election are partly in AD9). Validation against published current-line figures: g22 governor 34.4% D and g24
president 38.0% D two-party, both matching. 2020 has no published current-line figure to check against.

| | p16 | g16 | p18 | g18 | p20 | g20 | p22 | g22 | p24 | g24 |
|---|---|---|---|---|---|---|---|---|---|---|
| Registered (k) | 202 | 218 | 217 | 225 | 227 | 251 | 265 | 266 | 263 | 270 |
| Turnout | 46.6% | 76.8% | 40.8% | 66.4% | 51.2% | 84.0% | 35.0% | 54.8% | 39.5% | 76.0% |
| D two-party, top of ticket | Pres 46.5%\* | Pres 41.7% | Gov 40.0%\* | Gov 38.6% | Pres 43.6%\* | Pres 42.8% | Gov 36.9%\* | Gov 34.4% | Pres 33.8%\* | Pres 38.0% |

\* Primary: all Democratic vs all Republican candidates (2016 primary inflated by the contested Democratic race).
US Senate generals in 2016 and 2018 were Democrat vs Democrat.

### Registration on current AD9 lines

`python scripts/build_registration.py <statewide reg files>` writes `c5_{e}_reg.csv` (five counties, raw) and
`ad9_{e}_reg.csv`: 2022+ by precinct key; before 2022 scaled by `AD9_SHARE` (run `rebuild_old_lines.py` first).
Totals agree with the results files' `TOTREG` to within 1% in every election. A few 2022+ results precincts
(5–31 per election) have no registration row; they're precincts with no registered voters.

| | p16 | g16 | p18 | g18 | p20 | g20 | p22 | g22 | p24 | g24 |
|---|---|---|---|---|---|---|---|---|---|---|
| Dem | 32.3% | 32.5% | 32.0% | 31.2% | 31.9% | 32.1% | 32.9% | 33.0% | 32.6% | 31.6% |
| Rep | 44.3% | 43.0% | 41.7% | 40.4% | 41.4% | 41.5% | 40.4% | 40.2% | 41.4% | 42.0% |
| No party preference | 18.5% | 19.4% | 20.8% | 22.8% | 20.3% | 19.6% | 18.9% | 18.8% | 17.8% | 18.3% |
| Other parties | 4.9% | 5.1% | 5.5% | 5.5% | 6.5% | 6.9% | 7.8% | 8.0% | 8.2% | 8.2% |
| Hispanic surname | 21.1% | 22.1% | 23.1% | 23.5% | 24.0% | 25.0% | 26.2% | 26.5% | 26.8% | 27.4% |

### Older block maps (five counties)

* `c5_{e}_sr_blk_map.csv`: SWDB precinct-to-block maps for every election p12–g24, five AD9 counties only. p22, g22, p24 and g24 use 2020 blocks. **p12–g20 use 2010
  census blocks**, so they can't be joined straight to `ad9_p24_sr_blk_map.csv` (2020 blocks). They need the
  Census 2010-to-2020 block relationship file for California, or SWDB's `GeoConv_SRPREC_BLOCK20` versions.

### Copied from Stanislaus-County-District-1

`c099_p20_sov.csv`, `c099_g20_sov.csv`, `c099_g22_sov.csv`, `c099_g24_sov.csv`, `c099_g22_reg.csv`,
`c099_g22_sr_blk_map.csv`, `c099_g24_sr_blk_map.csv`, `county_099_g20_GeoConv_SRPREC_BLOCK20.csv`

Some `addist` values in these files are not district numbers (e.g. 3507 in g22), so they are probably
total or summary rows. Exclude them by filtering on the district number.

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

| Type | Elections | SWDB name | Notes |
|---|---|---|---|
| `sov` | p22 g22 p24 g24 (p26 if posted) | statement of vote by SR precinct | Has `addist`: AD9 rows are `addist == 9` |
| `reg` | same | registration by SR precinct | |
| `sov`, `reg` | g16 g18 p20 g20 | same, older elections | Old district lines: `addist` shows 12/21 etc., so these go through the block files below |
| `blk_map` | g16 g18 p20 g20 | SR precinct to 2020 census block conversion | |
| ~~`ad21_blocks.csv`~~ | — | 2021 Assembly district block assignment | Covered by `ad9_p24_sr_blk_map.csv` (below) |

## Already here

### AD9 statement of vote, filtered from the statewide SWDB files

`ad9_p22_sov.csv`, `ad9_g22_sov.csv`, `ad9_p24_sov.csv`, `ad9_g24_sov.csv`: rows with `ADDIST == 9`, with all-zero
contest columns dropped. `COUNTY` is SWDB's county sequence number (3 Amador, 5 Calaveras, 34 Sacramento,
39 San Joaquin, 50 Stanislaus), not FIPS.

* **Precinct keys differ:** p22, g22 and g24 use `SVPREC` (voting precincts); p24 uses `SRPREC`. Fine for district
  totals and per-election leans, but don't join across them without SWDB's SV-to-SR conversion.
* **Suppressed cells:** SWDB masks contest votes as `***` in very small precincts (g24: 50 rows, 223 ballots;
  p24: 4 rows, 17 ballots). These are blank in the filtered files.
* **No party registration:** `DEMREG`/`REPREG` are zero in these statewide files, so party registration needs the
  registration-by-precinct files.

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

### Copied from Stanislaus-County-District-1

`c099_p20_sov.csv`, `c099_g20_sov.csv`, `c099_g22_sov.csv`, `c099_g24_sov.csv`, `c099_g22_reg.csv`,
`c099_g22_sr_blk_map.csv`, `c099_g24_sr_blk_map.csv`, `county_099_g20_GeoConv_SRPREC_BLOCK20.csv`

Some `addist` values in these files are not district numbers (e.g. 3507 in g22), so they are probably
total or summary rows. Exclude them by filtering on the district number.

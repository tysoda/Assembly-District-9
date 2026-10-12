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
| `ad21_blocks.csv` | once | 2021 Assembly district block assignment | Places 2020 blocks in the current AD9 |

## Already here (copied from Stanislaus-County-District-1)

`c099_p20_sov.csv`, `c099_g20_sov.csv`, `c099_g22_sov.csv`, `c099_g24_sov.csv`, `c099_g22_reg.csv`,
`c099_g22_sr_blk_map.csv`, `c099_g24_sr_blk_map.csv`, `county_099_g20_GeoConv_SRPREC_BLOCK20.csv`

Some `addist` values in these files are not district numbers (e.g. 3507 in g22), so they are probably
total or summary rows. Exclude them by filtering on the district number.

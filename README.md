# espn-ff-api

Forked from the initial work of jman4190 ([blog post](https://jman4190.medium.com/how-to-use-python-with-the-espn-fantasy-draft-api-ecde38621b1b)). Pulls a league's ESPN draft/keeper history and season projections, and collects preseason ADP (Average Draft Position) data from a few different sources for use as model features.

`How to Use Python with the ESPN Fantasy API - Walkthrough.ipynb` in this repo is a standing reference for the ESPN Fantasy API itself -- every data point this kind of project can pull, how to call each one, and the joins/mappings needed to use them (current URLs/hosts, not the pre-2024 ones).

## Setup

**Requirements:**
```
pip install pandas numpy requests python-dotenv beautifulsoup4
```
Also requires an ESPN Fantasy Football account.

**Environment variables** -- create a `.env` file:
```
LEAGUE_ID=YOUR_LEAGUE_ID_HERE
SWID_COOKIE=YOUR_SWID_COOKIE_HERE
ESPN_S2_COOKIES=YOUR_ESPN_S2_COOKIE_HERE
```

**Each season**, update `static.py`: `projection_year`, `years`, and `league_teams` (owner names). `lineup_slot_mapping` only needs touching if roster settings themselves change.

## Running the pipeline

### Draft/keeper history and projections
```
python draft_history.py
python projection_history.py
```

### Preseason ADP momentum -- run periodically during the offseason
| Script | What it refreshes |
|---|---|
| `build_adp_momentum_dataset.py` + `compute_adp_momentum.py` | Historical ADP momentum (Wayback/FantasyPros) -- rarely needs rerunning once built |
| `compute_live_adp_momentum.py` | Current-season live ADP (Fantasy Football Calculator) -- rerun as the preseason progresses |
| `compute_adp_momentum_fallback.py` | Deeper-board fallback (FootballGuys) for players FFC doesn't cover |
| `compute_rookie_draft_capital.py` | Real NFL Draft capital for this season's rookie class |
| `compute_espn_adp_snapshot.py` | Weekly snapshot of ESPN's own crowd ADP (top 300, empirically PPR-leaning) -- building up a multi-season history to eventually replace the FFC/FootballGuys setup above, which mismatches both source and scoring format. Only collects within the same training-camp-through-draft-date window the other ADP features use; a run outside that window is a harmless no-op. |

These write to `data/*.csv`, for whatever downstream pipeline reads them.

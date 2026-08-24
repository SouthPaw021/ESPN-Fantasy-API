## Current-season ADP momentum, sourced from Fantasy Football Calculator's live
## graph endpoint -- NOT the historical Wayback+FantasyPros pipeline
## (build_adp_momentum_dataset.py / compute_adp_momentum.py), which can't be
## extended to the live season (FantasyPros went client-side-rendered in 2026,
## and FFC's graph endpoint ignores year/season params, always returning the
## current tracking window).
##
## Uses the same DELTA methodology as the historical feature's production
## metric (compute_adp_momentum.py's compute_momentum_delta), not a slope.
## FFC tracks near-daily, far denser than the historical Wayback snapshots
## the model was fit on, and a slope divides by however many days separate
## two points -- confirmed a 2-point raw-daily slope here hit 8.67 vs. the
## historical feature's max of ~8. Delta (first tracked vs. last tracked) is
## the same number regardless of sampling density, so no resampling needed.
##
## Deliberately Standard scoring, not PPR, even though this league plays PPR:
## the historical feature the model was actually fit on is Standard-scoring
## ADP (PPR tested head-to-head via walk-forward validation and lost -- see
## build_adp_momentum_dataset.py). Feeding the live model a PPR number would
## be a train/serve mismatch on top of an already-untested live signal.
##
## Team count doesn't matter -- confirmed FFC's graph endpoint returns
## identical data regardless of the teams= parameter.
import os
import re
import time
import requests
import pandas as pd

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECTIONS_CSV = os.path.join(REPO_DIR, "..", "Projections.csv")
DEFAULT_OUTPUT_CSV = os.path.join(REPO_DIR, "data", "adp_momentum_live.csv")

FFC_LIST_URL = "https://fantasyfootballcalculator.com/adp/standard/12-team/all"
FFC_GRAPH_URL = "https://fantasyfootballcalculator.com/adp/graph/data"

EXCLUDED_POSITIONS = {'K', 'D/ST'}
MIN_SNAPSHOTS_REQUIRED = 2  # matches compute_adp_momentum.py's floor

# NOT June 1, despite the raw scrape covering June-Sept -- training camps open
# mid-July (rookies ~7/17-18, veterans mostly 7/22-28), so June is still
# OTAs/minicamp buzz, not the roster-battle news that actually drives
# preseason ADP movement. Matches compute_adp_momentum.py's WINDOW_START_MD,
# set for the same reason after it wrongly treated 2021 rookies Najee
# Harris/Kyle Pitts as "off the board" under a June 1 start.
WINDOW_START = "2026-07-15"

# Matches compute_adp_momentum.py's MAX_RANKED_PLAYERS/SYNTHETIC_START_ADP --
# a fixed cap kept consistent across sources rather than each one deriving its
# own "worst tracked ADP that day" (varied wildly by source). 300 comfortably
# covers this league's entire real draft pool.
MAX_RANKED_PLAYERS = 300
SYNTHETIC_START_ADP = MAX_RANKED_PLAYERS + 1

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})


def _get_with_retries(url, params=None):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                print(f"    retry {attempt + 1}/{MAX_RETRIES - 1} after {wait}s ({e.__class__.__name__})")
                time.sleep(wait)
    raise last_exc


def _normalize_name(name):
    """Strips trailing generational suffixes (Jr./Sr./II/III/IV) and lowercases,
    for MATCHING only -- same as compute_adp_momentum.py's _normalize_name.
    FFC lists "Brian Robinson"/"Kenneth Walker" with no suffix, while
    Projections.csv carries "Brian Robinson Jr."/"Kenneth Walker III", so an
    exact match would silently miss real coverage. Output still uses
    Projections.csv's own spelling for the downstream merge."""
    return re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name.strip(), flags=re.IGNORECASE).strip().lower()


def _fetch_ffc_player_ids():
    """(player_id, name) pairs for every player on FFC's current overall ADP
    page, extracted from the graph-checkbox onclick handlers
    (updatePlayer(id, "Name")). NOT the data-graph="id:name" attribute, which
    is only present on the #1 ranked player (a page-load default, not a
    per-row id)."""
    resp = _get_with_retries(FFC_LIST_URL)
    pairs = re.findall(r'updatePlayer\((\d+),\s*&quot;([^&]+)&quot;\)', resp.text)
    return {name: pid for pid, name in pairs}


def _fetch_player_series(player_id):
    """[(date, adp), ...] for a player, Standard scoring, filtered to the
    window (>= WINDOW_START) and capped to MAX_RANKED_PLAYERS, sorted
    chronologically. No year/season param -- the graph endpoint ignores it
    and always returns the current live tracking window."""
    resp = _get_with_retries(FFC_GRAPH_URL, params={"player": player_id, "teams": 12, "format": "standard"})
    points = resp.json()
    window_start = pd.Timestamp(WINDOW_START)
    series = [
        (pd.Timestamp(ts, unit="ms"), adp) for ts, adp in points
        if pd.Timestamp(ts, unit="ms") >= window_start and adp <= MAX_RANKED_PLAYERS
    ]
    series.sort(key=lambda p: p[0])
    return series


def compute_live_momentum(projections_csv_path=DEFAULT_PROJECTIONS_CSV, output_csv_path=DEFAULT_OUTPUT_CSV):
    projections = pd.read_csv(projections_csv_path)
    relevant_players = set(projections[~projections['Position'].isin(EXCLUDED_POSITIONS)]['Player'])
    print(f"[compute_live_adp_momentum] {len(relevant_players)} relevant players in Projections.csv")

    ffc_ids = _fetch_ffc_player_ids()
    print(f"[compute_live_adp_momentum] {len(ffc_ids)} players found on FFC's live ADP page")

    # Normalized-name matching (see _normalize_name), not exact string
    # equality -- FFC drops generational suffixes entirely. Keyed by
    # Projections.csv's own spelling so downstream merges stay canonical.
    relevant_by_norm = {_normalize_name(p): p for p in relevant_players}
    matched = {}
    for ffc_name, pid in ffc_ids.items():
        canonical = relevant_by_norm.get(_normalize_name(ffc_name))
        if canonical is not None:
            matched[canonical] = pid
    print(f"[compute_live_adp_momentum] {len(matched)} of {len(relevant_players)} relevant players matched by name")

    # Pass 1: fetch every player's window-filtered series first. Needed
    # before any single player's delta can be computed, since we need to
    # know the window's first tracked date across the whole pool before
    # deciding which individual players were or weren't on it yet.
    all_series = {}
    for name, pid in matched.items():
        try:
            all_series[name] = _fetch_player_series(pid)
        except Exception as e:
            print(f"  {name} (id={pid}): skipped ({e})")
        time.sleep(REQUEST_DELAY_SECONDS)

    non_empty = {name: s for name, s in all_series.items() if s}
    first_date = min(s[0][0] for s in non_empty.values())
    print(f"[compute_live_adp_momentum] window first date {first_date.date()}, "
          f"synthetic start for not-yet-tracked players: {SYNTHETIC_START_ADP}")

    rows = []
    for name, series in non_empty.items():
        if len(series) < MIN_SNAPSHOTS_REQUIRED:
            continue
        on_first_day = [adp for date, adp in series if date == first_date]
        start_adp = on_first_day[0] if on_first_day else SYNTHETIC_START_ADP
        end_adp = series[-1][1]
        rows.append({"Player": name, "adp_momentum": start_adp - end_adp, "n_snapshots": len(series)})

    result = pd.DataFrame(rows, columns=["Player", "adp_momentum", "n_snapshots"])
    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    result.to_csv(output_csv_path, index=False)
    print(f"[compute_live_adp_momentum] wrote {len(result)} rows to {output_csv_path} "
          f"({len(matched) - len(result)} matched players had too few in-window snapshots)")
    return result


if __name__ == "__main__":
    compute_live_momentum()

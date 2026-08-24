## Current-season ADP momentum for players NOT covered by compute_live_adp_momentum.py
## (Fantasy Football Calculator, ~208 players max) -- sourced from
## footballguys.com/adp instead, which tracks a meaningfully deeper board.
## Fallback-only, deliberately: FootballGuys' scoring format is unconfirmed
## (no PPR/Standard label), and a direct comparison against FFC's
## confirmed-Standard rankings showed a real, if modest, PPR-leaning bias
## (pass-catching backs like Alvin Kamara ranked 12-20 spots better on
## FootballGuys). Containing that uncertainty to only the players FFC has no
## data for bounds the exposure to "no signal at all" cases, not a
## replacement of something more reliable.
##
## Same fixed 300-player cap / 301 synthetic-start convention as
## compute_adp_momentum.py and compute_live_adp_momentum.py. Matters more here:
## FootballGuys' tracked pool roughly doubled this preseason (352 players in
## mid-July to ~560 by late August), and without a fixed cap a player only
## added during that expansion would look like a genuine "came from nowhere"
## riser (happened to Najee Harris in testing -- tracked back to a real
## Giants depth-chart shakeup, but the mechanism can't tell that apart from a
## data artifact without independent confirmation).
import io
import os
import re
import time
import requests
import pandas as pd

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECTIONS_CSV = os.path.join(REPO_DIR, "..", "Projections.csv")
DEFAULT_LIVE_MOMENTUM_CSV = os.path.join(REPO_DIR, "data", "adp_momentum_live.csv")
DEFAULT_OUTPUT_CSV = os.path.join(REPO_DIR, "data", "adp_momentum_live_fallback.csv")

CDX_URL = "http://web.archive.org/cdx/search/cdx"
FBG_PAGE = "footballguys.com/adp"
FBG_LIVE_URL = "https://www.footballguys.com/adp"

EXCLUDED_POSITIONS = {'K', 'D/ST'}
MIN_SNAPSHOTS_REQUIRED = 2

# Matches compute_adp_momentum.py's WINDOW_START_MD -- training camps open
# mid-July, so anything before that is low-signal offseason noise.
WINDOW_START = "2026-07-15"

# Matches compute_adp_momentum.py's MAX_RANKED_PLAYERS/SYNTHETIC_START_ADP.
MAX_RANKED_PLAYERS = 300
SYNTHETIC_START_ADP = MAX_RANKED_PLAYERS + 1

REQUEST_TIMEOUT = 40
REQUEST_DELAY_SECONDS = 2
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})


def _normalize_name(name):
    """Strips trailing generational suffixes (Jr./Sr./II/III/IV) and lowercases,
    for MATCHING only -- same as compute_adp_momentum.py's/
    compute_live_adp_momentum.py's _normalize_name. FootballGuys formats
    suffixes inconsistently with Projections.csv (e.g. "Brian Robinson Jr" with
    no period), which would drop real coverage under exact matching. Output
    still uses Projections.csv's own spelling for the downstream merge."""
    return re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name.strip(), flags=re.IGNORECASE).strip().lower()


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


def _list_snapshots():
    """Wayback snapshot (timestamp, url) pairs for footballguys.com/adp within
    this preseason's window. FootballGuys is far more sparsely crawled than
    FantasyPros was for the historical feature -- a handful of snapshots per
    preseason, not 10-30 -- another reason this stays fallback-only."""
    params = {
        "url": FBG_PAGE,
        "output": "json",
        "from": pd.Timestamp(WINDOW_START).strftime("%Y%m%d"),
        "to": pd.Timestamp.now().strftime("%Y%m%d"),
        "collapse": "timestamp:8",
        "limit": 200,
    }
    resp = _get_with_retries(CDX_URL, params=params)
    rows = resp.json()
    if len(rows) <= 1:
        return []
    header, data_rows = rows[0], rows[1:]
    ts_idx, orig_idx = header.index("timestamp"), header.index("original")
    return [(r[ts_idx], r[orig_idx]) for r in data_rows]


def _fetch_snapshot_table(timestamp, original_url):
    url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"
    resp = _get_with_retries(url)
    tables = pd.read_html(io.StringIO(resp.text))
    return tables[0][["Player", "Consensus"]].rename(columns={"Consensus": "ADP"})


def _fetch_live_table():
    resp = _get_with_retries(FBG_LIVE_URL)
    tables = pd.read_html(io.StringIO(resp.text))
    return tables[0][["Player", "Consensus"]].rename(columns={"Consensus": "ADP"})


def compute_fallback_momentum(
    projections_csv_path=DEFAULT_PROJECTIONS_CSV,
    live_momentum_csv_path=DEFAULT_LIVE_MOMENTUM_CSV,
    output_csv_path=DEFAULT_OUTPUT_CSV,
):
    projections = pd.read_csv(projections_csv_path)
    relevant_players = set(projections[~projections['Position'].isin(EXCLUDED_POSITIONS)]['Player'])

    ffc_covered = set(pd.read_csv(live_momentum_csv_path)['Player']) if os.path.exists(live_momentum_csv_path) else set()
    fallback_targets = relevant_players - ffc_covered
    print(f"[compute_adp_momentum_fallback] {len(fallback_targets)} players need fallback coverage "
          f"({len(relevant_players)} relevant, {len(ffc_covered)} already covered by FFC)")

    frames = []
    snapshots = _list_snapshots()
    print(f"[compute_adp_momentum_fallback] {len(snapshots)} Wayback snapshots found in window")
    for timestamp, original_url in snapshots:
        try:
            table = _fetch_snapshot_table(timestamp, original_url)
            table["SnapshotDate"] = pd.Timestamp(timestamp[:8])
            frames.append(table)
        except Exception as e:
            print(f"  {timestamp}: skipped ({str(e)[:200]})")
        time.sleep(REQUEST_DELAY_SECONDS)

    try:
        live_table = _fetch_live_table()
        live_table["SnapshotDate"] = pd.Timestamp.now().normalize()
        frames.append(live_table)
        print("[compute_adp_momentum_fallback] live snapshot fetched successfully")
    except Exception as e:
        print(f"[compute_adp_momentum_fallback] live snapshot fetch failed: {str(e)[:200]}")

    if not frames:
        print("[compute_adp_momentum_fallback] no data collected, writing empty result")
        pd.DataFrame(columns=["Player", "adp_momentum", "n_snapshots"]).to_csv(output_csv_path, index=False)
        return pd.DataFrame(columns=["Player", "adp_momentum", "n_snapshots"])

    snaps = pd.concat(frames, ignore_index=True)
    snaps = snaps[snaps["SnapshotDate"] >= pd.Timestamp(WINDOW_START)]
    snaps = snaps[snaps["ADP"] <= MAX_RANKED_PLAYERS]

    first_date = snaps["SnapshotDate"].min()
    print(f"[compute_adp_momentum_fallback] window first date {first_date.date()}, "
          f"synthetic start for not-yet-tracked players: {SYNTHETIC_START_ADP}")

    # Normalized-name matching (see _normalize_name), not exact string
    # equality -- rows get grouped/matched by normalized name but written out
    # under Projections.csv's own canonical spelling.
    fallback_by_norm = {_normalize_name(p): p for p in fallback_targets}
    snaps["NormPlayer"] = snaps["Player"].apply(_normalize_name)

    rows = []
    for norm_player, g in snaps.groupby("NormPlayer"):
        canonical = fallback_by_norm.get(norm_player)
        if canonical is None:
            continue
        g = g.sort_values("SnapshotDate")
        if len(g) < MIN_SNAPSHOTS_REQUIRED:
            continue
        on_first_day = g["SnapshotDate"] == first_date
        start_adp = g.loc[on_first_day, "ADP"].iloc[0] if on_first_day.any() else SYNTHETIC_START_ADP
        end_adp = g["ADP"].iloc[-1]
        rows.append({"Player": canonical, "adp_momentum": start_adp - end_adp, "n_snapshots": len(g)})

    result = pd.DataFrame(rows, columns=["Player", "adp_momentum", "n_snapshots"])
    result.to_csv(output_csv_path, index=False)
    print(f"[compute_adp_momentum_fallback] wrote {len(result)} rows to {output_csv_path} "
          f"(of {len(fallback_targets)} fallback targets)")
    return result


if __name__ == "__main__":
    compute_fallback_momentum()

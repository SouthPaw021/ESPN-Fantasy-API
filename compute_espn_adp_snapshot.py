## Weekly (intended cadence -- see the Windows Task Scheduler setup, not
## enforced by this script itself) snapshot of ESPN's own crowd-sourced ADP
## (Average Draft Position) for the top 300 rosterable players, accumulating
## into a running historical dataset. The eventual goal is enough seasons of
## this to replace the current live ADP momentum setup
## (compute_live_adp_momentum.py's FFC source + the FootballGuys fallback),
## which mismatches both data source AND scoring format (FFC is confirmed
## Standard, this league plays PPR) and was only ever adopted because it was
## the sole option available in time to matter last preseason.
##
## Only collects within the same preseason window every other ADP feature in
## this pipeline uses (see _in_preseason_window, reusing
## compute_adp_momentum.py's own WINDOW_START_MD/DRAFT_DATES rather than
## re-declaring them) -- outside that window it's a clean no-op, not an
## error, so an unattended weekly scheduled run doesn't need to care what
## time of year it is.
##
## Field: player.ownership.averageDraftPosition, from the same kona_player_info
## view already used for projections/auction values (see espn_api.py's
## get_player_projections). NOT the same as draftRanksByRankType (ESPN's own
## editorial rank, which stays flatly rank=1/2/3... regardless of requested
## sort format). averageDraftPosition is also identical regardless of
## requested sort format -- one fixed number per player, not recomputed per
## scoring format on request.
##
## Empirically leans PPR, though unconfirmed/unlabeled anywhere: compared
## against FFC's confirmed-Standard ADP, pure rushers with light receiving
## work are penalized hard here (Derrick Henry: Standard ADP 7.3 vs this
## field's 19.06), while receiving-involved players are favored (Justin
## Jefferson: Standard 15.5 vs 12.12 here) -- the textbook PPR signature.
##
## sortDraftRanks in the request filter controls RESULT ORDER only, not which
## ADP value is returned -- a single request with limit=300 returns exactly
## 300 players, every one with a real (nonzero) averageDraftPosition, so no
## pagination is needed.
import io
import os
import json
import smtplib
import time
import traceback
from email.mime.text import MIMEText

import requests
import pandas as pd
from dotenv import load_dotenv

from compute_adp_momentum import DRAFT_DATES, WINDOW_START_MD

load_dotenv()

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_CSV = os.path.join(REPO_DIR, "data", "espn_adp_snapshots.csv")

LEAGUE_ID = os.getenv("LEAGUE_ID")
ESPN_COOKIES = {"swid": os.getenv("SWID_COOKIE"), "espn_s2": os.getenv("ESPN_S2_COOKIES")}

# Gmail SMTP, app-password auth (a real account password won't work here).
# ALERT_EMAIL_TO defaults to the same address if not set separately.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO") or GMAIL_ADDRESS
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

KONA_PLAYER_INFO_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}?view=kona_player_info"

# Same convention as every other ADP/momentum script in this pipeline --
# K/D-ST pricing is trivial and near-constant, not worth tracking.
EXCLUDED_POSITIONS = {"K", "D/ST"}
POSITION_MAPPING = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

MAX_RANKED_PLAYERS = 300

REQUEST_TIMEOUT = 30
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5

SESSION = requests.Session()
SESSION.headers.update({
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36",
    "x-fantasy-platform": "kona-PROD-5cd7fbc8756f958a4250012b7badf69a8b3717d4",
    "x-fantasy-source": "kona",
})


def _send_failure_email(season, error):
    """Best-effort -- a broken alerting path must never mask the original
    failure. If credentials aren't set, or the send itself fails, this
    prints why and returns rather than raising, so the caller's own re-raise
    of the real error is always what surfaces."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[compute_espn_adp_snapshot] GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set in .env -- "
              "cannot send failure email, see README for setup")
        return
    body = (
        f"compute_espn_adp_snapshot.py failed for season {season} at "
        f"{pd.Timestamp.now()}.\n\n{''.join(traceback.format_exception(type(error), error, error.__traceback__))}"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"[ESPN ADP Snapshot] FAILED - season {season}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ALERT_EMAIL_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"[compute_espn_adp_snapshot] sent failure alert to {ALERT_EMAIL_TO}")
    except Exception as email_exc:
        print(f"[compute_espn_adp_snapshot] failed to send alert email: {str(email_exc)[:300]}")


def _in_preseason_window(season, today=None):
    """Same window as every other ADP feature in this pipeline: training-camp-
    open (mid-July) through this league's actual draft date. Reused directly
    from compute_adp_momentum.py's WINDOW_START_MD/DRAFT_DATES so the two
    can't silently drift apart. A season with no known draft date yet is
    treated as always in-window -- better to over-collect than silently skip."""
    today = today or pd.Timestamp.now().normalize()
    window_start = pd.Timestamp(f"{season}-{WINDOW_START_MD}")
    draft_date = pd.Timestamp(DRAFT_DATES[season]) if season in DRAFT_DATES else None
    if today < window_start:
        return False
    if draft_date is not None and today > draft_date:
        return False
    return True


def _get_with_retries(url, headers):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, cookies=ESPN_COOKIES, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                print(f"    retry {attempt + 1}/{MAX_RETRIES - 1} after {wait}s ({e.__class__.__name__})")
                time.sleep(wait)
    raise last_exc


def _fetch_top_players(season, league_id, limit=MAX_RANKED_PLAYERS):
    """One request, no pagination needed -- a single call with limit=300
    returns exactly that many players, every one with a real ADP."""
    headers = dict(SESSION.headers)
    headers["x-fantasy-filter"] = json.dumps({
        "players": {
            "filterSlotIds": {"value": list(range(20))},
            "sortDraftRanks": {"sortPriority": 2, "sortAsc": True, "value": "PPR"},
            "limit": limit,
            "offset": 0,
        }
    })
    url = KONA_PLAYER_INFO_URL.format(season=season, league_id=league_id)
    resp = _get_with_retries(url, headers)
    return resp.json().get("players", [])


def compute_espn_adp_snapshot(season, output_csv_path=DEFAULT_OUTPUT_CSV, skip_window_check=False):
    if not skip_window_check and not _in_preseason_window(season):
        print(f"[compute_espn_adp_snapshot] outside the {season} preseason window "
              f"({season}-{WINDOW_START_MD} through {DRAFT_DATES.get(season, 'unknown draft date')}) -- skipping, "
              f"nothing written. Pass skip_window_check=True to override.")
        return None

    players = _fetch_top_players(season, LEAGUE_ID)
    print(f"[compute_espn_adp_snapshot] fetched {len(players)} players")

    snapshot_date = pd.Timestamp.now().normalize()
    rows = []
    for entry in players:
        p = entry["player"]
        position = POSITION_MAPPING.get(p.get("defaultPositionId"))
        if position in EXCLUDED_POSITIONS:
            continue
        adp = p.get("ownership", {}).get("averageDraftPosition")
        if not adp:
            continue
        rows.append({
            "SnapshotDate": snapshot_date, "Year": season, "Player ID": p["id"],
            "Player": p["fullName"], "Position": position, "ADP": adp,
        })

    new_rows = pd.DataFrame(rows).sort_values("ADP")
    print(f"[compute_espn_adp_snapshot] {len(new_rows)} non-K/D-ST players with real ADP "
          f"(top: {new_rows.iloc[0]['Player']} @ {new_rows.iloc[0]['ADP']})")

    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    if os.path.exists(output_csv_path):
        existing = pd.read_csv(output_csv_path, parse_dates=["SnapshotDate"])
        # One snapshot per calendar day per player -- re-running same-day
        # updates that day's row instead of accumulating duplicates.
        existing = existing[~((existing["SnapshotDate"] == snapshot_date) & (existing["Year"] == season))]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined = combined.sort_values(["Year", "SnapshotDate", "ADP"])
    combined.to_csv(output_csv_path, index=False)

    n_dates = combined[combined["Year"] == season]["SnapshotDate"].nunique()
    print(f"[compute_espn_adp_snapshot] wrote {len(combined)} total rows to {output_csv_path} "
          f"({n_dates} snapshot date(s) accumulated for {season})")
    return combined


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=pd.Timestamp.now().year,
                         help="Season to snapshot (default: current calendar year)")
    args = parser.parse_args()
    try:
        compute_espn_adp_snapshot(args.season)
    except Exception as exc:
        print(f"[compute_espn_adp_snapshot] FAILED: {str(exc)[:300]}")
        _send_failure_email(args.season, exc)
        raise  # still exit non-zero -- Task Scheduler's own history stays accurate too

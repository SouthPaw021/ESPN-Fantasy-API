## Real NFL Draft capital for every player rookie_flags.csv flags as
## is_rookie=True, refining the model's crude binary is_rookie feature with
## how early that rookie was actually drafted -- a first-round pick and an
## undrafted free agent otherwise get the same "+1" even though draft slot
## is one of the strongest real-world signals of expected opportunity.
##
## Produces two candidate encodings for fit_regression_model.py's ablation
## to pick between:
##   draft_round, draft_pick   -- this player's own real draft slot.
##   rookie_position_rank      -- how many same-position players were
##                                 drafted at or before this player's own
##                                 pick, THAT SEASON (controls for
##                                 position-specific draft-capital norms --
##                                 round 3 means something different for a
##                                 QB than a RB).
##
## draft_round/draft_pick come from ESPN's own public athlete API
## (site.web.api.espn.com/.../athletes/{id}), not a third-party dataset --
## it carries a displayDraft field ("2020: Rd 1, Pk 22 (MIN)") for drafted
## players and no field at all for genuine UDFAs. Queryable directly by the
## same Player ID already used as the join key throughout this pipeline, so
## no name-matching needed.
##
## rookie_position_rank does use nflverse's draft_picks.csv (public,
## GitHub-hosted, covers every draft 1980-2026) but only in aggregate, to
## count same-position picks before a given overall pick number in a given
## season -- never matched to our own players by name, since our player's
## real pick number already comes from ESPN.
import io
import os
import re
import time
import requests
import pandas as pd

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOKIE_CSV = os.path.join(REPO_DIR, "data", "rookie_flags.csv")
DEFAULT_DRAFT_CSV = os.path.join(REPO_DIR, "..", "Historical_Draft_Results.csv")
DEFAULT_PROJECTIONS_CSV = os.path.join(REPO_DIR, "..", "Projections.csv")
DEFAULT_OUTPUT_CSV = os.path.join(REPO_DIR, "data", "rookie_draft_capital.csv")

ATHLETE_URL = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{player_id}"
NFLVERSE_DRAFT_PICKS_URL = "https://github.com/nflverse/nflverse-data/releases/download/draft_picks/draft_picks.csv"

# Off-scale sentinels for confirmed UDFAs -- same convention as
# compute_adp_momentum.py's SYNTHETIC_START_ADP: deliberately worse than any
# real value, not a guessed one. A real draft runs ~257-262 picks and no
# single position sees more than ~45-50 picks in a class.
SENTINEL_UNDRAFTED_PICK = 300
SENTINEL_UNDRAFTED_ROUND = 8
SENTINEL_UNDRAFTED_POSITION_RANK = 60

DRAFT_RE = re.compile(r"(\d{4}):\s*Rd\s*(\d+),\s*Pk\s*(\d+)", re.IGNORECASE)

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 0.5
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


def _fetch_draft_pick(player_id):
    """Returns (draft_season, draft_round, draft_pick) or (None, None, None)
    for a confirmed UDFA (athlete found, no displayDraft field). Raises on a
    genuine fetch failure (bad id, network error) -- caller distinguishes
    "confirmed UDFA" from "couldn't check" rather than conflating the two."""
    resp = _get_with_retries(ATHLETE_URL.format(player_id=int(player_id)))
    athlete = resp.json().get("athlete", {})
    display_draft = athlete.get("displayDraft")
    if not display_draft:
        return None, None, None
    match = DRAFT_RE.search(display_draft)
    if not match:
        return None, None, None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _load_position_lookup(draft_csv_path, projections_csv_path):
    """(Year, Player ID) -> Position for historical rookie-years (from
    Historical_Draft_Results.csv), falling back to Player -> Position from
    Projections.csv (name-keyed, since it has no ID column -- same limitation
    as predict_auction_prices.py's own rookie merge) for the upcoming
    season, not yet in Historical_Draft_Results.csv."""
    historical = pd.read_csv(draft_csv_path, keep_default_na=False)
    by_id = {(int(row["Year"]), int(row["Player ID"])): row["Position"] for _, row in historical.iterrows()}
    projections = pd.read_csv(projections_csv_path)
    by_name = dict(zip(projections["Player"], projections["Position"]))
    return by_id, by_name


def _fetch_nflverse_draft_board():
    resp = _get_with_retries(NFLVERSE_DRAFT_PICKS_URL)
    return pd.read_csv(io.StringIO(resp.text))


def _build_position_rank_counter(draft_board):
    """{(season, position): sorted [pick, pick, ...]} for bisect-based
    counting -- how many same-position picks in that season landed at or
    before a given pick number."""
    counters = {}
    for (season, position), group in draft_board.groupby(["season", "position"]):
        counters[(season, position)] = sorted(group["pick"].tolist())
    return counters


def _position_rank(counters, season, position, pick):
    import bisect
    picks = counters.get((season, position))
    if picks is None:
        return None
    return bisect.bisect_right(picks, pick)


def _build_round_start_lookup(draft_board):
    """{(season, round): overall pick number of that round's first pick} --
    needed to convert a real overall pick into a within-round pick (the X
    in "Rd.X" draft-capital notation). Round sizes vary year to year with
    compensatory picks, so a fixed 32-per-round assumption would misnumber
    every later-round pick (round 2 alone ran to pick 39 in the 2025 class,
    7 comp picks deep)."""
    return draft_board.groupby(["season", "round"])["pick"].min().to_dict()


def _pick_in_round(round_start_lookup, season, round_, overall_pick):
    start = round_start_lookup.get((season, round_))
    if start is None:
        return None
    return overall_pick - start + 1


def compute_rookie_draft_capital(
    rookie_csv_path=DEFAULT_ROOKIE_CSV,
    draft_csv_path=DEFAULT_DRAFT_CSV,
    projections_csv_path=DEFAULT_PROJECTIONS_CSV,
    output_csv_path=DEFAULT_OUTPUT_CSV,
):
    rookies = pd.read_csv(rookie_csv_path)
    rookies = rookies[rookies["is_rookie"] == True].copy()
    print(f"[compute_rookie_draft_capital] {len(rookies)} rookie-year rows to look up "
          f"({rookies['Player ID'].nunique()} unique players)")

    position_by_id, position_by_name = _load_position_lookup(draft_csv_path, projections_csv_path)
    draft_board = _fetch_nflverse_draft_board()
    position_rank_counters = _build_position_rank_counter(draft_board)
    round_start_lookup = _build_round_start_lookup(draft_board)
    print(f"[compute_rookie_draft_capital] nflverse draft board loaded ({len(draft_board)} picks, "
          f"seasons {draft_board['season'].min()}-{draft_board['season'].max()})")

    rows = []
    for _, r in rookies.iterrows():
        player_id = r["Player ID"]
        year = int(r["Year"])
        position = position_by_id.get((year, int(player_id))) or position_by_name.get(r["Player"])

        try:
            draft_season, draft_round, draft_pick = _fetch_draft_pick(player_id)
        except Exception as e:
            print(f"  {r['Player']} (id={player_id}): skipped ({str(e)[:200]})")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if draft_pick is None:
            # Confirmed UDFA (or an unparseable displayDraft string, treated
            # the same way) -- deliberate off-scale sentinel values, not
            # missing ones. draft_pick_in_round stays genuinely null since no
            # within-round pick applies -- display logic should show
            # "Undrafted", not a fabricated "Rd 8, Pick X".
            rows.append({
                "Year": year, "Player": r["Player"], "Player ID": player_id, "Position": position,
                "draft_round": SENTINEL_UNDRAFTED_ROUND, "draft_pick": SENTINEL_UNDRAFTED_PICK,
                "draft_pick_in_round": None,
                "rookie_position_rank": SENTINEL_UNDRAFTED_POSITION_RANK,
                "rookie_draft_pick_available": True,
            })
        else:
            pos_rank = _position_rank(position_rank_counters, draft_season, position, draft_pick) if position else None
            pick_in_round = _pick_in_round(round_start_lookup, draft_season, draft_round, draft_pick)
            rows.append({
                "Year": year, "Player": r["Player"], "Player ID": player_id, "Position": position,
                "draft_round": draft_round, "draft_pick": draft_pick,
                "draft_pick_in_round": pick_in_round,
                "rookie_position_rank": pos_rank if pos_rank is not None else SENTINEL_UNDRAFTED_POSITION_RANK,
                "rookie_draft_pick_available": True,
            })
        time.sleep(REQUEST_DELAY_SECONDS)

    result = pd.DataFrame(rows, columns=[
        "Year", "Player", "Player ID", "Position", "draft_round", "draft_pick", "draft_pick_in_round",
        "rookie_position_rank", "rookie_draft_pick_available",
    ])
    result.to_csv(output_csv_path, index=False)
    undrafted = (result["draft_pick"] == SENTINEL_UNDRAFTED_PICK).sum()
    no_position_rank = (result["rookie_position_rank"] == SENTINEL_UNDRAFTED_POSITION_RANK).sum() - undrafted
    print(f"[compute_rookie_draft_capital] wrote {len(result)} rows to {output_csv_path} "
          f"({undrafted} confirmed UDFA, {no_position_rank} drafted but position-rank lookup failed, "
          f"{len(rookies) - len(result)} lookups failed entirely)")
    return result


if __name__ == "__main__":
    compute_rookie_draft_capital()

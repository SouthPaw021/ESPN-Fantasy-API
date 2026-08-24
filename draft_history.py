import json
import os

import requests
import pandas as pd
from espn_api import (
    get_draft_details, get_player_info, get_team_info,
    get_waiver_transactions, get_trade_transactions, get_rookie_indicator,
    get_current_rosters, get_player_projections, _fetch_playercard_transactions,
)
from static import years, position_mapping, league_teams
import os
from dotenv import load_dotenv

load_dotenv()

LEAGUE_ID = os.getenv('LEAGUE_ID')
SWID_COOKIE = os.getenv('SWID_COOKIE')
ESPN_S2_COOKIES = os.getenv('ESPN_S2_COOKIES')
espn_cookies = {"swid": SWID_COOKIE, "espn_s2": ESPN_S2_COOKIES}

DEFAULT_DRAFT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Historical_Draft_Results.csv')
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

WAIVER_EVENT_COLS = ['Year', 'Owner', 'Player', 'Player ID', 'bid_amount', 'Week']
TRADE_EVENT_COLS = ['Year', 'Owner', 'Player', 'Player ID', 'Week', 'Traded With', 'Also Received', 'Sent Out']
ROOKIE_FLAG_COLS = ['Year', 'Player', 'Player ID', 'is_rookie']
DRAFT_ROW_COLS = ['Owner', 'Player', 'Player ID', 'Team', 'Position', 'Pick', 'Kept', 'Paid', 'Year']

CACHE_SUBDIR = 'draft_history_cache'
CACHE_FILES = {
    'drafts': 'drafts.csv',
    'waivers': 'waiver_events.csv',
    'trades': 'trade_events.csv',
    'rookies': 'rookie_flags.csv',
    'meta': 'meta.json',
}


def _pull_draft_year(year):
    """Pass-1 pull for a single year: that year's draft, merged with player
    and team info, reshaped into the row/column layout the rest of this
    module expects. Returns (draft_rows_df, draft_ids, name_updates) --
    draft_ids and name_updates are what pass 2 (transactions/rookie flags)
    needs from this year, kept separate so callers don't re-derive them."""
    print(year)
    draft_df = get_draft_details(LEAGUE_ID, year, espn_cookies)
    player_df = get_player_info(year, espn_cookies)
    team_df = get_team_info(year)

    df2 = pd.merge(draft_df, player_df, how="inner", left_on="playerId", right_on="player_id")
    final_df = pd.merge(df2, team_df, how="inner", left_on="proTeamId", right_on="team_id")

    league_draft = final_df.replace({"defaultPositionId": position_mapping})
    league_draft_info = league_draft.replace({"teamId": league_teams})
    league_draft_final = league_draft_info[['teamId', 'fullName', 'playerId', 'abbrev', 'defaultPositionId', 'overallPickNumber', 'keeper', 'bidAmount']].copy()
    league_draft_final.rename(columns={'overallPickNumber': 'Pick', 'teamId': 'Owner', 'keeper': 'Kept', 'bidAmount': 'Paid',
                                        'defaultPositionId': 'Position', 'fullName': 'Player', 'abbrev': 'Team', 'playerId': 'Player ID'}, inplace=True)
    league_draft_final['Year'] = year
    # Converted here (not left for the caller to do once on the combined
    # dataframe) so a cached run's CSV round-trip and a freshly-pulled run's
    # in-memory frame always carry the same 'K'/'' string, never a raw
    # True/False that CSV text would otherwise blur into ambiguity.
    league_draft_final['Kept'] = league_draft_final['Kept'].replace({True: 'K', False: ''})

    draft_ids = draft_df['playerId'].tolist()
    name_updates = dict(zip(player_df['player_id'], player_df['fullName']))
    return league_draft_final, draft_ids, name_updates


def _pull_transactions_year(year, player_ids, rookie_target_year, rookie_target_ids, player_id_to_name):
    """Pass-2 pull for a single year: waiver claims, trades, and (for the
    upcoming draft class one year later) rookie flags, all sourced from one
    shared playercard fetch. Returns (waiver_df, trade_df, rookie_rows).

    Week (ESPN's own scoringPeriodId, not a calendar date) is kept on both
    event types so downstream consumers (Current Rosters' Transaction
    Detail column) can show roughly when an acquisition happened.

    get_trade_transactions returns one row per player MOVEMENT (a
    from_team_id/to_team_id leg), not one row per owner's participation --
    a multi-player trade has several legs sharing the same trade_id, and a
    3+ team trade can have legs pointing at more than one other owner. For
    each leg (one player arriving at one owner), this resolves that owner's
    full stake in the trade: 'Traded With' (the other owner(s) they dealt
    with), 'Also Received' (other players that also arrived at this SAME
    owner), and 'Sent Out' (players that left FROM this owner, wherever
    they went)."""
    playercard_data = _fetch_playercard_transactions(LEAGUE_ID, year, espn_cookies, player_ids)

    waiver_df = get_waiver_transactions(LEAGUE_ID, year, espn_cookies, player_ids, players_data=playercard_data)
    if len(waiver_df):
        waiver_df = waiver_df.copy()
        waiver_df['Player'] = waiver_df['player_id'].map(player_id_to_name)
        waiver_df['Player ID'] = waiver_df['player_id']
        waiver_df['Owner'] = waiver_df['team_id'].replace(league_teams)
        waiver_df['Year'] = year
        waiver_df['Week'] = waiver_df['week']
        waiver_df = waiver_df[WAIVER_EVENT_COLS]
    else:
        waiver_df = pd.DataFrame(columns=WAIVER_EVENT_COLS)

    raw_trade_df = get_trade_transactions(LEAGUE_ID, year, espn_cookies, player_ids, players_data=playercard_data)
    if len(raw_trade_df):
        raw_trade_df = raw_trade_df.copy()
        raw_trade_df['Player'] = raw_trade_df['player_id'].map(player_id_to_name)
        raw_trade_df['From Owner'] = raw_trade_df['from_team_id'].replace(league_teams)
        raw_trade_df['To Owner'] = raw_trade_df['to_team_id'].replace(league_teams)

        received_by = raw_trade_df.groupby(['trade_id', 'To Owner'])['Player'].apply(list).to_dict()
        sent_by = raw_trade_df.groupby(['trade_id', 'From Owner'])['Player'].apply(list).to_dict()
        counterparts_by = raw_trade_df.groupby(['trade_id', 'To Owner'])['From Owner'].apply(
            lambda s: sorted(set(s))
        ).to_dict()

        def _trade_leg_row(leg):
            trade_id, owner, player = leg['trade_id'], leg['To Owner'], leg['Player']
            also_received = [p for p in received_by.get((trade_id, owner), []) if p is not None and p != player]
            sent_out = [p for p in sent_by.get((trade_id, owner), []) if p is not None]
            counterparts = [o for o in counterparts_by.get((trade_id, owner), []) if o is not None]
            return {
                'Year': year,
                'Owner': owner,
                'Player': player,
                'Player ID': leg['player_id'],
                'Week': leg['week'],
                'Traded With': ', '.join(counterparts),
                'Also Received': ', '.join(also_received),
                'Sent Out': ', '.join(sent_out),
            }

        trade_df = pd.DataFrame([_trade_leg_row(leg) for _, leg in raw_trade_df.iterrows()])
        trade_df = trade_df[TRADE_EVENT_COLS]
    else:
        trade_df = pd.DataFrame(columns=TRADE_EVENT_COLS)

    rookie_rows = []
    if int(year) >= 2018:
        rookie_lookup = get_rookie_indicator(
            LEAGUE_ID, year, espn_cookies, rookie_target_ids, players_data=playercard_data
        )
        rookie_rows = [
            {'Year': rookie_target_year, 'Player': player_id_to_name.get(pid), 'Player ID': pid, 'is_rookie': rookie_lookup.get(pid)}
            for pid in rookie_target_ids
            if player_id_to_name.get(pid)
        ]

    return waiver_df, trade_df, rookie_rows


def _load_cache(cache_dir):
    """Returns the cached (drafts_df, waiver_df, trade_df, rookie_df) from
    the last full pull, or None if no cache exists yet or it was built for a
    different `years` list (e.g. static.py's `years` grew after a season
    rolled over)."""
    meta_path = os.path.join(cache_dir, CACHE_FILES['meta'])
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        meta = json.load(f)
    if meta.get('years') != years:
        return None

    try:
        drafts_df = pd.read_csv(os.path.join(cache_dir, CACHE_FILES['drafts']), keep_default_na=False)
        waiver_df = pd.read_csv(os.path.join(cache_dir, CACHE_FILES['waivers']), keep_default_na=False)
        trade_df = pd.read_csv(os.path.join(cache_dir, CACHE_FILES['trades']), keep_default_na=False)
        rookie_df = pd.read_csv(os.path.join(cache_dir, CACHE_FILES['rookies']), keep_default_na=False)
    except FileNotFoundError:
        return None

    # keep_default_na=False above keeps a genuinely blank 'Kept'/Position
    # cell as '' instead of NaN; 'Paid'/'bid_amount'/'is_rookie' still need
    # to come back as real numbers, not the strings CSV round-tripped them as.
    drafts_df['Paid'] = pd.to_numeric(drafts_df['Paid'], errors='coerce')
    if len(waiver_df):
        waiver_df['bid_amount'] = pd.to_numeric(waiver_df['bid_amount'], errors='coerce')
        waiver_df['Week'] = pd.to_numeric(waiver_df['Week'], errors='coerce')
    if len(trade_df):
        trade_df['Week'] = pd.to_numeric(trade_df['Week'], errors='coerce')
    if len(rookie_df):
        rookie_df['is_rookie'] = rookie_df['is_rookie'].map({'True': True, 'False': False, True: True, False: False})

    return drafts_df, waiver_df, trade_df, rookie_df


def _save_cache(cache_dir, drafts_df, waiver_df, trade_df, rookie_df):
    os.makedirs(cache_dir, exist_ok=True)
    drafts_df.to_csv(os.path.join(cache_dir, CACHE_FILES['drafts']), index=False)
    waiver_df.to_csv(os.path.join(cache_dir, CACHE_FILES['waivers']), index=False)
    trade_df.to_csv(os.path.join(cache_dir, CACHE_FILES['trades']), index=False)
    rookie_df.to_csv(os.path.join(cache_dir, CACHE_FILES['rookies']), index=False)
    with open(os.path.join(cache_dir, CACHE_FILES['meta']), 'w') as f:
        json.dump({'years': years}, f)


def compute_years_kept_streak(all_drafts_df, id_col='Player ID'):
    """Consecutive years each player has been kept, as of each row.

    Groups by player ID only (not player+owner), so a trade doesn't break
    the streak -- only a non-keeper year does: kept years carry through a
    trade but reset once the player re-enters the draft pool (drafted
    fresh, Kept != 'K'). A common keeper-league convention, not specific to
    any one league's rules. Returns a Series aligned to all_drafts_df's index.

    Grouped by id_col (ESPN's stable numeric player ID), not the 'Player'
    display name -- ESPN's fullName can drift a suffix between seasons,
    which would otherwise silently split one player's real history into two
    disconnected identities and reset their streak to 0.
    """
    results = {}
    for player_id, group in all_drafts_df.groupby(id_col):
        group = group.sort_values('Year')
        last_year = None
        kept_streak = 0
        for idx, row in group.iterrows():
            if row['Kept'] == 'K':
                if last_year is not None and row['Year'] == last_year + 1:
                    kept_streak += 1
                else:
                    kept_streak = 1
                last_year = row['Year']
            else:
                kept_streak = 0
                last_year = row['Year']
            results[idx] = kept_streak

    return pd.Series(results).reindex(all_drafts_df.index)


def refresh_draft_history(draft_csv_path=DEFAULT_DRAFT_CSV, data_dir=DEFAULT_DATA_DIR, rebuild_cache=False):
    """Pulls every season's draft/keeper data plus waiver, trade, and rookie
    history, computes Years Kept for each row, and writes
    Historical_Draft_Results.csv (draft_csv_path) plus data/waiver_events.csv,
    data/trade_events.csv, and data/rookie_flags.csv (data_dir) -- the first
    two so keeper_roster_snapshot.py can extend this same chain walk one more
    (hypothetical) year forward without re-fetching a decade of transaction
    history. rookie_flags.csv covers draft years 2019+ plus the upcoming
    season (see get_rookie_indicator in espn_api.py) -- ESPN's
    kona_playercard has no usable stats data for 2016/2017, so 2016-2018
    draft years get no rookie flag.

    Deliberately does NOT compute Franchise Eligible/Keeper Price here --
    those encode this league's specific keeper rules and live in
    keeper_history.py's refresh_keeper_columns instead, called separately
    right after this (see excel_export.py), so this function stays reusable
    for any league's ESPN draft history, keeper rules or not.

    A completed season's draft recap data never changes, so the whole pull
    is cached as one unit under data/draft_history_cache/ and reused as-is
    on every run within the same offseason: if the cache already covers
    exactly the years in static.py's `years` list, this skips ESPN's API
    entirely. The cache self-invalidates once `years` grows (a new season's
    draft happens and the boundary moves forward) -- pass rebuild_cache=True
    to force a rebuild sooner, e.g. if ESPN retroactively corrects something
    or the upcoming season's rookie-flag target list needs refreshing.
    """
    cache_dir = os.path.join(data_dir, CACHE_SUBDIR)

    cached = None if rebuild_cache else _load_cache(cache_dir)
    if cached is not None:
        all_drafts_df, all_waiver_events, all_trade_events, all_rookie_flags = cached
    else:
        # Create empty dataframes to append to
        all_drafts_df = pd.DataFrame()
        all_waiver_events = pd.DataFrame(columns=WAIVER_EVENT_COLS)
        all_trade_events = pd.DataFrame(columns=TRADE_EVENT_COLS)
        all_rookie_flags = pd.DataFrame(columns=ROOKIE_FLAG_COLS)

        # Pass 1: fetch every year's draft data, and remember each year's
        # drafted player ids (+ a global player_id -> name map) for pass 2.
        draft_ids_by_year = {}
        player_id_to_name = {}
        drafts_parts = []
        for year in years:
            draft_rows, draft_ids_by_year[year], name_updates = _pull_draft_year(year)
            drafts_parts.append(draft_rows)
            player_id_to_name.update(name_updates)  # chronological, oldest first
        all_drafts_df = pd.concat(drafts_parts, ignore_index=True)

        # Pass 2: waiver claims, trades, and rookie flags. No transaction
        # data exists for pre-2018 seasons; the pullers just return empty
        # rows for those years.
        #
        # Scoped to the union of that year's draftees AND next year's (not
        # all of player_df -- for a past season, get_player_info returns
        # ESPN's entire all-time player database, blowing out the
        # X-Fantasy-Filter header). Next year's draftees have to be included
        # too: a player kept next year without ever being drafted this year
        # (added by waiver mid-season) needs THIS year's transaction log
        # checked for them.
        #
        # The most recent year in `years` has no completed "next year" draft
        # to union against yet -- falls back to the live upcoming-season
        # roster + full projected draft pool instead. Only pulled here,
        # inside the cache-miss branch, since it depends on the upcoming
        # season's live (still-shifting) state.
        upcoming_year = int(years[-1]) + 1
        upcoming_roster_df = get_current_rosters(LEAGUE_ID, upcoming_year, espn_cookies)
        upcoming_roster_ids = upcoming_roster_df['player_id'].tolist()
        player_id_to_name.update(dict(zip(upcoming_roster_df['player_id'], upcoming_roster_df['fullName'])))

        upcoming_projected_ids = get_player_projections(LEAGUE_ID, upcoming_year, espn_cookies)['player_id'].tolist()
        upcoming_player_info_df = get_player_info(upcoming_year, espn_cookies)
        player_id_to_name.update(dict(zip(upcoming_player_info_df['player_id'], upcoming_player_info_df['fullName'])))

        waiver_parts = []
        trade_parts = []
        rookie_parts = []
        for i, year in enumerate(years):
            if i + 1 < len(years):
                next_year_ids = draft_ids_by_year[years[i + 1]]
            else:
                next_year_ids = list(set(upcoming_roster_ids) | set(upcoming_projected_ids))
            player_ids = list(set(draft_ids_by_year[year]) | set(next_year_ids))

            if int(year) >= 2018:
                rookie_target_year, rookie_target_ids = (
                    (years[i + 1], draft_ids_by_year[years[i + 1]]) if i + 1 < len(years)
                    else (upcoming_year, upcoming_projected_ids)
                )
            else:
                rookie_target_year, rookie_target_ids = None, []

            waiver_df, trade_df, rookie_rows = _pull_transactions_year(
                year, player_ids, rookie_target_year, rookie_target_ids, player_id_to_name
            )
            waiver_parts.append(waiver_df)
            trade_parts.append(trade_df)
            rookie_parts.extend(rookie_rows)

        all_waiver_events = pd.concat(waiver_parts, ignore_index=True) if waiver_parts else all_waiver_events
        all_trade_events = pd.concat(trade_parts, ignore_index=True) if trade_parts else all_trade_events
        all_rookie_flags = pd.DataFrame(rookie_parts, columns=ROOKIE_FLAG_COLS)

        _save_cache(cache_dir, all_drafts_df, all_waiver_events, all_trade_events, all_rookie_flags)
        print(f"[draft_history] built draft-history cache for {years[0]}-{years[-1]} ({cache_dir})")

    # Reorder columns and format/replace values
    all_drafts_df['Year'] = all_drafts_df['Year'].astype(int)
    # Both event tables carry Year as the raw string from the `years` loop;
    # cast to int so their (Player, Year) lookup keys actually match
    # all_drafts_df's -- otherwise keeper_history.py's later pass (reading
    # these same CSVs back off disk) would silently miss every lookup.
    all_waiver_events['Year'] = all_waiver_events['Year'].astype(int)
    all_trade_events['Year'] = all_trade_events['Year'].astype(int)

    # Sort the DataFrame by 'Player' and 'Year'
    all_drafts_df = all_drafts_df.sort_values(['Player', 'Year']).reset_index(drop=True)

    all_drafts_df['Years Kept'] = compute_years_kept_streak(all_drafts_df)

    # Reorder w/ new column -- Franchise Eligible/Keeper Price get added
    # later by keeper_history.py's refresh_keeper_columns, not here.
    all_drafts_df = all_drafts_df[['Year', 'Owner', 'Player', 'Player ID', 'Team', 'Position', 'Kept', 'Years Kept', 'Paid', 'Pick']]

    # Sort by Paid, Kept, Owner, and then Year last
    all_drafts_df.sort_values(by=['Year', 'Owner', 'Kept', 'Paid'], ascending=[False, True, False, False], inplace=True)

    # Format 'Paid' as currency
    all_drafts_df['Paid'] = all_drafts_df['Paid'].apply(lambda x: "${:,.0f}".format(x))

    # Waiver/trade events, saved for reuse by the keeper roster-snapshot tool so
    # it doesn't have to re-fetch a decade of transaction history just to extend
    # this same chain walk one more (hypothetical, not-yet-drafted) year forward.
    os.makedirs(data_dir, exist_ok=True)
    all_waiver_events.to_csv(os.path.join(data_dir, 'waiver_events.csv'), index=False)
    all_trade_events.to_csv(os.path.join(data_dir, 'trade_events.csv'), index=False)
    all_rookie_flags.to_csv(os.path.join(data_dir, 'rookie_flags.csv'), index=False)

    # Export to CSV
    all_drafts_df.to_csv(draft_csv_path, index=False)
    print(f"[draft_history] wrote {len(all_drafts_df)} rows to {draft_csv_path}")


if __name__ == "__main__":
    import sys
    refresh_draft_history(rebuild_cache='--rebuild-cache' in sys.argv)

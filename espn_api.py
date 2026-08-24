## Getting Draft Data and Player Season Projections via ESPN Fantasy Football API v3
import requests
import pandas as pd
import os
import json
from dotenv import load_dotenv
from static import projection_year

load_dotenv()

# Get draft details
def get_draft_details(league_id, season_id, espn_cookies):
    headers  = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        }
    
    # Got this url from the network tab in chrome and worked for older season
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{}?view=mDraftDetail&view=mSettings&view=mTeam&view=modular&view=mNav&seasonId={}".format(league_id, season_id)
    r = requests.get(url,
                    headers=headers,
                    cookies=espn_cookies)
    espn_raw_data = r.json()
    espn_draft_detail = espn_raw_data[0]
    draft_picks = espn_draft_detail['draftDetail']['picks']
    df = pd.DataFrame(draft_picks)
    
    # Get only columns we need in draft detail
    draft_df = df[['overallPickNumber', 'playerId', 'teamId', 'bidAmount', 'keeper']].copy()
    return draft_df

# Get player info
def get_player_info(season_id, espn_cookies):
    custom_headers  = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        'x-fantasy-filter': '{"filterActive":null}',
        'x-fantasy-platform': 'kona-PROD-1dc40132dc2070ef47881dc95b633e62cebc9913',
        'x-fantasy-source': 'kona'
    }
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}/players?scoringPeriodId=0&view=players_wl".format(season_id)
    r = requests.get(url,
                    cookies=espn_cookies,
                    headers=custom_headers)
    player_data = r.json()
    df = pd.DataFrame(player_data)
    
    # Get only needed columns for players
    player_df = df[['defaultPositionId','fullName','id','proTeamId']].copy()
    
    # Rename in column
    player_df.rename(columns = {'id':'player_id'}, inplace = True)
    return player_df

# Get team info
def get_team_info(season_id):
    headers  = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        }
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}?view=proTeamSchedules_wl".format(season_id)
    r = requests.get(url,
                    headers=headers)
    team_data = r.json()
    team_names = team_data['settings']['proTeams']
    df = pd.DataFrame(team_names)
    # Get only needed columns for teams
    team_df = df[['id', 'abbrev']].copy()
    # Rename in column
    team_df.rename(columns = {'id':'team_id'}, inplace = True)
    return team_df

    # Get player projections
def get_player_projections(league_id, season_id, espn_cookies):
    projection_headers  = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        'x-fantasy-filter': '{"players":{"filterSlotIds":{"value":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,23,24]},"sortDraftRanks":{"sortPriority":2,"sortAsc":true,"value":"PPR"},"limit":350,"offset":0,"filterRanksForScoringPeriodIds":{"value":[1]},"filterRanksForRankTypes":{"value":["PPR"]}}}',
        'x-fantasy-platform': 'kona-PROD-5cd7fbc8756f958a4250012b7badf69a8b3717d4',
        'x-fantasy-source': 'kona'
    }
    
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}/segments/0/leagues/{}?view=kona_player_info".format(season_id, league_id)
    r = requests.get(url,
                    cookies=espn_cookies,
                    headers=projection_headers)
    projection_raw_data = r.json()
    projection_detail = projection_raw_data

    merged_data = []

    for i in range(350):
        player = projection_detail['players'][i]
        for stat_line in player['player']['stats']:
            if stat_line['id'] == '10' + projection_year:
                try:
                    values = [
                        player['id'],
                        player['draftAuctionValue'],
                        player['keeperValue'],
                        stat_line['appliedTotal'],
                        stat_line['appliedAverage']
                    ]
                    merged_data.append(values)
                    break
                except:
                    print("Failed to format data for: {}".format(player['fullName']))

    # Create a DataFrame from the merged_data list
    projections_df = pd.DataFrame(merged_data, columns=[
        'id',
        'draftAuctionValue',
        'keeperValue',
        'appliedTotal',
        'appliedAverage',
    ])

    # Rename in column
    projections_df.rename(columns = {'id':'player_id'}, inplace = True)
    return projections_df

# Every player's complete transaction history (draft, waiver/free-agent
# add/drop, trade) for a season, via the kona_playercard view (undocumented,
# found via network inspection), filtered to a specific player pool through
# an X-Fantasy-Filter header. Shared by get_waiver_transactions and
# get_trade_transactions so callers needing both only fetch once.
def _fetch_playercard_transactions(league_id, season_id, espn_cookies, player_ids):
    headers = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        'X-Fantasy-Filter': json.dumps({"players": {"filterIds": {"value": player_ids}}}),
    }
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}/segments/0/leagues/{}".format(season_id, league_id)
    r = requests.get(url,
                    headers=headers,
                    cookies=espn_cookies,
                    params={'view': 'kona_playercard', 'scoringPeriodId': 25})
    return r.json().get('players', [])


# Whether each player has NO real (statSourceId 0, i.e. actual accumulated
# stats rather than a projection) entry for the season immediately before
# season_id -- a reliable proxy for "first year in the league," since a true
# rookie or someone who didn't play the prior season won't have one. Shares
# the same kona_playercard payload as get_waiver_transactions /
# get_trade_transactions -- pass players_data in to avoid a second fetch.
def get_rookie_indicator(league_id, season_id, espn_cookies, target_player_ids, players_data=None):
    """Determines rookie status for target_player_ids going into season_id + 1's
    draft, based on whether each had real (statSourceId=0) NFL production
    DURING season_id. players_data must be a kona_playercard response queried
    in season_id's own context -- a historical-season query only ever
    returns that season's own stats, never a bundled prior-year view, so
    this has to be computed one year at a time walking forward, then
    applied to the NEXT year's draftees, not season_id's own draftees.

    Only reliable when season_id >= 2018: ESPN's kona_playercard has no
    usable stats data at all for 2016/2017.

    ESPN's batched responses OMIT a player entirely when they have no data
    for season_id, so this iterates target_player_ids directly and treats
    "missing from players_data" the same as "present with no real entry" --
    both mean is_rookie=True. Do not pre-filter target_player_ids to only
    those found in players_data.
    """
    if players_data is None:
        players_data = _fetch_playercard_transactions(league_id, season_id, espn_cookies, target_player_ids)

    real_stats_pids = set()
    for player in players_data:
        p = player.get('player', {})
        player_id = p.get('id')
        if player_id is None:
            continue
        stats = p.get('stats', [])
        has_real_stats = any(
            s.get('externalId') == str(season_id) and s.get('statSourceId') == 0
            for s in stats
        )
        if has_real_stats:
            real_stats_pids.add(player_id)
    return {pid: pid not in real_stats_pids for pid in target_player_ids}


# Get waiver claim / free agent pickup bid amounts for a set of players in a season.
# Needed for keeper cost basis: a regular keeper slot costs the higher of the
# player's drafted price or their waiver-claim price.
def get_waiver_transactions(league_id, season_id, espn_cookies, player_ids, players_data=None):
    if players_data is None:
        players_data = _fetch_playercard_transactions(league_id, season_id, espn_cookies, player_ids)

    # A WAIVER transaction (drop+add bundled) can appear under more than one
    # involved player's own transaction history - dedupe by the transaction's
    # own id so a single real acquisition isn't counted twice.
    seen_ids = set()
    rows = []
    for player in players_data:
        for t in player.get('transactions', []):
            if t.get('status') != 'EXECUTED' or t.get('type') not in ('WAIVER', 'FREEAGENT'):
                continue
            txn_id = t.get('id')
            if txn_id in seen_ids:
                continue
            seen_ids.add(txn_id)
            for item in (t.get('items') or []):
                if item.get('type') != 'ADD':
                    continue
                rows.append({
                    'player_id': item.get('playerId'),
                    'team_id': item.get('toTeamId'),
                    'week': t.get('scoringPeriodId'),
                    'bid_amount': t.get('bidAmount', 0),
                    'transaction_type': t.get('type'),
                })

    waiver_df = pd.DataFrame(rows, columns=['player_id', 'team_id', 'week', 'bid_amount', 'transaction_type'])
    return waiver_df


# Get every trade (player, from-team, to-team) for a set of players in a season.
# Needed to distinguish a franchise-eligibility-breaking trade acquisition from
# a clean draft-day origin. A trade's items carry the exact fromTeamId/toTeamId/
# playerId straight from ESPN - no roster-diff inference needed.
def get_trade_transactions(league_id, season_id, espn_cookies, player_ids, players_data=None):
    if players_data is None:
        players_data = _fetch_playercard_transactions(league_id, season_id, espn_cookies, player_ids)

    # The same trade (grouped by relatedTransactionId, or its own id if that's
    # absent) can appear under more than one involved player's own transaction
    # history - dedupe by trade id + player id so a single real movement isn't
    # counted twice.
    seen = set()
    rows = []
    for player in players_data:
        for t in player.get('transactions', []):
            if t.get('status') != 'EXECUTED':
                continue
            items = [item for item in (t.get('items') or []) if item.get('type') == 'TRADE']
            if not items:
                continue
            trade_id = t.get('relatedTransactionId') or t.get('id')
            for item in items:
                key = (trade_id, item.get('playerId'))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    'trade_id': trade_id,
                    'player_id': item.get('playerId'),
                    'from_team_id': item.get('fromTeamId'),
                    'to_team_id': item.get('toTeamId'),
                    'week': t.get('scoringPeriodId'),
                })

    trade_df = pd.DataFrame(rows, columns=['trade_id', 'player_id', 'from_team_id', 'to_team_id', 'week'])
    return trade_df


# Get every team's current roster, live -- who's actually rostered right now,
# not reconstructed from draft/transaction history. Each entry's
# acquisitionType ('DRAFT', 'TRADE', or 'ADD' for waiver/free-agent) is how
# ESPN itself tracks that player's most recent acquisition by their current
# team, straight from the source.
def get_current_rosters(league_id, season_id, espn_cookies):
    headers = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
    }
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}/segments/0/leagues/{}".format(season_id, league_id)
    r = requests.get(url,
                    headers=headers,
                    cookies=espn_cookies,
                    params={'view': ['mRoster', 'mTeam']})
    data = r.json()

    rows = []
    for team in data.get('teams', []):
        team_id = team.get('id')
        for entry in team.get('roster', {}).get('entries', []):
            player = entry.get('playerPoolEntry', {}).get('player', {})
            rows.append({
                'team_id': team_id,
                'player_id': entry.get('playerId'),
                'fullName': player.get('fullName'),
                'proTeamId': player.get('proTeamId'),
                'defaultPositionId': player.get('defaultPositionId'),
                'acquisitionType': entry.get('acquisitionType'),
                'acquisitionDate': entry.get('acquisitionDate'),
                'lineupSlotId': entry.get('lineupSlotId'),
            })

    roster_df = pd.DataFrame(rows, columns=[
        'team_id', 'player_id', 'fullName', 'proTeamId', 'defaultPositionId',
        'acquisitionType', 'acquisitionDate', 'lineupSlotId',
    ])
    return roster_df
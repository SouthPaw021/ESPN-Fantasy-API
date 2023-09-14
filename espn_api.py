## Getting Draft Data and Player Season Projections via ESPN Fantasy Football API v3
import requests
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

# Get draft details
def get_draft_details(league_id, season_id, espn_cookies):
    headers  = {
        'Connection': 'keep-alive',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        }
    
    # Got this url from the network tab in chrome and worked for older season
    url = "https://fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{}?view=mDraftDetail&view=mSettings&view=mTeam&view=modular&view=mNav&seasonId={}".format(league_id, season_id)
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
    url = "https://fantasy.espn.com/apis/v3/games/ffl/seasons/{}/players?scoringPeriodId=0&view=players_wl".format(season_id)
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
    url = "https://fantasy.espn.com/apis/v3/games/ffl/seasons/{}?view=proTeamSchedules_wl".format(season_id)
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
    url = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{}/segments/0/leagues/{}?scoringPeriodId=0&view=kona_player_info".format(season_id, league_id)
    r = requests.get(url,
                    cookies=espn_cookies,
                    headers=projection_headers)
    projection_raw_data = r.json()
    projection_detail = projection_raw_data

    merged_data = []

    for i in range(350):
        player = projection_detail['players'][i]
        values = [
            player['id'],
            player['draftAuctionValue'],
            player['keeperValue'],
            player['player']['stats'][0]['appliedAverage'],
            player['player']['stats'][0]['appliedTotal']
            #player['ratings']['0']['positionalRanking'],
            #player['ratings']['0']['totalRanking'],
            #player['ratings']['0']['totalRating']
        ]
        merged_data.append(values)

    # Create a DataFrame from the merged_data list
    projections_df = pd.DataFrame(merged_data, columns=[
        'id',
        'draftAuctionValue',
        'keeperValue',
        'appliedAverage',
        'appliedTotal',
        #'positionalRanking',
        #'totalRanking',
        #'totalRating'
    ])
    
    # Rename in column
    projections_df.rename(columns = {'id':'player_id'}, inplace = True)
    return projections_df
import requests
import pandas as pd
from espn_api import get_player_info, get_team_info, get_player_projections
from static import projection_year, position_mapping, league_teams
import os
from dotenv import load_dotenv

load_dotenv()

LEAGUE_ID = os.getenv('LEAGUE_ID')
SWID_COOKIE = os.getenv('SWID_COOKIE')
ESPN_S2_COOKIES = os.getenv('ESPN_S2_COOKIES')
espn_cookies = {"swid": SWID_COOKIE, "espn_s2": ESPN_S2_COOKIES}

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECTIONS_CSV = os.path.join(REPO_DIR, '..', 'Projections.csv')
DEFAULT_HISTORICAL_POOL_CSV = os.path.join(REPO_DIR, 'data', 'historical_player_pool.csv')


def refresh_projections(projections_csv_path=DEFAULT_PROJECTIONS_CSV, historical_pool_csv_path=DEFAULT_HISTORICAL_POOL_CSV):
    """Pulls current-season projections, writes Projections.csv, and appends
    to the historical snapshot. Returns the projections dataframe."""
    all_projections_df = pd.DataFrame()

    # Get all needed info for the year
    year = projection_year
    projections_df = get_player_projections(LEAGUE_ID, year, espn_cookies)
    player_df = get_player_info(year, espn_cookies)
    team_df = get_team_info(year)

    # Merge tables together
    df2 = pd.merge(projections_df, player_df, how="inner", left_on="player_id", right_on = "player_id")
    final_df = pd.merge(df2, team_df, how="inner", left_on="proTeamId", right_on = "team_id")

    # Rename columns and map values for easier consumption
    league_projections = final_df.replace({"defaultPositionId": position_mapping})
    projections_final = league_projections[['fullName', 'abbrev', 'defaultPositionId', 'draftAuctionValue', 'appliedTotal', 'appliedAverage']].copy()
    projections_final.rename(columns = {'fullName':'Player', 'abbrev':'Team', 'defaultPositionId':'Position', 'draftAuctionValue':'Projected $', 'appliedTotal':'Points',
    'appliedAverage':'Pts/Wk'}, inplace = True)
    projections_final['Year'] = year

    #Concatenate all DataFrames in the list
    all_projections_df = pd.concat([all_projections_df, projections_final])

    # Also appends into data/historical_player_pool.csv for the league-history app's draft value stats.
    historical_snapshot = all_projections_df[['Year', 'Player', 'Team', 'Position', 'Points', 'Projected $', 'Pts/Wk']].copy()
    historical_snapshot.columns = ['year', 'player', 'team', 'position', 'projected_points', 'projected_dollar', 'projected_weekly_avg']
    historical_snapshot['year'] = historical_snapshot['year'].astype(int)
    historical_snapshot['position_rank'] = historical_snapshot.groupby('position')['projected_points'] \
        .rank(method='first', ascending=False).astype(int)

    os.makedirs(os.path.dirname(historical_pool_csv_path), exist_ok=True)
    if os.path.exists(historical_pool_csv_path):
        existing_historical = pd.read_csv(historical_pool_csv_path)
        existing_historical = existing_historical[existing_historical['year'] != int(year)]
        historical_snapshot = pd.concat([existing_historical, historical_snapshot], ignore_index=True)
    historical_snapshot.to_csv(historical_pool_csv_path, index=False)

    # Reorder columns and format/replace values
    all_projections_df = all_projections_df[['Year', 'Player', 'Team', 'Position', 'Points', 'Projected $', 'Pts/Wk']]
    all_projections_df['Projected $'] = all_projections_df['Projected $'].apply(lambda x: "${:,.0f}".format(x))
    all_projections_df.replace("$0","$1",inplace=True)
    all_projections_df['Year'] = all_projections_df['Year'].astype(int)
    all_projections_df['Points'] = all_projections_df['Points'].astype(float).round(2)
    all_projections_df['Pts/Wk'] = all_projections_df['Pts/Wk'].astype(float).round(2)
    all_projections_df.sort_values(by=['Position', 'Points', 'Year'], ascending=False, inplace=True)

    #Export to CSV
    all_projections_df.to_csv(projections_csv_path, index=False)
    print(f"[projection_history] wrote {len(all_projections_df)} rows to {projections_csv_path}")
    return all_projections_df


if __name__ == "__main__":
    refresh_projections()

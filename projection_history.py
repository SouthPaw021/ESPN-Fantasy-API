import requests
import pandas as pd
from espn_api import get_player_info, get_team_info, get_player_projections
from static import projection_years, position_mapping, league_teams 
import os
from dotenv import load_dotenv

load_dotenv()

# Load in environment variables
LEAGUE_ID = os.getenv('LEAGUE_ID')
SWID_COOKIE = os.getenv('SWID_COOKIE')
ESPN_S2_COOKIES = os.getenv('ESPN_S2_COOKIES')

# Update this to use env variables
espn_cookies = {"swid": SWID_COOKIE,
                "espn_s2": ESPN_S2_COOKIES}

# Create an empty dataframe to append to
all_projections_df = pd.DataFrame()

# Loop over all the years
for year in projection_years:
    print(year)
    # Get all needed info for the year
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

# Reorder columns and format/replace values
all_projections_df = all_projections_df[['Year', 'Player', 'Team', 'Position', 'Projected $', 'Points', 'Pts/Wk']]
all_projections_df['Projected $'] = all_projections_df['Projected $'].apply(lambda x: "${:,.0f}".format(x))
all_projections_df['Year'] = all_projections_df['Year'].astype(int)

#Export to CSV
all_projections_df.to_csv('../Projections.csv', index=False)
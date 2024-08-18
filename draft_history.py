import requests
import pandas as pd
from espn_api import get_draft_details, get_player_info, get_team_info
from static import years, position_mapping, league_teams 
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
all_drafts_df = pd.DataFrame()

# Loop over all the years
for year in years:
    print(year)
    # Get all needed info for the year
    draft_df = get_draft_details(LEAGUE_ID, year, espn_cookies)
    player_df = get_player_info(year, espn_cookies)
    team_df = get_team_info(year)
   
    # Merge tables together
    df2 = pd.merge(draft_df, player_df, how="inner", left_on="playerId", right_on = "player_id")
    final_df = pd.merge(df2, team_df, how="inner", left_on="proTeamId", right_on = "team_id")
   
    # Rename columns and map values for easier consumption
    league_draft = final_df.replace({"defaultPositionId": position_mapping})
    league_draft_info = league_draft.replace({"teamId": league_teams})
    league_draft_final = league_draft_info[['teamId',  'fullName', 'abbrev', 'defaultPositionId', 'overallPickNumber', 'keeper', 'bidAmount']].copy()
    league_draft_final.rename(columns = {'overallPickNumber':'Pick', 'teamId':'Owner', 'keeper':'Kept', 'bidAmount':'Paid',
                              'defaultPositionId':'Position', 'fullName':'Player', 'abbrev': 'Team'}, inplace = True)
    league_draft_final['Year'] = year
    
    # Concatenate all DataFrames in the list
    all_drafts_df = pd.concat([all_drafts_df, league_draft_final])

# Reorder columns and format/replace values
all_drafts_df = all_drafts_df[['Year', 'Owner', 'Player', 'Team', 'Position', 'Kept', 'Paid', 'Pick']]
all_drafts_df['Paid'] = all_drafts_df['Paid'].apply(lambda x: "${:,.0f}".format(x))
all_drafts_df['Kept'] = all_drafts_df['Kept'].replace({True: 'K', False: ''})
all_drafts_df['Year'] = all_drafts_df['Year'].astype(int)

# Sort the DataFrame by 'Player' and 'Year'
all_drafts_df.sort_values(by=['Player', 'Year'], ascending=[True, True], inplace=True)

# Adds # of years in a row keepers have been kept
current_player = None
streak_count = 0
years_kept = []

for index, row in all_drafts_df.iterrows():
    if row['Kept'] == 'K':
        if row['Player'] == current_player:
            streak_count += 1
        else:
            current_player = row['Player']
            streak_count = 1
    else:
        streak_count = 0
    
    years_kept.append(streak_count)

all_drafts_df['Years Kept'] = years_kept

# Identify if keepers were initially drafted or undrafted and added via waiver claim?
all_drafts_df['Drafted/Waiver'] = all_drafts_df.apply(lambda row: 'Drafted' if row['Kept'] == 'K' and row['Player'] in all_drafts_df.loc[all_drafts_df['Year'] == row['Year'] - 1, 'Player'].values else ('' if row['Kept'] != 'K' else 'Waiver Claim'), axis=1)

# Reorder w/ new columns
all_drafts_df = all_drafts_df[['Year', 'Owner', 'Player', 'Team', 'Position', 'Kept', 'Drafted/Waiver', 'Years Kept', 'Paid', 'Pick']]

# Sort by Paid, Kept, Owner, and then Year last
all_drafts_df.sort_values(by=['Year', 'Owner', 'Kept', 'Paid'], ascending=[False, True, False, False], inplace=True)

# Export to CSV
all_drafts_df.to_csv('../Historical_Draft_Results.csv', index=False)
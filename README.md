# espn-ff-api
Forked form the initial work of jman4190
[Link to Blog Post](https://jman4190.medium.com/how-to-use-python-with-the-espn-fantasy-draft-api-ecde38621b1b) 

How to Access the Unofficial ESPN Fantasy Footbball API with Python to pull League Draft History and upcoming season player projections

Note: ESPN's API changed in 2024 and the new API URL has been swapped into the code where needed

## Requirements
- pandas
- requests
- espn fantasy football account
- python-dotenv

## Storing Environment Variables
Create a `.env` file and add the following
```
export LEAGUE_ID = 'YOU_LEAGUE_ID_HERE'
export SWID_COOKIE = "YOUR_SWID_COOKIE_HERE"
export ESPN_S2_COOKIES = "YOUR_ESPN_S2_COOKIE_HERE"
```
## Modifying Variables
Make sure to update your league_id and adjust the years and league owners lists in `static.py` 
Also update projection_year in `static.py` to grab projections for the upcoming season

## Running the script
```
After you have updated both files, run the following:
$ python draft_history.py
$ projection.history.py
```

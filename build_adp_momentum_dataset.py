## Historical preseason ADP (Average Draft Position) snapshots, pulled from Wayback
## Machine archives of fantasypros.com's Standard-scoring ADP page
## (overall.php). Deliberately NOT the PPR page, even though this league
## scores PPR: tested head-to-head via walk-forward validation, and PPR
## momentum performed worse than both Standard AND the no-feature baseline
## (MAE 5.13 vs. Standard's 4.98 and baseline's 5.04), not just in the years
## it's missing entirely. PPR would've been the more conceptually correct
## source, but losing 4 of 9 training years' coverage to get it cost more
## than the scoring-format mismatch itself.
##
## Covers 2016-2022 and 2024-2025 -- 2023 is a real, unfilled gap (FantasyPros
## restructured their ADP URLs that year and Wayback's coverage of the new
## URL during preseason is too thin); 2026+ isn't covered here since
## FantasyPros moved that page to client-side rendering, so live/current-season
## data comes from their API instead (see predict_auction_prices.py's ADP
## momentum feature).
##
## One row per (year, snapshot date, player) -- the raw material for
## per-player preseason ADP momentum (compute_adp_momentum.py), not the
## momentum feature itself.
import os
import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(REPO_DIR, "data")
DEFAULT_OUTPUT_CSV = os.path.join(DEFAULT_DATA_DIR, "adp_snapshots_historical.csv")

CDX_URL = "http://web.archive.org/cdx/search/cdx"
ADP_PAGE = "fantasypros.com/nfl/adp/overall.php"

# Historical years only -- 2023 skipped (URL restructure that year left Wayback's
# preseason coverage too thin, see module docstring), 2026+ not attempted here.
HISTORICAL_YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2024, 2025]

# Preseason window: covers training camp through the last preseason game, which
# is when this league's real draft happens -- matches the actual decision window
# a momentum feature needs to capture.
WINDOW_START_MD = "0601"
WINDOW_END_MD = "0930"

REQUEST_TIMEOUT = 40
REQUEST_DELAY_SECONDS = 2  # be polite to archive.org, not the target site itself
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5  # doubles each retry: 5, 10, 20, 40

# archive.org has been observed to refuse connections under a default
# python-requests identity/no keep-alive -- a plain browser UA plus a shared
# pooled Session avoids that, on top of the retry/backoff below.
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


def list_snapshots(year):
    """Returns sorted list of (timestamp, original_url) for every archived
    capture of the ADP page within that year's preseason window. collapse=
    timestamp:8 dedupes to at most one snapshot per calendar day -- daily
    resolution is already finer than a momentum feature needs."""
    params = {
        "url": ADP_PAGE,
        "output": "json",
        "from": f"{year}{WINDOW_START_MD}",
        "to": f"{year}{WINDOW_END_MD}",
        "collapse": "timestamp:8",
        "limit": 500,
    }
    resp = _get_with_retries(CDX_URL, params=params)
    rows = resp.json()
    if len(rows) <= 1:
        return []
    header, data_rows = rows[0], rows[1:]
    ts_idx = header.index("timestamp")
    orig_idx = header.index("original")
    return [(r[ts_idx], r[orig_idx]) for r in data_rows]


def fetch_snapshot_html(timestamp, original_url):
    """id_ suffix returns the raw archived page (no Wayback toolbar injected),
    exactly as originally served."""
    url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"
    resp = _get_with_retries(url)
    return resp.text


def parse_adp_table(html):
    """Extracts (player, team, position, adp) rows from the archived page's
    real player-data table. Two things to work around, confirmed by sampling
    2016/2019/2022/2024/2025 directly:
      1. Some years' pages have TWO elements with id="data" -- the real player
         table, and an unrelated settings/experts-picker modal reusing the
         same id. Picking the one whose <thead> contains "AVG" disambiguates.
      2. Older years' row markup uses unclosed <tr> tags, which BeautifulSoup's
         default html.parser nests instead of treating as siblings, corrupting
         cell counts. The lxml backend parses these correctly -- required for
         pre-2023 pages.
    Player name comes from the fp-player-name attribute, not the link text --
    sidesteps suffix inconsistencies (e.g. "Jr.", "III") the same way this
    pipeline's rookie-flag join already had to for ESPN's own player names.
    """
    soup = BeautifulSoup(html, "lxml")
    target = None
    for table in soup.find_all("table", id="data"):
        thead = table.find("thead")
        if thead and "AVG" in thead.get_text().upper():
            target = table
            break
    if target is None:
        return []

    tbody = target.find("tbody")
    if tbody is None:
        return []

    rows = []
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        name_tag = tr.find(attrs={"fp-player-name": True})
        player = name_tag["fp-player-name"] if name_tag else tds[1].get_text(strip=True)
        if not player:
            continue

        small = tr.find("small")
        team = None
        if small:
            m = re.match(r"([A-Za-z]{2,4})", small.get_text(strip=True))
            team = m.group(1) if m else None

        pos_raw = tds[2].get_text(strip=True)
        pos_m = re.match(r"([A-Za-z/]+)", pos_raw)
        position = pos_m.group(1) if pos_m else None

        adp_raw = tds[-1].get_text(strip=True)
        try:
            adp = float(adp_raw)
        except ValueError:
            continue

        rows.append({"Player": player, "Team": team, "Position": position, "ADP": adp})

    return rows


def build_dataset(years=HISTORICAL_YEARS, output_csv_path=DEFAULT_OUTPUT_CSV):
    """Saves progress after every year, not just once at the end -- a
    multi-hundred-request run against a third-party archive can die partway
    through. A year that fails entirely (e.g. its CDX listing exhausts
    retries) is skipped, logged, and the run continues with the rest."""
    all_rows = []
    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)

    for year in years:
        try:
            snapshots = list_snapshots(year)
        except Exception as e:
            print(f"[{year}] SKIPPED ENTIRELY -- could not list snapshots ({e})")
            continue

        print(f"[{year}] {len(snapshots)} snapshot(s) found in preseason window")
        parsed_count = 0
        for timestamp, original_url in snapshots:
            try:
                html = fetch_snapshot_html(timestamp, original_url)
                rows = parse_adp_table(html)
            except Exception as e:
                print(f"  [{year}] {timestamp}: skipped ({e})")
                continue
            if not rows:
                print(f"  [{year}] {timestamp}: no player rows found, skipping")
                continue
            snapshot_date = pd.Timestamp(timestamp[:8])
            for r in rows:
                r["Year"] = year
                r["SnapshotDate"] = snapshot_date
            all_rows.extend(rows)
            parsed_count += 1
            time.sleep(REQUEST_DELAY_SECONDS)
        print(f"[{year}] parsed {parsed_count}/{len(snapshots)} snapshots successfully")

        df = pd.DataFrame(all_rows, columns=["Year", "SnapshotDate", "Player", "Team", "Position", "ADP"])
        df.to_csv(output_csv_path, index=False)
        print(f"[{year}] progress saved -- {len(df)} total rows so far in {output_csv_path}")

    print(f"\n[build_adp_momentum_dataset] done, {len(all_rows)} rows total in {output_csv_path}")
    return pd.DataFrame(all_rows, columns=["Year", "SnapshotDate", "Player", "Team", "Position", "ADP"])


if __name__ == "__main__":
    build_dataset()

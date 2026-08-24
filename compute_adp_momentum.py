## Turns build_adp_momentum_dataset.py's raw per-snapshot ADP rows into one
## momentum number per (Year, Player): how much that player's draft-value
## trended across the preseason, leading into that year's actual draft.
##
## Two metrics live here:
##   - compute_momentum_delta() / _delta_for_group: PRODUCTION. Total
##     movement, no day-normalization -- first tracked value vs. last.
##   - compute_momentum() / _slope_for_group: kept for reference, not used.
##     An OLS rate of change (picks/day), statistically tied with delta on
##     the 2016-2025 walk-forward ablation, but delta won for a practical
##     reason: this league's live current-season data
##     (compute_live_adp_momentum.py, from Fantasy Football Calculator) is
##     tracked far more densely (near-daily) than the sparse historical
##     Wayback snapshots (10-30/year) this was built on. A slope divides by
##     however many days separate two points, so dense sampling inflates
##     modest movement into wild rates -- a 2026 test case hit 8.67 on 2 raw
##     daily points vs. the historical slope feature's max of ~8. Delta
##     doesn't have this problem: first-tracked-to-last-tracked is the same
##     number regardless of sampling density.
##
## Design choices carried over from how this got scoped (apply to both metrics
## unless noted):
##   - Raw ADP, negated (so positive = rising value), not percentile within
##     each snapshot. Percentile was the original design (ADP isn't evenly
##     spaced -- pick 5->3 is a bigger jump than pick 150->148), but tested
##     head-to-head via walk-forward validation it was a net negative vs. not
##     having the feature at all, while raw ADP beat both percentile and the
##     no-feature baseline in 7 of 8 years. Percentile's denominator (how
##     many players were tracked that day) varies snapshot to snapshot and
##     injects its own noise.
##   - A slope over every available snapshot, not a 2-point delta. Wayback's
##     crawl cadence is uneven year to year (10-30 snapshots/year), so a
##     fixed "early"/"late" date would mean an arbitrary nearest-available
##     snapshot in thin years.
##   - Only snapshots up to that year's ACTUAL draft date count. This league
##     drafts the Sunday after the last preseason game, and several years'
##     scraped snapshots run into late September -- including those would
##     leak information the real draft-day price could never have reflected.
##   - The OUTPUT is filtered down to just (Year, Player) pairs actually
##     drafted in THIS league that year -- the only rows
##     build_regression_dataset.py's join could ever use. Also drops most of
##     the noise: a deep-bench NFL player nobody here would draft, bouncing
##     in and out of a national site's tracking threshold, was the dominant
##     source of unreliable-looking momentum values.
import os
import re
import pandas as pd
import numpy as np

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(REPO_DIR, "data")
DEFAULT_SNAPSHOTS_CSV = os.path.join(DEFAULT_DATA_DIR, "adp_snapshots_historical.csv")
DEFAULT_OUTPUT_CSV = os.path.join(DEFAULT_DATA_DIR, "adp_momentum_historical.csv")
DEFAULT_DRAFT_HISTORY_CSV = os.path.join(REPO_DIR, "..", "Historical_Draft_Results.csv")

# This league's actual draft date each year -- always the Sunday after the
# last NFL preseason game. Snapshots after this date are post-draft and
# excluded from that year's momentum calculation entirely.
DRAFT_DATES = {
    2016: "2016-09-04",
    2017: "2017-09-03",
    2018: "2018-08-26",
    2019: "2019-09-01",
    2020: "2020-08-30",
    2021: "2021-08-29",
    2022: "2022-09-04",
    2023: "2023-09-03",
    2024: "2024-08-25",
    2025: "2025-08-24",
    2026: "2026-08-30",
}

MIN_SNAPSHOTS_REQUIRED = 2  # the slope-based metric needs at least two points

# Same exclusion build_regression_dataset.py applies to the model's training
# rows -- K/D-ST keeper-value dynamics don't work like skill positions, and
# their ADP is thin/sporadically tracked, producing momentum slopes that are
# mostly noise (nearly every extreme momentum value before this filter was a
# kicker or D/ST with only the minimum 2 snapshots).
EXCLUDED_POSITIONS = {'K', 'D/ST'}

# NOT June 1, despite the raw scrape covering June-Sept -- real training
# camps open mid-July, and fantasy sites' ADP tracking for that year's
# rookies lags the real NFL draft by several weeks while a stable consensus
# forms. A June cutoff wrongly treated rookies like Najee Harris and Kyle
# Pitts (both top-2021 picks, clearly not "off the board") as not-yet-tracked,
# handing them the worst possible synthetic starting point in _delta_for_group.
WINDOW_START_MD = "07-15"

# A player not tracked in a given snapshot is treated as ranked outside the
# top MAX_RANKED_PLAYERS -- capping every snapshot to this size and using a
# FIXED synthetic starting point (MAX_RANKED_PLAYERS + 1) rather than
# "whatever the worst tracked ADP happened to be that day," which varied
# wildly (~167 to ~353) by source and date and isn't a meaningful signal.
# 300 comfortably covers this league's entire real draft pool.
MAX_RANKED_PLAYERS = 300
SYNTHETIC_START_ADP = MAX_RANKED_PLAYERS + 1


def _normalize_name(name):
    """Strips trailing generational suffixes (Jr./Sr./II/III/IV) and
    lowercases, for MATCHING players only -- FantasyPros silently added
    "Sr." to Kyle Pitts partway through the 2025 season (10 more cases like
    it across other years, mostly 2020). Grouping by the raw Player string
    would otherwise fragment one continuous player's trajectory into two,
    and the second fragment would look like a brand-new player to
    _delta_for_group. Output still uses Historical_Draft_Results.csv's own
    spelling -- this is purely an internal matching key."""
    return re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name.strip(), flags=re.IGNORECASE).strip().lower()


def _load_and_filter_snapshots(snapshots_csv_path):
    """Shared prep for both metrics: drop K/D-ST, drop anything before that
    year's training-camp-open cutoff (see WINDOW_START_MD), then drop
    anything after that year's real draft date (this league drafts the
    Sunday after the last preseason game -- snapshots after that would leak
    information the real draft-day price could never have reflected)."""
    df = pd.read_csv(snapshots_csv_path, parse_dates=["SnapshotDate"])

    before_pos = len(df)
    df = df[~df["Position"].isin(EXCLUDED_POSITIONS)]
    print(f"[compute_adp_momentum] dropped {before_pos - len(df)} K/D-ST rows "
          f"({before_pos} -> {len(df)})")

    window_starts = df["Year"].map(lambda y: pd.Timestamp(f"{y}-{WINDOW_START_MD}"))
    before = len(df)
    df = df[df["SnapshotDate"] >= window_starts]
    print(f"[compute_adp_momentum] dropped {before - len(df)} pre-camp rows "
          f"({before} -> {len(df)})")

    draft_dates = df["Year"].map(DRAFT_DATES).apply(pd.Timestamp)
    before = len(df)
    df = df[df["SnapshotDate"] <= draft_dates]
    print(f"[compute_adp_momentum] dropped {before - len(df)} post-draft-date rows "
          f"({before} -> {len(df)})")

    before = len(df)
    df = df[df["ADP"] <= MAX_RANKED_PLAYERS]
    print(f"[compute_adp_momentum] dropped {before - len(df)} rows ranked outside the "
          f"top {MAX_RANKED_PLAYERS} ({before} -> {len(df)})")

    df["NormPlayer"] = df["Player"].apply(_normalize_name)
    return df


def _restrict_to_drafted_players(result, draft_history_csv_path):
    """Keep only (Year, Player) pairs this league actually drafted that year
    -- the only rows build_regression_dataset.py's join could ever use. Also
    drops most of the national-pool noise as a side effect.

    Matches on normalized name (_normalize_name), not the raw Player string
    -- a mid-season FantasyPros rename could otherwise cause a real drafted
    player to be dropped as "not matching". Surviving rows' Player column
    gets overwritten with Historical_Draft_Results.csv's own spelling,
    which is what the downstream join needs."""
    draft_history = pd.read_csv(draft_history_csv_path)
    draft_history["NormPlayer"] = draft_history["Player"].apply(_normalize_name)
    canonical_name = {
        (row["Year"], row["NormPlayer"]): row["Player"]
        for _, row in draft_history.iterrows()
    }
    before_relevance = len(result)
    result = result[result.apply(lambda r: (r["Year"], r["NormPlayer"]) in canonical_name, axis=1)].copy()
    result["Player"] = result.apply(lambda r: canonical_name[(r["Year"], r["NormPlayer"])], axis=1)
    print(f"[compute_adp_momentum] dropped {before_relevance - len(result)} rows for players "
          f"not drafted in this league that year ({before_relevance} -> {len(result)})")
    return result.drop(columns=["NormPlayer"])


def _slope_for_group(g):
    """OLS slope of -ADP vs. day-of-preseason (negated so positive = rising
    value). Days measured from that player-year's own first available
    snapshot, not a fixed calendar date -- only the rate of change matters.

    Known limitation: anchoring day-0 to the player's own first snapshot
    means a player not yet tracked at the season's start has that entire
    "off the board" period silently erased -- the slope only reflects
    movement after they're already on the board. It also means a short
    tracked window inflates the rate for a given amount of real movement,
    regardless of whether that's a genuine sudden event or just a player
    who wasn't relevant enough to be tracked earlier."""
    if len(g) < MIN_SNAPSHOTS_REQUIRED:
        return pd.Series({"adp_momentum": np.nan, "n_snapshots": len(g)})
    days = (g["SnapshotDate"] - g["SnapshotDate"].min()).dt.days.to_numpy(dtype=float)
    if days.max() == 0:  # all same-day duplicates, shouldn't happen but guard anyway
        return pd.Series({"adp_momentum": np.nan, "n_snapshots": len(g)})
    neg_adp = -g["ADP"].to_numpy(dtype=float)
    slope, _intercept = np.polyfit(days, neg_adp, 1)
    return pd.Series({"adp_momentum": slope, "n_snapshots": len(g)})


def _season_first_dates(df):
    """For each Year: the date of that season's first tracked snapshot, used
    by _delta_for_group to know whether a player was already tracked at the
    season's start. The synthetic starting point itself is the fixed
    SYNTHETIC_START_ADP, not derived from this data."""
    return df.groupby("Year")["SnapshotDate"].min().to_dict()


def _delta_for_group(year, g, first_dates):
    """Total ADP movement (negated so positive = rising value), first
    tracked value vs. last tracked value -- no day-normalization, so a
    gradual real climb counts the same as a sudden one of equal size.

    A player not yet tracked at the season's first snapshot is treated as
    having started at SYNTHETIC_START_ADP -- crediting them for the implied
    rise from "off the board" into wherever they first appear, rather than
    leaving that period invisible."""
    first_date = first_dates[year]
    g = g.sort_values("SnapshotDate")
    on_first_day = g["SnapshotDate"] == first_date
    start_adp = g.loc[on_first_day, "ADP"].iloc[0] if on_first_day.any() else SYNTHETIC_START_ADP
    end_adp = g["ADP"].iloc[-1]
    return start_adp - end_adp, len(g)


def compute_momentum(
    snapshots_csv_path=DEFAULT_SNAPSHOTS_CSV,
    output_csv_path=DEFAULT_OUTPUT_CSV,
    draft_history_csv_path=DEFAULT_DRAFT_HISTORY_CSV,
):
    """Kept for reference only, NOT production -- see module docstring for
    why delta won. _slope_for_group has the actual OLS slope logic."""
    df = _load_and_filter_snapshots(snapshots_csv_path)

    result = (
        df.groupby(["Year", "NormPlayer"])
        .apply(_slope_for_group, include_groups=False)
        .reset_index()
    )
    result = result.dropna(subset=["adp_momentum"])
    result["n_snapshots"] = result["n_snapshots"].astype(int)

    # A representative display name per (Year, NormPlayer) -- purely to
    # satisfy _restrict_to_drafted_players' interface; that function
    # overwrites it with Historical_Draft_Results.csv's own spelling for
    # anything that actually matches.
    rep_names = df.groupby(["Year", "NormPlayer"])["Player"].first().reset_index()
    result = result.merge(rep_names, on=["Year", "NormPlayer"], how="left")

    result = _restrict_to_drafted_players(result, draft_history_csv_path)

    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    result.to_csv(output_csv_path, index=False)
    print(f"[compute_adp_momentum] wrote {len(result)} player-year momentum rows to {output_csv_path}")
    return result


def compute_momentum_delta(
    snapshots_csv_path=DEFAULT_SNAPSHOTS_CSV,
    output_csv_path=None,
    draft_history_csv_path=DEFAULT_DRAFT_HISTORY_CSV,
):
    """PRODUCTION metric: total delta, see _delta_for_group -- see module
    docstring for why this won over the OLS slope kept in compute_momentum().
    Same MIN_SNAPSHOTS_REQUIRED floor as the slope metric -- originally
    assumed this metric wouldn't need one, since even a single real reading
    is a meaningful comparison against the starting point in principle. Real
    cases (Cam Ward 2025, one real snapshot despite four earlier chances to
    be captured) showed a single thin reading against the synthetic start
    still produces an unreliable-looking outlier."""
    if output_csv_path is None:
        output_csv_path = os.path.join(DEFAULT_DATA_DIR, "adp_momentum_historical_delta.csv")

    df = _load_and_filter_snapshots(snapshots_csv_path)
    first_dates = _season_first_dates(df)

    rows = []
    for (year, norm_player), g in df.groupby(["Year", "NormPlayer"]):
        momentum, n_snapshots = _delta_for_group(year, g, first_dates)
        # Representative display name -- see compute_momentum's identical
        # comment; _restrict_to_drafted_players overwrites this.
        rows.append({"Year": year, "NormPlayer": norm_player, "Player": g["Player"].iloc[0],
                      "adp_momentum": momentum, "n_snapshots": n_snapshots})
    result = pd.DataFrame(rows)
    before_thin = len(result)
    result = result[result["n_snapshots"] >= MIN_SNAPSHOTS_REQUIRED]
    print(f"[compute_adp_momentum] dropped {before_thin - len(result)} rows with fewer than "
          f"{MIN_SNAPSHOTS_REQUIRED} snapshots ({before_thin} -> {len(result)})")
    result = _restrict_to_drafted_players(result, draft_history_csv_path)

    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    result.to_csv(output_csv_path, index=False)
    print(f"[compute_adp_momentum] wrote {len(result)} player-year delta rows to {output_csv_path}")
    return result


if __name__ == "__main__":
    compute_momentum_delta(output_csv_path=DEFAULT_OUTPUT_CSV)

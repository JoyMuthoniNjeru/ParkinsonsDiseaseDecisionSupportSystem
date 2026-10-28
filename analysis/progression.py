# analysis/progression.py
# ML Phase 1, step 5: how fast each of the 26 MDS-UPDRS Part I/II items typically changes
# for people with PD, in score points per year. The ML component later compares a patient's
# own recent trend against this ("is this changing faster than typical?").
#
# Method:
#   - Same participants and dates as severity_baseline.py (shared loader in ppmi_data.py).
#   - For each participant and item, fit a straight line (linear regression) through their
#     scores against years since diagnosis. The slope = change in score per year.
#   - Only participants with >= MIN_VISITS visits spanning >= MIN_SPAN_YEARS, so a slope isn't
#     built from two visits a few months apart.
#   - Typical change = median slope across participants; typical range = 25th-75th percentile.
#     Scores are whole numbers, so many people score the same at every visit (slope exactly 0)
#     and the median is often 0. The MEAN slope is reported too, as the more useful average.
#   - Also per stage (slope fitted on the visits inside that stage only).
#
# PPMI DUA: prints and saves AGGREGATED results only -- no rows, no PATNOs. Groups with fewer
# than MIN_PARTICIPANTS people are left out (small-numbers rule).
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppmi_data import MIN_PARTICIPANTS, OUT_DIR, STAGE_LABELS, item_meta, load_item_scores  # noqa: E402

MIN_VISITS = 3          # visits needed to fit a slope for one person
MIN_SPAN_YEARS = 1.5    # first to last visit must be at least this far apart
CHANGE = 0.05           # slopes within +/- this many points/year count as "stable"


def person_slopes(scores, group_cols):
    """Least-squares slope (score points per year) per participant and group.
    slope = sum((x - mean_x) * (y - mean_y)) / sum((x - mean_x)^2), with x = years, y = score."""
    keys = group_cols + ["PATNO"]
    g = scores.groupby(keys, observed=True)
    df = scores.assign(
        x_dev=scores["years"] - g["years"].transform("mean"),
        y_dev=scores["score"] - g["score"].transform("mean"),
    )
    df["xy"] = df["x_dev"] * df["y_dev"]
    df["xx"] = df["x_dev"] ** 2
    per = df.groupby(keys, observed=True).agg(
        visits=("score", "size"),
        span=("years", lambda y: y.max() - y.min()),
        xy=("xy", "sum"), xx=("xx", "sum"),
    ).reset_index()
    per = per[(per["visits"] >= MIN_VISITS) & (per["span"] >= MIN_SPAN_YEARS) & (per["xx"] > 0)]
    per["slope"] = per["xy"] / per["xx"]
    return per


def summarise(per, group_cols):
    out = per.groupby(group_cols, observed=True)["slope"].agg(
        participants="count", mean="mean", median="median",
        p25=lambda s: s.quantile(0.25), p75=lambda s: s.quantile(0.75),
        pct_worse=lambda s: (s > CHANGE).mean() * 100,
        pct_better=lambda s: (s < -CHANGE).mean() * 100,
    ).reset_index()
    return out[out["participants"] >= MIN_PARTICIPANTS]


scores = load_item_scores()

# Overall: all of each participant's visits
overall = summarise(person_slopes(scores, ["code"]), ["code"])
overall.insert(1, "stage", "all")
# Per stage: only the visits inside each stage
by_stage = summarise(person_slopes(scores, ["code", "stage"]), ["code", "stage"])
by_stage["stage"] = by_stage["stage"].astype(str)

result = item_meta().merge(pd.concat([overall, by_stage], ignore_index=True), on="code")
for col in ["mean", "median", "p25", "p75"]:
    result[col] = result[col].round(3)
for col in ["pct_worse", "pct_better"]:
    result[col] = result[col].round(1)

# --- Print: typical yearly change overall, plus the median per stage -------------------------
print(f"\nSlopes need >= {MIN_VISITS} visits over >= {MIN_SPAN_YEARS} years. "
      f"'Stable' = within +/-{CHANGE} points/year.")
print("Participants with a slope (overall, median across items): "
      f"{int(overall['participants'].median())}")

pd.set_option("display.width", 250)
view = result[result["stage"] == "all"].set_index(["item", "name"])[
    ["participants", "mean", "median", "p25", "p75", "pct_worse", "pct_better"]]
stage_means = (result[result["stage"] != "all"]
               .pivot_table(index=["item", "name"], columns="stage", values="mean",
                            aggfunc="first", sort=False))
stage_means = stage_means[[s for s in STAGE_LABELS if s in stage_means.columns]]
stage_means.columns = [f"mean {c}" for c in stage_means.columns]
stage_people = result[result["stage"] != "all"].groupby("stage")["participants"].median()
print("Participants with a slope per stage (median across items): "
      + ", ".join(f"{st}: {int(stage_people[st])}" for st in STAGE_LABELS if st in stage_people))
print("\nChange per year (score points, 0-4 scale): mean and median slope (25th-75th pct), "
      "% of people getting worse / better; then the mean slope inside each stage")
print(view.join(stage_means).to_string())

OUT_DIR.mkdir(parents=True, exist_ok=True)
out_file = OUT_DIR / "progression_baseline.csv"
result.to_csv(out_file, index=False)
print(f"\nSaved to {out_file}")

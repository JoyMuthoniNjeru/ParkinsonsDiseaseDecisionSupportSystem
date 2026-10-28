# analysis/severity_baseline.py
# ML Phase 1, step 4: typical severity of each of the 26 MDS-UPDRS Part I/II items for people
# with PD, grouped by years since diagnosis. The ML component later compares a patient's own
# scores against the typical range for their stage ("compared to typical").
#
# Method:
#   - PD only: participants in PD_Diagnosis_History with a diagnosis date (PDDXDT).
#   - years since diagnosis = visit date (INFODT) - diagnosis date; pre-diagnosis visits dropped.
#   - Each participant counts ONCE per item and stage: their visits in that stage are averaged
#     first, so people with many visits don't outweigh people with few.
#   - Typical range = 25th-75th percentile of those participant averages.
#
# PPMI DUA: prints and saves AGGREGATED results only -- no rows, no PATNOs. Groups with fewer
# than MIN_PARTICIPANTS people are left out (small-numbers rule).
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from updrs_items import UPDRS_ITEMS  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # reads PPMI_DATA_DIR

MIN_PARTICIPANTS = 10
STAGE_EDGES = [0, 3, 6, float("inf")]           # years since diagnosis
STAGE_LABELS = ["0-2 years", "3-5 years", "6+ years"]
VALID_SCORES = {0, 1, 2, 3, 4}                  # anything else (e.g. "unable to rate" codes) is dropped
MODERATE = 3                                    # same as the Rule Engine's alert threshold

ppmi_dir = os.getenv("PPMI_DATA_DIR")
if not ppmi_dir:
    sys.exit("PPMI_DATA_DIR is not set. Add it to .env, e.g. PPMI_DATA_DIR=C:\\ppmi_data")
raw_dir = Path(ppmi_dir) / "raw"
out_dir = Path(ppmi_dir) / "output"


def find_csv(prefix):
    """Newest CSV whose name starts with prefix + a date, e.g. 'MDS-UPDRS_Part_I_11Oct2026.csv'."""
    matches = sorted(p for p in raw_dir.rglob(f"{prefix}*.csv")
                     if p.name[len(prefix):len(prefix) + 1].isdigit())
    if not matches:
        sys.exit(f"{prefix}*.csv not found under {raw_dir}")
    return matches[-1]


def parse_dates(values):
    """PPMI dates are usually MM/YYYY; fall back to pandas' own parsing for anything else."""
    dates = pd.to_datetime(values, format="%m/%Y", errors="coerce")
    leftover = dates.isna() & values.notna()
    dates[leftover] = pd.to_datetime(values[leftover], errors="coerce")
    return dates


# --- Diagnosis dates -------------------------------------------------------------------------
dx = pd.read_csv(find_csv("PD_Diagnosis_History_"), low_memory=False)
dx["dx_date"] = parse_dates(dx["PDDXDT"])
print(f"PD_Diagnosis_History: {dx['PATNO'].nunique()} participants, "
      f"{dx['dx_date'].notna().sum()} with a usable diagnosis date")
dx = dx.dropna(subset=["dx_date"]).groupby("PATNO", as_index=False)["dx_date"].min()

# --- Item scores, long format: one row per participant x visit x item -----------------------
long_parts = []
for file_prefix in sorted({i["file"] for i in UPDRS_ITEMS}):
    codes = [i["code"] for i in UPDRS_ITEMS if i["file"] == file_prefix]
    df = pd.read_csv(find_csv(file_prefix), low_memory=False)
    df["visit_date"] = parse_dates(df["INFODT"])
    print(f"{file_prefix}: {len(df)} visits, {df['visit_date'].notna().mean():.0%} with a usable visit date")
    long_parts.append(df.melt(id_vars=["PATNO", "visit_date"], value_vars=codes,
                              var_name="code", value_name="score"))
scores = pd.concat(long_parts, ignore_index=True)
scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
scores = scores[scores["score"].isin(VALID_SCORES)]

# --- Years since diagnosis + stage -----------------------------------------------------------
scores = scores.merge(dx, on="PATNO", how="inner")  # PD participants only
scores["years"] = (scores["visit_date"] - scores["dx_date"]).dt.days / 365.25
before = len(scores)
scores = scores[scores["years"] >= 0]  # drops visits before diagnosis or without a date
print(f"\nScores used: {len(scores)} item scores from {scores['PATNO'].nunique()} PD participants "
      f"({before - len(scores)} dropped: before diagnosis or no visit date)")
scores["stage"] = pd.cut(scores["years"], bins=STAGE_EDGES, labels=STAGE_LABELS, right=False)

# --- Summarise: each participant counts once per item and stage ------------------------------
per_person = (scores.groupby(["code", "stage", "PATNO"], observed=True)["score"]
              .mean().rename("person_avg").reset_index())
summary = (per_person.groupby(["code", "stage"], observed=True)["person_avg"]
           .agg(participants="count", mean="mean", median="median",
                p25=lambda s: s.quantile(0.25), p75=lambda s: s.quantile(0.75))
           .reset_index())
moderate = (scores.assign(moderate_plus=scores["score"] >= MODERATE)
            .groupby(["code", "stage"], observed=True)
            .agg(visits=("score", "size"), pct_visits_moderate_plus=("moderate_plus", "mean"))
            .reset_index())
summary = summary.merge(moderate, on=["code", "stage"])
hidden = (summary["participants"] < MIN_PARTICIPANTS).sum()
summary = summary[summary["participants"] >= MIN_PARTICIPANTS]

# Add the app's names / categories and keep the item order from updrs_items.py
meta = pd.DataFrame(UPDRS_ITEMS)[["item", "code", "name", "category"]]
summary = meta.merge(summary, on="code")
for col in ["mean", "median", "p25", "p75"]:
    summary[col] = summary[col].round(2)
summary["pct_visits_moderate_plus"] = (summary["pct_visits_moderate_plus"] * 100).round(1)

# --- Print a compact view: typical range per stage --------------------------------------------
stage_counts = per_person.groupby("stage", observed=True)["PATNO"].nunique()
print("\nParticipants per stage: " + ", ".join(f"{s}: {n}" for s, n in stage_counts.items()))
if hidden:
    print(f"{hidden} item/stage groups hidden (fewer than {MIN_PARTICIPANTS} participants)")

view = summary.assign(typical=lambda d: d["median"].map("{:.1f}".format) + " ("
                      + d["p25"].map("{:.1f}".format) + "-" + d["p75"].map("{:.1f}".format) + ")")
table = view.pivot_table(index=["item", "name"], columns="stage", values="typical",
                         aggfunc="first", observed=True, sort=False)
pct = view.pivot_table(index=["item", "name"], columns="stage", values="pct_visits_moderate_plus",
                       aggfunc="first", observed=True, sort=False)
table.columns = [str(c) for c in table.columns]  # plain text headers (stage is a category type)
pct.columns = [f"% >=3 {c}" for c in pct.columns]
pd.set_option("display.width", 250)
print("\nTypical score per item: median of participant averages (25th-75th percentile), 0-4 scale")
print(table.join(pct).to_string())

out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "severity_baseline.csv"
summary.to_csv(out_file, index=False)
print(f"\nSaved to {out_file}")

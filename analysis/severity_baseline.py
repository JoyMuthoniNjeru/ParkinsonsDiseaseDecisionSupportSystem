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
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppmi_data import MIN_PARTICIPANTS, OUT_DIR, item_meta, load_item_scores  # noqa: E402

MODERATE = 3  # same as the Rule Engine's alert threshold

scores = load_item_scores()

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
summary = item_meta().merge(summary, on="code")
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

OUT_DIR.mkdir(parents=True, exist_ok=True)
out_file = OUT_DIR / "severity_baseline.csv"
summary.to_csv(out_file, index=False)
print(f"\nSaved to {out_file}")

# analysis/medications.py
# ML Phase 1, step 3: which PD medications are most common in PPMI's LEDD log, and their
# usual strengths and doses per day. The result becomes the app's MedicationReference list.
#
# PPMI DUA: prints and saves AGGREGATED results only -- no rows, no PATNOs. Any drug or
# strength used by fewer than MIN_PARTICIPANTS people is left out (small-numbers rule).
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # reads PPMI_DATA_DIR

MIN_PARTICIPANTS = 10   # hide anything fewer people than this use
TOP_DRUGS = 25          # how many medications to list
TOP_STRENGTHS = 3       # usual strengths shown per medication

ppmi_dir = os.getenv("PPMI_DATA_DIR")
if not ppmi_dir:
    sys.exit("PPMI_DATA_DIR is not set. Add it to .env, e.g. PPMI_DATA_DIR=C:\\ppmi_data")
raw_dir = Path(ppmi_dir) / "raw"
out_dir = Path(ppmi_dir) / "output"

matches = sorted(raw_dir.rglob("LEDD_Concomitant_Medication_Log_*.csv"))
if not matches:
    sys.exit(f"LEDD_Concomitant_Medication_Log_*.csv not found under {raw_dir}")
led = pd.read_csv(matches[-1], low_memory=False)

# LEDTRT is free text: tidy it so "Sinemet ", "SINEMET" and "sinemet" count as one drug
led["drug"] = led["LEDTRT"].astype(str).str.upper().str.strip().str.replace(r"\s+", " ", regex=True)
led["strength"] = led["LEDDOSSTR"].astype(str).str.upper().str.strip()
led = led[~led["drug"].isin(["", "NAN"])]

def median_or_none(values):
    """Median of a column, or None if it has no numbers (avoids numpy 'empty slice' warnings)."""
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    return numbers.median() if len(numbers) else None


summary = []
by_drug = led.groupby("drug")
for drug, rows in by_drug:
    people = rows["PATNO"].nunique()
    if people < MIN_PARTICIPANTS:
        continue
    # Usual strengths, each counted by people (not rows), small groups hidden
    strengths = (
        rows[~rows["strength"].isin(["", "NAN"])]
        .groupby("strength")["PATNO"].nunique()
        .sort_values(ascending=False)
    )
    strengths = strengths[strengths >= MIN_PARTICIPANTS].head(TOP_STRENGTHS)
    summary.append({
        "drug": drug,
        "participants": people,
        "usual_strengths": "; ".join(f"{s} ({n})" for s, n in strengths.items()),
        "median_dose": median_or_none(rows["LEDDOSE"]),
        "median_per_day": median_or_none(rows["LEDDOSFRQ"]),
    })

result = (pd.DataFrame(summary)
          .sort_values("participants", ascending=False)
          .head(TOP_DRUGS)
          .reset_index(drop=True))

hidden = by_drug.ngroups - len(summary)
print(f"LEDD log: {led['PATNO'].nunique()} participants, {by_drug.ngroups} distinct medication names")
print(f"Showing the top {len(result)}; {hidden} names used by fewer than {MIN_PARTICIPANTS} people are hidden.\n")
pd.set_option("display.width", 250, "display.max_colwidth", 80)
print(result.to_string())

out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "medications_summary.csv"
result.to_csv(out_file, index=False)
print(f"\nSaved to {out_file}")

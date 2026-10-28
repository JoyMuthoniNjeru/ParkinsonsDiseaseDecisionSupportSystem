# analysis/medications.py
# ML Phase 1, step 3: which PD medications are most common in PPMI's LEDD log, and their
# usual strengths and doses per day. The result becomes the app's MedicationReference list.
#
# The LEDD log's medication name (LEDTRT) is free text, so the same drug appears under many
# names ("SINEMET", "CARBIDOPA/LEVODOPA IR", ...). GENERIC_PATTERNS groups them by generic drug.
#
# PPMI DUA: prints and saves AGGREGATED results only -- no rows, no PATNOs. Any drug or
# strength used by fewer than MIN_PARTICIPANTS people is left out (small-numbers rule).
import os
import re
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # reads PPMI_DATA_DIR

MIN_PARTICIPANTS = 10   # hide anything fewer people than this use
TOP_STRENGTHS = 4       # usual strengths shown per medication

# Extended/controlled-release markers in a medication name
_RELEASE = r"\b(CR|ER|XR|XL|CONTROLLED|EXTENDED|PROLONGED)\b"

# (pattern, generic name) -- checked in order, first match wins, so the more specific
# patterns (Rytary, Madopar, CR/ER) come before the general "carbidopa/levodopa" one.
# Spellings are loose on purpose: sites typed e.g. PRAMIPEXOL, RASAGILINA, ROPINEROLE.
GENERIC_PATTERNS = [
    (r"RYTARY|CREXONT", "Carbidopa/Levodopa ER capsules"),
    (r"INBRIJA|INHAL", "Levodopa inhaled"),
    (r"STALEVO", "Carbidopa/Levodopa/Entacapone"),
    (r"MADOPAR|BENSERAZIDE", "Levodopa/Benserazide"),
    (rf"(CARBIDOPA|LEVODOPA|SINEMET).*{_RELEASE}", "Carbidopa/Levodopa CR"),
    (r"CARBIDOPA|LEVODOPA|SINEMET|DOPICAR", "Carbidopa/Levodopa"),
    (r"RASAGILIN|AZILECT", "Rasagiline"),
    (r"SELEGILIN|ELDEPRYL|ZELAPAR", "Selegiline"),
    (r"SAFINAMID|XADAGO", "Safinamide"),
    (r"AMANTADIN|GOCOVRI|OSMOLEX", "Amantadine"),
    (r"ENTACAPON|COMTAN", "Entacapone"),
    (r"OPICAPON|ONGENTYS", "Opicapone"),
    (rf"(PRAMIPEXOL|MIRAPEX|SIFROL).*{_RELEASE}", "Pramipexole ER"),
    (r"PRAMIPEXOL|MIRAPEX|SIFROL", "Pramipexole"),
    (rf"(ROPIN[IE]ROL|REQUIP).*{_RELEASE}", "Ropinirole ER"),
    (r"ROPIN[IE]ROL|REQUIP", "Ropinirole"),
    (r"ROTIGOTIN|NEUPRO", "Rotigotine patch"),
    (r"PIRIBEDIL|CLARIUM|TRIVASTAL", "Piribedil"),
    (r"TRIHEXYPHENIDYL|ARTANE", "Trihexyphenidyl"),
    (r"ISTRADEFYLLIN|NOURIANZ", "Istradefylline"),
]
OTHER = "(other)"


def to_generic(name):
    for pattern, generic in GENERIC_PATTERNS:
        if re.search(pattern, name):
            return generic
    return OTHER


def tidy_strength(text):
    """'25/100 MG PO' -> '25/100 mg', '6 MG TRANSDERMAL' -> '6 mg' (drops the route)."""
    text = re.sub(r"\b(PO|ORAL|TRANSDERMAL|SL|SUBLINGUAL)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("MG", "mg")


def median_or_none(values):
    """Median of a column, or None if it has no numbers (avoids numpy 'empty slice' warnings)."""
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    return numbers.median() if len(numbers) else None


ppmi_dir = os.getenv("PPMI_DATA_DIR")
if not ppmi_dir:
    sys.exit("PPMI_DATA_DIR is not set. Add it to .env, e.g. PPMI_DATA_DIR=C:\\ppmi_data")
raw_dir = Path(ppmi_dir) / "raw"
out_dir = Path(ppmi_dir) / "output"

matches = sorted(raw_dir.rglob("LEDD_Concomitant_Medication_Log_*.csv"))
if not matches:
    sys.exit(f"LEDD_Concomitant_Medication_Log_*.csv not found under {raw_dir}")
led = pd.read_csv(matches[-1], low_memory=False)

# Tidy the free text so "Sinemet ", "SINEMET" and "sinemet" are the same name
led["drug"] = led["LEDTRT"].astype(str).str.upper().str.strip().str.replace(r"\s+", " ", regex=True)
led = led[~led["drug"].isin(["", "NAN"])]
led["generic"] = led["drug"].map(to_generic)
led["strength"] = led["LEDDOSSTR"].astype(str).str.upper().str.strip().map(tidy_strength)

summary = []
for generic, rows in led[led["generic"] != OTHER].groupby("generic"):
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
        "generic": generic,
        "participants": people,
        "names_merged": rows["drug"].nunique(),
        "usual_strengths": "; ".join(f"{s} ({n})" for s, n in strengths.items()),
        "median_dose": median_or_none(rows["LEDDOSE"]),
        "median_per_day": median_or_none(rows["LEDDOSFRQ"]),
    })

result = pd.DataFrame(summary).sort_values("participants", ascending=False).reset_index(drop=True)

# Names that matched no pattern but are common enough to show -- check these for missed drugs
unmatched = (led[led["generic"] == OTHER].groupby("drug")["PATNO"].nunique()
             .sort_values(ascending=False))
unmatched = unmatched[unmatched >= MIN_PARTICIPANTS]

print(f"LEDD log: {led['PATNO'].nunique()} participants, {led['drug'].nunique()} distinct medication names, "
      f"grouped into {len(result)} generic medications.\n")
pd.set_option("display.width", 250, "display.max_colwidth", 90)
print(result.to_string())
print(f"\nCommon names that matched no pattern (used by >= {MIN_PARTICIPANTS} people):")
print(unmatched.to_string() if len(unmatched) else "  none")

out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "medications_summary.csv"
result.to_csv(out_file, index=False)
print(f"\nSaved to {out_file}")

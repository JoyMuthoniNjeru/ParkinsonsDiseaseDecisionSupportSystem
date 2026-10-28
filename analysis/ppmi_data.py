# analysis/ppmi_data.py
# Shared PPMI loading for the analysis scripts, so they all use the same participants,
# dates and stages. Code only -- the data itself stays in PPMI_DATA_DIR (outside the repo).
#
# PPMI DUA: these functions return data to the calling script; scripts must only print or
# save AGGREGATED results (no rows, no PATNOs) and apply the MIN_PARTICIPANTS rule.
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from updrs_items import UPDRS_ITEMS  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # reads PPMI_DATA_DIR

MIN_PARTICIPANTS = 10                           # hide any group smaller than this (small-numbers rule)
STAGE_EDGES = [0, 3, 6, float("inf")]           # years since diagnosis
STAGE_LABELS = ["0-2 years", "3-5 years", "6+ years"]
VALID_SCORES = {0, 1, 2, 3, 4}                  # anything else (e.g. "unable to rate" codes) is dropped

_ppmi_dir = os.getenv("PPMI_DATA_DIR")
if not _ppmi_dir:
    sys.exit("PPMI_DATA_DIR is not set. Add it to .env, e.g. PPMI_DATA_DIR=C:\\ppmi_data")
RAW_DIR = Path(_ppmi_dir) / "raw"
OUT_DIR = Path(_ppmi_dir) / "output"


def find_csv(prefix):
    """Newest CSV whose name is prefix + a date, e.g. 'MDS-UPDRS_Part_I_11Oct2026.csv'."""
    matches = sorted(p for p in RAW_DIR.rglob(f"{prefix}*.csv")
                     if p.name[len(prefix):len(prefix) + 1].isdigit())
    if not matches:
        sys.exit(f"{prefix}*.csv not found under {RAW_DIR}")
    return matches[-1]


def parse_dates(values):
    """PPMI dates are usually MM/YYYY; fall back to pandas' own parsing for anything else."""
    dates = pd.to_datetime(values, format="%m/%Y", errors="coerce")
    leftover = dates.isna() & values.notna()
    dates[leftover] = pd.to_datetime(values[leftover], errors="coerce")
    return dates


def load_item_scores(verbose=True):
    """All 26 item scores for PD participants, one row per participant x visit x item:
    PATNO, code, score (0-4), years (since diagnosis, >= 0), stage."""
    dx = pd.read_csv(find_csv("PD_Diagnosis_History_"), low_memory=False)
    dx["dx_date"] = parse_dates(dx["PDDXDT"])
    if verbose:
        print(f"PD_Diagnosis_History: {dx['PATNO'].nunique()} participants, "
              f"{dx['dx_date'].notna().sum()} with a usable diagnosis date")
    dx = dx.dropna(subset=["dx_date"]).groupby("PATNO", as_index=False)["dx_date"].min()

    parts = []
    for file_prefix in sorted({i["file"] for i in UPDRS_ITEMS}):
        codes = [i["code"] for i in UPDRS_ITEMS if i["file"] == file_prefix]
        df = pd.read_csv(find_csv(file_prefix), low_memory=False)
        df["visit_date"] = parse_dates(df["INFODT"])
        if verbose:
            print(f"{file_prefix}: {len(df)} visits, "
                  f"{df['visit_date'].notna().mean():.0%} with a usable visit date")
        parts.append(df.melt(id_vars=["PATNO", "visit_date"], value_vars=codes,
                             var_name="code", value_name="score"))
    scores = pd.concat(parts, ignore_index=True)
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    scores = scores[scores["score"].isin(VALID_SCORES)]

    scores = scores.merge(dx, on="PATNO", how="inner")  # PD participants only
    scores["years"] = (scores["visit_date"] - scores["dx_date"]).dt.days / 365.25
    before = len(scores)
    scores = scores[scores["years"] >= 0]  # drops visits before diagnosis or without a date
    if verbose:
        print(f"\nScores used: {len(scores)} item scores from {scores['PATNO'].nunique()} PD participants "
              f"({before - len(scores)} dropped: before diagnosis or no visit date)")
    scores["stage"] = pd.cut(scores["years"], bins=STAGE_EDGES, labels=STAGE_LABELS, right=False)
    return scores[["PATNO", "code", "score", "years", "stage"]].reset_index(drop=True)


def item_meta():
    """The app's item number / name / category per PPMI code, in updrs_items.py order."""
    return pd.DataFrame(UPDRS_ITEMS)[["item", "code", "name", "category"]]

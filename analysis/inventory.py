# analysis/inventory.py
# Lists every PPMI CSV with its row count, participant count and column names.
# Prints NO data rows -- only file names, counts and column names (PPMI DUA).
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()  # reads .env so we get PPMI_DATA_DIR

ppmi_dir = os.getenv("PPMI_DATA_DIR")
if not ppmi_dir:
    sys.exit("PPMI_DATA_DIR is not set. Add it to .env, e.g. PPMI_DATA_DIR=C:\\ppmi_data")

data_dir = Path(ppmi_dir) / "raw"
csv_files = sorted(data_dir.rglob("*.csv"))  # rglob also looks inside subfolders
if not csv_files:
    sys.exit(f"No CSV files found in {data_dir}")

for csv_file in csv_files:
    df = pd.read_csv(csv_file, low_memory=False)
    participants = df["PATNO"].nunique() if "PATNO" in df.columns else "n/a"
    print(f"\n=== {csv_file.relative_to(data_dir)} ===")
    print(f"rows: {len(df)}, participants: {participants}, columns: {len(df.columns)}")
    print(list(df.columns))

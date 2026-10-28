# analysis/updrs_items.py
# The 26 MDS-UPDRS Part I + II items the app uses as its symptom list, mapped to
# their PPMI column codes. ONE place for this list: the PPMI analysis scripts and
# seed.py both import it. Codes only -- no PPMI data in this file, safe to commit.
#
# All items are scored 0-4 (Normal, Slight, Mild, Moderate, Severe), the same scale
# as SymptomRecord.severity in the app, so no rescaling is needed.
#
# rated_by: who scores the item in PPMI. Items 1.1-1.6 are clinician-rated there but
# self-reported in our app -- a limitation to note in Chapter 5.

# PPMI files (in PPMI_DATA_DIR/raw) that hold each group of items
PART_I_CLINICIAN_FILE = "MDS-UPDRS_Part_I_"
PART_I_PATIENT_FILE = "MDS-UPDRS_Part_I_Patient_Questionnaire_"
PART_II_PATIENT_FILE = "MDS_UPDRS_Part_II__Patient_Questionnaire_"

UPDRS_ITEMS = [
    # Part I: non-motor experiences of daily living
    {"item": "1.1",  "code": "NP1COG",  "name": "Memory and thinking",      "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.2",  "code": "NP1HALL", "name": "Hallucinations",           "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.3",  "code": "NP1DPRS", "name": "Low mood",                 "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.4",  "code": "NP1ANXS", "name": "Anxiety",                  "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.5",  "code": "NP1APAT", "name": "Loss of motivation",       "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.6",  "code": "NP1DDS",  "name": "Urges and compulsions",    "category": "non-motor", "rated_by": "clinician", "file": PART_I_CLINICIAN_FILE},
    {"item": "1.7",  "code": "NP1SLPN", "name": "Sleep problems at night",  "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.8",  "code": "NP1SLPD", "name": "Daytime sleepiness",       "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.9",  "code": "NP1PAIN", "name": "Pain or odd sensations",   "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.10", "code": "NP1URIN", "name": "Bladder problems",         "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.11", "code": "NP1CNST", "name": "Constipation",             "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.12", "code": "NP1LTHD", "name": "Dizziness on standing",    "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    {"item": "1.13", "code": "NP1FATG", "name": "Fatigue",                  "category": "non-motor", "rated_by": "patient",   "file": PART_I_PATIENT_FILE},
    # Part II: motor experiences of daily living
    {"item": "2.1",  "code": "NP2SPCH", "name": "Speech",                       "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.2",  "code": "NP2SALV", "name": "Saliva and drooling",          "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.3",  "code": "NP2SWAL", "name": "Chewing and swallowing",       "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.4",  "code": "NP2EAT",  "name": "Eating (using cutlery)",       "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.5",  "code": "NP2DRES", "name": "Dressing",                     "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.6",  "code": "NP2HYGN", "name": "Washing and hygiene",          "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.7",  "code": "NP2HWRT", "name": "Handwriting",                  "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.8",  "code": "NP2HOBB", "name": "Hobbies and activities",       "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.9",  "code": "NP2TURN", "name": "Turning in bed",               "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.10", "code": "NP2TRMR", "name": "Tremor",                       "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.11", "code": "NP2RISE", "name": "Getting up (bed, car, chair)", "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.12", "code": "NP2WALK", "name": "Walking and balance",          "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
    {"item": "2.13", "code": "NP2FREZ", "name": "Freezing",                     "category": "motor", "rated_by": "patient", "file": PART_II_PATIENT_FILE},
]

# Quick lookups, e.g. CODE_TO_NAME["NP2TRMR"] -> "Tremor"
CODE_TO_NAME = {i["code"]: i["name"] for i in UPDRS_ITEMS}
NAME_TO_CODE = {i["name"]: i["code"] for i in UPDRS_ITEMS}

assert len(UPDRS_ITEMS) == 26, "the agreed symptom list is all 26 Part I + II items"

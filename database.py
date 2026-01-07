import csv
import sqlite3
from pathlib import Path


TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mrn TEXT,
    encounter TEXT,
    note_csn_id TEXT,
    note_date TEXT,
    note_type TEXT,
    note TEXT
);
"""

WORKING_RESULTS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS working_results (
    id INTEGER PRIMARY KEY,
    ScopeType_NLP TEXT,
    Colonoscopy_NLP INTEGER,
    ColonoscopyInformation_NLP TEXT,
    Endoscopy_NLP INTEGER,
    EndoscopyInformation_NLP TEXT,
    NumberOfDuodenalBiopsies_NLP INTEGER,
    DuodenalBiopsiesTaken_NLP INTEGER,
    DuodenalBiopsiesInformation_NLP TEXT,
    FellowPresent_NLP INTEGER,
    FellowInformation_NLP TEXT,
    ScopeType_LLM TEXT,
    Colonoscopy_LLM INTEGER,
    ColonoscopyInformation_LLM TEXT,
    Endoscopy_LLM INTEGER,
    EndoscopyInformation_LLM TEXT,
    NumberOfDuodenalBiopsies_LLM INTEGER,
    DuodenalBiopsiesTaken_LLM INTEGER,
    DuodenalBiopsiesInformation_LLM TEXT,
    FellowPresent_LLM INTEGER,
    FellowInformation_LLM TEXT,
    RawResponse_LLM TEXT,
    AllHardDataInAgreement INTEGER,
    FOREIGN KEY (id) REFERENCES notes(id)
);
"""


INSERT_SQL = """
INSERT INTO notes (
    mrn,
    encounter,
    note_csn_id,
    note_date,
    note_type,
    note
) VALUES (?, ?, ?, ?, ?, ?);
"""


SELECT_ALL_SQL = "SELECT * FROM notes;"


CHECK_DUPLICATE_SQL = """
SELECT COUNT(*) FROM notes
WHERE mrn = ? AND encounter = ? AND note_csn_id = ? 
  AND note_date = ? AND note_type = ? AND note = ?;
"""


def create_db(db_path: Path):
    """Creates SQLite database and tables (notes, working_results). Returns database connection."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(TABLE_SCHEMA)
        conn.execute(WORKING_RESULTS_TABLE_SCHEMA)
    return conn


def ingest_csv(csv_path: Path, conn: sqlite3.Connection):
    """Reads CSV file and inserts rows into database, skipping duplicates. Returns None."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        required_columns = {
            "MRN",
            "Encounter",
            "NoteCsnID",
            "NoteDate",
            "NoteType",
            "Note",
        }

        if not required_columns.issubset(reader.fieldnames):
            raise ValueError(f"CSV must contain columns: {required_columns}")

        rows = [
            (
                row["MRN"],
                row["Encounter"],
                row["NoteCsnID"],
                row["NoteDate"],
                row["NoteType"],
                row["Note"],
            )
            for row in reader
        ]

    with conn:
        new_rows = []
        duplicates_count = 0
        
        for row in rows:
            cursor = conn.execute(CHECK_DUPLICATE_SQL, row)
            count = cursor.fetchone()[0]
            
            if count == 0:
                new_rows.append(row)
            else:
                duplicates_count += 1
        
        if new_rows:
            conn.executemany(INSERT_SQL, new_rows)
        
        if duplicates_count > 0:
            print(f"Skipped {duplicates_count} duplicate row(s) during import.")


def fetch_all_notes(conn: sqlite3.Connection):
    """Executes query to fetch all notes from database. Returns cursor."""
    return conn.execute(SELECT_ALL_SQL)


CHECK_RESULT_EXISTS_SQL = "SELECT COUNT(*) FROM working_results WHERE id = ?;"


INSERT_WORKING_RESULT_SQL = """
INSERT INTO working_results (
    id,
    ScopeType_NLP,
    Colonoscopy_NLP,
    ColonoscopyInformation_NLP,
    Endoscopy_NLP,
    EndoscopyInformation_NLP,
    NumberOfDuodenalBiopsies_NLP,
    DuodenalBiopsiesTaken_NLP,
    DuodenalBiopsiesInformation_NLP,
    FellowPresent_NLP,
    FellowInformation_NLP,
    ScopeType_LLM,
    Colonoscopy_LLM,
    ColonoscopyInformation_LLM,
    Endoscopy_LLM,
    EndoscopyInformation_LLM,
    NumberOfDuodenalBiopsies_LLM,
    DuodenalBiopsiesTaken_LLM,
    DuodenalBiopsiesInformation_LLM,
    FellowPresent_LLM,
    FellowInformation_LLM,
    RawResponse_LLM,
    AllHardDataInAgreement
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def check_result_exists(conn: sqlite3.Connection, id: int) -> bool:
    """Checks if a working_result already exists for the given id. Returns bool."""
    cursor = conn.execute(CHECK_RESULT_EXISTS_SQL, (id,))
    count = cursor.fetchone()[0]
    return count > 0


def insert_working_result(
    conn: sqlite3.Connection,
    id: int,
    nlp_result: dict,
    llm_result: dict = None,
    raw_llm_response: str = None
):
    """Inserts or updates working_result with NLP and optional LLM data. Returns None."""
    # Calculate hard data agreement if both results exist
    all_hard_data_in_agreement = None
    if llm_result is not None:
        # Compare only numerical fields
        comparison_fields = [
            "Colonoscopy", "Endoscopy",
            "NumberOfDuodenalBiopsies", "DuodenalBiopsiesTaken", "FellowPresent"
        ]
        all_hard_data_in_agreement = 1
        for field in comparison_fields:
            nlp_val = nlp_result.get(field)
            llm_val = llm_result.get(field)
            if nlp_val != llm_val:
                all_hard_data_in_agreement = 0
                break
    
    values = (
        id,
        nlp_result.get("ScopeType"),
        nlp_result.get("Colonoscopy"),
        nlp_result.get("ColonoscopyInformation"),
        nlp_result.get("Endoscopy"),
        nlp_result.get("EndoscopyInformation"),
        nlp_result.get("NumberOfDuodenalBiopsies"),
        nlp_result.get("DuodenalBiopsiesTaken"),
        nlp_result.get("DuodenalBiopsiesInformation"),
        nlp_result.get("FellowPresent"),
        nlp_result.get("FellowInformation"),
        llm_result.get("ScopeType") if llm_result else None,
        llm_result.get("Colonoscopy") if llm_result else None,
        llm_result.get("ColonoscopyInformation") if llm_result else None,
        llm_result.get("Endoscopy") if llm_result else None,
        llm_result.get("EndoscopyInformation") if llm_result else None,
        llm_result.get("NumberOfDuodenalBiopsies") if llm_result else None,
        llm_result.get("DuodenalBiopsiesTaken") if llm_result else None,
        llm_result.get("DuodenalBiopsiesInformation") if llm_result else None,
        llm_result.get("FellowPresent") if llm_result else None,
        llm_result.get("FellowInformation") if llm_result else None,
        raw_llm_response,
        all_hard_data_in_agreement
    )
    
    with conn:
        conn.execute(INSERT_WORKING_RESULT_SQL, values)

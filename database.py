import csv
import sqlite3
from pathlib import Path


NOTES_TABLE_SCHEMA = """
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
    NLP_Failed INTEGER,
    LLM_Failed INTEGER,
    FOREIGN KEY (id) REFERENCES notes(id)
);
"""

FINAL_RESULTS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS final_results (
    id INTEGER PRIMARY KEY,
    ScopeType TEXT,
    NumberOfDuodenalBiopsies INTEGER,
    DuodenalBiopsiesTaken INTEGER,
    FellowPresent INTEGER,
    FOREIGN KEY (id) REFERENCES notes(id)
);
"""

INSERT_FINAL_RESULT_SQL = """
INSERT INTO final_results (
    id,
    ScopeType,
    NumberOfDuodenalBiopsies,
    DuodenalBiopsiesTaken,
    FellowPresent
) VALUES (
    ?, ?, ?, ?, ?
);
"""

SELECT_ALL_FINAL_RESULTS = "SELECT * FROM final_results;"

INSERT_NOTE_SQL = """
INSERT INTO notes (
    mrn,
    encounter,
    note_csn_id,
    note_date,
    note_type,
    note
) VALUES (?, ?, ?, ?, ?, ?);
"""


SELECT_ALL_NOTES_SQL = "SELECT * FROM notes;"

SELECT_ALL_WORKING_RESULTS_SQL = """
SELECT
    id,
    ScopeType_NLP,
    ScopeType_LLM,
    NumberOfDuodenalBiopsies_NLP,
    NumberOfDuodenalBiopsies_LLM,
    DuodenalBiopsiesTaken_NLP,
    DuodenalBiopsiesTaken_LLM,
    FellowPresent_NLP,
    FellowPresent_LLM,
    AllHardDataInAgreement,
    NLP_Failed,
    LLM_Failed
FROM working_results
ORDER BY id;
"""



CHECK_DUPLICATE_NOTE_SQL = """
SELECT COUNT(*) FROM notes
WHERE mrn = ? AND encounter = ? AND note_csn_id = ? 
  AND note_date = ? AND note_type = ? AND note = ?;
"""


def create_db(db_path: Path):
    """
    Creates or connects to a SQLite database and initializes the notes and working_results tables.
    
    Parameters:
        db_path: Path to the database file.
    
    Returns:
        sqlite3.Connection: Database connection object.
    """
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(NOTES_TABLE_SCHEMA)
        conn.execute(WORKING_RESULTS_TABLE_SCHEMA)
    return conn


def ingest_csv(csv_path: Path, conn: sqlite3.Connection):
    """
    Ingests CSV data into the database, skipping duplicate rows.
    Validates that required columns (MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note) are present.
    
    Parameters:
        csv_path: Path to the CSV file to ingest.
        conn: Database connection object.
    """
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
            cursor = conn.execute(CHECK_DUPLICATE_NOTE_SQL, row)
            count = cursor.fetchone()[0]
            
            if count == 0:
                new_rows.append(row)
            else:
                duplicates_count += 1
        
        if new_rows:
            conn.executemany(INSERT_NOTE_SQL, new_rows)
        
        if duplicates_count > 0:
            print(f"Skipped {duplicates_count} duplicate row(s) during import.")


def fetch_all_notes(conn: sqlite3.Connection):
    """
    Fetches all notes from the database.
    
    Parameters:
        conn: Database connection object.
    
    Returns:
        sqlite3.Cursor: Cursor object containing all note records.
    """
    return conn.execute(SELECT_ALL_NOTES_SQL)


CHECK_WORKING_RESULT_EXISTS_SQL = "SELECT COUNT(*) FROM working_results WHERE id = ?;"


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
    AllHardDataInAgreement,
    NLP_Failed,
    LLM_Failed
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def check_working_result_exists(conn: sqlite3.Connection, id: int) -> bool:
    """
    Checks if a working result already exists for the given note ID.
    
    Parameters:
        conn: Database connection object.
        id: Note ID to check.
    
    Returns:
        bool: True if a result exists, False otherwise.
    """
    cursor = conn.execute(CHECK_WORKING_RESULT_EXISTS_SQL, (id,))
    count = cursor.fetchone()[0]
    return count > 0


def insert_working_result(
    conn: sqlite3.Connection,
    id: int,
    nlp_result: dict,
    llm_result: dict = None,
    raw_llm_response: str = None,
    nlp_failed: int = 0,
    llm_failed: int = 0
):
    """
    Inserts NLP and LLM analysis results into the working_results table.
    Compares hard data fields between NLP and LLM results to set AllHardDataInAgreement flag.
    
    Parameters:
        conn: Database connection object.
        id: Note ID to associate with the results.
        nlp_result: Dictionary containing NLP analysis results.
        llm_result: Optional dictionary containing LLM analysis results.
        raw_llm_response: Optional raw LLM response string.
        nlp_failed: Integer flag indicating if NLP analysis failed (1 = failed, 0 = success).
        llm_failed: Integer flag indicating if LLM analysis failed (1 = failed, 0 = success).
    """
    all_hard_data_in_agreement = None
    if llm_result is not None:
        comparison_fields = [
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
        all_hard_data_in_agreement,
        nlp_failed,
        llm_failed
    )
    
    with conn:
        conn.execute(INSERT_WORKING_RESULT_SQL, values)

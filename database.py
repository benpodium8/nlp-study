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
        conn.execute(FINAL_RESULTS_TABLE_SCHEMA)
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

        if not required_columns.issubset(reader.fieldnames or []):
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
    llm_result: dict | None = None,
    raw_llm_response: str | None = None,
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


CHECK_FINAL_RESULT_EXISTS_SQL = "SELECT COUNT(*) FROM final_results WHERE id = ?;"


def check_final_result_exists(conn: sqlite3.Connection, id: int) -> bool:
    """
    Checks if a final result already exists for the given note ID.

    Parameters:
        conn: Database connection object.
        id: Note ID to check.

    Returns:
        bool: True if a final result exists, False otherwise.
    """
    cursor = conn.execute(CHECK_FINAL_RESULT_EXISTS_SQL, (id,))
    count = cursor.fetchone()[0]
    return count > 0


def export_combined_results_to_csv(output_folder="output"):
    """
    Combine the final_results table with the notes table and export as a CSV.
    Creates the output folder if it doesn't exist.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect("data.db") as conn:
        cursor = conn.cursor()

        # Combine final_results with notes
        query = """
        SELECT notes.*, final_results.ScopeType, final_results.NumberOfDuodenalBiopsies,
               final_results.DuodenalBiopsiesTaken, final_results.FellowPresent
        FROM notes
        LEFT JOIN final_results ON notes.id = final_results.id;
        """
        cursor.execute(query)
        combined_results = cursor.fetchall()

        # Get column names
        column_names = [desc[0] for desc in cursor.description]

        # Write to CSV
        output_file = output_path / "combined_results.csv"
        with open(output_file, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(column_names)  # Write header
            writer.writerows(combined_results)  # Write data

    print(f"Combined results exported to {output_file}")


def safe_export_combined_results_to_csv(output_folder="output"):
    """
    Safely export the combined results to a CSV file only if the file does not already exist.
    If the file exists, it skips the export and prints a message.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    output_file = output_path / "combined_results.csv"

    # Check if the output file already exists
    if output_file.exists():
        print(f"File '{output_file}' already exists. Export skipped.")
        return

    export_combined_results_to_csv(output_folder)


def export_full_combined_results_to_csv(output_folder="output"):
    """
    Export all tables and their contents into a single CSV file.
    Creates the output folder if it doesn't exist.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect("data.db") as conn:
        cursor = conn.cursor()

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        # Prepare output file
        output_file = output_path / "full_combined_results.csv"
        with open(output_file, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)

            for table_name, in tables:
                writer.writerow([f"Table: {table_name}"])  # Table name header

                # Fetch all rows from the table
                cursor.execute(f"SELECT * FROM {table_name};")
                rows = cursor.fetchall()

                # Write column names and rows
                column_names = [desc[0] for desc in cursor.description]
                writer.writerow(column_names)
                writer.writerows(rows)

                writer.writerow([])  # Blank line between tables

    print(f"Full combined results exported to {output_file}")


def export_all_tables_combined_to_csv(output_folder="output"):
    """
    Combine all tables together using SQL joins and export the result as a CSV.
    Creates the output folder if it doesn't exist.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect("data.db") as conn:
        cursor = conn.cursor()

        # Combine all tables using SQL joins
        query = """
        SELECT notes.*, 
               working_results.ScopeType_NLP, working_results.Colonoscopy_NLP, 
               working_results.Endoscopy_NLP, working_results.NumberOfDuodenalBiopsies_NLP, 
               working_results.DuodenalBiopsiesTaken_NLP, working_results.FellowPresent_NLP,
               working_results.ScopeType_LLM, working_results.Colonoscopy_LLM, 
               working_results.Endoscopy_LLM, working_results.NumberOfDuodenalBiopsies_LLM, 
               working_results.DuodenalBiopsiesTaken_LLM, working_results.FellowPresent_LLM,
               final_results.ScopeType AS Final_ScopeType, 
               final_results.NumberOfDuodenalBiopsies AS Final_NumberOfDuodenalBiopsies, 
               final_results.DuodenalBiopsiesTaken AS Final_DuodenalBiopsiesTaken, 
               final_results.FellowPresent AS Final_FellowPresent
        FROM notes
        LEFT JOIN working_results ON notes.id = working_results.id
        LEFT JOIN final_results ON notes.id = final_results.id;
        """
        cursor.execute(query)
        combined_results = cursor.fetchall()

        # Get column names
        column_names = [desc[0] for desc in cursor.description]

        # Write to CSV
        output_file = output_path / "all_tables_combined_results.csv"
        with open(output_file, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(column_names)  # Write header
            writer.writerows(combined_results)  # Write data

    print(f"All tables combined results exported to {output_file}")


def safe_export_all_tables_combined_to_csv(output_folder="output"):
    """
    Safely combine all tables together using SQL joins and export the result as a CSV file.
    If the file exists, it skips the export and prints a message.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    output_file_1 = output_path / "all_tables_combined_results.csv"
    output_file_2 = output_path / "full_combined_results.csv"

    # Check if the output file already exists
    if output_file_1.exists():
        print(f"File '{output_file_1}' already exists. Export skipped.")
        return
    if output_file_2.exists():
            print(f"File '{output_file_2}' already exists. Export skipped.")
            return

    export_all_tables_combined_to_csv(output_folder)
    export_full_combined_results_to_csv(output_folder)


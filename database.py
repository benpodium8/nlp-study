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

# The fields the LLM extracts, in the order they are stored and exported.
# llm_analysis.EXPECTED_FIELDS validates the same set (plus the note id).
RESULT_FIELDS = (
    "ScopeType",
    "Colonoscopy",
    "ColonoscopyInformation",
    "Endoscopy",
    "EndoscopyInformation",
    "NumberOfDuodenalBiopsies",
    "DuodenalBiopsiesTaken",
    "DuodenalBiopsiesInformation",
    "FellowPresent",
    "FellowInformation",
)

RESULTS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    ScopeType TEXT,
    Colonoscopy INTEGER,
    ColonoscopyInformation TEXT,
    Endoscopy INTEGER,
    EndoscopyInformation TEXT,
    NumberOfDuodenalBiopsies INTEGER,
    DuodenalBiopsiesTaken INTEGER,
    DuodenalBiopsiesInformation TEXT,
    FellowPresent INTEGER,
    FellowInformation TEXT,
    RawResponse TEXT,
    LLM_Failed INTEGER,
    FOREIGN KEY (id) REFERENCES notes(id)
);
"""

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

CHECK_DUPLICATE_NOTE_SQL = """
SELECT COUNT(*) FROM notes
WHERE mrn = ? AND encounter = ? AND note_csn_id = ?
  AND note_date = ? AND note_type = ? AND note = ?;
"""

SELECT_NOTES_SQL = "SELECT id, note FROM notes ORDER BY id;"

CHECK_RESULT_EXISTS_SQL = "SELECT COUNT(*) FROM results WHERE id = ?;"

INSERT_RESULT_SQL = f"""
INSERT INTO results (
    id,
    {", ".join(RESULT_FIELDS)},
    RawResponse,
    LLM_Failed
) VALUES ({", ".join("?" * (len(RESULT_FIELDS) + 3))});
"""

COMBINED_RESULTS_SQL = f"""
SELECT notes.*,
       {", ".join(f"results.{field}" for field in RESULT_FIELDS)},
       results.LLM_Failed
FROM notes
LEFT JOIN results ON notes.id = results.id
ORDER BY notes.id;
"""


def create_db(db_path: Path):
    """
    Creates or connects to a SQLite database and initializes the notes and results tables.

    Parameters:
        db_path: Path to the database file.

    Returns:
        sqlite3.Connection: Database connection object.
    """
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(NOTES_TABLE_SCHEMA)
        conn.execute(RESULTS_TABLE_SCHEMA)
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

    print(f"Ingested {len(new_rows)} new note(s).")
    if duplicates_count > 0:
        print(f"Skipped {duplicates_count} duplicate row(s) during import.")


def check_result_exists(conn: sqlite3.Connection, id: int) -> bool:
    """
    Checks if a result already exists for the given note ID.

    Parameters:
        conn: Database connection object.
        id: Note ID to check.

    Returns:
        bool: True if a result exists, False otherwise.
    """
    cursor = conn.execute(CHECK_RESULT_EXISTS_SQL, (id,))
    count = cursor.fetchone()[0]
    return count > 0


def insert_result(
    conn: sqlite3.Connection,
    id: int,
    result: dict | None,
    raw_response: str | None,
    llm_failed: int,
):
    """
    Inserts one note's LLM analysis into the results table.

    Parameters:
        conn: Database connection object.
        id: Note ID to associate with the result.
        result: Dictionary of extracted fields, or None when the LLM failed.
        raw_response: Verbatim LLM response text, or None when the LLM failed.
        llm_failed: 1 if the LLM analysis failed, 0 if it succeeded.
    """
    extracted = tuple(result.get(field) if result else None for field in RESULT_FIELDS)
    values = (id, *extracted, raw_response, llm_failed)

    with conn:
        conn.execute(INSERT_RESULT_SQL, values)


def _write_combined_results(conn: sqlite3.Connection, output_file: Path):
    """
    Writes the notes table joined with each note's extracted fields to a CSV.
    """
    cursor = conn.execute(COMBINED_RESULTS_SQL)
    column_names = [desc[0] for desc in cursor.description]

    with output_file.open(mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)
        writer.writerows(cursor)

    print(f"Combined results exported to {output_file}")


def _write_full_dump(conn: sqlite3.Connection, output_file: Path):
    """
    Writes every table in full, one after another, to a single CSV.
    Each table is introduced by a "Table: <name>" line and ends with a blank line.
    """
    tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")]

    with output_file.open(mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)

        for table_name in tables:
            cursor = conn.execute(f"SELECT * FROM {table_name};")
            column_names = [desc[0] for desc in cursor.description]

            writer.writerow([f"Table: {table_name}"])
            writer.writerow(column_names)
            writer.writerows(cursor)
            writer.writerow([])

    print(f"Full combined results exported to {output_file}")


def export_results(conn: sqlite3.Connection, output_folder="output"):
    """
    Exports the database to CSV files in output_folder, creating the folder if needed.
    Each file is written only if it does not already exist (write-once); an existing
    file is left alone and a message is printed.

        combined_results.csv       notes joined with their extracted fields
        full_combined_results.csv  every table dumped in full, including raw LLM responses
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    exports = (
        (output_path / "combined_results.csv", _write_combined_results),
        (output_path / "full_combined_results.csv", _write_full_dump),
    )

    for output_file, write in exports:
        if output_file.exists():
            print(f"File '{output_file}' already exists. Export skipped.")
        else:
            write(conn, output_file)

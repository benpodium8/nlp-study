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

# The fields the LLM extracts, with their SQLite types, in storage and export order.
# llm_analysis.EXPECTED_FIELDS validates the same set (plus the note id).
RESULT_FIELDS = (
    ("ScopeType", "TEXT"),
    ("Colonoscopy", "INTEGER"),
    ("ColonoscopyInformation", "TEXT"),
    ("Endoscopy", "INTEGER"),
    ("EndoscopyInformation", "TEXT"),
    ("NumberOfDuodenalBiopsies", "INTEGER"),
    ("DuodenalBiopsiesTaken", "INTEGER"),
    ("DuodenalBiopsiesInformation", "TEXT"),
    ("FellowPresent", "INTEGER"),
    ("FellowInformation", "TEXT"),
)

# Debugging columns data_worker records for every note next to the extracted fields.
DEBUG_FIELDS = (
    ("LLM_Failed", "INTEGER"),    # 1 when the model call or its validation failed; the extracted fields are then null
    ("LLM_Attempts", "INTEGER"),  # model calls made for this note (retries happen only on invalid JSON)
    ("LLM_Seconds", "REAL"),      # wall-clock seconds spent on this note, all attempts included
    ("LLM_Error", "TEXT"),        # the final error message when LLM_Failed is 1
    ("RawResponse", "TEXT"),      # verbatim text of the model response that was accepted, for audit
)

RESULTS_COLUMNS = RESULT_FIELDS + DEBUG_FIELDS

RESULTS_TABLE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    {", ".join(f"{name} {sqltype}" for name, sqltype in RESULTS_COLUMNS)},
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

COUNT_NOTES_SQL = "SELECT COUNT(*) FROM notes;"

# Notes that have no results row yet, in id order.
SELECT_PENDING_NOTES_SQL = """
SELECT notes.id, notes.note
FROM notes
LEFT JOIN results ON notes.id = results.id
WHERE results.id IS NULL
ORDER BY notes.id;
"""

INSERT_RESULT_SQL = f"""
INSERT INTO results (
    id,
    {", ".join(name for name, _ in RESULTS_COLUMNS)}
) VALUES ({", ".join("?" * (len(RESULTS_COLUMNS) + 1))});
"""

# One row per note: identifiers, the extracted fields, the debugging columns, then the note text last.
EXPORT_RESULTS_SQL = f"""
SELECT notes.id, notes.mrn, notes.encounter, notes.note_csn_id, notes.note_date, notes.note_type,
       {", ".join(f"results.{name}" for name, _ in RESULTS_COLUMNS)},
       notes.note
FROM notes
LEFT JOIN results ON notes.id = results.id
ORDER BY notes.id;
"""


def create_db(db_path: Path):
    """
    Creates or connects to a SQLite database and initializes the notes and results tables.
    Adds any results column that an older database lacks.

    Parameters:
        db_path: Path to the database file.

    Returns:
        sqlite3.Connection: Database connection object.
    """
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(NOTES_TABLE_SCHEMA)
        conn.execute(RESULTS_TABLE_SCHEMA)
        _add_missing_result_columns(conn)
    return conn


def _add_missing_result_columns(conn: sqlite3.Connection):
    """
    Brings the results table of an existing database up to the current column set,
    so a data.db created before a column was introduced keeps working.
    Existing rows get NULL in any column added here.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(results);")}
    for name, sqltype in RESULTS_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE results ADD COLUMN {name} {sqltype};")
            print(f"Added missing column {name} to the results table.")


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

    print(
        f"Read {len(rows)} row(s) from {csv_path}: "
        f"{len(new_rows)} new note(s) added, {duplicates_count} duplicate(s) skipped."
    )


def insert_result(
    conn: sqlite3.Connection,
    id: int,
    result: dict | None,
    raw_response: str | None,
    llm_failed: int,
    attempts: int,
    seconds: float,
    error: str | None,
):
    """
    Inserts one note's LLM analysis, with its debugging columns, into the results table.

    Parameters:
        conn: Database connection object.
        id: Note ID to associate with the result.
        result: Dictionary of extracted fields, or None when the LLM failed.
        raw_response: Verbatim text of the accepted model response, or None when the LLM failed.
        llm_failed: 1 if the LLM analysis failed, 0 if it succeeded.
        attempts: Number of model calls made for this note.
        seconds: Wall-clock seconds spent on this note.
        error: The final error message when the analysis failed, else None.
    """
    extracted = tuple(result.get(name) if result else None for name, _ in RESULT_FIELDS)
    debug = {
        "LLM_Failed": llm_failed,
        "LLM_Attempts": attempts,
        "LLM_Seconds": seconds,
        "LLM_Error": error,
        "RawResponse": raw_response,
    }
    values = (id, *extracted, *(debug[name] for name, _ in DEBUG_FIELDS))

    with conn:
        conn.execute(INSERT_RESULT_SQL, values)


def export_results(conn: sqlite3.Connection, output_folder="output"):
    """
    Writes every note with its extracted fields and debugging columns to output_folder/results.csv,
    creating the folder if needed. The file is written only if it does not already exist
    (write-once); an existing file is left alone and a message is printed.
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    output_file = output_path / "results.csv"
    if output_file.exists():
        print(
            f"File '{output_file}' already exists. Export skipped; "
            "move it aside and run --analyze again to regenerate it from the database."
        )
        return

    cursor = conn.execute(EXPORT_RESULTS_SQL)
    column_names = [desc[0] for desc in cursor.description]

    row_count = 0
    with output_file.open(mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)
        for row in cursor:
            writer.writerow(row)
            row_count += 1

    print(f"Wrote {row_count} row(s) to {output_file}.")

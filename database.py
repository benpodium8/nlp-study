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

# Whether a human should look at the row before the study uses it, and why (review.py).
REVIEW_FIELDS = (
    ("ManualReviewRequired", "TEXT"),  # "true" or "false"
    ("ManualReviewReason", "TEXT"),    # every trigger, separated by "; ", when "true"
)

# Debugging columns data_worker records for every note next to the extracted fields.
DEBUG_FIELDS = (
    ("LLM_Failed", "INTEGER"),      # 1 when no attempt gave an answer the note supports (or the call failed); the extracted fields are then null
    ("LLM_Attempts", "INTEGER"),    # model calls made for this note
    ("LLM_Seconds", "REAL"),        # wall-clock seconds spent on this note, all attempts included
    ("LLM_Error", "TEXT"),          # why the note failed (the last attempt's rejection) when LLM_Failed is 1
    ("LLM_Rejections", "TEXT"),     # why each rejected attempt was rejected, one per line, for accepted and failed notes alike
    ("RawResponse", "TEXT"),        # verbatim text of the accepted model response; of the last rejected one when the note failed
)

RESULTS_COLUMNS = RESULT_FIELDS + REVIEW_FIELDS + DEBUG_FIELDS

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

# The header names a notes CSV must carry (case-sensitive; extra columns are ignored).
REQUIRED_CSV_COLUMNS = ("MRN", "Encounter", "NoteCsnID", "NoteDate", "NoteType", "Note")

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
    added = []
    for name, sqltype in RESULTS_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE results ADD COLUMN {name} {sqltype};")
            print(f"Added missing column {name} to the results table.")
            added.append(name)
    if "ManualReviewRequired" in added:
        _backfill_manual_review(conn)


def _backfill_manual_review(conn: sqlite3.Connection):
    """
    Fills the manual-review columns for results stored before they existed, from the
    columns those rows already have, so an older database exports "true"/"false" too.
    """
    from review import manual_review  # imported here: review depends on llm_analysis only

    rows = conn.execute(
        f"""
        SELECT results.id, notes.note, {", ".join(f"results.{name}" for name, _ in RESULT_FIELDS)},
               results.LLM_Failed, results.LLM_Attempts, results.LLM_Error, results.LLM_Rejections
        FROM results JOIN notes ON notes.id = results.id;
        """
    ).fetchall()
    for row in rows:
        note_id, note = row[0], row[1]
        extracted = dict(zip((name for name, _ in RESULT_FIELDS), row[2:2 + len(RESULT_FIELDS)]))
        llm_failed, attempts, error, rejections = row[2 + len(RESULT_FIELDS):]
        required, reason = manual_review(
            note, None if llm_failed else extracted, llm_failed or 0, attempts or 1, error, rejections
        )
        conn.execute(
            "UPDATE results SET ManualReviewRequired = ?, ManualReviewReason = ? WHERE id = ?;",
            ("true" if required else "false", reason, note_id),
        )
    print(f"Filled ManualReviewRequired for {len(rows)} existing result(s).")


def ingest_csv(csv_path: Path, conn: sqlite3.Connection):
    """
    Ingests CSV data into the database, skipping duplicate rows.
    Raises ValueError, naming the headers found, when a REQUIRED_CSV_COLUMNS entry is missing.

    Parameters:
        csv_path: Path to the CSV file to ingest.
        conn: Database connection object.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        found = reader.fieldnames or []
        missing = [name for name in REQUIRED_CSV_COLUMNS if name not in found]
        if missing:
            raise ValueError(
                f"CSV is missing the column(s) {', '.join(missing)}. "
                f"Required (case-sensitive): {', '.join(REQUIRED_CSV_COLUMNS)}. "
                f"Header found: {', '.join(found) or '(empty)'}"
            )

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


def database_status(db_path: Path) -> str:
    """
    One line about db_path for the no-argument help: how many notes it holds, how many
    are analysed and failed, and how many are pending; or why it cannot be read.
    Opens the file read-only and never creates it.
    """
    if not db_path.exists():
        return "not found here; it is created on the first run"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]
            tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")}
            if "results" in tables:
                done, failed = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LLM_Failed), 0) FROM results;"
                ).fetchone()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(results);")}
                if "ManualReviewRequired" in columns:
                    review = conn.execute(
                        "SELECT COUNT(*) FROM results WHERE ManualReviewRequired = 'true';"
                    ).fetchone()[0]
                else:
                    review = "not yet computed"
            else:
                done = failed = review = 0
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"exists but could not be read ({e})"
    return (
        f"{notes} note(s), {done} analysed ({failed} failed, {review} flagged for manual review), "
        f"{notes - done} pending"
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
    rejections: str | None = None,
    review_required: bool = False,
    review_reason: str | None = None,
):
    """
    Inserts one note's LLM analysis, with its review and debugging columns, into the results table.

    Parameters:
        conn: Database connection object.
        id: Note ID to associate with the result.
        result: Dictionary of extracted fields, or None when the LLM failed.
        raw_response: Verbatim text of the accepted model response; of the last rejected
            one when the LLM failed; None when there was no response.
        llm_failed: 1 if the LLM analysis failed, 0 if it succeeded.
        attempts: Number of model calls made for this note.
        seconds: Wall-clock seconds spent on this note.
        error: The final error message when the analysis failed, else None.
        rejections: Why each rejected attempt was rejected, one per line, else None.
        review_required: Whether a human should look at this row (review.manual_review).
        review_reason: Why, when review_required; else None.
    """
    extracted = tuple(result.get(name) if result else None for name, _ in RESULT_FIELDS)
    review = ("true" if review_required else "false", review_reason)
    debug = {
        "LLM_Failed": llm_failed,
        "LLM_Attempts": attempts,
        "LLM_Seconds": seconds,
        "LLM_Error": error,
        "LLM_Rejections": rejections,
        "RawResponse": raw_response,
    }
    values = (id, *extracted, *review, *(debug[name] for name, _ in DEBUG_FIELDS))

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

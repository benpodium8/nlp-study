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
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(TABLE_SCHEMA)
    return conn


def ingest_csv(csv_path: Path, conn: sqlite3.Connection):
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
    """Fetch all notes from the database and return cursor with results."""
    return conn.execute(SELECT_ALL_SQL)

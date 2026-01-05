import argparse
import csv
import sqlite3
from pathlib import Path
from rich.console import Console
from rich.table import Table



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
        conn.executemany(INSERT_SQL, rows)


def print_db_contents(conn: sqlite3.Connection):
    cursor = conn.execute(SELECT_ALL_SQL)
    columns = [description[0] for description in cursor.description]
    
    # Exclude the 'note' column
    note_index = columns.index("note")
    filtered_columns = [col for col in columns if col != "note"]

    console = Console()
    table = Table(title="Database contents", show_header=True, header_style="bold magenta")
    
    # Add columns to the table
    for col in filtered_columns:
        table.add_column(col)
    
    # Add rows to the table
    for row in cursor:
        # Exclude the note column value
        filtered_row = [str(value) if value is not None else "" for i, value in enumerate(row) if i != note_index]
        table.add_row(*filtered_row)
    
    console.print("\n")
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Ingest CSV into SQLite and print contents")
    parser.add_argument("csv_file", type=Path, help="Path to input CSV file")
    parser.add_argument("--db", type=Path, default=Path("data.db"), help="SQLite database file")

    args = parser.parse_args()

    conn = create_db(args.db)
    ingest_csv(args.csv_file, conn)
    print_db_contents(conn)
    conn.close()


if __name__ == "__main__":
    main()

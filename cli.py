import argparse
from pathlib import Path

from database import create_db, ingest_csv
from display import print_db_contents


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

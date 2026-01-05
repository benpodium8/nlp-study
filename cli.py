import argparse
from pathlib import Path

from database import create_db, ingest_csv
from display import print_db_contents


def main():
    parser = argparse.ArgumentParser(description="Ingest CSV into SQLite and print contents")
    parser.add_argument("csv_file", type=Path, nargs="?", help="Path to input CSV file")
    parser.add_argument("--db", type=Path, default=Path("data.db"), help="SQLite database file")
    parser.add_argument("--print", action="store_true", help="Print database contents without ingesting CSV")

    args = parser.parse_args()

    conn = create_db(args.db)
    
    if args.print:
        # Just print the database (with note column truncated)
        print_db_contents(conn, include_note=True)
    elif args.csv_file:
        # Ingest CSV and print (without note column)
        ingest_csv(args.csv_file, conn)
        print_db_contents(conn, include_note=False)
    else:
        parser.error("Either provide a CSV file or use --print to display database contents")
    
    conn.close()


if __name__ == "__main__":
    main()

import argparse
import sys
from pathlib import Path

from database import create_db, ingest_csv
from display import print_db_contents
from data_worker import data_worker


def setup_parser():
    """Creates and configures command-line argument parser. Returns ArgumentParser instance."""
    parser = argparse.ArgumentParser(description="Ingest CSV into SQLite and print contents")
    parser.add_argument("--csv", type=Path, help="Path to input CSV file")
    parser.add_argument("--print", action="store_true", help="Print database contents without ingesting CSV")
    parser.add_argument("--go", action="store_true", help="Run the NLP worker function")
    return parser


def handle_data_worker_mode(conn, csv_file):
    """Ingests CSV if provided, then runs NLP/LLM analysis on all notes. Returns None."""
    if csv_file:
        ingest_csv(csv_file, conn)
    data_worker(conn)


def handle_print_mode(conn):
    """Prints all database contents including notes. Returns None."""
    print_db_contents(conn, include_note=True)


def handle_csv_ingest_mode(conn, csv_file):
    """Ingests CSV file into database and prints contents without notes. Returns None."""
    ingest_csv(csv_file, conn)
    print_db_contents(conn, include_note=False)


def main():
    """Main entry point: parses arguments and executes appropriate mode. Returns None."""
    parser = setup_parser()
    args = parser.parse_args()

    if not args.go and not args.print and not args.csv:
        parser.print_help()
        sys.exit(0)

    conn = create_db(Path("data.db"))
    
    try:
        if args.go:
            handle_data_worker_mode(conn, args.csv)
        elif args.print:
            handle_print_mode(conn)
        elif args.csv:
            handle_csv_ingest_mode(conn, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

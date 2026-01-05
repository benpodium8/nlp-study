import argparse
import sys
from pathlib import Path

from database import create_db, ingest_csv
from display import print_db_contents
from data_worker import data_worker


def setup_parser():
    """Configure and return the argument parser."""
    parser = argparse.ArgumentParser(description="Ingest CSV into SQLite and print contents")
    parser.add_argument("--csv", type=Path, help="Path to input CSV file")
    parser.add_argument("--db", type=Path, default=Path("data.db"), help="SQLite database file")
    parser.add_argument("--print", action="store_true", help="Print database contents without ingesting CSV")
    parser.add_argument("--go", action="store_true", help="Run the NLP worker function")
    return parser


def handle_data_worker_mode(conn, csv_file):
    """Handle --go mode: ingest CSV if provided, then run NLP worker."""
    if csv_file:
        ingest_csv(csv_file, conn)
    data_worker(conn)


def handle_print_mode(conn):
    """Handle --print mode: display database contents with note column."""
    print_db_contents(conn, include_note=True)


def handle_csv_ingest_mode(conn, csv_file):
    """Handle CSV ingestion mode: ingest CSV and display contents without note column."""
    ingest_csv(csv_file, conn)
    print_db_contents(conn, include_note=False)


def main():
    parser = setup_parser()
    args = parser.parse_args()

    # If no arguments provided, show help and exit
    if not args.go and not args.print and not args.csv:
        parser.print_help()
        sys.exit(0)

    # Create database connection
    conn = create_db(args.db)
    
    try:
        # Route to appropriate handler based on arguments
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

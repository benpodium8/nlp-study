import argparse
import sys
from pathlib import Path

from database import create_db, ingest_csv
from display import print_db_contents
from data_worker import data_worker


def setup_parser():
    """Creates and configures command-line argument parser. Returns ArgumentParser instance."""
    parser = argparse.ArgumentParser(
        description="Clinical note analysis tool: ingest CSV data, analyze with NLP/LLM, and view results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
            Examples:
            %(prog)s --csv data.csv                    # Ingest CSV and display summary
            %(prog)s --print                           # Display all database contents
            %(prog)s --analyze                         # Run NLP and LLM analysis on all notes
            %(prog)s --csv data.csv --analyze          # Ingest CSV then analyze
        """
    )
    
    parser.add_argument(
        "--csv",
        type=Path,
        metavar="FILE",
        help="Path to CSV file to ingest into database (required columns: MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note)"
    )
    
    parser.add_argument(
        "--print",
        action="store_true",
        help="Display database contents (includes full notes when used alone)"
    )
    
    parser.add_argument(
        "--analyze",
        action="store_true",
        dest="analyze",
        help="Run NLP and LLM analysis on all notes in the database"
    )
    
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

    if not args.analyze and not args.print and not args.csv:
        parser.print_help()
        sys.exit(0)

    # Validate CSV file exists if provided
    if args.csv and not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    conn = create_db(Path("data.db"))
    
    try:
        if args.analyze:
            handle_data_worker_mode(conn, args.csv)
        elif args.print:
            handle_print_mode(conn)
        elif args.csv:
            handle_csv_ingest_mode(conn, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

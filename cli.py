import argparse
import sys
from pathlib import Path
from database import create_db, ingest_csv, safe_export_combined_results_to_csv
from display import print_db_contents, print_working_results
from data_worker import data_worker
from reconcile_working_results import reconcile_working_results



def setup_parser():
    """
    Creates and configures an argument parser for the CLI.
    
    Returns:
        argparse.ArgumentParser: Configured argument parser with CSV, print, analyze, and results options.
    """
    parser = argparse.ArgumentParser(
        description="Clinical note analysis tool: ingest CSV data, analyze with NLP/LLM, and view results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=""
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
    
    parser.add_argument(
        "--working_results",
        action="store_true",
        help="Display working results table (NLP and LLM analysis output)"
    )
    
    return parser


def handle_data_worker_mode(conn, csv_file):
    """
    Handles data worker mode: ingests CSV if provided and runs analysis on all notes.
    
    Parameters:
        conn: Database connection object.
        csv_file: Optional path to CSV file to ingest before analysis.
    """
    if csv_file:
        ingest_csv(csv_file, conn)
    data_worker(conn)


def handle_print_mode(conn):
    """
    Handles print mode: displays all database contents including full notes.
    
    Parameters:
        conn: Database connection object.
    """
    print_db_contents(conn, include_note=True)


def handle_csv_ingest_mode(conn, csv_file):
    """
    Handles CSV ingest mode: imports CSV data and displays database contents without full notes.
    
    Parameters:
        conn: Database connection object.
        csv_file: Path to CSV file to ingest.
    """
    ingest_csv(csv_file, conn)
    print_db_contents(conn, include_note=False)


def handle_working_results_mode(conn):
    """
    Handles results mode: displays the working results table with NLP and LLM analysis.
    
    Parameters:
        conn: Database connection object.
    """
    print_working_results(conn)


def main():
    """
    Main entry point: parses command-line arguments and executes the appropriate mode.
    Handles CSV ingestion, analysis, printing, and results display based on user input.
    """
    parser = setup_parser()
    args = parser.parse_args()

    if not args.analyze and not args.print and not args.csv and not args.working_results:
        parser.print_help()
        sys.exit(0)

    if args.csv and not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    conn = create_db(Path("data.db"))
    
    try:
        if args.analyze:
            handle_data_worker_mode(conn, args.csv)
            reconcile_working_results(conn)
            safe_export_combined_results_to_csv()
        elif args.print:
            handle_print_mode(conn)
        elif args.working_results:
            handle_working_results_mode(conn)
        elif args.csv:
            handle_csv_ingest_mode(conn, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

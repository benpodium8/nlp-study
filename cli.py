import argparse
import sys
from pathlib import Path

from database import create_db, ingest_csv, export_results
from data_worker import data_worker


def setup_parser():
    """
    Creates and configures the argument parser for the CLI.

    Returns:
        argparse.ArgumentParser: Parser with the --csv and --analyze options.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Clinical note analysis tool: ingest a CSV of notes, extract structured "
            "data from each note with a local LLM, and export the results to CSV"
        ),
    )

    parser.add_argument(
        "--csv",
        type=Path,
        metavar="FILE",
        help=(
            "CSV file to ingest into the database (required columns: MRN, Encounter, "
            "NoteCsnID, NoteDate, NoteType, Note); duplicate rows are skipped"
        ),
    )

    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run the LLM on every note that has no result yet, then export the CSVs to output/",
    )

    return parser


def main():
    """
    Main entry point: ingests the CSV if one is given, then runs the analysis if requested.
    With no options, prints the help text.
    """
    parser = setup_parser()
    args = parser.parse_args()

    if not args.csv and not args.analyze:
        parser.print_help()
        sys.exit(0)

    if args.csv and not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    db_path = Path("data.db")
    print(f"Using database {db_path.resolve()}")
    conn = create_db(db_path)

    try:
        if args.csv:
            ingest_csv(args.csv, conn)
        if args.analyze:
            data_worker(conn)
            export_results(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

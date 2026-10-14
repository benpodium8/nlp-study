import argparse
import sys
from pathlib import Path

from database import REQUIRED_CSV_COLUMNS, create_db, database_status, ingest_csv, export_results
from data_worker import data_worker
from llm_analysis import LOCAL_MODEL, ollama_status

DB_PATH = Path("data.db")
EXPORT_PATH = Path("output") / "results.csv"

DESCRIPTION = f"""\
Extract structured data from endoscopy procedure notes with a local LLM.

Reads notes from a CSV into a local SQLite database ({DB_PATH}), sends each note once to
the model {LOCAL_MODEL} (or the one given with --model) through Ollama, stores the answer
next to the note, and writes {EXPORT_PATH}. Nothing leaves this machine.

Run with no options to see this text plus a check of what is in place right now."""

EPILOG = f"""\
What the program needs
  1. The Python packages in requirements.txt (rich, ollama), installed in the active
     environment:                                    pip install -r requirements.txt
  2. Ollama running on this machine and reachable at OLLAMA_HOST (default
     http://127.0.0.1:11434), with the model pulled: ollama pull {LOCAL_MODEL}
     Only --analyze talks to the model; ingesting with --csv alone does not.
  3. A CSV of notes, UTF-8 without a byte-order mark, whose header row contains
     exactly these column names (case-sensitive; extra columns are ignored):
       {", ".join(REQUIRED_CSV_COLUMNS)}
     Notes may span lines if the field is quoted. Rows identical to an existing note
     are skipped on ingest.
  4. Run it from the project directory: {DB_PATH} and {EXPORT_PATH.parent}/ are created in
     the current directory, and a run resumes from whatever {DB_PATH} already holds.

Typical use
  python app.py --csv notes.csv             ingest only (no model needed)
  python app.py --analyze --csv notes.csv   ingest, analyse every note, write {EXPORT_PATH}
  python app.py --analyze                   resume: analyse only the notes without a result
  python app.py --analyze --model gemma3:4b local testing only: analyse with another model
  python app.py --clean                     delete {DB_PATH} and {EXPORT_PATH} from earlier runs
  python app.py --clean --analyze --csv notes.csv   start over from scratch

Output
  {EXPORT_PATH}   one row per note: the identifiers, the ten extracted fields,
                       ManualReviewRequired ("true"/"false") with ManualReviewReason,
                       the debugging columns (LLM_Failed, LLM_Attempts, LLM_Seconds,
                       LLM_Error, LLM_Rejections, RawResponse) and the note text last.
                       A note whose answers the note text does not support is recorded
                       with LLM_Failed 1, empty fields and the reason in LLM_Error.
                       Write-once: move it aside to regenerate it from the database.
  {DB_PATH}              the same data, kept for audit and for resuming.
  Both contain PHI; treat them accordingly."""


def setup_parser():
    """
    Creates and configures the argument parser for the CLI.

    Returns:
        argparse.ArgumentParser: Parser with the --csv, --analyze, --model and --clean options.
    """
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--csv",
        type=Path,
        metavar="FILE",
        help=(
            f"CSV file to ingest into {DB_PATH} (required columns: "
            f"{', '.join(REQUIRED_CSV_COLUMNS)}); duplicate rows are skipped"
        ),
    )

    parser.add_argument(
        "--analyze",
        action="store_true",
        help=f"Run the LLM on every note that has no result yet, then write {EXPORT_PATH}",
    )

    parser.add_argument(
        "--model",
        default=LOCAL_MODEL,
        metavar="NAME",
        help=(
            f"Local testing override: use this Ollama model instead of {LOCAL_MODEL}, "
            "e.g. gemma3:4b; it must be pulled. Study runs should omit it"
        ),
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            f"Delete the files earlier runs generated ({DB_PATH} and its journal files, "
            f"{EXPORT_PATH}) before doing anything else. This discards every stored result"
        ),
    )

    return parser


def clean():
    """
    Deletes the files earlier runs generated: the database (with any SQLite journal files)
    and the export. Removes the export folder too if that leaves it empty.
    """
    generated = [DB_PATH] + [DB_PATH.with_name(DB_PATH.name + s) for s in ("-journal", "-wal", "-shm")]
    generated.append(EXPORT_PATH)

    removed = False
    for path in generated:
        if path.exists():
            path.unlink()
            print(f"Deleted {path}")
            removed = True

    if EXPORT_PATH.parent.is_dir() and not any(EXPORT_PATH.parent.iterdir()):
        EXPORT_PATH.parent.rmdir()
        print(f"Deleted {EXPORT_PATH.parent}/")

    if not removed:
        print("Nothing to clean.")


def print_status(model: str):
    """
    Reports whether each thing the program needs is in place right now:
    the Ollama server, the model, the database and the export file.
    """
    print("Current status")

    status = ollama_status(model)
    if status.reachable:
        print(f"  Ollama     reachable at {status.host}")
        if status.model_available:
            print(f"  Model      {model} is available")
        else:
            print(
                f"  Model      {model} is NOT pulled (server has: {status.detail}). "
                f"Run: ollama pull {model}"
            )
    else:
        print(
            f"  Ollama     NOT reachable at {status.host} ({status.detail}). "
            "Start it (ollama serve) or set OLLAMA_HOST. Needed for --analyze only."
        )
        print(f"  Model      {model} (cannot check until Ollama is reachable)")

    print(f"  Database   {DB_PATH}: {database_status(DB_PATH)}")

    if EXPORT_PATH.exists():
        print(f"  Export     {EXPORT_PATH} exists (write-once; move it aside to regenerate it)")
    else:
        print(f"  Export     {EXPORT_PATH} not written yet")


def main():
    """
    Main entry point: cleans up earlier runs if asked, ingests the CSV if one is given,
    then runs the analysis if requested.
    With no options, prints the help text and the current status.
    """
    parser = setup_parser()
    args = parser.parse_args()

    if args.csv and not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    if args.clean:
        clean()
        if not args.csv and not args.analyze:
            sys.exit(0)

    if not args.csv and not args.analyze:
        parser.print_help()
        print()
        print_status(args.model)
        sys.exit(0)

    print(f"Using database {DB_PATH.resolve()}")
    conn = create_db(DB_PATH)

    try:
        if args.csv:
            try:
                ingest_csv(args.csv, conn)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        if args.analyze:
            data_worker(conn, args.model)
            export_results(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

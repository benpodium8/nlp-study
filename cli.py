import argparse
import sys
from pathlib import Path

from database import REQUIRED_CSV_COLUMNS, create_db, database_status, ingest_csv, export_results
from data_worker import data_worker
from llm_analysis import CONTEXT_LENGTH, MODELS, ModelSpec, model_served, ollama_status

DB_PATH = Path("data.db")
EXPORT_PATH = Path("output") / "results.csv"

# Result columns from before they were labelled belong to the first default model, whatever
# model a run uses (see database.create_db).
LEGACY_LABEL = MODELS[0].label
MODEL_NAMES = ", ".join(m.name for m in MODELS)
PULL_COMMANDS = "\n       ".join(f"ollama pull {m.name}" for m in MODELS)

DESCRIPTION = f"""\
Extract structured data from endoscopy procedure notes with local LLMs.

Reads notes from a CSV into a local SQLite database ({DB_PATH}), sends each note once to
each of the default models, one model at a time (or only to the one given with --model):
  {MODEL_NAMES}
through Ollama, stores every model's answers next to the note in columns labelled with the
model name and context window, for example "ScopeType ({MODELS[0].label})", and writes
{EXPORT_PATH}. Nothing leaves this machine.

Run with no options to see this text plus a check of what is in place right now."""

EPILOG = f"""\
What the program needs
  1. The Python packages in requirements.txt (rich, ollama), installed in the active
     environment:                                    pip install -r requirements.txt
  2. Ollama running on this machine and reachable at OLLAMA_HOST (default
     http://127.0.0.1:11434), with each model pulled:
       {PULL_COMMANDS}
     Only --analyze talks to the models; ingesting with --csv alone does not.
  3. A CSV of notes, UTF-8 without a byte-order mark, whose header row contains
     exactly these column names (case-sensitive; extra columns are ignored):
       {", ".join(REQUIRED_CSV_COLUMNS)}
     Notes may span lines if the field is quoted. Rows identical to an existing note
     are skipped on ingest.
  4. Run it from the project directory: {DB_PATH} and {EXPORT_PATH.parent}/ are created in
     the current directory, and a run resumes from whatever {DB_PATH} already holds.

Typical use
  python app.py --csv notes.csv             ingest only (no model needed)
  python app.py --analyze --csv notes.csv   ingest, analyse every note with every model, write {EXPORT_PATH}
  python app.py --analyze                   resume: analyse only the notes a model has no result for
  python app.py --analyze --model gemma3:4b local testing only: analyse with another model,
                                            stored under its own label (gemma3:4b@{CONTEXT_LENGTH})
  python app.py --clean                     delete {DB_PATH} and {EXPORT_PATH} from earlier runs
  python app.py --clean --analyze --csv notes.csv   start over from scratch

Output
  {EXPORT_PATH}   one row per note: the identifiers, then one block of columns per
                       model, each column named "<field> (<model>@<context>)", for example
                       "ScopeType ({MODELS[0].label})". A block holds the ten extracted
                       fields, ManualReviewRequired ("true"/"false") with ManualReviewReason,
                       and the debugging columns (LLM_Failed, LLM_Attempts, LLM_Seconds,
                       LLM_Error, LLM_Rejections, RawResponse). The note text comes last.
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
        help=f"Run each model on every note that has no result for it yet, then write {EXPORT_PATH}",
    )

    parser.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help=(
            f"Local testing override: send the notes to this Ollama model only, instead of "
            f"{MODEL_NAMES}; it must be pulled. Its answers go to their own columns, labelled "
            f"NAME@{CONTEXT_LENGTH}. Study runs should omit it"
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


def print_status(models: tuple[ModelSpec, ...]):
    """
    Reports whether each thing the program needs is in place right now:
    the Ollama server, each model, the database and the export file.
    """
    print("Current status")

    status = ollama_status(models[0].name)
    if status.reachable:
        print(f"  Ollama     reachable at {status.host}")
        for model in models:
            if model_served(model.name, status.served):
                print(f"  Model      {model.name} is available (context {model.num_ctx}; columns labelled {model.label})")
            else:
                print(
                    f"  Model      {model.name} is NOT pulled (server has: {status.detail}). "
                    f"Run: ollama pull {model.name}"
                )
    else:
        print(
            f"  Ollama     NOT reachable at {status.host} ({status.detail}). "
            "Start it (ollama serve) or set OLLAMA_HOST. Needed for --analyze only."
        )
        for model in models:
            print(f"  Model      {model.name} (cannot check until Ollama is reachable)")

    lines = database_status(DB_PATH, [model.label for model in models], LEGACY_LABEL)
    print(f"  Database   {DB_PATH}: {lines[0]}")
    for line in lines[1:]:
        print(f"             {line}")

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
    models = (ModelSpec(args.model, CONTEXT_LENGTH),) if args.model else MODELS

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
        print_status(models)
        sys.exit(0)

    print(f"Using database {DB_PATH.resolve()}")
    conn = create_db(DB_PATH, [model.label for model in models], LEGACY_LABEL)

    try:
        if args.csv:
            try:
                ingest_csv(args.csv, conn)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        if args.analyze:
            data_worker(conn, models)
            export_results(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

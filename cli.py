import argparse
import sys
import time
from pathlib import Path

from rich.markup import escape

from consensus import consolidate
from database import (
    COUNT_NOTES_SQL, REQUIRED_CSV_COLUMNS, create_db, database_status, ingest_csv, model_statistics, pending_notes,
)
from data_worker import SUMMARY_EVERY, WorkerOutcome, data_worker
from export import EXPORT_NAME, REVIEW_EXPORT_NAME, export_results
from llm_analysis import LOCATIONS, MODELS, ModelSpec, max_note_chars, model_served, note_fits_context, ollama_status
from reporter import FIELD_TALLIES, HEARTBEAT_SECONDS, ModelPlan, Reporter, duration, plural

# A model's stored results give its own time per note for the run estimate once it has this many.
STORED_RATE_MIN_NOTES = 5

DB_PATH = Path("data.db")
EXPORT_PATH = Path("output") / EXPORT_NAME
REVIEW_EXPORT_PATH = Path("output") / REVIEW_EXPORT_NAME
GUIDE_PATH = Path("results-csv-guide.md")

# Result columns from before they were labelled belong to the first default model, whatever
# model a run uses (see database.create_db).
LEGACY_LABEL = MODELS[0].label
MODEL_NAMES = ", ".join(m.name for m in MODELS)
LOCAL_MODEL_NAMES = ", ".join(m.name for m in LOCATIONS["local"])
PULL_COMMANDS = "\n       ".join(f"ollama pull {m.name}" for m in MODELS)

DESCRIPTION = f"""\
Extract structured data from endoscopy procedure notes with local LLMs.

Reads notes from a CSV into a local SQLite database ({DB_PATH}), sends each note once to
each of the study models, one model at a time:
  {MODEL_NAMES}
through Ollama, stores every model's answers next to the note in columns labelled with the
model name and context window, for example "ScopeType ({MODELS[0].label})", consolidates
the models' answers into a cautious consensus block labelled "(consensus)", and writes
{EXPORT_PATH} and its review-sized copy {REVIEW_EXPORT_PATH}. Nothing leaves this machine. --location local runs the same pipeline with
small stand-in models ({LOCAL_MODEL_NAMES}) for a workstation test.

Run with no options to see this text plus a check of what is in place right now."""

EPILOG = f"""\
What the program needs
  1. The Python packages in requirements.txt (rich, ollama), installed in the active
     environment:                                    pip install -r requirements.txt
  2. Ollama running on this machine and reachable at OLLAMA_HOST (default
     http://127.0.0.1:11434), with each model pulled:
       {PULL_COMMANDS}
     (for --location local: {LOCAL_MODEL_NAMES}).
     Only --analyze talks to the models; ingesting with --csv alone does not.
  3. A CSV of notes, UTF-8 (a byte-order mark is skipped), whose header row contains
     these columns, in any case, spaces, underscores and dashes ignored, the usual
     other spellings accepted (EncounterID, note_csn_id, ...), extra columns ignored:
       {", ".join(REQUIRED_CSV_COLUMNS)}
     Notes may span lines if the field is quoted. Rows identical to an existing note
     are skipped on ingest.
  4. Run it from the project directory: {DB_PATH} and {EXPORT_PATH.parent}/ are created in
     the current directory, and a run resumes from whatever {DB_PATH} already holds.

Typical use
  python app.py --csv notes.csv             ingest only (no model needed)
  python app.py --analyze --csv notes.csv   ingest, analyse every note with every model, build the
                                            consensus, write {EXPORT_PATH}
  python app.py --analyze                   resume: analyse only the notes a model has no result for,
                                            then rebuild the consensus
  python app.py --analyze --location local  workstation test: the same run with the small
                                            models, each stored under its own label
  python app.py --clean                     delete {DB_PATH} and the exports from earlier runs
  python app.py --clean --analyze --csv notes.csv   start over from scratch

While it runs
  Timestamped lines: a run header (database, Ollama version, models, steps, work left), one
  line per note (time, attempts, token counts, the fields), one per rejected attempt, a
  progress summary every {SUMMARY_EVERY} notes with the time left for the model and the run, a
  summary per model, the consensus and export summaries, and closing tables. In a terminal a
  live panel shows the note in progress (what the model is doing, tokens, speed) and the
  totals; NLP_PLAIN=1 turns it off. In a log (not a terminal) a heartbeat line is written
  every NLP_HEARTBEAT_SECONDS ({HEARTBEAT_SECONDS:.0f}) while a model call is in progress. The run stops early
  when Ollama is unreachable or three model calls in a row fail; a model that is not pulled
  is skipped; the export is skipped when the run is incomplete. Exit codes: 0 complete,
  1 bad input, 2 incomplete, 130 interrupted (Ctrl-C keeps every stored result; --analyze
  resumes).

Output
  {EXPORT_PATH}   one row per note, laid out for Google Sheets or Excel; {GUIDE_PATH}
                       explains every column. The note identifiers come first (columns A
                       to F), then the consensus block, every column titled
                       "<what> (consensus)": "Needs human review" (TRUE or FALSE, column G:
                       the review queue) with "Review reasons", "Outcome" (unanimous,
                       agreed, majority, partial, none), "Models answered", a one-line
                       "Summary", the ten fields in plain words ("Upper endoscopy
                       performed", "Scope type", "Number of duodenal biopsies", ...),
                       "Models consulted", "Disputed fields" and "How the models voted";
                       then one block per model, every column titled
                       "<what> (<model>@<context>)", for example
                       "Scope type ({MODELS[0].label})": "Failed", the same ten fields, the
                       model's own "Needs review" with "Review reasons", "Attempts",
                       "Seconds" and "Failure reason"; then every model's "Rejected
                       attempts" and "Raw model response"; then "Note length (characters)"
                       and the note text. Yes/no values are TRUE or FALSE; a cell is empty
                       where there is no answer (no consensus, a failed model, a count the
                       note does not state).
                       The consensus block holds the fields the models agree on: a value
                       needs at least two models that answered and agree, a strict
                       majority settles a disagreement (and flags the note), and a field
                       with no majority stays empty. "Needs human review" is TRUE where a
                       human must look: the models disagreed, the agreed answer leaves a
                       study field open, contradicts the note's own attestation of a
                       trainee or sums two biopsy counts from one sentence, a model has not
                       run, or fewer than two models answered and the row is not a true
                       empty. A note a model never read (too long to send, or the call
                       failed) is always flagged: it may hold study data nobody checked. A
                       note every model read is flagged when it looks like a procedure
                       note (a procedure note type, upper endoscopy wording, or a model
                       that found a procedure) and released unflagged otherwise: the empty
                       row is the answer for a note about something else. A model's failure
                       alone does not flag a row the others agree on. A model whose answers
                       the note text does not support is recorded with "Failed" TRUE, empty
                       fields and the reason in "Failure reason".
                       Write-once: move it aside to regenerate it from the database.
  {REVIEW_EXPORT_PATH}   the same rows with the identifiers, the consensus block and the
                       note length only (columns A to X as in results.csv, then the
                       length): about 4% of the size, so the full corpus opens in Google
                       Sheets. Open it for the review queue and the study values, and
                       results.csv for the evidence, the model blocks and the note text.
                       Written with results.csv, and write-once together with it.
  {DB_PATH}              the same data, kept for audit and for resuming.
  All contain PHI; treat them accordingly."""


def setup_parser():
    """
    Creates and configures the argument parser for the CLI.

    Returns:
        argparse.ArgumentParser: Parser with the --csv, --analyze, --location and --clean options.
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
        help=(
            f"Run each model on every note that has no result for it yet, rebuild the consensus "
            f"of the models for every note, then write {EXPORT_PATH}"
        ),
    )

    parser.add_argument(
        "--location",
        choices=sorted(LOCATIONS),
        default="server",
        help=(
            f"Which model set to run. server (the default): the study models, {MODEL_NAMES}. "
            f"local: small stand-ins for a workstation test, {LOCAL_MODEL_NAMES}. Either way "
            f"every model's answers go to its own labelled columns; the models must be pulled"
        ),
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            f"Delete the files earlier runs generated ({DB_PATH} and its journal files, "
            f"{EXPORT_PATH}, {REVIEW_EXPORT_PATH}) before doing anything else. This discards every stored result"
        ),
    )

    return parser


def clean():
    """
    Deletes the files earlier runs generated: the database (with any SQLite journal files)
    and the export. Removes the export folder too if that leaves it empty.
    """
    generated = [DB_PATH] + [DB_PATH.with_name(DB_PATH.name + s) for s in ("-journal", "-wal", "-shm")]
    generated += [EXPORT_PATH, REVIEW_EXPORT_PATH]

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
        print(f"  Ollama     reachable at {status.host}" + (f" (version {status.version})" if status.version else ""))
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

    for path in (EXPORT_PATH, REVIEW_EXPORT_PATH):
        if path.exists():
            print(f"  Export     {path} exists (write-once; move both exports aside to regenerate them)")
        else:
            print(f"  Export     {path} not written yet")


def run_header(reporter: Reporter, conn, args, models: tuple[ModelSpec, ...], steps: list[str]):
    """The first lines of a run: where the data is, what the server has, and what will happen."""
    notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]
    items = [("database", f"{DB_PATH.resolve()} · {plural(notes, 'note')}")]
    if args.analyze:
        status = ollama_status(models[0].name)
        if status.reachable:
            version = f"version {status.version}" if status.version else "version unknown"
            items.append(("Ollama", f"{version} at {status.host}"))
        else:
            items.append(("Ollama", f"[bold red]NOT reachable[/bold red] at {status.host} ({escape(status.detail)})"))
        described = []
        for model in models:
            info = status.info(model.name) if status.reachable else None
            pulled = "" if not status.reachable else (f" ({info.describe()})" if info else " [bold red](not pulled)[/bold red]")
            described.append(f"{escape(model.label)}{pulled}")
        items.append(("models", f"{len(models)} (--location {args.location}): " + " · ".join(described)))
    items.append(("steps", " · ".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))))
    if args.analyze:
        feedback = (
            "a live panel below the log" if reporter.live_mode
            else f"a heartbeat line every {HEARTBEAT_SECONDS:.0f}s during a long model call (NLP_HEARTBEAT_SECONDS)"
        )
        items.append(("feedback", f"one line per note, one per rejected attempt, a progress summary every {SUMMARY_EVERY} notes, {feedback}"))
    reporter.header(items)


def work_plan(reporter: Reporter, conn, models: tuple[ModelSpec, ...]) -> dict:
    """
    What --analyze has to do: each model's pending notes (and how many of them are too long
    to send), with the model's own time per note from stored results when it has enough.
    Logged as one line and handed to the reporter for the run-level estimate.
    """
    plan = {}
    for model in models:
        rows = pending_notes(conn, model.label)
        too_long = sum(not note_fits_context(length, model.num_ctx) for _, length in rows)
        stats = model_statistics(conn, model.label)
        stored = stats.mean_seconds if stats and len(stats.seconds) >= STORED_RATE_MIN_NOTES else None
        plan[model.label] = ModelPlan(len(rows), too_long, stored)
    reporter.set_plan(plan)

    parts = []
    total_sent = 0
    for label, item in plan.items():
        text = f"{escape(label)} {item.pending:,} pending"
        if item.too_long:
            text += f" ({item.too_long:,} too long to send)"
        if item.stored_mean is not None:
            text += f", {item.stored_mean:.1f}s per note so far"
        parts.append(text)
        total_sent += item.to_send
    reporter.header([("work", f"{plural(total_sent, 'model call')} at least: " + " · ".join(parts))])
    return plan


def ingest_summary_lines(reporter: Reporter, summary, models: tuple[ModelSpec, ...]):
    """After an ingest: the note types read and how many new notes exceed the models' windows."""
    if summary.note_types:
        shown = summary.note_types.most_common(8)
        rest = len(summary.note_types) - len(shown)
        types = ", ".join(f"{escape(name or '(blank)')} {n:,}" for name, n in shown) + (f", {plural(rest, 'other type')}" if rest else "")
        reporter.log(f"[bold]types[/bold]     {types}")
    for window in sorted({model.num_ctx for model in models}):
        limit = max_note_chars(window)
        over = sum(length > limit for length in summary.new_note_lengths)
        if over:
            reporter.warn(
                f"too long for the {window:,}-token context window (more than {limit:,} characters), "
                f"to be recorded as failed without a model call: {over:,} of the {plural(summary.new, 'new note')}"
            )


def run_summary(reporter: Reporter, conn, models: tuple[ModelSpec, ...], outcome: WorkerOutcome, exported, elapsed: float, location: str):
    """The closing summary: a table of every model's stored results, the files, and what to do next."""
    results, fields = [], []
    for model in models:
        stats = model_statistics(conn, model.label)
        if stats is None or stats.analysed == 0:
            results.append([model.label, 0] + [""] * 7)
            fields.append([model.label] + [""] * len(FIELD_TALLIES))
            continue
        results.append([
            model.label, stats.analysed, stats.ok, stats.failed, stats.flagged, stats.retries,
            f"{stats.mean_seconds:.1f}" if stats.mean_seconds is not None else "",
            f"{stats.median_seconds:.1f}" if stats.median_seconds is not None else "",
            duration(sum(stats.seconds)),
        ])
        fields.append([model.label, *(stats.fields[key] for key, _ in FIELD_TALLIES)])
    reporter.table(
        "Results stored per model (every run so far)",
        ["model", "analysed", "ok", "failed", "flagged", "rejected attempts", "s/note", "median", "model time"],
        results,
    )
    reporter.table(
        "Notes answered with 1 (or a scope name) per model",
        ["model", "egd", "colonoscopy", "duodenal biopsies", "count stated", "fellow", "scope named"],
        fields,
    )
    resume = f"python app.py --analyze{'' if location == 'server' else f' --location {location}'}"
    items = [("elapsed", duration(elapsed))]
    if outcome.stopped:
        items.append(("outcome", f"[bold red]stopped early[/bold red]: {escape(outcome.stopped)}"))
        items.append(("next", f"fix the server, then resume with: {resume}"))
    elif outcome.skipped:
        items.append(("outcome", f"[bold yellow]incomplete[/bold yellow]: {', '.join(escape(l) for l in outcome.skipped)} not run (not pulled)"))
        items.append(("next", f"pull the model(s), then resume with: {resume}"))
    else:
        items.append(("outcome", f"complete: {plural(outcome.processed, 'note')} analysed in this run"))
        if exported:
            items.append(("export", f"{exported} and {REVIEW_EXPORT_PATH} (write-once; move both aside to regenerate them)"))
            items.append(("next", 'the review queue is every row with "Needs human review (consensus)" TRUE (column G); '
                                  f'"Outcome (consensus)" unanimous marks the rows every model agreed on; {GUIDE_PATH} explains the columns'))
        else:
            items.append(("export", f"{EXPORT_PATH} or {REVIEW_EXPORT_PATH} already existed and both were left alone; move them aside and run {resume} to regenerate them"))
    reporter.header(items)


def main():
    """
    Main entry point: cleans up earlier runs if asked, ingests the CSV if one is given,
    then runs the analysis if requested.
    With no options, prints the help text and the current status.
    """
    parser = setup_parser()
    args = parser.parse_args()
    models = LOCATIONS[args.location]

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

    sys.exit(run(args, models))


def run(args, models: tuple[ModelSpec, ...]) -> int:
    """Runs the requested steps with a Reporter and returns the exit code: 0 done, 1 bad input, 2 incomplete, 130 interrupted."""
    started = time.perf_counter()
    conn = create_db(DB_PATH, [model.label for model in models], LEGACY_LABEL)
    reporter = Reporter.create()
    steps = []
    if args.csv:
        steps.append(f"ingest {args.csv}")
    if args.analyze:
        steps += [f"analyse with {plural(len(models), 'model')}", "build the consensus", f"write {EXPORT_PATH}"]
    step = 0

    def section(title: str):
        nonlocal step
        step += 1
        reporter.section(f"Step {step} of {len(steps)}: {title}")

    report = lambda message: reporter.log(escape(message))  # noqa: E731  (plain text from the modules, markup-safe)
    exit_code = 0
    try:
        with reporter:
            run_header(reporter, conn, args, models, steps)
            if args.csv:
                section(f"Ingest {args.csv}")
                try:
                    summary = ingest_csv(args.csv, conn, report, reporter.ingest_progress)
                except ValueError as e:
                    reporter.error(escape(str(e)))
                    return 1
                ingest_summary_lines(reporter, summary, models)
            if args.analyze:
                section(f"Analyse with {plural(len(models), 'model')}")
                work_plan(reporter, conn, models)
                outcome = data_worker(conn, models, reporter)

                section("Build the consensus")
                reporter.busy_progress("building the consensus", 0, None)
                consolidate(conn, models, report, lambda done, total: reporter.busy_progress("building the consensus", done, total))
                reporter.busy_done()

                section(f"Write {EXPORT_PATH}")
                exported = None
                if outcome.complete:
                    reporter.busy_progress(f"writing {EXPORT_PATH}", 0, None)
                    exported = export_results(conn, EXPORT_PATH.parent, report)
                    reporter.busy_done()
                elif outcome.stopped:
                    reporter.warn(f"export skipped: the run stopped early, so {EXPORT_PATH} would be incomplete and, being write-once, would block the complete one")
                else:
                    reporter.warn(f"export skipped: {', '.join(outcome.skipped)} did not run, so {EXPORT_PATH} would be incomplete and, being write-once, would block the complete one")
                run_summary(reporter, conn, models, outcome, exported, time.perf_counter() - started, args.location)
                exit_code = 0 if outcome.complete else 2
            else:
                reporter.header([("elapsed", duration(time.perf_counter() - started))])
    except KeyboardInterrupt:
        m = reporter.model
        where = ""
        if m is not None:
            where = f" {m.spec.label} had stored {m.done} of {plural(m.pending, 'note')} in this pass;"
            if m.call is not None:
                where += f" note {m.call.note_id} was in progress and is not stored."
        resume = f"python app.py --analyze{'' if args.location == 'server' else f' --location {args.location}'}"
        reporter.log(f"[bold red]Interrupted.[/bold red]{where} Every stored result is kept; resume with: {resume}")
        exit_code = 130
    finally:
        conn.close()
    return exit_code


if __name__ == "__main__":
    main()

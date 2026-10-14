import time
from datetime import timedelta
from typing import NamedTuple, Sequence

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn

from database import COUNT_NOTES_SQL, insert_result, pending_notes
from llm_analysis import (
    MODELS, ModelSpec, llm_analysis, LLMAnalysisError, RetryableLLMError, note_fits_context, too_long_error,
)
from review import manual_review

# Total attempts per note when the model's answer is rejected (invalid JSON, runaway text,
# a value that fails validation, or an answer the note does not support). A failed call
# (server down) is recorded on the first attempt.
MAX_ATTEMPTS = 5

# A running summary (done, failed, speed, time remaining) is logged after this many notes.
SUMMARY_EVERY = 10


class Analysis(NamedTuple):
    result: dict | None       # the validated fields, or None when the LLM failed
    raw_response: str | None  # verbatim model text of the accepted attempt; of the last rejected one when the LLM failed
    llm_failed: int           # 0 or 1
    attempts: int             # model calls made for this note
    error: str | None         # the final error message when llm_failed is 1
    rejections: list[str]     # why each rejected attempt was rejected, in order
    brief_error: str | None = None  # error without the rejected value, safe to log (no note text)


def analyze_note(note_id: int, note: str, model: ModelSpec, console: Console) -> Analysis:
    """
    Runs the LLM analysis on one note with the given model.
    Retries when the model's answer is rejected (RetryableLLMError: unusable output, or an
    answer the note does not support), up to MAX_ATTEMPTS attempts in total, telling the
    model why its previous answer was rejected; any other failure is recorded immediately.
    Every rejection is kept, so the export shows why an accepted answer needed retries and
    why a failed note failed on each attempt.
    A note too long for the model's context window is not sent at all (Ollama would silently
    drop the instructions and the start of the note); it is recorded as failed with 0 attempts.
    """
    if not note_fits_context(note, model.num_ctx):
        error = too_long_error(note, model.num_ctx)
        return Analysis(None, None, 1, 0, error, [], error)

    rejected = None
    rejections = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result, raw_response = llm_analysis(note_id, note, model, attempt, rejected)
            return Analysis(result, raw_response, 0, attempt, None, rejections)
        except RetryableLLMError as e:
            rejected = str(e)
            rejections.append(f"attempt {attempt}: {e}")
            if attempt < MAX_ATTEMPTS:
                _log(console, 
                    f"[yellow]retry [/yellow] note {note_id}: "
                    f"attempt {attempt}/{MAX_ATTEMPTS}: {escape(e.brief)}"
                )
                continue
            return Analysis(None, e.raw, 1, attempt, str(e), rejections, e.brief)
        except LLMAnalysisError as e:
            return Analysis(None, None, 1, attempt, str(e), rejections, str(e))
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            return Analysis(None, None, 1, attempt, error, rejections, error)


def _log(console: Console, message: str):
    """
    Prints one timestamped line. Unlike console.log it never wraps, so the lines stay
    greppable in a job log that is not a terminal.
    """
    console.print(f"[{time.strftime('%H:%M:%S')}] {message}", soft_wrap=True)


def _duration(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))


def _log_progress(console: Console, done: int, pending: int, failed: int, elapsed: float):
    per_note = elapsed / done
    remaining = per_note * (pending - done)
    _log(console, 
        f"[bold]progress[/bold] {done}/{pending} done, {failed} failed, "
        f"{per_note:.1f}s per note, {_duration(elapsed)} elapsed, about {_duration(remaining)} remaining"
    )


def data_worker(conn, models: Sequence[ModelSpec] = MODELS):
    """
    Runs the LLM analysis on every note that has no result yet for each model in models,
    one model at a time so Ollama holds a single model loaded, and stores each result with
    its debugging columns in the database under the model's label.
    Logs one line per note, a running summary every SUMMARY_EVERY notes, and a summary per
    model. Only the structured fields are logged, never note text or model output.
    """
    console = Console()
    total_notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]

    for index, model in enumerate(models, start=1):
        _log(console, f"Model: {model.label} ({index} of {len(models)})")
        _run_model(conn, model, total_notes, console)


def _run_model(conn, model: ModelSpec, total_notes: int, console: Console):
    """Analyses every note that has no result under model's label yet, and stores each one."""
    rows = pending_notes(conn, model.label)
    pending = len(rows)

    _log(console, 
        f"{total_notes} note(s) in the database, {total_notes - pending} already analysed with "
        f"{model.label}, {pending} to process."
    )
    if pending == 0:
        console.print("[bold yellow]No notes to process.[/bold yellow]")
        return

    failed_ids = []
    review_count = 0
    started = time.perf_counter()

    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:

        task = progress.add_task(f"Analysing notes with {model.name}", total=pending)

        for done, (note_id, note) in enumerate(rows, start=1):
            note_started = time.perf_counter()
            analysis = analyze_note(note_id, note, model, console)
            seconds = round(time.perf_counter() - note_started, 2)
            rejections = "\n".join(analysis.rejections) or None
            review_required, review_reason = manual_review(
                note, analysis.result, analysis.llm_failed, analysis.attempts, analysis.error,
                rejections, model.num_ctx,
            )
            review_count += review_required

            insert_result(
                conn,
                note_id,
                model.label,
                analysis.result,
                analysis.raw_response,
                analysis.llm_failed,
                analysis.attempts,
                seconds,
                analysis.error,
                rejections,
                review_required,
                review_reason,
            )

            if analysis.llm_failed:
                failed_ids.append(note_id)
                attempts = (
                    "without a model call" if analysis.attempts == 0
                    else f"after {analysis.attempts} attempt(s), {seconds:.1f}s"
                )
                _log(console, 
                    f"[red]FAILED[/red] note {note_id} ({done}/{pending}) "
                    f"{attempts}: {escape(analysis.brief_error)}"
                )
            else:
                r = analysis.result
                _log(console, 
                    f"[green]ok    [/green] note {note_id} ({done}/{pending}) "
                    f"{seconds:.1f}s, attempt {analysis.attempts}: "
                    f"scope={escape(repr(r['ScopeType']))} egd={r['Endoscopy']} "
                    f"colonoscopy={r['Colonoscopy']} duodenal_biopsies={r['NumberOfDuodenalBiopsies']} "
                    f"taken={r['DuodenalBiopsiesTaken']} fellow={r['FellowPresent']}"
                    + (f" [yellow]review:[/yellow] {review_reason}" if review_required else "")
                )

            progress.advance(task)

            if done % SUMMARY_EVERY == 0 and done < pending:
                _log_progress(console, done, pending, len(failed_ids), time.perf_counter() - started)

    elapsed = time.perf_counter() - started
    _log(console, 
        f"[bold green]Finished[/bold green] {pending} note(s) with {model.label} in {_duration(elapsed)}: "
        f"{pending - len(failed_ids)} ok, {len(failed_ids)} failed, {review_count} flagged for "
        f"manual review, {elapsed / pending:.1f}s per note."
    )
    if failed_ids:
        _log(console, f"Failed note id(s): {', '.join(str(i) for i in failed_ids)}")

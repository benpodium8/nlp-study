import time
from datetime import timedelta
from typing import NamedTuple

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn

from database import COUNT_NOTES_SQL, SELECT_PENDING_NOTES_SQL, insert_result
from llm_analysis import LOCAL_MODEL, llm_analysis, LLMAnalysisError

# Total attempts per note when the model returns something that is not JSON.
# Every other failure is recorded on the first attempt.
MAX_ATTEMPTS = 5

# A running summary (done, failed, speed, time remaining) is logged after this many notes.
SUMMARY_EVERY = 10


class Analysis(NamedTuple):
    result: dict | None       # the validated fields, or None when the LLM failed
    raw_response: str | None  # verbatim model text of the accepted attempt, or None
    llm_failed: int           # 0 or 1
    attempts: int             # model calls made for this note
    error: str | None         # the final error message when llm_failed is 1


def analyze_note(note_id: int, note: str, model: str, console: Console) -> Analysis:
    """
    Runs the LLM analysis on one note with the given model.
    Retries when the model's output cannot be parsed as JSON, up to MAX_ATTEMPTS
    attempts in total; any other failure is recorded immediately.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result, raw_response = llm_analysis(note_id, note, model, attempt)
            return Analysis(result, raw_response, 0, attempt, None)
        except LLMAnalysisError as e:
            error = str(e)
            is_json_error = "Invalid JSON" in error or "JSONDecodeError" in error
            if is_json_error and attempt < MAX_ATTEMPTS:
                _log(console, 
                    f"[yellow]retry [/yellow] note {note_id}: "
                    f"attempt {attempt}/{MAX_ATTEMPTS} did not return valid JSON"
                )
                continue
            return Analysis(None, None, 1, attempt, error)
        except Exception as e:
            return Analysis(None, None, 1, attempt, f"{type(e).__name__}: {e}")


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


def data_worker(conn, model: str = LOCAL_MODEL):
    """
    Runs the LLM analysis (with model) on every note that has no result yet and stores each
    result, with its debugging columns, in the database. Logs one line per note,
    a running summary every SUMMARY_EVERY notes, and a final summary.
    Only the structured fields are logged, never note text or model output.
    """
    console = Console()

    total_notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]
    rows = conn.execute(SELECT_PENDING_NOTES_SQL).fetchall()
    pending = len(rows)

    _log(console, f"Model: {model}")
    _log(console, 
        f"{total_notes} note(s) in the database, {total_notes - pending} already analysed, "
        f"{pending} to process."
    )
    if pending == 0:
        console.print("[bold yellow]No notes to process.[/bold yellow]")
        return

    failed_ids = []
    started = time.perf_counter()

    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:

        task = progress.add_task("Analysing notes", total=pending)

        for done, (note_id, note) in enumerate(rows, start=1):
            note_started = time.perf_counter()
            analysis = analyze_note(note_id, note, model, console)
            seconds = round(time.perf_counter() - note_started, 2)

            insert_result(
                conn,
                note_id,
                analysis.result,
                analysis.raw_response,
                analysis.llm_failed,
                analysis.attempts,
                seconds,
                analysis.error,
            )

            if analysis.llm_failed:
                failed_ids.append(note_id)
                _log(console, 
                    f"[red]FAILED[/red] note {note_id} ({done}/{pending}) "
                    f"after {analysis.attempts} attempt(s), {seconds:.1f}s: {escape(analysis.error)}"
                )
            else:
                r = analysis.result
                _log(console, 
                    f"[green]ok    [/green] note {note_id} ({done}/{pending}) "
                    f"{seconds:.1f}s, attempt {analysis.attempts}: "
                    f"scope={escape(repr(r['ScopeType']))} egd={r['Endoscopy']} "
                    f"colonoscopy={r['Colonoscopy']} duodenal_biopsies={r['NumberOfDuodenalBiopsies']} "
                    f"taken={r['DuodenalBiopsiesTaken']} fellow={r['FellowPresent']}"
                )

            progress.advance(task)

            if done % SUMMARY_EVERY == 0 and done < pending:
                _log_progress(console, done, pending, len(failed_ids), time.perf_counter() - started)

    elapsed = time.perf_counter() - started
    _log(console, 
        f"[bold green]Finished[/bold green] {pending} note(s) in {_duration(elapsed)}: "
        f"{pending - len(failed_ids)} ok, {len(failed_ids)} failed, {elapsed / pending:.1f}s per note."
    )
    if failed_ids:
        _log(console, f"Failed note id(s): {', '.join(str(i) for i in failed_ids)}")

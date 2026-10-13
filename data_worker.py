from database import SELECT_NOTES_SQL, check_result_exists, insert_result
from llm_analysis import llm_analysis, LLMAnalysisError
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.console import Console

# Total attempts per note when the model returns something that is not JSON.
# Every other failure is recorded on the first attempt.
MAX_ATTEMPTS = 5


def analyze_note(note_id: int, note: str, console: Console):
    """
    Runs the LLM analysis on one note.
    Retries when the model's output cannot be parsed as JSON, up to MAX_ATTEMPTS
    attempts in total; any other failure is recorded immediately.

    Returns:
        tuple: (result dict or None, raw response or None, llm_failed flag 0/1).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result, raw_response = llm_analysis(note_id, note)
            return result, raw_response, 0
        except LLMAnalysisError as e:
            error_msg = str(e)
            is_json_error = "Invalid JSON" in error_msg or "JSONDecodeError" in error_msg
            if is_json_error and attempt < MAX_ATTEMPTS:
                continue
            console.log(f"[red]LLM ERROR[/red] id={note_id}: {e}")
            return None, None, 1
        except Exception as e:
            console.log(f"[red]LLM ERROR[/red] id={note_id}: {e}")
            return None, None, 1


def data_worker(conn):
    """
    Processes all notes in the database: runs the LLM analysis on each note,
    skipping notes that already have a result, and stores the results in the database.
    """
    console = Console()

    rows = conn.execute(SELECT_NOTES_SQL).fetchall()

    total = len(rows)
    if total == 0:
        console.print("[bold yellow]No notes to process.[/bold yellow]")
        return

    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:

        task = progress.add_task("Processing notes", total=total)

        for note_id, note in rows:
            if check_result_exists(conn, note_id):
                progress.advance(task)
                continue

            result, raw_response, llm_failed = analyze_note(note_id, note, console)
            insert_result(conn, note_id, result, raw_response, llm_failed)

            progress.advance(task)

    console.print("[bold green]Finished processing notes.[/bold green]")

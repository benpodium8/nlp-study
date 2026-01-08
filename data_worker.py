from database import SELECT_ALL_NOTES_SQL, check_working_result_exists, insert_working_result
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis, LLMAnalysisError
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.console import Console

def data_worker(conn):
    """
    Processes all notes in the database: runs NLP and LLM analysis on each note,
    skipping notes that already have results, and stores the results in the database.
    """
    console = Console()

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_NOTES_SQL)
        rows = cursor.fetchall()

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

            for row in rows:
                note = row[6]
                id = row[0]

                if check_working_result_exists(conn, id):
                    progress.advance(task)
                    continue

                default_nlp_result = {
                    "id": id,
                    "ScopeType": None,
                    "Colonoscopy": 0,
                    "ColonoscopyInformation": None,
                    "Endoscopy": 0,
                    "EndoscopyInformation": None,
                    "NumberOfDuodenalBiopsies": 0,
                    "DuodenalBiopsiesTaken": 0,
                    "DuodenalBiopsiesInformation": None,
                    "FellowPresent": 0,
                    "FellowInformation": None,
                }

                nlp_failed = 0
                try:
                    nlp_result = nlp_analysis(id, note)
                except Exception as e:
                    nlp_result = default_nlp_result
                    nlp_failed = 1
                    console.log(f"[yellow]NLP ERROR[/yellow] id={id}: {e}")

                llm_result = None
                raw_llm_response = None
                llm_failed = 0
                max_retries = 5
                retry_count = 0
                success = False

                while retry_count < max_retries and not success:
                    try:
                        llm_result, raw_llm_response = llm_analysis(id, note)
                        success = True
                    except LLMAnalysisError as e:
                        error_msg = str(e)
                        is_json_error = "Invalid JSON" in error_msg or "JSONDecodeError" in error_msg

                        if is_json_error and retry_count < max_retries - 1:
                            retry_count += 1
                        else:
                            llm_failed = 1
                            console.log(f"[red]LLM ERROR[/red] id={id}: {e}")
                            break
                    except Exception as e:
                        llm_failed = 1
                        console.log(f"[red]LLM ERROR[/red] id={id}: {e}")
                        break

                insert_working_result(
                    conn,
                    id,
                    nlp_result,
                    llm_result,
                    raw_llm_response,
                    nlp_failed,
                    llm_failed,
                )

                progress.advance(task)

        console.print("[bold green]Finished processing working results.[/bold green]")

import time
from typing import NamedTuple, Sequence

from database import COUNT_NOTES_SQL, insert_result, pending_notes
from llm_analysis import (
    MODELS, ModelSpec, llm_analysis, LLMAnalysisError, RetryableLLMError, estimated_prompt_tokens, load_model,
    model_served, note_fits_context, ollama_status, too_long_error,
)
from reporter import CallProgress, Reporter
from review import manual_review

# Total attempts per note when the model's answer is rejected (invalid JSON, runaway text,
# a value that fails validation, or an answer the note does not support). A failed call
# (server down) is recorded on the first attempt.
MAX_ATTEMPTS = 5

# A running summary (done, failed, speed, time remaining) is logged after this many notes.
SUMMARY_EVERY = 10

# When this many notes in a row fail because the model call itself failed (the server is
# down, the model gone, out of memory), the pass stops instead of recording every remaining
# note as failed: those notes stay pending for the next --analyze.
CALL_FAILURES_BEFORE_STOP = 3


class Analysis(NamedTuple):
    result: dict | None       # the validated fields, or None when the LLM failed
    raw_response: str | None  # verbatim model text of the accepted attempt; of the last rejected one when the LLM failed
    llm_failed: int           # 0 or 1
    attempts: int             # model calls made for this note
    error: str | None         # the final error message when llm_failed is 1
    rejections: list[str]     # why each rejected attempt was rejected, in order
    brief_error: str | None = None     # error without the rejected value, safe to log (no note text)
    brief_rejections: tuple = ()       # each rejection without its value, safe to log and tally
    note_id: int | None = None


class WorkerOutcome(NamedTuple):
    """How a data_worker run ended, for the CLI's exit code and the export decision."""
    processed: int            # notes analysed in this run, all models together
    skipped: list             # labels of models that could not run (not pulled); their notes stay pending
    stopped: str | None       # why the run stopped early, when it did (notes stay pending)

    @property
    def complete(self) -> bool:
        return not self.skipped and self.stopped is None


def analyze_note(
    note_id: int, note: str, model: ModelSpec, call: CallProgress | None = None, reporter: Reporter | None = None,
) -> Analysis:
    """
    Runs the LLM analysis on one note with the given model.
    Retries when the model's answer is rejected (RetryableLLMError: unusable output, or an
    answer the note does not support), up to MAX_ATTEMPTS attempts in total, telling the
    model why its previous answer was rejected; any other failure is recorded immediately.
    Every rejection is kept, so the export shows why an accepted answer needed retries and
    why a failed note failed on each attempt.
    A note too long for the model's context window is not sent at all (Ollama would silently
    drop the instructions and the start of the note); it is recorded as failed with 0 attempts.
    call, when given, is fed the streamed response so the reporter can show what the model
    is doing; reporter, when given, logs each retry.
    """
    if not note_fits_context(note, model.num_ctx):
        error = too_long_error(note, model.num_ctx)
        return Analysis(None, None, 1, 0, error, [], error, (), note_id)

    rejected = None
    rejections = []
    briefs = []
    observer = call.chunk if call is not None else None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if call is not None:
            call.start_attempt(attempt)
        try:
            result, raw_response = llm_analysis(note_id, note, model, attempt, rejected, observer=observer)
            return Analysis(result, raw_response, 0, attempt, None, rejections, None, tuple(briefs), note_id)
        except RetryableLLMError as e:
            if call is not None:
                call.end_attempt()
            rejected = str(e)
            rejections.append(f"attempt {attempt}: {e}")
            briefs.append(e.brief)
            if attempt < MAX_ATTEMPTS:
                if reporter is not None and call is not None:
                    reporter.note_retry(call, attempt, e.brief)
                continue
            return Analysis(None, e.raw, 1, attempt, str(e), rejections, e.brief, tuple(briefs), note_id)
        except LLMAnalysisError as e:
            if call is not None:
                call.end_attempt()
            return Analysis(None, None, 1, attempt, str(e), rejections, str(e), tuple(briefs), note_id)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if call is not None:
                call.end_attempt()
            error = f"{type(e).__name__}: {e}"
            return Analysis(None, None, 1, attempt, error, rejections, error, tuple(briefs), note_id)


def data_worker(conn, models: Sequence[ModelSpec] = MODELS, reporter: Reporter | None = None) -> WorkerOutcome:
    """
    Runs the LLM analysis on every note that has no result yet for each model in models,
    one model at a time so Ollama holds a single model loaded, and stores each result with
    its debugging columns in the database under the model's label.
    Before each model: checks that the server is reachable (else the run stops) and the
    model pulled (else the model is skipped), and loads the model so the first note does
    not pay for it silently. Everything it does goes through reporter: one line per note,
    a summary every SUMMARY_EVERY notes, a summary per model, and the live panel or
    heartbeat in between. Only the structured fields are logged, never note text or model
    output. The pass stops after CALL_FAILURES_BEFORE_STOP consecutive call failures.
    """
    reporter = reporter or Reporter.create()
    total_notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]
    processed = 0
    skipped = []

    for index, model in enumerate(models, start=1):
        status = ollama_status(model.name)
        if not status.reachable:
            reason = f"Ollama is not reachable at {status.host} ({status.detail}); start it (ollama serve) or set OLLAMA_HOST"
            reporter.error(reason)
            return WorkerOutcome(processed, skipped, reason)
        if not model_served(model.name, status.served):
            reporter.model_skipped(model, f"not pulled on the server (it has: {status.detail}). Run: ollama pull {model.name}")
            skipped.append(model.label)
            continue
        info = status.info(model.name)
        run_processed, stopped = _run_model(conn, model, index, len(models), total_notes, reporter, info)
        processed += run_processed
        if stopped:
            return WorkerOutcome(processed, skipped, stopped)

    return WorkerOutcome(processed, skipped, None)


def _run_model(conn, model: ModelSpec, index: int, count: int, total_notes: int, reporter: Reporter, info) -> tuple[int, str | None]:
    """
    Analyses every note that has no result under model's label yet, and stores each one.
    Returns (notes processed, why the pass stopped early or None).
    """
    rows = pending_notes(conn, model.label)
    pending = len(rows)
    too_long = sum(not note_fits_context(note, model.num_ctx) for _, note in rows)
    run = reporter.begin_model(model, index, count, total_notes, total_notes - pending, pending, too_long)

    details = f"{model.name}: {info.describe()}" if info else model.name
    reporter.log(f"[bold]model[/bold]     {details}; context {model.num_ctx:,} tokens; think={model.think!r}")
    plan = f"{total_notes:,} note(s) in the database, {total_notes - pending:,} already analysed with {model.label}, {pending:,} to process"
    if too_long:
        plan += (
            f" ({pending - too_long:,} to send to the model; too long for the {model.num_ctx:,}-token window, "
            f"recorded as failed without a call: {too_long:,})"
        )
    reporter.log(f"[bold]notes[/bold]     {plan}.")
    if pending == 0:
        reporter.model_nothing_to_do()
        return 0, None

    if pending > too_long:
        reporter.model_loading()
        try:
            loaded = load_model(model)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            reporter.model_load_failed(f"{type(e).__name__}: {e}")
        else:
            reporter.model_loaded(loaded.seconds, loaded.placement, loaded.context_length)

    for position, (note_id, note) in enumerate(rows, start=1):
        call = run.begin_note(note_id, position, estimated_prompt_tokens(note), MAX_ATTEMPTS)
        note_started = time.perf_counter()
        analysis = analyze_note(note_id, note, model, call, reporter)
        seconds = round(time.perf_counter() - note_started, 2)
        call.phase = "storing"
        rejections = "\n".join(analysis.rejections) or None
        review_required, review_reason = manual_review(
            note, analysis.result, analysis.llm_failed, analysis.attempts, analysis.error,
            rejections, model.num_ctx,
        )

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

        run.finish_note(analysis, seconds, review_required, review_reason)
        reporter.note_done(call, analysis, seconds, review_required, review_reason)

        if run.call_failures >= CALL_FAILURES_BEFORE_STOP:
            reason = _call_failure_reason(model, run.call_failures, analysis.brief_error)
            reporter.model_stopped(reason)
            return position, reason

        if position % SUMMARY_EVERY == 0 and position < pending:
            reporter.progress_summary()

    reporter.model_finished()
    return pending, None


def _call_failure_reason(model: ModelSpec, failures: int, last_error: str | None) -> str:
    """Why the pass stops after consecutive call failures: what a fresh look at the server says."""
    status = ollama_status(model.name)
    if not status.reachable:
        return f"{failures} model calls in a row failed, and Ollama is no longer reachable at {status.host} ({status.detail})"
    if not model_served(model.name, status.served):
        return f"{failures} model calls in a row failed, and the server no longer serves {model.name} (it has: {status.detail})"
    return (
        f"{failures} model calls in a row failed although the server is reachable and serves {model.name}; "
        f"the last error was: {last_error}. Check the server log"
    )

"""
What the program shows while it runs.

Every event the pipeline reports goes through one Reporter. It writes timestamped lines:
one per note (ok or FAILED, with the time taken, the attempt count, Ollama's token counts
and the structured fields), one per rejected attempt (retry, with the reason), a progress
summary with the projected time left every data_worker.SUMMARY_EVERY notes, and one per
phase (ingest, each model, consensus, export) with its own summary.

When stdout is a terminal it also keeps a live panel at the bottom of the screen showing
what is happening right now: which note and attempt the model is on, whether it is still
reading the prompt or already answering, how many tokens it has produced at what speed,
the running totals, the time left for this model and for the whole run, and the tally of
the fields extracted so far. The panel refreshes ten times a second; the lines scroll
above it. Its spinner turns at the pace of the token stream: faster the more tokens a second
the model produces, a crawl while nothing is arriving.

When stdout is not a terminal (an SGE job log, a pipe), there is no panel: the same lines
are written, plus a heartbeat line every HEARTBEAT_SECONDS while a model call is in
progress, so that `tail -f` on the log always shows whether the program is alive and what
it is waiting for. NLP_PLAIN=1 forces this plain mode in a terminal.

Nothing here prints note text or model output: only identifiers, counts, timings, token
counts, the structured fields and the rejection reasons with their values stripped.
"""

import os
import re
import threading
import time
from collections import Counter, deque
from datetime import timedelta
from typing import NamedTuple, Sequence

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

# Plain mode: a heartbeat line is written when a model call has been running this long
# since the last line, and again every this many seconds after that.
HEARTBEAT_SECONDS = float(os.environ.get("NLP_HEARTBEAT_SECONDS", "60"))

# How many of the latest per-note times the "last N" rate averages.
RECENT_NOTES = 10

# A call whose load_duration reaches this many seconds (re)loaded the model; the note line says so.
LOAD_NOTICE_SECONDS = 1.0

# The live panel redraws this many times a second: enough to show the spinner change pace.
LIVE_REFRESH_PER_SECOND = 10

# The panel's spinner (rich's "dots" frames) is driven by the token stream. At
# SPINNER_REFERENCE_TOKENS_PER_SECOND it turns at its normal pace (one frame every
# SPINNER_INTERVAL seconds); the pace scales with the square root of the rate, so a quarter of
# the reference is half pace and four times it is double, within SPINNER_MIN_SPEED and
# SPINNER_MAX_SPEED. While no tokens are arriving (the server reading the prompt, a model
# loading, a stalled stream) it crawls at the minimum. The rate it follows is measured over
# the last SPINNER_RATE_WINDOW seconds, so a stall shows within that time; the window is long
# enough that a 27B model on a CPU, under one token a second, still registers a rate.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_INTERVAL = 0.08
SPINNER_REFERENCE_TOKENS_PER_SECOND = 20.0
SPINNER_MIN_SPEED = 0.25
SPINNER_MAX_SPEED = 3.0
SPINNER_RATE_WINDOW = 5.0


def spinner_speed(tokens_per_second: float | None) -> float:
    """The spinner's pace for a stream rate: 1.0 at the reference rate, square-root scaled, bounded."""
    if not tokens_per_second or tokens_per_second <= 0:
        return SPINNER_MIN_SPEED
    pace = (tokens_per_second / SPINNER_REFERENCE_TOKENS_PER_SECOND) ** 0.5
    return min(SPINNER_MAX_SPEED, max(SPINNER_MIN_SPEED, pace))


class TokenSpinner:
    """
    A spinner whose pace can change between refreshes. rich's Spinner picks its frame from the
    wall clock times one fixed speed, so changing the speed would make it jump about; this one
    keeps its own clock, advanced at whatever pace each refresh asks for, and only moves forward.
    """

    def __init__(self, style: str = "cyan"):
        self.style = style
        self.phase = 0.0          # seconds of animation at pace 1.0
        self.last_time = None

    def frame(self, speed: float = 1.0, now: float | None = None) -> Text:
        """The frame to show now, after advancing the animation at speed since the last call."""
        now = time.perf_counter() if now is None else now
        if self.last_time is not None:
            self.phase += max(0.0, now - self.last_time) * speed
        self.last_time = now
        index = int(self.phase / SPINNER_INTERVAL) % len(SPINNER_FRAMES)
        return Text(SPINNER_FRAMES[index], style=self.style)

# The field tallies, in display order: counter key -> text.
FIELD_TALLIES = (
    ("egd", "egd"),
    ("colonoscopy", "colonoscopy"),
    ("biopsies", "duodenal biopsies"),
    ("counted", "count stated"),
    ("fellow", "fellow"),
    ("scope", "scope named"),
)

# How a review reason (review.manual_review) is counted: the first prefix that matches.
REVIEW_REASON_KEYS = (
    ("note too long", "note too long for the window, not sent"),
    ("the model call failed", "the model call failed"),
    ("no answer the note supports", "no answer the note supports"),
    ("accepted on attempt", "accepted only after a rejected answer"),
    ("upper endoscopy documented but no scope name", "upper endoscopy but no scope name"),
    ("duodenal biopsies taken but no count", "duodenal biopsies but no count"),
    ("note is about", "note may exceed the context window"),
)

_DIGITS_RE = re.compile(r"\d+")


def duration(seconds: float | None) -> str:
    """A time span as 0:11:21 (days when needed), or ? when unknown."""
    if seconds is None:
        return "?"
    return str(timedelta(seconds=max(0, round(seconds))))


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """'1 note', '2 notes'."""
    return f"{count:,} {singular if count == 1 else (plural_form or singular + 's')}"


def rejection_key(brief: str) -> str:
    """
    The category of a rejection, for the tally: the first sentence of the brief message
    (RetryableLLMError.brief, which carries no value from the note), numbers replaced by N.
    """
    first = re.split(r"\. |:\s|;|\(", brief, maxsplit=1)[0].strip().rstrip(".;,")
    return _shorten(_DIGITS_RE.sub("N", first))


def review_key(reason: str) -> str:
    """The category of one manual-review reason, for the tally."""
    for prefix, key in REVIEW_REASON_KEYS:
        if reason.startswith(prefix):
            return key
    return _shorten(_DIGITS_RE.sub("N", reason.split(";")[0]))


def _shorten(text: str, limit: int = 100) -> str:
    """text cut at a word boundary to about limit characters, with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " ..."


def top(counter: Counter, limit: int = 4) -> str:
    """'reason (12), other reason (3)' for the most common entries of counter."""
    return ", ".join(f"{escape(key)} ({n})" for key, n in counter.most_common(limit))


class ModelPlan(NamedTuple):
    """One model's share of the run, for the run-level time estimate."""
    pending: int               # notes this run still has to process with the model
    too_long: int              # of which are recorded as failed without a model call
    stored_mean: float | None  # mean seconds per note in results stored earlier, if enough of them

    @property
    def to_send(self) -> int:
        return self.pending - self.too_long


class CallProgress:
    """
    What the model is doing for one note right now. llm_analysis.llm_analysis feeds it every
    streamed chunk through chunk(); the live panel and the heartbeat read it. One instance
    per note: start_attempt resets the per-attempt counters, the note totals accumulate.
    """

    def __init__(self, note_id: int, position: int, total: int, max_attempts: int, prompt_tokens_estimate: int):
        self.note_id = note_id
        self.position = position          # 1-based, of total notes in this model's pass
        self.total = total
        self.max_attempts = max_attempts
        self.prompt_tokens_estimate = prompt_tokens_estimate
        self.note_started = time.perf_counter()
        self.attempt = 0
        self.attempt_started = self.note_started
        self.phase = "sending"            # sending, thinking, answering, checking, storing
        self.tokens = 0                   # answer tokens streamed in this attempt
        self.thinking_tokens = 0          # thinking tokens streamed in this attempt
        self.first_token_at = None
        self.last_token_at = None
        self.recent = deque()             # arrival times of the tokens in the last SPINNER_RATE_WINDOW seconds
        self.final = None                 # this attempt's last chunk (done), with Ollama's counts
        # Totals over the note's attempts, from Ollama's counts where the stream completed.
        self.prompt_tokens = 0
        self.answer_tokens = 0
        self.thinking_total = 0
        self.eval_seconds = 0.0
        self.load_seconds = 0.0
        self.cut = False                  # an attempt's answer hit the num_predict cap

    def start_attempt(self, attempt: int):
        self.attempt = attempt
        self.attempt_started = time.perf_counter()
        self.phase = "sending"
        self.tokens = self.thinking_tokens = 0
        self.first_token_at = self.last_token_at = None
        self.recent.clear()
        self.final = None

    def chunk(self, response):
        """The observer llm_analysis calls with each streamed chunk (an ollama ChatResponse)."""
        now = time.perf_counter()
        message = response.message
        if message.thinking:
            self.thinking_tokens += 1
            self.phase = "thinking"
        if message.content:
            self.tokens += 1
            self.phase = "answering"
        if message.thinking or message.content:
            if self.first_token_at is None:
                self.first_token_at = now
            self.last_token_at = now
            self.recent.append(now)
        if response.done:
            self.final = response
            self.phase = "checking"
            self.prompt_tokens += response.prompt_eval_count or 0
            self.answer_tokens += response.eval_count or self.tokens
            self.thinking_total += self.thinking_tokens
            self.eval_seconds += (response.eval_duration or 0) / 1e9
            self.load_seconds += (response.load_duration or 0) / 1e9
            if response.done_reason == "length":
                self.cut = True

    def end_attempt(self):
        """Closes the attempt's books when it ended without a final chunk (an error mid-stream)."""
        if self.final is None:
            self.answer_tokens += self.tokens
            self.thinking_total += self.thinking_tokens

    def attempt_seconds(self) -> float:
        return time.perf_counter() - self.attempt_started

    def note_seconds(self) -> float:
        return time.perf_counter() - self.note_started

    def rate(self) -> float | None:
        """Tokens per second streamed in this attempt so far; None before the first token."""
        if self.first_token_at is None:
            return None
        elapsed = time.perf_counter() - self.first_token_at
        streamed = self.tokens + self.thinking_tokens
        return streamed / elapsed if elapsed > 0.5 and streamed > 1 else None

    def recent_rate(self) -> float | None:
        """
        Tokens per second over the last SPINNER_RATE_WINDOW seconds, what the spinner and the
        activity text follow; None when fewer than two tokens arrived in that time.
        Only the reader prunes the window, so the stream's appends never race it.
        """
        now = time.perf_counter()
        cutoff = now - SPINNER_RATE_WINDOW
        while self.recent and self.recent[0] < cutoff:
            self.recent.popleft()
        if len(self.recent) < 2:
            return None
        return len(self.recent) / max(now - self.recent[0], SPINNER_INTERVAL)

    def spinner_speed(self) -> float:
        """The pace the panel's spinner should turn at for this attempt right now."""
        if self.phase in ("thinking", "answering"):
            return spinner_speed(self.recent_rate())
        return SPINNER_MIN_SPEED

    def tokens_text(self) -> str:
        """'1,912 prompt + 118 answer tokens, 2.1 tok/s' over the note's attempts so far."""
        text = f"{self.prompt_tokens:,} prompt + {self.answer_tokens:,} answer tokens"
        if self.thinking_total:
            text += f" (+ {self.thinking_total:,} thinking)"
        if self.eval_seconds > 0:
            text += f", {(self.answer_tokens + self.thinking_total) / self.eval_seconds:.1f} tok/s"
        return text

    def notices(self) -> str:
        """Things worth a word on the note's line: a model reload, an answer cut at the cap."""
        notes = []
        if self.load_seconds >= LOAD_NOTICE_SECONDS:
            notes.append(f"model loaded in {self.load_seconds:.1f}s")
        if self.cut:
            notes.append("an answer was cut at the output token cap")
        return f" [{'; '.join(notes)}]" if notes else ""

    def activity(self) -> str:
        """What is happening in this attempt, for the panel and the heartbeat."""
        elapsed = duration(self.attempt_seconds())
        if self.phase == "checking":
            return "checking the answer against the note"
        if self.phase == "storing":
            return "storing the result"
        if self.first_token_at is None:
            return (
                f"waiting for the first token (the server is reading a prompt of about "
                f"{self.prompt_tokens_estimate:,} tokens) · {elapsed}"
            )
        rate = self.recent_rate()
        if rate:
            speed = f" · {rate:.1f} tok/s"
        elif self.last_token_at is not None and time.perf_counter() - self.last_token_at >= SPINNER_RATE_WINDOW:
            speed = f" · no token for {duration(time.perf_counter() - self.last_token_at)}"
        else:
            speed = ""
        if self.phase == "thinking":
            return f"thinking · {self.thinking_tokens:,} tokens{speed} · {elapsed}"
        thought = f" after {self.thinking_tokens:,} thinking" if self.thinking_tokens else ""
        return f"answering · {self.tokens:,} tokens{thought}{speed} · {elapsed}"


class ModelRun:
    """Running totals for one model's pass over the notes it has no result for yet."""

    def __init__(self, spec, index: int, count: int, total_notes: int, already: int, pending: int, too_long: int):
        self.spec = spec
        self.index = index                # 1-based position in the run's model list
        self.count = count                # models in the run
        self.total_notes = total_notes
        self.already = already            # notes the model had analysed before this run
        self.pending = pending            # notes this pass processes
        self.too_long = too_long          # of which are recorded as failed without a call
        self.started = time.perf_counter()
        self.finished = None
        self.loading_since = None
        self.load_seconds = None
        self.placement = None             # where the loaded model sits, as `ollama ps` puts it
        self.call = None                  # CallProgress of the note in progress
        self.done = self.sent = self.ok = self.failed = self.flagged = self.retries = 0
        self.seconds = []                 # per note sent to the model, in order
        self.prompt_tokens = self.answer_tokens = self.thinking_tokens = 0
        self.eval_seconds = 0.0
        self.fields = Counter()
        self.rejections = Counter()
        self.review_reasons = Counter()
        self.failed_ids = []
        self.call_failures = 0            # consecutive notes whose model call failed
        self.call_failed_ids = []         # notes recorded as failed because the call failed
        self.stopped = None               # why the pass stopped early, when it did

    @property
    def to_send(self) -> int:
        return self.pending - self.too_long

    @property
    def remaining(self) -> int:
        return self.pending - self.done

    def elapsed(self) -> float:
        return (self.finished or time.perf_counter()) - self.started

    def begin_note(self, note_id: int, position: int, prompt_tokens_estimate: int, max_attempts: int) -> CallProgress:
        self.call = CallProgress(note_id, position, self.pending, max_attempts, prompt_tokens_estimate)
        return self.call

    def finish_note(self, analysis, seconds: float, review_required: bool, review_reason: str | None):
        """Adds one finished note (a data_worker.Analysis) to the totals."""
        call = self.call
        self.done += 1
        if analysis.attempts > 0:
            self.sent += 1
            self.seconds.append(seconds)
            if call is not None:
                self.prompt_tokens += call.prompt_tokens
                self.answer_tokens += call.answer_tokens
                self.thinking_tokens += call.thinking_total
                self.eval_seconds += call.eval_seconds
        self.retries += len(analysis.brief_rejections)
        for brief in analysis.brief_rejections:
            self.rejections[rejection_key(brief)] += 1
        if analysis.llm_failed:
            self.failed += 1
            self.failed_ids.append(analysis.note_id)
            if analysis.error and analysis.error.startswith("LLM call failed"):
                self.call_failures += 1
                self.call_failed_ids.append(analysis.note_id)
            else:
                self.call_failures = 0
        else:
            self.ok += 1
            self.call_failures = 0
            r = analysis.result
            self.fields["egd"] += r["Endoscopy"] == 1
            self.fields["colonoscopy"] += r["Colonoscopy"] == 1
            self.fields["biopsies"] += r["DuodenalBiopsiesTaken"] == 1
            self.fields["counted"] += r["DuodenalBiopsiesTaken"] == 1 and r["NumberOfDuodenalBiopsies"] is not None
            self.fields["fellow"] += r["FellowPresent"] == 1
            self.fields["scope"] += r["ScopeType"] is not None
        if review_required:
            self.flagged += 1
            for reason in (review_reason or "").split("; "):
                if reason and not reason.startswith("see "):   # "; see LLM_Error" points at a column, it is not a reason
                    self.review_reasons[review_key(reason)] += 1
        self.call = None

    def mean_seconds(self) -> float | None:
        return sum(self.seconds) / len(self.seconds) if self.seconds else None

    def recent_seconds(self) -> float | None:
        recent = self.seconds[-RECENT_NOTES:]
        return sum(recent) / len(recent) if recent else None

    def median_seconds(self) -> float | None:
        if not self.seconds:
            return None
        ordered = sorted(self.seconds)
        return ordered[len(ordered) // 2]

    def tokens_per_second(self) -> float | None:
        return (self.answer_tokens + self.thinking_tokens) / self.eval_seconds if self.eval_seconds > 0 else None

    def eta_seconds(self) -> float | None:
        """Seconds left for this pass, from the mean time of the notes sent so far."""
        mean = self.mean_seconds()
        if mean is None:
            return None
        eta = (self.to_send - self.sent) * mean
        if self.call is not None and self.call.attempt > 0:
            eta -= self.call.note_seconds()
        return max(0.0, eta)

    def rate_text(self) -> str:
        mean = self.mean_seconds()
        if mean is None:
            return "no note sent yet"
        text = f"{mean:.1f}s per note"
        if len(self.seconds) > RECENT_NOTES:
            text += f" (last {RECENT_NOTES}: {self.recent_seconds():.1f}s)"
        return text


class Reporter:
    """The one place the pipeline reports to; see the module docstring."""

    def __init__(self, console: Console, live: bool):
        self.console = console
        self.live_mode = live
        self._live = None
        self._stop = threading.Event()
        self._thread = None
        self._last_line_at = time.perf_counter()
        self.run_started = time.perf_counter()
        self.phase = None             # the current section title
        self.model = None             # the ModelRun in progress
        self.ingest = None            # (checked, total, new, duplicates) while ingesting
        self.busy = None              # (what, done, total) during the consensus and export phases
        self.plan = {}                # label -> ModelPlan of each model still to run, in run order
        self._spinner = TokenSpinner()

    @classmethod
    def create(cls) -> "Reporter":
        """A Reporter in live mode for a terminal, plain mode otherwise (or with NLP_PLAIN=1)."""
        console = Console(highlight=False)
        live = console.is_terminal and os.environ.get("NLP_PLAIN") != "1"
        if not console.is_terminal:
            # A log is not wrapped by a terminal; give tables room instead of folding them at 80.
            console = Console(highlight=False, width=max(console.width, 120))
        return cls(console, live)

    # -- lifecycle ---------------------------------------------------------------------

    def __enter__(self):
        if self.live_mode:
            self._live = Live(
                get_renderable=self._render, console=self.console, refresh_per_second=LIVE_REFRESH_PER_SECOND,
                transient=True, vertical_overflow="visible",
            )
            self._live.start()
        else:
            self._thread = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        return False

    # -- lines -------------------------------------------------------------------------

    def log(self, message: str):
        """One timestamped line; never wrapped, so a job log stays greppable."""
        self.console.print(f"[dim][{time.strftime('%H:%M:%S')}][/dim] {message}", soft_wrap=True)
        self._last_line_at = time.perf_counter()

    def warn(self, message: str):
        self.log(f"[bold yellow]warning[/bold yellow] {message}")

    def error(self, message: str):
        self.log(f"[bold red]error[/bold red] {message}")

    def section(self, title: str):
        """Opens a phase: a rule across the terminal, or a marked line in a log."""
        self.phase = title
        if self.live_mode:
            self.console.rule(f"[bold]{escape(title)}[/bold]", style="cyan", align="left")
        else:
            self.log(f"[bold cyan]== {escape(title)} ==[/bold cyan]")

    def header(self, items: Sequence[tuple[str, str]]):
        """Aligned 'key  value' lines, for the run header and the summaries."""
        width = max([len(key) for key, _ in items] + [8])
        for key, value in items:
            self.log(f"[bold]{escape(key):<{width}}[/bold]  {value}")

    def table(self, title: str, columns: Sequence[str], rows: Sequence[Sequence[object]], justify_from: int = 1):
        """A summary table, printed as a block (lines are not timestamped)."""
        table = Table(
            title=title, title_justify="left", title_style="bold",
            box=box.SIMPLE_HEAD if self.live_mode else box.ASCII2, show_edge=False, pad_edge=False,
        )
        for index, column in enumerate(columns):
            table.add_column(column, justify="right" if index >= justify_from else "left", no_wrap=index >= justify_from)
        for row in rows:
            table.add_row(*("" if value is None else str(value) for value in row))
        self.console.print(table)
        self.console.print()
        self._last_line_at = time.perf_counter()

    # -- ingest ------------------------------------------------------------------------

    def ingest_progress(self, checked: int, total: int, new: int, duplicates: int):
        """database.ingest_csv's progress callback."""
        self.ingest = (checked, total, new, duplicates)
        if not self.live_mode and checked and (checked % 1000 == 0 or checked == total):
            self.log(f"[bold]ingest[/bold]    {checked:,}/{total:,} rows checked against the database: {new:,} new, {duplicates:,} duplicates")
        if checked == total:
            self.ingest = None

    # -- analysis ----------------------------------------------------------------------

    def set_plan(self, plan: dict):
        """{label: ModelPlan} for every model of the run, in run order (for the run-level estimate)."""
        self.plan = dict(plan)

    def begin_model(self, spec, index: int, count: int, total_notes: int, already: int, pending: int, too_long: int) -> ModelRun:
        self.model = ModelRun(spec, index, count, total_notes, already, pending, too_long)
        self.section(f"Analyse · model {index} of {count}: {spec.label}")
        return self.model

    def model_loading(self):
        self.model.loading_since = time.perf_counter()
        self.log(f"[bold]load[/bold]      loading {escape(self.model.spec.name)} into memory, context {self.model.spec.num_ctx:,} tokens ...")

    def model_loaded(self, seconds: float, placement: str | None, context_length: int | None):
        m = self.model
        m.loading_since = None
        m.load_seconds = seconds
        m.placement = placement
        where = f": {placement}" if placement else ""
        context = ""
        if context_length and context_length != m.spec.num_ctx:
            context = f" [yellow](the server loaded it with a {context_length:,}-token context, not {m.spec.num_ctx:,})[/yellow]"
        self.log(f"[bold]load[/bold]      loaded in {seconds:.1f}s{where}{context}")

    def model_load_failed(self, error: str):
        self.model.loading_since = None
        self.warn(f"could not load the model ahead of the first note ({escape(error)}); the first call will load it")

    def note_retry(self, call: CallProgress, attempt: int, brief: str):
        m = self.model
        tokens = ""
        if call.final is not None:
            tokens = f", {call.final.prompt_eval_count or 0:,} prompt + {call.final.eval_count or call.tokens:,} answer tokens"
            if call.final.done_reason == "length":
                tokens += " [answer cut at the output token cap]"
        self.log(
            f"[yellow]retry [/yellow] note {call.note_id} ({call.position}/{m.pending}) "
            f"attempt {attempt}/{call.max_attempts} rejected after {call.attempt_seconds():.1f}s{tokens}: {escape(brief)}"
        )

    def note_done(self, call: CallProgress, analysis, seconds: float, review_required: bool, review_reason: str | None):
        m = self.model
        where = f"note {call.note_id} ({call.position}/{m.pending})"
        if analysis.llm_failed:
            if analysis.attempts == 0:
                how = "without a model call"
            else:
                how = f"after {plural(analysis.attempts, 'attempt')}, {seconds:.1f}s, {call.tokens_text()}{call.notices()}"
            self.log(f"[red]FAILED[/red] {where} {how}: {escape(analysis.brief_error or '')}")
            return
        r = analysis.result
        review = f" [yellow]review:[/yellow] {escape(review_reason)}" if review_required else ""
        self.log(
            f"[green]ok    [/green] {where} {seconds:.1f}s, attempt {analysis.attempts}, {call.tokens_text()}{call.notices()}: "
            f"scope={escape(repr(r['ScopeType']))} egd={r['Endoscopy']} colonoscopy={r['Colonoscopy']} "
            f"duodenal_biopsies={r['NumberOfDuodenalBiopsies']} taken={r['DuodenalBiopsiesTaken']} "
            f"fellow={r['FellowPresent']}{review}"
        )

    def fields_text(self, m: ModelRun) -> str:
        parts = []
        for key, text in FIELD_TALLIES:
            value = m.fields[key]
            parts.append(f"{text} {value}" if key != "counted" else f"(count stated {value})")
        return " · ".join(parts).replace(" · (count stated", " (count stated")

    def run_eta(self, m: ModelRun) -> tuple[float | None, int, bool]:
        """
        (seconds, models after this one, estimated) for the rest of the run: this model's
        time left plus each later model's notes at its own stored rate, or at this model's
        rate when it has none (then estimated is True).
        """
        eta = m.eta_seconds()
        labels = list(self.plan)
        later = labels[labels.index(m.spec.label) + 1:] if m.spec.label in labels else labels
        estimated = False
        if eta is None:
            return None, len(later), False
        mean = m.mean_seconds()
        for label in later:
            plan = self.plan[label]
            rate = plan.stored_mean
            if rate is None:
                rate, estimated = mean, True
            eta += plan.to_send * rate
        return eta, len(later), estimated

    def _later_text(self, m: ModelRun) -> str:
        eta, later, estimated = self.run_eta(m)
        if not later:
            return "last model"
        if eta is None:
            return f"{plural(later, 'more model')} after this one"
        return f"{plural(later, 'more model')} after this one, about {duration(eta)} for the rest of the run{' at this speed' if estimated else ''}"

    def progress_summary(self):
        """The running summary logged every SUMMARY_EVERY notes."""
        m = self.model
        percent = 100 * m.done / m.pending if m.pending else 100
        eta = m.eta_seconds()
        left = f"about {duration(eta)} left for this model" if eta is not None else "time left unknown"
        self.log(
            f"[bold]progress[/bold] {m.done}/{m.pending} ({percent:.0f}%): {m.ok} ok, {m.failed} failed, "
            f"{m.flagged} flagged for review, {plural(m.retries, 'rejected attempt')}; {m.rate_text()}; "
            f"{duration(m.elapsed())} elapsed, {left}; {self._later_text(m)}"
        )
        rejections = f"; rejections: {top(m.rejections)}" if m.rejections else ""
        self.log(f"[bold]fields[/bold]   {self.fields_text(m)}{rejections}")

    def model_finished(self):
        m = self.model
        m.finished = time.perf_counter()
        self.plan.pop(m.spec.label, None)
        mean = f"{m.mean_seconds():.1f}s per note (median {m.median_seconds():.1f}s)" if m.seconds else "no note sent"
        speed = f", {m.tokens_per_second():.1f} tok/s" if m.tokens_per_second() else ""
        self.log(
            f"[bold green]Finished[/bold green] {plural(m.pending, 'note')} with {escape(m.spec.label)} in {duration(m.elapsed())}: "
            f"{m.ok} ok, {m.failed} failed, {m.flagged} flagged for manual review, {plural(m.retries, 'rejected attempt')}; "
            f"{mean}; {m.prompt_tokens:,} prompt and {m.answer_tokens:,} answer tokens"
            + (f" (+ {m.thinking_tokens:,} thinking)" if m.thinking_tokens else "") + speed + "."
        )
        self.log(f"[bold]fields[/bold]   {self.fields_text(m)}")
        if m.failed_ids:
            self.log(f"Failed note id(s): {', '.join(str(i) for i in m.failed_ids)}")
        if m.review_reasons:
            self.log(f"Review reasons: {top(m.review_reasons, 8)}")
        if m.rejections:
            self.log(f"Rejection reasons: {top(m.rejections, 8)}")
        self.model = None

    def model_nothing_to_do(self):
        m = self.model
        self.plan.pop(m.spec.label, None)
        self.log(f"[bold yellow]Nothing to do:[/bold yellow] every note already has a result from {escape(m.spec.label)}.")
        self.model = None

    def model_stopped(self, reason: str):
        m = self.model
        m.stopped = reason
        m.finished = time.perf_counter()
        self.log(f"[bold red]STOPPED[/bold red] {escape(m.spec.label)} after {m.done} of {plural(m.pending, 'note')}: {escape(reason)}")
        self.log(
            f"The remaining {plural(m.remaining, 'note')} stay pending and are picked up by the next --analyze."
            + (
                f" Note id(s) {', '.join(str(i) for i in m.call_failed_ids)} were recorded as failed with "
                f"'LLM call failed' and are not retried on resume; clear their result to retry them."
                if m.call_failed_ids else ""
            )
        )
        self.model = None

    def model_skipped(self, spec, reason: str):
        self.plan.pop(spec.label, None)
        self.log(f"[bold yellow]SKIPPED[/bold yellow] {escape(spec.label)}: {escape(reason)}")

    # -- consensus and export ----------------------------------------------------------

    def busy_progress(self, what: str, done: int, total: int | None = None):
        """What a quick phase is doing (the live panel shows it; a log gets nothing until it ends)."""
        self.busy = (what, done, total)

    def busy_done(self):
        self.busy = None

    # -- plain-mode heartbeat ----------------------------------------------------------

    def _heartbeat_loop(self):
        while not self._stop.wait(1.0):
            m = self.model
            call = m.call if m is not None else None
            if call is None or call.attempt == 0:
                continue
            if time.perf_counter() - self._last_line_at < HEARTBEAT_SECONDS:
                continue
            eta = m.eta_seconds()
            left = f", about {duration(eta)} left for this model" if eta is not None else ""
            self.log(
                f"[dim]...   [/dim] note {call.note_id} ({call.position}/{m.pending}) attempt {call.attempt}/{call.max_attempts}: "
                f"{call.activity()} on this attempt, {duration(call.note_seconds())} on this note; "
                f"{m.done} done, {m.failed} failed, {duration(m.elapsed())} elapsed{left}"
            )

    # -- live panel --------------------------------------------------------------------

    def _render(self):
        try:
            if self.model is not None:
                return self._render_model(self.model)
            if self.ingest is not None:
                return self._render_ingest()
            if self.busy is not None:
                return self._render_busy()
        except Exception as e:  # never let a display slip kill the refresh thread
            return Text(f"(display error: {type(e).__name__}: {e})", style="red")
        return Text("")

    def _spinner_row(self, text: str, speed: float = 1.0):
        """The spinner, turning at speed (1.0 is its normal pace), beside a line of text."""
        row = Table.grid(padding=(0, 1))
        row.add_column(width=2)
        row.add_column()
        row.add_row(self._spinner.frame(speed), Text(text))
        return row

    def _bar_row(self, completed: int, total: int, label: str):
        row = Table.grid(expand=True, padding=(0, 1))
        row.add_column(ratio=1)
        row.add_column(justify="right", no_wrap=True)
        row.add_row(ProgressBar(total=max(total, 1), completed=completed, width=None), label)
        return row

    def _panel(self, body, title: str):
        return Panel(
            body, title=title, title_align="left", subtitle=f"run {duration(time.perf_counter() - self.run_started)}",
            subtitle_align="right", border_style="cyan", padding=(0, 1),
        )

    def _render_model(self, m: ModelRun):
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column()
        call = m.call
        speed = SPINNER_MIN_SPEED
        if m.loading_since is not None:
            activity = f"loading {m.spec.name} into memory · {duration(time.perf_counter() - m.loading_since)}"
        elif call is not None and call.attempt > 0:
            activity = f"note {call.position} of {m.pending} (id {call.note_id}) · attempt {call.attempt} of {call.max_attempts} · {call.activity()}"
            if call.attempt > 1:
                activity += f" · {duration(call.note_seconds())} on this note"
            speed = call.spinner_speed()
        elif call is not None:
            activity = f"note {call.position} of {m.pending} (id {call.note_id}) · too long for the window, recording it as failed"
        else:
            activity = "preparing the next note"
        grid.add_row(self._spinner_row(activity, speed))
        percent = 100 * m.done / m.pending if m.pending else 100
        grid.add_row(self._bar_row(m.done, m.pending, f"{m.done}/{m.pending} · {percent:.0f}%"))
        speed = f" · {m.tokens_per_second():.1f} tok/s" if m.tokens_per_second() else ""
        rejected = m.retries + (call.attempt - 1 if call is not None and call.attempt > 1 else 0)
        grid.add_row(Text(
            f"ok {m.ok} · failed {m.failed} · flagged {m.flagged} · rejected attempts {rejected} · {m.rate_text()}{speed}"
        ))
        eta = m.eta_seconds()
        left = f"about {duration(eta)} left for this model" if eta is not None else "time left unknown until a note is done"
        placement = f" · {m.placement}" if m.placement else ""
        grid.add_row(Text(f"elapsed {duration(m.elapsed())} · {left} · {self._later_text(m)}{placement}"))
        grid.add_row(Text(self.fields_text(m), style="dim"))
        return self._panel(grid, f"[bold]Analysing with {escape(m.spec.label)}[/bold] · model {m.index} of {m.count}")

    def _render_ingest(self):
        checked, total, new, duplicates = self.ingest
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column()
        grid.add_row(self._spinner_row(f"checking the rows against the database · {new:,} new · {duplicates:,} duplicates"))
        grid.add_row(self._bar_row(checked, total, f"{checked:,}/{total:,}"))
        return self._panel(grid, "[bold]Ingesting[/bold]")

    def _render_busy(self):
        what, done, total = self.busy
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column()
        grid.add_row(self._spinner_row(what if total is None else f"{what} · {done:,}/{total:,}"))
        if total:
            grid.add_row(self._bar_row(done, total, f"{100 * done / total:.0f}%"))
        return self._panel(grid, f"[bold]{escape(self.phase or '')}[/bold]")

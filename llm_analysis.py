import json
import os
import re
from typing import NamedTuple, Tuple
import ollama

# MedGemma 27B (Gemma 3 base), Q4_K_M, 17.4 GB. The registry build declares no "requires" and ships
# no separate projector layer, so it loads on Argon's pinned Ollama 0.24.0 (driver 535).
# Model blob digest as of 2026-10-13: sha256:4b97da6b21a8fb1405a0d1efee462bfd6c4d3c46307bac8b34373835f07ca758
LOCAL_MODEL = "medgemma:27b"

# Where the ollama client connects when OLLAMA_HOST is not set.
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

# Context window in tokens, passed on every call so a workstation run uses the same window
# as an Argon job (setup.sh starts Ollama with OLLAMA_CONTEXT_LENGTH from the same variable).
# Without it Ollama picks its own default, 4096 on a CPU-only machine. 8192 is the window at
# which medgemma:27b and its KV cache fit on Argon's A10 (24 GB); anything larger spills the
# model to the CPU. A note that does not fit this window (about 18,000 characters, roughly 2%
# of the corpus) is failed without a model call rather than silently truncated (see
# note_fits_context); raise NLP_CONTEXT_LENGTH only on a GPU with room for the larger cache.
CONTEXT_LENGTH = int(os.environ.get("NLP_CONTEXT_LENGTH", "8192"))


class ModelSpec(NamedTuple):
    """
    One model the program sends every note to: the Ollama model name, the context window
    (num_ctx) it is called with, and the chat call's think setting. Its label,
    "<name>@<num_ctx>" such as "medgemma:27b@8192", names the result columns that hold its
    answers (database.column_name), so two models, or one model called with two window
    sizes, never share a column.

    think goes to Ollama as it is. False turns reasoning off for a model that would otherwise
    think before answering (Gemma 4) and is accepted for one that cannot think at all
    (MedGemma; Ollama 0.24.0 rejects think only when it is true for such a model). A level
    ("low", "medium" or "high") is for a reasoning model that returns empty content when
    given a boolean False (gpt-oss on 0.24.0). None leaves the model's own default.
    """
    name: str
    num_ctx: int
    think: bool | str | None = False

    @property
    def label(self) -> str:
        return f"{self.name}@{self.num_ctx}"


# The models every note is sent to, in run order, all with the same context window so that
# every model sees the same notes (a note that does not fit the window is failed for each
# model alike). Each one's extracted fields, review flags and debugging columns are stored
# and exported under its own label, for a comparison across models. They run one after
# another: each fits Argon's A10 (24 GB) on its own with its KV cache, not together.
#
# setup.sh and migrate.sh read the names of this block with sed: keep one ModelSpec per line,
# the first with LOCAL_MODEL and every other with its name as a double-quoted literal first
# argument (ARGON_RUNBOOK.md 15.2). --location local swaps in LOCAL_MODELS for a workstation test.
#
#   gemma4:26b-a4b-it-q4_K_M  Gemma 4 26B mixture of experts (3.8B active), Q4_K_M, 18.0 GB,
#       Apache 2.0. Vision is merged into its GGUF and its config requires only Ollama 0.20,
#       so it loads on 0.24.0 (the default gemma4:26b tag does not). Blob digest as of
#       2026-10-13: sha256:7121486771cb... think=False: it would otherwise reason before
#       answering, six times slower.
#   gpt-oss:20b  OpenAI gpt-oss 20B mixture of experts (3.6B active), MXFP4, 13.8 GB,
#       Apache 2.0. A reasoning model: think must be a level, not False. Its registry config
#       says it requires Ollama 0.30.0, which 0.24.0 ignores: it was load-tested there on
#       2026-10-13 (model_candidates.md), so setup.sh lists it in NLP_REQUIRES_VERIFIED.
MODELS = (
    ModelSpec(LOCAL_MODEL, CONTEXT_LENGTH),
    ModelSpec("gemma4:26b-a4b-it-q4_K_M", CONTEXT_LENGTH),
    ModelSpec("gpt-oss:20b", CONTEXT_LENGTH, think="low"),
)

# Small stand-ins for a workstation run (--location local): the same pipeline, prompt, window
# and checks, each model in its own labelled block, so the mechanics can be exercised without
# the 14 to 18 GB study models. Not fetched by setup.sh; pull them with `ollama pull`. Gemma 4
# and Qwen 3.5 reason by default, so think=False keeps their answers to the JSON.
LOCAL_MODELS = (
    ModelSpec("ministral-3:3b", CONTEXT_LENGTH),
    ModelSpec("gemma4:e2b", CONTEXT_LENGTH),
    ModelSpec("qwen3.5:4b", CONTEXT_LENGTH),
)

# The model set each --location value selects. "server" (the default) is the study set.
LOCATIONS = {"server": MODELS, "local": LOCAL_MODELS}

# How much of the context window a note may take. Measured with the Gemma 3 tokenizer, which
# medgemma:27b and gemma3 share: the system prompt and user template take 1,664 tokens, and
# this clinical text runs 3.2 to 3.8 characters per token. Ollama does not refuse a prompt
# that exceeds num_ctx: it keeps the first few tokens and the tail that fits, dropping the
# instructions and the start of the note, and logs "truncating input prompt" only on the
# server. The estimate is conservative (3 characters per token, room kept for the answer), and
# a note that might be cut is not sent to the model at all: data_worker records it as failed
# with too_long_error, since an answer from a prompt without its instructions cannot be trusted.
PROMPT_OVERHEAD_TOKENS = 1700
NOTE_CHARS_PER_TOKEN = 3.0
ANSWER_RESERVE_TOKENS = 512
TOO_LONG_PREFIX = "the note is too long for the model"

# The LLM_Error of a model call that failed (the server, the model, or the program while
# handling the answer; nothing about the note), as llm_analysis and data_worker record it.
CALL_FAILED_PREFIX = "LLM call failed"

# Why a model gave no answer for a note, read from its LLM_Error (failure_kind): the note was
# not sent (too long for the window), the call itself failed, or every answer it gave was
# rejected (the note does not support it, or the output was unusable). review.py and
# consensus.py word their reasons by it, since an empty result means something different in
# each case: nobody read the note, the program could not ask, or the model was asked and refuted.
NOT_SENT, CALL_FAILED, REJECTED = "not sent", "call failed", "rejected"


def failure_kind(error: str | None) -> str:
    """The kind of failure an LLM_Error records: NOT_SENT, CALL_FAILED, else REJECTED."""
    if error and error.startswith(TOO_LONG_PREFIX):
        return NOT_SENT
    if error and error.startswith(CALL_FAILED_PREFIX):
        return CALL_FAILED
    return REJECTED


def estimated_prompt_tokens(note: str) -> int:
    """A conservative (high) estimate of the tokens the prompt for note takes."""
    return PROMPT_OVERHEAD_TOKENS + int(len(note) / NOTE_CHARS_PER_TOKEN)


def max_note_chars(context_length: int = CONTEXT_LENGTH) -> int:
    """The longest note (in characters) whose prompt, plus a normal answer, fits context_length tokens."""
    return int((context_length - ANSWER_RESERVE_TOKENS - PROMPT_OVERHEAD_TOKENS) * NOTE_CHARS_PER_TOKEN)


def note_fits_context(note: str, context_length: int = CONTEXT_LENGTH) -> bool:
    """Whether the prompt for note, plus a normal answer, fits a context window of context_length."""
    return estimated_prompt_tokens(note) + ANSWER_RESERVE_TOKENS <= context_length


def too_long_error(note: str, context_length: int = CONTEXT_LENGTH) -> str:
    """The LLM_Error text for a note that was not sent because it does not fit the context."""
    return (
        f"{TOO_LONG_PREFIX}: about {estimated_prompt_tokens(note)} prompt tokens "
        f"({len(note)} characters), more than the {context_length}-token context window allows "
        f"(NLP_CONTEXT_LENGTH); no model call was made."
    )


# Generation settings. Temperature 0 makes decoding greedy, so the same note gives the
# same answer on every run, which a study needs; Ollama's server default is 0.8.
# A valid answer is ~120 tokens; num_predict stops a model stuck repeating a phrase
# instead of letting it run to the context limit.
GENERATION_OPTIONS = {"temperature": 0, "seed": 0, "num_ctx": CONTEXT_LENGTH, "num_predict": 2048}

# Greedy decoding gives the same output on every call, so a retry after a repetition
# loop would loop again. Retries therefore sample, with the attempt number as the
# seed so a rerun of the study still reproduces them, and penalise repeated tokens
# (sampling alone left gemma3:4b looping on some notes; 1.1 cleared them).
RETRY_TEMPERATURE = 0.8
RETRY_REPEAT_PENALTY = 1.1


def generation_options(attempt: int, num_ctx: int = CONTEXT_LENGTH) -> dict:
    """
    Returns the Ollama options for the given attempt (1-based) and context window: greedy
    on the first attempt, seeded sampling with a repeat penalty on retries.
    """
    if attempt == 1:
        return {**GENERATION_OPTIONS, "num_ctx": num_ctx}
    return {
        **GENERATION_OPTIONS,
        "num_ctx": num_ctx,
        "temperature": RETRY_TEMPERATURE,
        "seed": attempt,
        "repeat_penalty": RETRY_REPEAT_PENALTY,
    }

EXPECTED_FIELDS = {
    "id": int,
    "ScopeType": (str, type(None)),
    "Colonoscopy": int,
    "ColonoscopyInformation": (str, type(None)),
    "Endoscopy": int,
    "EndoscopyInformation": (str, type(None)),
    "NumberOfDuodenalBiopsies": (int, type(None)),  # null = taken, but no count stated
    "DuodenalBiopsiesTaken": int,
    "DuodenalBiopsiesInformation": (str, type(None)),
    "FellowPresent": int,
    "FellowInformation": (str, type(None)),
}

BOOL_FIELDS = {
    "Colonoscopy",
    "Endoscopy",
    "DuodenalBiopsiesTaken",
    "FellowPresent",
}

INT_FIELDS = BOOL_FIELDS | {"NumberOfDuodenalBiopsies"}

NULLABLE_STRING_FIELDS = {
    "ScopeType",
    "ColonoscopyInformation",
    "EndoscopyInformation",
    "DuodenalBiopsiesInformation",
    "FellowInformation",
}

INTEGER_RE = re.compile(r"-?\d+")

# Longest value the model may write in a text field. A real justification quote is one
# sentence or line, well under this; a value that reaches it is a repetition loop the
# schema cut off, and the attempt is retried.
MAX_TEXT_CHARS = 1000

# Rejections that are a repetition loop (the model repeating itself until the text cap or
# Ollama's repeat limit stopped it) rather than a wrong or unsupported answer. A retry after
# one says nothing about the answer, so review.py does not count it. The wording matches the
# two messages raised in _parse_and_check and llm_analysis, old databases included.
LOOP_REJECTION_RE = re.compile(
    r"repeated text until it reached the \d+-character limit|repeated itself until Ollama stopped it"
)


def is_loop_rejection(message: str) -> bool:
    """Whether a rejection message (or an LLM_Rejections line) records a repetition loop."""
    return bool(LOOP_REJECTION_RE.search(message))

# Passed to Ollama as format=, which constrains decoding to this schema: the reply is
# always a bare JSON object with exactly these keys, 0/1 flags and bounded strings.
# It mirrors EXPECTED_FIELDS ("id" is added by the program, not the model).
_TEXT = {"type": ["string", "null"], "maxLength": MAX_TEXT_CHARS}
_FLAG = {"type": "integer", "enum": [0, 1]}
_SCHEMA_PROPERTIES = {
    "ScopeType": _TEXT,
    "Colonoscopy": _FLAG,
    "ColonoscopyInformation": _TEXT,
    "Endoscopy": _FLAG,
    "EndoscopyInformation": _TEXT,
    "NumberOfDuodenalBiopsies": {"type": ["integer", "null"], "minimum": 0},
    "DuodenalBiopsiesTaken": _FLAG,
    "DuodenalBiopsiesInformation": _TEXT,
    "FellowPresent": _FLAG,
    "FellowInformation": _TEXT,
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": _SCHEMA_PROPERTIES,
    "required": list(_SCHEMA_PROPERTIES),
    "additionalProperties": False,
}

# The study definitions come from the researcher's review comments and highlighted answers on
# the ten example notes (see ARGON_RUNBOOK.md section 15.5): the upper endoscope only, bulb
# biopsies are duodenal and counted once, "taken but count not stated" must stay distinct from
# "none taken", and fellow presence is read from the attestation, not from the word "fellow".
SYSTEM_PROMPT = """\
You are a clinical information extraction engine for a research study of upper endoscopy (EGD) procedure notes. You read ONE clinical note and return ONE JSON object describing the procedure documented in that note. You never explain, summarise, or write anything outside the JSON.

WHAT THE STUDY NEEDS FROM EACH NOTE
- Which upper endoscope (gastroscope) was used. Colonoscopes do not matter.
- Whether an upper endoscopy and/or a colonoscopy was actually performed in this note's procedure.
- Whether biopsies were taken from the duodenum (the minimum the study needs), and how many if the note says. "Taken, number not stated" must stay distinguishable from "none taken".
- Whether a fellow (trainee) took part in the procedure, judged from the attending's attestation.

The notes come from a mixed pool: procedure notes, progress notes, admission notes, discharge instructions. Many mention endoscopy without documenting one that was performed. Only a procedure performed and documented in THIS note counts; history, plans, recommendations, scheduling and patient instructions never count.

OUTPUT
Return exactly this object and nothing else: no markdown fences, no comments, no extra keys, no trailing commas.
{
  "ScopeType": string | null,
  "Colonoscopy": 0 | 1,
  "ColonoscopyInformation": string | null,
  "Endoscopy": 0 | 1,
  "EndoscopyInformation": string | null,
  "NumberOfDuodenalBiopsies": integer | null,
  "DuodenalBiopsiesTaken": 0 | 1,
  "DuodenalBiopsiesInformation": string | null,
  "FellowPresent": 0 | 1,
  "FellowInformation": string | null
}
Each "...Information" value is the single sentence or line from the note that justifies the answer, copied verbatim; it is null when the answer it supports is 0 or null.

FIELD RULES

1. Endoscopy: 1 only if an upper endoscopy (EGD, gastroscopy, esophagogastroduodenoscopy) was performed as this note's procedure. Evidence is a procedure or findings section describing it: the scope passed through the mouth or oropharynx, the esophagus intubated, the stomach or duodenum examined. PEG tube placement done with a gastroscope counts as an upper endoscopy. Otherwise 0, including "history of EGD", "EGD recommended", "EGD scheduled", "after your endoscopy".

2. Colonoscopy: 1 only if a colonoscopy was performed as part of this note's procedure (scope through the anus, cecum or colon examined). Otherwise 0, including "no colonoscopy was performed", "colonoscopy tomorrow", "after your colonoscopy", "prior colonoscopy in 2019".

3. ScopeType: the upper endoscope only, named as the note names it: brand and model when both are given ("Olympus GIF-H190", "Olympus Q180", "Olympus GIF XP190N"), the model alone if only the model is given ("GIF-H 190"), the brand alone if only the brand is given ("Olympus gastroscope"). If the brand and the model appear apart ("Olympus endoscope (GIF-H180)"), join them ("Olympus GIF-H180"). Copy the name itself, not the sentence around it: never "Olympus H190 endoscope was lubricated", just "Olympus H190". Ignore colonoscopes and sigmoidoscopes completely (names with "colonoscope", PCF or CF models); a note that names only a colonoscope has ScopeType null. Null when no upper endoscope brand or model is stated, and null when Endoscopy is 0.

4. DuodenalBiopsiesTaken: 1 if at least one biopsy was taken from the duodenum during this procedure. The duodenal bulb ("bulb"), D1, D2, D3, and the first, second or third portion of the duodenum are all the duodenum. Biopsies of the esophagus, stomach, antrum, gastric body, colon, terminal ileum or rectum are not. Statements such as "random duodenal biopsies were taken" or "biopsies were taken from the duodenum to rule out celiac disease" count even without a number. "BIOPSY: None", "no biopsies were taken", or no mention of duodenal biopsies means 0.

5. NumberOfDuodenalBiopsies: the total number of biopsies taken from the duodenum when the note states it. Otherwise null when DuodenalBiopsiesTaken is 1 (taken, count not stated) and 0 when DuodenalBiopsiesTaken is 0. Count the duodenum once: "6 biopsies from the duodenum including 2 from the bulb" is 6, not 8 and not 2; "4 biopsies from the second portion and 2 from the bulb" is 6. Never use a CPT code, a date, a list number, or a count of biopsies from another organ.

6. DuodenalBiopsiesInformation: the sentence stating the duodenal biopsies, verbatim; null when DuodenalBiopsiesTaken is 0.

7. FellowPresent: 1 if a fellow or other trainee (resident) took part in performing this note's procedure; 0 otherwise. Decide from the attending's attestation and the provider lines, not from the word "fellow":
   - "Staff Only" attestation (the attending performed the entire procedure alone): 0.
   - "Teaching Statement" or "Teaching Physician Statement" (the attending states that a trainee performed the procedure under supervision, or that the attending was present and actively participated while a trainee performed): 1, even when the trainee is never called a fellow.
   - A trainee listed as performing or assisting ("Fellow: Jane Doe", "assisted by the GI fellow", a fellow signing as a performing provider): 1.
   - "Fellow: None", "RESIDENT/FELLOW: None", or no trainee involved in the procedure: 0.
   - A fellow mentioned in another role ("discussed with the fellow on call", a fellow's clinic note) is not participation: 0.
   - If no procedure was performed in this note (Endoscopy 0 and Colonoscopy 0): 0.

8. FellowInformation: the attestation sentence or the line naming the trainee, verbatim; null when FellowPresent is 0.

GENERAL
- Use only what the note states explicitly. Never guess from what is usual.
- Every "...Information" value and ScopeType must be copied from the note. The program checks each one against the note and rejects the whole answer when a quote is not in the note, when it does not mention the thing it is evidence for, when ScopeType is a name the note does not state, or when NumberOfDuodenalBiopsies is not the number the quoted sentence states. When no sentence in the note supports a 1, the answer is 0; when the note names no upper endoscope, ScopeType is null.
- Read the whole note, including headers such as OPERATION/PROCEDURE, PROCEDURE DETAILS, FINDINGS, SPECIMENS, BIOPSY, Teaching Statement, Staff Only, RESIDENT/FELLOW, and the signature block.
- When one note documents both an upper endoscopy and a colonoscopy, answer ScopeType and the biopsy fields for the upper endoscopy and set Colonoscopy to 1 as well.
"""

USER_PROMPT = """\
Extract the study fields from the clinical note below and return only the JSON object.

NOTE:
\"\"\"{note}\"\"\"
"""

# Appended to the user prompt on a retry. The rejected answer itself is not sent back:
# when it is a repetition loop, showing it to the model invites the same loop.
RETRY_PROMPT = """
Your previous answer for this note was rejected: {reason}
Answer again, following the OUTPUT and FIELD RULES exactly.
"""

JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE
)

TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
SINGLE_QUOTE_KEY_RE = re.compile(r"'([A-Za-z0-9_]+)'\s*:")
SINGLE_QUOTE_VALUE_RE = re.compile(r":\s*'([^']*)'")


class LLMAnalysisError(Exception):
    pass


class RetryableLLMError(LLMAnalysisError):
    """
    The model's answer was rejected in a way another attempt may fix: invalid JSON, a runaway
    string, a value that fails validation, or an answer the note does not support (see
    verify_against_note). The message is shown to the model on the retry and, when every
    attempt fails, recorded in LLM_Error. brief is the message without the rejected value,
    so it holds no note text and can be logged. raw is the verbatim model text of the
    rejected attempt, kept for audit.
    """

    def __init__(self, message: str, value: str | None = None, raw: str | None = None):
        self.brief = message
        self.raw = raw
        if value is not None:
            message = f"{message} The rejected value was {_excerpt(value)}."
        super().__init__(message)


class ModelInfo(NamedTuple):
    """What the server lists about one pulled model."""
    name: str
    size_bytes: int | None       # on disk
    parameter_size: str | None   # "26.4B"
    quantization: str | None     # "Q4_K_M"

    def describe(self) -> str:
        """'26.4B parameters, Q4_K_M, 17.4 GB on disk', with whatever the server gave."""
        parts = []
        if self.parameter_size:
            parts.append(f"{self.parameter_size} parameters")
        if self.quantization:
            parts.append(self.quantization)
        if self.size_bytes:
            parts.append(f"{self.size_bytes / 1e9:.1f} GB on disk")
        return ", ".join(parts) or "no details from the server"


class OllamaStatus(NamedTuple):
    host: str                   # the server address the client uses
    reachable: bool             # the server answered /api/tags
    model_available: bool       # the requested model is among the models it serves
    detail: str                 # the error when unreachable, else the served model names
    served: tuple[str, ...] = ()  # the served model names, for checking further models
    version: str | None = None  # the server's version, when it told us
    infos: dict = {}            # served model name -> ModelInfo

    def info(self, model: str) -> ModelInfo | None:
        """The listing of model (or model:latest), if the server serves it."""
        return self.infos.get(model) or self.infos.get(f"{model}:latest")


def model_served(model: str, served: tuple[str, ...]) -> bool:
    """Whether model is among the served names (Ollama lists "name" as "name:latest" too)."""
    return model in served or f"{model}:latest" in served


def ollama_status(model: str = LOCAL_MODEL, timeout: float = 3.0) -> OllamaStatus:
    """
    Probes the Ollama server (OLLAMA_HOST, else the default) without loading a model,
    bounded by timeout seconds, and reports whether model is available there.
    """
    host = os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST
    version = None
    try:
        with ollama.Client(timeout=timeout) as client:
            models = client.list().models
            try:
                version = client._client.get("/api/version").json().get("version")
            except Exception:
                pass  # an older server, or no /api/version: the version is just unknown
    except Exception as e:
        return OllamaStatus(host, False, False, f"{type(e).__name__}: {e}")
    names = tuple(m.model for m in models if m.model)
    infos = {
        m.model: ModelInfo(
            m.model, m.size,
            m.details.parameter_size if m.details else None,
            m.details.quantization_level if m.details else None,
        )
        for m in models if m.model
    }
    return OllamaStatus(host, True, model_served(model, names), ", ".join(names) or "no models pulled", names, version, infos)


class LoadResult(NamedTuple):
    """How loading a model ahead of its first note went (load_model)."""
    seconds: float
    placement: str | None       # "100% GPU", "100% CPU" or "40%/60% CPU/GPU", as `ollama ps` puts it, with the loaded size
    context_length: int | None  # the window the server loaded it with, when it says


def load_model(spec: ModelSpec, timeout: float | None = None) -> LoadResult:
    """
    Loads spec's model into memory with the window and think setting the notes are sent
    with (a chat call with no messages, which Ollama answers once the model is loaded), and
    asks the server where it put it. Raises whatever the server raised.
    """
    import time
    started = time.perf_counter()
    with ollama.Client(timeout=timeout) as client:
        client.chat(model=spec.name, messages=[], think=spec.think, options={"num_ctx": spec.num_ctx})
        seconds = time.perf_counter() - started
        placement = context_length = None
        try:
            for running in client.ps().models:
                if model_served(spec.name, (running.model or "", running.name or "")):
                    placement = _placement(running.size or 0, running.size_vram or 0)
                    context_length = running.context_length
        except Exception:
            pass  # a server without /api/ps: the placement is just unknown
    return LoadResult(seconds, placement, context_length)


def _placement(size: int, size_vram: int) -> str:
    """Where a loaded model sits, the way `ollama ps` reports it, with its loaded size."""
    loaded = f"{size / 1e9:.1f} GB loaded"
    if size_vram == 0:
        return f"100% CPU, {loaded}"
    if size_vram >= size:
        return f"100% GPU, {loaded}"
    cpu = round(100 * (size - size_vram) / size)
    return f"{cpu}%/{100 - cpu}% CPU/GPU, {loaded}"


def _strip_markdown_fences(text: str) -> str:
    """
    Extracts JSON content from markdown code fences if present.
    """
    match = JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def parse_json_with_repair(text: str) -> dict:
    """
    Attempts to parse JSON. If parsing fails, applies common repairs
    and retries once before failing.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    repaired = text.strip()

    # Remove trailing commas
    repaired = TRAILING_COMMA_RE.sub(r"\1", repaired)

    # Repair single-quoted keys and values
    repaired = SINGLE_QUOTE_KEY_RE.sub(r'"\1":', repaired)
    repaired = SINGLE_QUOTE_VALUE_RE.sub(r': "\1"', repaired)

    # Trim to outermost JSON object
    start = repaired.find("{")
    end = repaired.rfind("}")
    if start != -1 and end != -1 and end > start:
        repaired = repaired[start:end + 1]

    return json.loads(repaired)


def normalize_llm_result(parsed: dict) -> dict:
    """
    Brings near-miss values the model may return onto the schema and keeps the biopsy
    fields consistent with each other:
      - true/false and numeric strings ("4") become integers; empty strings become null
      - a null boolean field becomes 0
      - NumberOfDuodenalBiopsies stays null only when biopsies were taken without a stated
        count; a non-numeric count ("several") is treated as "not stated"
      - a positive count implies DuodenalBiopsiesTaken = 1, and no biopsies implies a count of 0
      - a count of 0 next to DuodenalBiopsiesTaken = 1 means the count was not stated (null)
    """
    for field in INT_FIELDS:
        value = parsed.get(field)
        if isinstance(value, bool):
            parsed[field] = int(value)
        elif isinstance(value, str):
            stripped = value.strip()
            if INTEGER_RE.fullmatch(stripped):
                parsed[field] = int(stripped)
            elif field == "NumberOfDuodenalBiopsies" or stripped.lower() in ("", "null", "none"):
                parsed[field] = None

    for field in BOOL_FIELDS:
        if parsed.get(field) is None:
            parsed[field] = 0

    for field in NULLABLE_STRING_FIELDS:
        value = parsed.get(field)
        if isinstance(value, str) and not value.strip():
            parsed[field] = None

    count = parsed.get("NumberOfDuodenalBiopsies")
    if isinstance(count, int) and count > 0:
        parsed["DuodenalBiopsiesTaken"] = 1
    if parsed.get("DuodenalBiopsiesTaken") == 0 and count is None:
        parsed["NumberOfDuodenalBiopsies"] = 0
    if parsed.get("DuodenalBiopsiesTaken") == 1 and count == 0:
        parsed["NumberOfDuodenalBiopsies"] = None

    return parsed


def _validate_llm_result(result: dict):
    """
    Validates that the LLM result dictionary contains all required fields,
    has no unexpected fields, and uses correct types and values.
    """
    # Required fields and types
    for field, expected_type in EXPECTED_FIELDS.items():
        if field not in result:
            raise RetryableLLMError(f"the field {field} is missing.")

        if not isinstance(result[field], expected_type):
            raise RetryableLLMError(
                f"{field} must be {_type_name(expected_type)}, got {json.dumps(result[field])}."
            )

    # No extra fields
    extra_fields = set(result.keys()) - set(EXPECTED_FIELDS.keys())
    if extra_fields:
        raise RetryableLLMError(f"unexpected fields {', '.join(sorted(extra_fields))}.")

    # Boolean-as-int enforcement
    for field in BOOL_FIELDS:
        if result[field] not in (0, 1):
            raise RetryableLLMError(f"{field} must be 0 or 1, got {json.dumps(result[field])}.")

    count = result["NumberOfDuodenalBiopsies"]
    if count is not None and count < 0:
        raise RetryableLLMError(f"NumberOfDuodenalBiopsies must not be negative, got {count}.")


def _type_name(expected_type) -> str:
    """Names an EXPECTED_FIELDS type the way the prompt does ("a string or null")."""
    names = {str: "a string", int: "an integer", type(None): "null"}
    types = expected_type if isinstance(expected_type, tuple) else (expected_type,)
    return " or ".join(names.get(t, t.__name__) for t in types)


# ---------------------------------------------------------------------------------------
# Checks of the answer against the note.
#
# The schema and type checks only say an answer is well-formed. The checks below say whether
# the note supports it: every quoted sentence must occur in the note and be about the thing
# it is evidence for, the flags must agree with each other and with their evidence, the scope
# name must be one the note states, and the biopsy count must be a number the quoted sentence
# states. An answer that fails any check is rejected and the note is retried; a note that
# keeps failing is recorded as failed with the reason, never with an unsupported answer.
# The study prefers a missing answer to a wrong one.
#
# The term lists are deliberately broad. They are there to catch evidence about the wrong
# thing (a limb examination quoted for an endoscopy, an eosinophil count quoted as a biopsy
# count, a brand the note never mentions), not to extract the fields again.
# ---------------------------------------------------------------------------------------

_NOT_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# The evidence field that must support each flag.
EVIDENCE_FOR_FLAG = {
    "Endoscopy": "EndoscopyInformation",
    "Colonoscopy": "ColonoscopyInformation",
    "DuodenalBiopsiesTaken": "DuodenalBiopsiesInformation",
    "FellowPresent": "FellowInformation",
}

# A sentence quoted as evidence of an upper endoscopy, or of a colonoscopy, must mention one.
ENDOSCOPY_TERMS_RE = re.compile(
    r"egd|endoscop|gastroscop|esophagogastroduodenoscop|o?esophag|stomach|gastric|duoden|pylor"
    r"|antr(um|al)|fundus|cardia|\bpeg\b|gastrostomy|oropharynx|hypopharynx|upper gi|\bscope\b"
    r"|z[- ]?line",
    re.I,
)
COLONOSCOPY_TERMS_RE = re.compile(
    r"colonoscop|\bcolon(ic)?\b|ca?ecum|cecal|ileum|ileal|ileocecal|rect(um|al|osigmoid)|sigmoid"
    r"|\banus\b|\banal\b|flexure|bowel prep|withdrawal time",
    re.I,
)
# ...and must describe the procedure being carried out. A procedure note always has a sentence
# with the scope being passed, advanced or withdrawn, or a findings line; a bare name ("EGD",
# "Procedure: EGD", "Endoscopy with Foreign Body Removal") is how history lists, pre-procedure
# assessments and patient instructions mention a procedure, and those never count.
PERFORMED_RE = re.compile(
    r"intubated|advanced|inserted|passed|placed|introduced|withdrawn|removed|performed|examined"
    r"|visuali[sz]ed|inspected|retroflexed|tolerated|completed|lubricated|reached|extracted"
    r"|findings|operation",
    re.I,
)
# ...and must not be history, a plan or instructions. These words mark history, plans,
# recommendations and patient instructions, which never count (prompt rules 1 and 2).
HISTORY_OR_PLAN_RE = re.compile(
    r"\b(recommend\w*|schedul\w*|planned|plans?|will|should|history of|h/o|s/p|status post"
    r"|prior(?!\s+to)|previous\w*|tomorrow|next (week|month|year)|follow[- ]?up|pending|await\w*"
    r"|consider\w*|discuss\w*|instructions?|after your|before your|underwent|was done on)\b",
    re.I,
)

# A sentence quoted as evidence of duodenal biopsies must mention biopsies and name the
# duodenum as the study defines it (bulb, D1 to D3, first to third portion). Biopsies "to rule
# out celiac disease" are duodenal by definition, so that phrase names the site too.
BIOPSY_TERMS_RE = re.compile(r"biops|\bbx\b|specimen|sample|tissue|forceps|histo|patholog", re.I)
DUODENAL_SITE_RE = re.compile(
    r"duoden|\bbulb\b|\bd[123]\b|\b(first|second|third|1st|2nd|3rd)\s+(portion|part)|c(o)?eliac",
    re.I,
)
NO_BIOPSY_RE = re.compile(
    r"\bno (duodenal |random |small bowel )?biops|biops(y|ies)?\s*:?\s*(none|no)\b"
    r"|biops\w*\s+(were|was)\s+not|no specimens|specimens?\s*:?\s*none",
    re.I,
)
# Sites that are not the duodenum. When a biopsy quote also counts one of these, its numbers
# cannot simply be added up to verify the duodenal total.
OTHER_SITE_RE = re.compile(
    r"o?esophag|stomach|gastric|antr(um|al)|fundus|cardia|colon|ileum|ileal|rect(um|al)|sigmoid|\bclo\b",
    re.I,
)

# A sentence quoted as evidence of a fellow must mention a trainee or an attestation, and must
# not be the attestation that rules a trainee out (prompt rule 7). "Was present and actively
# participated" is the teaching-physician attestation wording in these notes (the attending
# attesting to a procedure a trainee performed), so it counts even without a heading.
TRAINEE_TERMS_RE = re.compile(
    r"fellow|resident|trainee|teaching|supervis|assist|attest|\bpgy|house officer|in training"
    r"|student|\bintern\b|actively participated",
    re.I,
)
NO_TRAINEE_RE = re.compile(
    r"staff only|(fellow|resident|trainee)s?\s*:?\s*none\b|no (fellow|resident|trainee)", re.I
)

# A scope name is short, looks like a brand or model, and is not a colonoscope (prompt rule 3).
MAX_SCOPE_CHARS = 80
SCOPE_NAME_RE = re.compile(
    r"\d|olympus|pentax|fuji|storz|boston|ambu|hoya|scope|endoscop|gastroscop", re.I
)
COLONOSCOPE_RE = re.compile(r"colonoscop|sigmoidoscop|\bpcf|\bcf[- ]?[a-z]?\d", re.I)

# More duodenal biopsies than this in one procedure is not a count but some other number from
# the note (an eosinophil count, a CPT code, a date).
MAX_PLAUSIBLE_BIOPSIES = 20
NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split(),
        start=1,
    )
}
NUMBER_WORDS["single"] = 1
# A quote with one of these contains a number that is part of another ("6 ... including 2
# from the bulb", "4 biopsies (one from the bulb)", "5 biopsies with 2 from the bulb"), so its
# numbers must not be added up. Either parenthesis counts: a quote may hold only the closing one.
NO_SUM_RE = re.compile(r"includ|of which|total|[()]|\bwith\s+\w+\s+from\b", re.I)
# Numbers in a biopsy sentence that are not counts: a duodenal site (D1 to D3, "D2-3", "D2-D3",
# the 1st to 3rd portion).
SITE_NUMBER_RE = re.compile(r"\bd\s?[123](\s?-\s?d?[123])?\b|\b[123](st|nd|rd)\b", re.I)


def squash(text: str) -> str:
    """
    Lower-cases text and drops everything but letters and digits, so that a quote and the
    note can be compared regardless of spacing, punctuation, quotation marks and line breaks.
    """
    return _NOT_ALNUM_RE.sub("", text.lower())


def _excerpt(text: str, limit: int = 80) -> str:
    """The start of text, on one line, for a rejection message."""
    text = " ".join(text.split())
    return repr(text if len(text) <= limit else text[:limit] + "...")


def _numbers_in(text: str) -> list[int]:
    """
    Every count-sized number in text, written as digits or as a word ("Six biopsies"); the
    number of a duodenal site ("D2", "the 2nd portion") is not one.
    """
    text = SITE_NUMBER_RE.sub(" ", text)
    numbers = [int(digits) for digits in re.findall(r"\d+", text)]
    numbers += [NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NUMBER_WORDS]
    return [n for n in numbers if 1 <= n <= MAX_PLAUSIBLE_BIOPSIES]


def _additive(quote: str, numbers: list[int]) -> bool:
    """
    Whether the quote counts biopsies from two or more duodenal sites that add up ("4 biopsies
    of the duodenum and 2 from the bulb", "4 for histopathology and 4 for disaccharidases"):
    two or more numbers, none part of another (NO_SUM_RE) and no other organ counted
    (OTHER_SITE_RE).
    """
    return len(numbers) >= 2 and not NO_SUM_RE.search(quote) and not OTHER_SITE_RE.search(quote)


def summed_count_quote(quote: str) -> bool:
    """
    Whether a biopsy quote is additive (_additive): it counts biopsies from two or more parts
    of the duodenum, and the duodenal total it supports is their sum. The consensus flags a
    count that rests on such a sentence, so the researcher can confirm that reading.
    """
    return _additive(quote, _numbers_in(quote))


def supported_counts(quote: str) -> set[int]:
    """
    The duodenal biopsy counts a quoted sentence can support. An additive sentence supports
    only the sum of its numbers: "4 biopsies were taken of the duodenum and 3 additional from
    the bulb" is 7, not 4 or 3, since the bulb is the duodenum. Any other sentence supports
    each number it states: "6 biopsies ... including 2 from the bulb" is 6 (and never 8), and
    a sentence that also counts another organ is not added up.
    """
    numbers = _numbers_in(quote)
    if _additive(quote, numbers):
        return {sum(numbers)}
    return set(numbers)


def _scope_in_note(scope: str, squashed_note: str) -> bool:
    """
    The scope name occurs in the note as given, or, when the model joined a brand and a model
    number that the note states apart ("Olympus endoscope (GIF-H180)"), each part does.
    """
    if squash(scope) in squashed_note:
        return True
    parts = re.findall(r"[a-z0-9]+", scope.lower())
    return bool(parts) and all(part in squashed_note for part in parts)


def _check_procedure_evidence(field: str, quote: str, terms: re.Pattern, what: str):
    """
    The evidence for a performed procedure mentions it, says it was carried out, and is not
    history or a plan.
    """
    if not terms.search(quote):
        raise RetryableLLMError(
            f"{field} does not mention {what}. Quote the sentence that documents the "
            f"procedure being performed in this note, or answer 0.",
            quote,
        )
    if not PERFORMED_RE.search(quote):
        raise RetryableLLMError(
            f"{field} names {what} but does not say it was carried out (no scope passed, "
            f"advanced or withdrawn, nothing examined, no findings). Quote the procedure or "
            f"findings sentence that shows it was performed in this note, or answer 0 if the "
            f"note only mentions it.",
            quote,
        )
    marker = HISTORY_OR_PLAN_RE.search(quote)
    if marker:
        raise RetryableLLMError(
            f"{field} contains '{marker.group(0)}', so it reads as history, a plan, a "
            f"recommendation or instructions rather than {what} performed in this note. "
            f"Only a procedure performed and documented in this note counts; quote its "
            f"procedure or findings sentence, or answer 0.",
            quote,
        )


def verify_against_note(result: dict, note: str):
    """
    Raises RetryableLLMError when the note does not support the (schema-valid) result:
    a quote that is not in the note, evidence about the wrong thing or ruling the answer out,
    a 1 without evidence, flags that contradict each other, a scope name the note does not
    state or that is a colonoscope, or a biopsy count the quoted sentence does not state.
    The message says what was wrong, for the retry prompt and for LLM_Error.
    """
    squashed_note = squash(note)

    # Every quote must come from the note.
    for field in EVIDENCE_FOR_FLAG.values():
        quote = result[field]
        if quote is not None and squash(quote) not in squashed_note:
            raise RetryableLLMError(
                f"{field} is not a sentence from the note. Copy the sentence exactly as the "
                f"note has it, or answer 0 with {field} null if the note does not support it.",
                quote,
            )

    # Every 1 needs evidence, and the evidence must be about the right thing.
    for flag, field in EVIDENCE_FOR_FLAG.items():
        if result[flag] == 1 and result[field] is None:
            raise RetryableLLMError(
                f"{flag} is 1 but {field} is null. Quote the sentence from the note that "
                f"shows it, or answer 0."
            )

    if result["Endoscopy"] == 1:
        _check_procedure_evidence(
            "EndoscopyInformation", result["EndoscopyInformation"], ENDOSCOPY_TERMS_RE, "an upper endoscopy"
        )
    if result["Colonoscopy"] == 1:
        _check_procedure_evidence(
            "ColonoscopyInformation", result["ColonoscopyInformation"], COLONOSCOPY_TERMS_RE, "a colonoscopy"
        )

    if result["DuodenalBiopsiesTaken"] == 1:
        quote = result["DuodenalBiopsiesInformation"]
        if not BIOPSY_TERMS_RE.search(quote):
            raise RetryableLLMError(
                "DuodenalBiopsiesInformation does not mention biopsies. Quote the sentence "
                "stating the duodenal biopsies, or answer 0.",
                quote,
            )
        if not DUODENAL_SITE_RE.search(quote):
            raise RetryableLLMError(
                "DuodenalBiopsiesInformation does not name a duodenal site (duodenum, bulb, "
                "D1 to D3, first to third portion). Quote the sentence, or the sentences "
                "around it, that say the biopsies came from the duodenum, or answer 0 if they "
                "came from elsewhere.",
                quote,
            )
        if NO_BIOPSY_RE.search(quote):
            raise RetryableLLMError(
                "DuodenalBiopsiesInformation says no biopsies were taken, which contradicts "
                "DuodenalBiopsiesTaken 1.",
                quote,
            )

    if result["FellowPresent"] == 1:
        quote = result["FellowInformation"]
        if NO_TRAINEE_RE.search(quote):
            raise RetryableLLMError(
                "FellowInformation is a Staff Only attestation or says there was no trainee; "
                "that means FellowPresent 0.",
                quote,
            )
        if not TRAINEE_TERMS_RE.search(quote):
            raise RetryableLLMError(
                "FellowInformation does not mention a fellow, resident or trainee, or a "
                "teaching attestation. Quote the attestation or the line naming the trainee, "
                "or answer 0.",
                quote,
            )

    # The flags must agree with each other.
    if result["FellowPresent"] == 1 and result["Endoscopy"] == 0 and result["Colonoscopy"] == 0:
        raise RetryableLLMError(
            "FellowPresent is 1 but no procedure was performed in this note (Endoscopy 0 and "
            "Colonoscopy 0); FellowPresent is then 0."
        )
    if result["DuodenalBiopsiesTaken"] == 1 and result["Endoscopy"] == 0:
        raise RetryableLLMError(
            "DuodenalBiopsiesTaken is 1 but Endoscopy is 0; duodenal biopsies are taken during "
            "an upper endoscopy. Answer Endoscopy 1 with its evidence, or DuodenalBiopsiesTaken 0."
        )

    # The scope name must be the note's, and an upper endoscope.
    scope = result["ScopeType"]
    if scope is not None:
        if result["Endoscopy"] == 0:
            raise RetryableLLMError(
                "ScopeType is given but Endoscopy is 0; ScopeType is null when no upper "
                "endoscopy was performed in this note.",
                scope,
            )
        if len(scope) > MAX_SCOPE_CHARS:
            raise RetryableLLMError(
                "ScopeType is a sentence, not a scope name. Give only the brand and model, "
                "such as 'Olympus GIF-H190'.",
                scope,
            )
        if COLONOSCOPE_RE.search(scope):
            raise RetryableLLMError(
                "ScopeType names a colonoscope. Only the upper endoscope counts; ScopeType is "
                "null when the note names no upper endoscope.",
                scope,
            )
        if not SCOPE_NAME_RE.search(scope):
            raise RetryableLLMError(
                "ScopeType does not look like an endoscope brand or model. Give the brand and "
                "model the note states, or null.",
                scope,
            )
        if not _scope_in_note(scope, squashed_note):
            raise RetryableLLMError(
                "ScopeType is not stated in the note. Copy the brand and model the note gives "
                "for the upper endoscope, or answer null if it gives none.",
                scope,
            )

    # The count must be a number the quoted sentence states.
    count = result["NumberOfDuodenalBiopsies"]
    if count is not None and count > MAX_PLAUSIBLE_BIOPSIES:
        raise RetryableLLMError(
            f"NumberOfDuodenalBiopsies is {count}, which is not a plausible number of duodenal "
            f"biopsies from one procedure; it is some other number from the note. Use the "
            f"number of biopsies the note states for the duodenum, or null when it states none."
        )
    if count:
        quote = result["DuodenalBiopsiesInformation"]
        supported = supported_counts(quote)
        if count not in supported:
            numbers = _numbers_in(quote)
            if _additive(quote, numbers):
                stated = (
                    f"{' and '.join(str(n) for n in numbers)} from different parts of the duodenum, "
                    f"{sum(numbers)} in all, and the duodenal total is their sum"
                )
            else:
                stated = ", ".join(str(n) for n in sorted(supported)) or "no number"
            raise RetryableLLMError(
                f"NumberOfDuodenalBiopsies is {count} but DuodenalBiopsiesInformation states "
                f"{stated}. Use the number the note states for the duodenum (bulb included, "
                f"counted once), quoting the sentence that states it, or null when it states none."
            )


def llm_analysis(
    id: int, note: str, model: ModelSpec | str = LOCAL_MODEL, attempt: int = 1, rejected: str | None = None,
    observer=None,
) -> Tuple[dict, str]:
    """
    Analyzes a clinical note using an LLM (the Ollama model given by model, a ModelSpec or a
    bare model name called with the default context window) to extract the study fields.
    attempt (1-based) selects the generation options; see generation_options. rejected is the
    reason the previous attempt failed (a RetryableLLMError message); it is added to the prompt.
    The response is streamed; observer, when given, is called with every chunk (an ollama
    ChatResponse: a token of thinking or content, and last the done chunk with the server's
    token counts and timings), so the caller can show what the model is doing.

    Raises:
        RetryableLLMError: the output was unusable or the note does not support the answer;
            another attempt may succeed.
        LLMAnalysisError: the call itself failed.

    Returns:
        (result, raw): the validated field dictionary (with the note id) and the verbatim model text.
    """
    spec = model if isinstance(model, ModelSpec) else ModelSpec(str(model), CONTEXT_LENGTH)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT.format(note=note)},
    ]
    if rejected:
        messages[1]["content"] += RETRY_PROMPT.format(reason=rejected)

    # Each call (every note and every retry) gets its own client, so its HTTP connection
    # is closed when the call returns instead of being held open between notes. The answer
    # is streamed token by token and joined here; the text is the same as an unstreamed call's.
    content = []
    try:
        with ollama.Client() as client:
            stream = client.chat(model=spec.name, messages=messages, think=spec.think, stream=True,
                                 format=RESPONSE_SCHEMA, options=generation_options(attempt, spec.num_ctx))
            for chunk in stream:
                if chunk.message.content:
                    content.append(chunk.message.content)
                if observer is not None:
                    observer(chunk)
    except Exception as e:
        # Ollama stops a model that keeps repeating itself ("prediction aborted, token repeat
        # limit reached", an error in the stream). That is the repetition loop the retries exist for.
        if "repeat limit" in str(e).lower():
            raise RetryableLLMError(f"the model repeated itself until Ollama stopped it ({e}).")
        raise LLMAnalysisError(f"{CALL_FAILED_PREFIX}: {e}")

    raw = "".join(content)
    try:
        parsed = _parse_and_check(id, note, raw)
    except RetryableLLMError as e:
        e.raw = raw
        raise
    return parsed, raw


def _parse_and_check(id: int, note: str, raw: str) -> dict:
    """
    Turns the model text into the validated field dictionary: parses the JSON (repairing
    common slips), normalises near-miss values, checks the schema, then checks the answer
    against the note. Raises RetryableLLMError at the first problem.
    """
    clean = _strip_markdown_fences(raw)

    try:
        parsed = parse_json_with_repair(clean)
    except json.JSONDecodeError as e:
        raise RetryableLLMError(f"the output was not valid JSON ({e}).")
    if not isinstance(parsed, dict):
        raise RetryableLLMError("the output was not a JSON object.")

    runaway = [k for k, v in parsed.items() if isinstance(v, str) and len(v) >= MAX_TEXT_CHARS]
    if runaway:
        raise RetryableLLMError(
            f"{', '.join(runaway)} repeated text until it reached the {MAX_TEXT_CHARS}-character limit; "
            "quote one sentence or line from the note."
        )

    parsed["id"] = id

    parsed = normalize_llm_result(parsed)

    _validate_llm_result(parsed)

    verify_against_note(parsed, note)

    return parsed

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

# Generation settings. Temperature 0 makes decoding greedy, so the same note gives the
# same answer on every run, which a study needs; Ollama's server default is 0.8.
# A valid answer is ~120 tokens; num_predict stops a model stuck repeating a phrase
# instead of letting it run to the context limit.
GENERATION_OPTIONS = {"temperature": 0, "num_predict": 1024}

# Greedy decoding gives the same output on every call, so a retry after a repetition
# loop would loop again. Retries therefore sample, with the attempt number as the
# seed so a rerun of the study still reproduces them.
RETRY_TEMPERATURE = 0.8


def generation_options(attempt: int) -> dict:
    """
    Returns the Ollama options for the given attempt (1-based): greedy on the first
    attempt, seeded sampling on retries.
    """
    if attempt == 1:
        return GENERATION_OPTIONS
    return {**GENERATION_OPTIONS, "temperature": RETRY_TEMPERATURE, "seed": attempt}

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
- Read the whole note, including headers such as OPERATION/PROCEDURE, PROCEDURE DETAILS, FINDINGS, SPECIMENS, BIOPSY, Teaching Statement, Staff Only, RESIDENT/FELLOW, and the signature block.
- When one note documents both an upper endoscopy and a colonoscopy, answer ScopeType and the biopsy fields for the upper endoscopy and set Colonoscopy to 1 as well.
"""

USER_PROMPT = """\
Extract the study fields from the clinical note below and return only the JSON object.

NOTE:
\"\"\"{note}\"\"\"
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


class OllamaStatus(NamedTuple):
    host: str              # the server address the client uses
    reachable: bool        # the server answered /api/tags
    model_available: bool  # the requested model is among the models it serves
    detail: str            # the error when unreachable, else the served model names


def ollama_status(model: str = LOCAL_MODEL, timeout: float = 3.0) -> OllamaStatus:
    """
    Probes the Ollama server (OLLAMA_HOST, else the default) without loading a model,
    bounded by timeout seconds, and reports whether model is available there.
    """
    host = os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST
    try:
        models = ollama.Client(timeout=timeout).list().models
    except Exception as e:
        return OllamaStatus(host, False, False, f"{type(e).__name__}: {e}")
    names = [m.model for m in models if m.model]
    available = model in names or f"{model}:latest" in names
    return OllamaStatus(host, True, available, ", ".join(names) or "no models pulled")


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

    return parsed


def _validate_llm_result(result: dict):
    """
    Validates that the LLM result dictionary contains all required fields,
    has no unexpected fields, and uses correct types and values.
    """
    # Required fields and types
    for field, expected_type in EXPECTED_FIELDS.items():
        if field not in result:
            raise LLMAnalysisError(f"Missing field: {field}")

        if not isinstance(result[field], expected_type):
            raise LLMAnalysisError(
                f"Invalid type for '{field}': "
                f"expected {expected_type}, got {type(result[field])}"
            )

    # No extra fields
    extra_fields = set(result.keys()) - set(EXPECTED_FIELDS.keys())
    if extra_fields:
        raise LLMAnalysisError(f"Unexpected fields returned: {extra_fields}")

    # Boolean-as-int enforcement
    for field in BOOL_FIELDS:
        if result[field] not in (0, 1):
            raise LLMAnalysisError(
                f"{field} must be 0 or 1, got {result[field]}"
            )


def llm_analysis(id: int, note: str, model: str = LOCAL_MODEL, attempt: int = 1) -> Tuple[dict, str]:
    """
    Analyzes a clinical note using an LLM (the Ollama model named by model) to extract the study fields.
    attempt (1-based) selects the generation options; see generation_options.

    Returns:
        (result, raw): the validated field dictionary (with the note id) and the verbatim model text.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT.format(note=note)},
    ]

    try:
        response = ollama.chat(model=model, messages=messages, think=False, options=generation_options(attempt))
    except Exception as e:
        raise LLMAnalysisError(f"LLM call failed: {e}")

    raw = response["message"]["content"]
    clean = _strip_markdown_fences(raw)

    try:
        parsed = parse_json_with_repair(clean)
    except json.JSONDecodeError as e:
        raise LLMAnalysisError(f"Invalid JSON returned by LLM: {e}")

    parsed["id"] = id

    parsed = normalize_llm_result(parsed)

    _validate_llm_result(parsed)

    return parsed, raw

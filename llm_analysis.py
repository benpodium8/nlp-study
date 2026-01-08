import json
import re
from typing import Tuple
import ollama

LOCAL_MODEL = "gemma2:2b"

EXPECTED_FIELDS = {
    "id": int,
    "ScopeType": (str, type(None)),
    "Colonoscopy": int,
    "ColonoscopyInformation": (str, type(None)),
    "Endoscopy": int,
    "EndoscopyInformation": (str, type(None)),
    "NumberOfDuodenalBiopsies": int,
    "DuodenalBiopsiesTaken": int,
    "DuodenalBiopsiesInformation": (str, type(None)),
    "FellowPresent": int,
    "FellowInformation": (str, type(None)),
}

JSON_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    re.DOTALL | re.IGNORECASE
)

TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class LLMAnalysisError(Exception):
    pass


def _strip_markdown_fences(text: str) -> str:
    """
    Removes markdown code fences (```json or ```) from text if present.
    """
    m = JSON_FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
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

    # Trim to outermost JSON object
    start = repaired.find("{")
    end = repaired.rfind("}")
    if start != -1 and end != -1 and end > start:
        repaired = repaired[start:end + 1]

    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise e


def normalize_llm_result(parsed: dict) -> dict:
    """
    Ensures required integer fields are never None.
    """
    int_fields = [
        "Colonoscopy",
        "Endoscopy",
        "NumberOfDuodenalBiopsies",
        "DuodenalBiopsiesTaken",
        "FellowPresent",
    ]

    for field in int_fields:
        if parsed.get(field) is None:
            parsed[field] = 0

    return parsed


def _validate_llm_result(result: dict):
    """
    Validates that the LLM result dictionary contains all required fields
    with correct types.
    """
    for field, expected_type in EXPECTED_FIELDS.items():
        if field not in result:
            raise LLMAnalysisError(f"Missing field: {field}")

        if not isinstance(result[field], expected_type):
            raise LLMAnalysisError(
                f"Invalid type for '{field}': "
                f"expected {expected_type}, got {type(result[field])}"
            )


def llm_analysis(id: int, note: str) -> Tuple[dict, str]:
    """
    Analyzes a clinical note using an LLM to extract structured data.
    """
    messages = [
        {
            "role": "system",
            "content": """
                You are a strict clinical information extraction API.

                CRITICAL OUTPUT RULES:
                - Return ONLY valid JSON.
                - No markdown, no explanations, no comments.
                - The response MUST start with { and end with }.
                - Do NOT guess or infer missing information.
                - If information is not explicitly stated, use null or 0 as appropriate.
                - Use integers 1 or 0 for all boolean fields.

                FINAL REMINDER:
                - ALL integer fields must be integers (never null)
                - No trailing commas
                - JSON must parse successfully

                MATCH THIS SCHEMA EXACTLY:
                {
                    "ScopeType": string | null,
                    "Colonoscopy": 0 | 1,
                    "ColonoscopyInformation": string | null,
                    "Endoscopy": 0 | 1,
                    "EndoscopyInformation": string | null,
                    "NumberOfDuodenalBiopsies": integer,
                    "DuodenalBiopsiesTaken": 0 | 1,
                    "DuodenalBiopsiesInformation": string | null,
                    "FellowPresent": 0 | 1,
                    "FellowInformation": string | null
                }
                FIELD-SPECIFIC EXTRACTION RULES:

                1) ScopeType
                - Extract ONLY the brand + model of an UPPER endoscope.
                - Examples: "Olympus H190", "Pentax EG-2990i"
                - Look for phrases like:
                - "intubated with an Olympus H190 endoscope"
                - "advanced using a Fujifilm endoscope"
                - EXCLUDE colonoscopes and colonoscopy scopes.
                - If the scope is associated with colonoscopy or colon terms, DO NOT extract it.
                - If unclear or absent, return null.

                2) Colonoscopy
                - Set to 1 ONLY if a colonoscopy was performed.
                - Trigger terms include "colonoscopy".
                - Otherwise set to 0.

                3) ColonoscopyInformation
                - If Colonoscopy == 1, include the most relevant sentence describing it.
                - Otherwise return null.

                4) Endoscopy
                - Set to 1 ONLY if an UPPER endoscopy / EGD was performed.
                - Trigger terms include:
                - "upper endoscopy"
                - "EGD"
                - Esophagus + endoscope use
                - Otherwise set to 0.

                5) EndoscopyInformation
                - If Endoscopy == 1, include the most relevant sentence describing it.
                - Otherwise return null.

                6) NumberOfDuodenalBiopsies (MOST IMPORTANT)
                - Extract the NUMBER of biopsies taken from the duodenum.
                - Duodenum includes:
                - "duodenum"
                - "duodenal"
                - "bulb" or "bulb of the duodenum"
                - Only report a number if the biopsies are clearly associated with the duodenum.
                - If multiple biopsy numbers are mentioned, prefer the one closest to duodenal terms.
                - If no explicit number is stated, return 0.
                - Do NOT sum numbers unless explicitly stated as total duodenal biopsies.

                7) DuodenalBiopsiesTaken
                - Set to 1 if at least one duodenal biopsy was taken.
                - Set to 0 otherwise.

                8) DuodenalBiopsiesInformation
                - If duodenal biopsies were taken, include the most relevant sentence.
                - Otherwise return null.

                9) FellowPresent
                - Set to 1 ONLY if a fellow is explicitly mentioned.
                - Fellow information typically appears near the END of the note.
                - If the note explicitly states no fellow, set to 0.
                - Otherwise set to 0.

                10) FellowInformation
                - If FellowPresent == 1, include the sentence mentioning the fellow.
                - Otherwise return null.

                ABSOLUTE RULE:
                If a value is not explicitly stated in the text, DO NOT infer it.
                """
        },
        {
            "role": "user",
            "content": f'''
                Parse the following clinical note and extract the structured data.

                "{note}"
            '''
        }
    ]

    response = ollama.chat(model=LOCAL_MODEL, messages=messages)

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

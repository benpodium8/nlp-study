import json
import re
from typing import Tuple
import ollama

LOCAL_MODEL = "gemma4:e2b"

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

BOOL_FIELDS = {
    "Colonoscopy",
    "Endoscopy",
    "DuodenalBiopsiesTaken",
    "FellowPresent",
}

JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE
)

TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
SINGLE_QUOTE_KEY_RE = re.compile(r"'([A-Za-z0-9_]+)'\s*:")
SINGLE_QUOTE_VALUE_RE = re.compile(r":\s*'([^']*)'")


class LLMAnalysisError(Exception):
    pass


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


def llm_analysis(id: int, note: str) -> Tuple[dict, str]:
    """
    Analyzes a clinical note using an LLM to extract structured data.
    """
    messages = [
        {
            "role": "system",
            "content": """
                You are a deterministic clinical information extraction engine.

                YOUR TASK:
                - Extract ONLY explicitly stated information from the provided clinical note.
                - Produce ONE valid JSON object that matches the schema exactly.
                - Do NOT explain, summarize, or include any text outside the JSON.

                ABSOLUTE OUTPUT RULES:
                - Output MUST be valid JSON.
                - Output MUST start with { and end with }.
                - Do NOT include markdown, comments, or prose.
                - Do NOT infer, guess, or assume.
                - If information is not explicitly stated, use null or 0.
                - All boolean fields MUST be integers: 1 or 0.
                - All integer fields MUST be integers (never null).
                - Do NOT include extra fields.
                - Do NOT include trailing commas.

                SCHEMA (MATCH EXACTLY):
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

                FIELD EXTRACTION RULES:

                1. ScopeType
                - Extract ONLY the brand + model of an UPPER endoscope.
                - Examples: "Olympus H190", "Pentax EG-2990i".
                - Ignore colonoscopes and colonoscopy-related scopes.
                - If unclear or not explicitly stated, return null.

                2. Colonoscopy
                - Set to 1 ONLY if a colonoscopy was performed.
                - Trigger word: "colonoscopy".
                - Otherwise set to 0.

                3. ColonoscopyInformation
                - If Colonoscopy == 1, copy the single most relevant sentence verbatim.
                - Otherwise return null.

                4. Endoscopy
                - Set to 1 ONLY if an upper endoscopy / EGD was performed.
                - Triggers include: "upper endoscopy", "EGD", esophageal intubation with an endoscope.
                - Otherwise set to 0.

                5. EndoscopyInformation
                - If Endoscopy == 1, copy the single most relevant sentence verbatim.
                - Otherwise return null.

                6. NumberOfDuodenalBiopsies
                - Extract the explicit number of biopsies taken from the duodenum.
                - Duodenum includes: "duodenum", "duodenal", "bulb", "duodenal bulb".
                - Use ONLY explicitly stated numbers.
                - If no explicit number is stated, return 0.

                7. DuodenalBiopsiesTaken
                - Set to 1 if at least one duodenal biopsy was taken.
                - Otherwise set to 0.

                8. DuodenalBiopsiesInformation
                - If duodenal biopsies were taken, copy the most relevant sentence verbatim.
                - Otherwise return null.

                9. FellowPresent
                - Set to 1 ONLY if a fellow is explicitly mentioned.
                - If explicitly stated that no fellow was present, set to 0.
                - Otherwise set to 0.

                10. FellowInformation
                - If FellowPresent == 1, copy the sentence mentioning the fellow verbatim.
                - Otherwise return null.
                """
                        },
                        {
                            "role": "user",
                            "content": f"""
                Extract the structured data from the following clinical note.

                NOTE:
                \"\"\"{note}\"\"\"
                """
        }
    ]

    try:
        response = ollama.chat(model=LOCAL_MODEL, messages=messages)
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

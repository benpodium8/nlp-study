import re
import spacy

nlp = spacy.blank("en")
nlp.add_pipe("sentencizer")

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
}

def normalize_number(text: str):
    """
    Converts a text representation of a number (digit string or number word) to an integer.
    
    Parameters:
        text: Text that may be a digit string or number word (e.g., "5" or "five").
    
    Returns:
        int or None: Integer value if conversion succeeds, None otherwise.
    """
    t = text.lower()
    if t.isdigit():
        return int(t)
    return NUMBER_WORDS.get(t)

BIOPSY_COUNT_PREFIX_RE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b.{0,40}?\bbiops",
    re.I
)

BIOPSY_COUNT_SUFFIX_RE = re.compile(
    r"\bbiops(?:y|ies)\b.{0,40}?\b(\d+)\b",
    re.I
)

SCOPE_RE = re.compile(
    r"\b(Olympus|Pentax|Fujifilm)\s+([A-Za-z0-9\- ]{2,30})",
    re.I
)

UPPER_PROC_TERMS = {"upper endoscopy", "egd"}
COLON_PROC_TERMS = {"colonoscopy"}

DUODENUM_TERMS = {"duodenum", "duodenal", "bulb"}
BIOPSY_TERMS = {"biopsy", "biopsies", "biopsied"}
NEGATION_TERMS = {"no", "not", "none", "without"}

PROCEDURE_VERBS = {
    "placed", "intubated", "advanced", "inserted",
    "performed", "taken", "biopsy", "biopsies"
}

COLON_EXCLUDE_TERMS = {
    "colon", "colonic", "rectum", "rectal",
    "ileum", "ileal", "cecum", "sigmoid",
    "colonoscope", "pcf", "cf"
}

def contains_any(text: str, terms: set) -> bool:
    """
    Checks if any of the given terms appear in the text.
    
    Parameters:
        text: Text to search within.
        terms: Set of terms to search for.
    
    Returns:
        bool: True if any term is found in the text, False otherwise.
    """
    return any(t in text for t in terms)

def has_negation(text: str) -> bool:
    """
    Checks if the text contains any negation terms (no, not, none, without).
    
    Parameters:
        text: Text to check for negation.
    
    Returns:
        bool: True if negation terms are found, False otherwise.
    """
    return any(n in text for n in NEGATION_TERMS)

def nlp_analysis(id: int, note: str):
    """
    Analyzes a clinical note using NLP techniques to extract structured data.
    Extracts information about scope type, colonoscopy, endoscopy, duodenal biopsies, and fellow presence.
    
    Parameters:
        id: Note ID to include in the result.
        note: Clinical note text to analyze.
    
    Returns:
        dict: Dictionary containing extracted fields: id, ScopeType, Colonoscopy, ColonoscopyInformation,
              Endoscopy, EndoscopyInformation, NumberOfDuodenalBiopsies, DuodenalBiopsiesTaken,
              DuodenalBiopsiesInformation, FellowPresent, FellowInformation.
    """
    doc = nlp(note)
    sents = list(doc.sents)

    result = {
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

    duodenum_sentence_idxs = set()
    biopsy_count_candidates = []

    for i, sent in enumerate(sents):
        s = sent.text.lower()

        if (
            result["Endoscopy"] == 0
            and contains_any(s, UPPER_PROC_TERMS)
            and contains_any(s, PROCEDURE_VERBS)
        ):
            result["Endoscopy"] = 1
            result["EndoscopyInformation"] = sent.text

        if result["Colonoscopy"] == 0 and contains_any(s, COLON_PROC_TERMS):
            result["Colonoscopy"] = 1
            result["ColonoscopyInformation"] = sent.text

        if result["ScopeType"] is None and "endoscope" in s:
            if not contains_any(s, COLON_EXCLUDE_TERMS):
                m = SCOPE_RE.search(sent.text)
                if m:
                    scope = f"{m.group(1)} {m.group(2)}".strip()
                    scope = re.sub(r"\s+endoscope$", "", scope, flags=re.I)
                    result["ScopeType"] = scope

        if result["Endoscopy"] == 0:
            if "esophagus" in s and "endoscope" in s and not contains_any(s, COLON_EXCLUDE_TERMS):
                result["Endoscopy"] = 1
                result["EndoscopyInformation"] = sent.text

        if contains_any(s, DUODENUM_TERMS):
            duodenum_sentence_idxs.add(i)

        if contains_any(s, BIOPSY_TERMS) and not has_negation(s):
            m = BIOPSY_COUNT_PREFIX_RE.search(sent.text)
            if not m:
                m = BIOPSY_COUNT_SUFFIX_RE.search(sent.text)
            if m:
                count = normalize_number(m.group(1))
                if count:
                    biopsy_count_candidates.append((count, i, sent.text))

        if (
            result["FellowPresent"] == 0
            and "fellow" in s
            and "none" not in s
            and sent.start > 0.7 * len(doc)
        ):
            result["FellowPresent"] = 1
            result["FellowInformation"] = sent.text

    for count, idx, text in biopsy_count_candidates:
        if any(abs(idx - d) <= 2 for d in duodenum_sentence_idxs):
            result["NumberOfDuodenalBiopsies"] = count
            result["DuodenalBiopsiesTaken"] = 1
            result["DuodenalBiopsiesInformation"] = text
            break

    if result["DuodenalBiopsiesTaken"] == 0:
        for i in duodenum_sentence_idxs:
            s = sents[i].text.lower()
            if contains_any(s, BIOPSY_TERMS) and not has_negation(s):
                result["DuodenalBiopsiesTaken"] = 1
                result["DuodenalBiopsiesInformation"] = sents[i].text
                break

    return result

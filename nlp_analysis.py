import re
import spacy

# ---------- NLP ----------
nlp = spacy.blank("en")
nlp.add_pipe("sentencizer")

# ---------- Number normalization ----------
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
}

def normalize_number(text: str):
    t = text.lower()
    if t.isdigit():
        return int(t)
    return NUMBER_WORDS.get(t)

# ---------- Regex ----------
BIOPSY_COUNT_RE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"\s+(?:random\s+)?biops(?:y|ies)",
    re.I
)

SCOPE_RE = re.compile(
    r"\b(Olympus|Pentax|Fujifilm)\s+([A-Za-z0-9\- ]{2,30})\s+"
    r"(?:endoscope|gastroscope)",
    re.I
)

# ---------- Semantic vocab ----------
UPPER_PROC_TERMS = {"upper endoscopy", "egd"}
COLON_PROC_TERMS = {"colonoscopy"}

DUODENUM_TERMS = {"duodenum", "duodenal", "bulb"}
BIOPSY_TERMS = {"biopsy", "biopsies"}

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
    return any(t in text for t in terms)

# ---------- Main ----------
def nlp_analysis(id: int, note: str):
    doc = nlp(note)

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

    for sent in doc.sents:
        s = sent.text.lower()

        # ---------- Endoscopy (procedural only, set once) ----------
        if (
            result["Endoscopy"] == 0
            and contains_any(s, UPPER_PROC_TERMS)
            and contains_any(s, PROCEDURE_VERBS)
        ):
            result["Endoscopy"] = 1
            result["EndoscopyInformation"] = sent.text

        # ---------- Colonoscopy ----------
        if result["Colonoscopy"] == 0 and contains_any(s, COLON_PROC_TERMS):
            result["Colonoscopy"] = 1
            result["ColonoscopyInformation"] = sent.text

        # ---------- Scope Type (upper only) ----------
        if result["ScopeType"] is None and "endoscope" in s:
            if not contains_any(s, COLON_EXCLUDE_TERMS):
                m = SCOPE_RE.search(sent.text)
                if m:
                    result["ScopeType"] = f"{m.group(1)} {m.group(2)}".strip()

        # ---------- Duodenal biopsies ----------
        if (
            contains_any(s, BIOPSY_TERMS)
            and contains_any(s, DUODENUM_TERMS)
            and result["NumberOfDuodenalBiopsies"] == 0
        ):
            m = BIOPSY_COUNT_RE.search(sent.text)
            if m:
                count = normalize_number(m.group(1))
                if count:
                    result["NumberOfDuodenalBiopsies"] = count
                    result["DuodenalBiopsiesTaken"] = 1
                    result["DuodenalBiopsiesInformation"] = sent.text

        # ---------- Fellow (footer only, no negation) ----------
        if (
            result["FellowPresent"] == 0
            and "fellow" in s
            and "none" not in s
            and sent.start > 0.7 * len(doc)
        ):
            result["FellowPresent"] = 1
            result["FellowInformation"] = sent.text

    return result

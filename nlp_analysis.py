import re
import spacy

nlp = spacy.blank("en")
nlp.add_pipe("sentencizer")


SCOPE_REGEX = re.compile(
    r"\b(Olympus|Pentax|Fujifilm)\s+[A-Za-z0-9\- ]{2,20}",
    re.I
)

NUMBER_REGEX = re.compile(r"\b(\d+)\b")

UPPER_PROC_TERMS = {"endoscopy", "egd"}

COLON_PROC_TERMS = {"colonoscopy"}

DUODENUM_TERMS = {"duodenum", "duodenal", "bulb"}

BIOPSY_TERMS = {"biopsy", "biopsies"}

COLON_EXCLUDE_TERMS = {
    "colon", "colonic", "rectum", "rectal",
    "ileum", "ileal", "cecum", "sigmoid",
    "colonoscope", "pcf", "cf"
}

FELLOW_TERMS = {"fellow"}

def contains_any(text: str, terms: set) -> bool:
    return any(t in text for t in terms)

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

        # Endoscopy
        if contains_any(s, UPPER_PROC_TERMS):
            result["Endoscopy"] = 1
            result["EndoscopyInformation"] = sent.text

        # Colonoscopy
        if contains_any(s, COLON_PROC_TERMS):
            result["Colonoscopy"] = 1
            result["ColonoscopyInformation"] = sent.text

        # Scope Type (Upper only)
        if "endoscope" in s and not contains_any(s, COLON_EXCLUDE_TERMS):
            m = SCOPE_REGEX.search(sent.text)
            if m:
                result["ScopeType"] = m.group(0).strip()

        # Duodenal Biopsies
        if contains_any(s, BIOPSY_TERMS) and contains_any(s, DUODENUM_TERMS):
            nums = NUMBER_REGEX.findall(s)
            count = sum(map(int, nums)) if nums else 1

            result["NumberOfDuodenalBiopsies"] += count
            result["DuodenalBiopsiesTaken"] = 1
            result["DuodenalBiopsiesInformation"] = sent.text

        # Fellow
        if contains_any(s, FELLOW_TERMS):
            result["FellowPresent"] = 1
            result["FellowInformation"] = sent.text

    return result

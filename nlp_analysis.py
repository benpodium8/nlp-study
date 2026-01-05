import spacy
import medspacy
from medspacy.target_matcher import TargetRule
from medspacy.context import ConText


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

def normalize_number(token_text: str) -> int | None:
    text = token_text.lower()
    if text.isdigit():
        return int(text)
    return NUMBER_WORDS.get(text)


# --------- Build NLP pipeline once ---------

def build_nlp():
    nlp = spacy.blank("en")

    # Sentence splitting
    nlp.add_pipe("sentencizer")
    nlp.add_pipe("medspacy_pyrush")

    # Target matcher (spaCy v3 factory)
    nlp.add_pipe("medspacy_target_matcher")
    target_matcher = nlp.get_pipe("medspacy_target_matcher")

    # Colonoscopy
    target_matcher.add(
        TargetRule(
            literal="colonoscopy",
            category="COLONOSCOPY",
            pattern=[{"LOWER": "colonoscopy"}],
        )
    )

    # Endoscopy / EGD
    target_matcher.add(
        TargetRule(
            literal="endoscopy",
            category="ENDOSCOPY",
            pattern=[{"LOWER": "endoscopy"}],
        )
    )
    target_matcher.add(
        TargetRule(
            literal="egd",
            category="ENDOSCOPY",
            pattern=[{"LOWER": "egd"}],
        )
    )

    # Duodenal biopsies
    target_matcher.add(
        TargetRule(
            literal="duodenal biopsy",
            category="DUODENAL_BIOPSY",
            pattern=[
                {"LIKE_NUM": True},
                {"OP": "*"},  # allows "random", "multiple", etc.
                {"LOWER": "biopsies"},
                {"LOWER": {"IN": ["were", "was"]}, "OP": "?"},
                {"LOWER": "taken", "OP": "?"},
                {"LOWER": {"IN": ["of", "from"]}, "OP": "?"},
                {"LOWER": "the", "OP": "?"},
                {"LOWER": {"IN": ["duodenum", "duodenal", "bulb"]}},
            ],
        )
    )


    # Scope type (upper endoscope only)
    target_matcher.add(
        TargetRule(
            literal="scope type",
            category="SCOPE_TYPE",
            pattern=[
                {"LOWER": {"IN": ["olympus", "pentax", "fujifilm"]}},
                {"TEXT": {"REGEX": "[A-Za-z0-9\\-]+"}, "OP": "+"},
            ],
        )
    )

    # Fellow
    target_matcher.add(
        TargetRule(
            literal="fellow",
            category="FELLOW",
            pattern=[{"LOWER": "fellow"}],
        )
    )

    # ConText (negation, etc.)
    nlp.add_pipe("medspacy_context")

    return nlp


# Build pipeline once
_NLP = build_nlp()


# --------- Main analysis function ---------

def nlp_analysis(id: int, note: str):
    doc = _NLP(note)

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

    for ent in doc.ents:
        if ent._.is_negated:
            continue

        if ent.label_ == "COLONOSCOPY":
            result["Colonoscopy"] = 1
            result["ColonoscopyInformation"] = ent.sent.text

        elif ent.label_ == "ENDOSCOPY":
            result["Endoscopy"] = 1
            result["EndoscopyInformation"] = ent.sent.text

        elif ent.label_ == "DUODENAL_BIOPSY":
            for token in ent:
                if token.like_num:
                    value = normalize_number(token.text)
                    if value is not None:
                        result["NumberOfDuodenalBiopsies"] += value
                        result["DuodenalBiopsiesTaken"] = 1
                        result["DuodenalBiopsiesInformation"] = ent.sent.text

        elif ent.label_ == "SCOPE_TYPE":
            if "colon" not in ent.text.lower():
                result["ScopeType"] = ent.text

        elif ent.label_ == "FELLOW":
            result["FellowPresent"] = 1
            result["FellowInformation"] = ent.sent.text

    return result

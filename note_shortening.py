"""
Shortens a note too long for the model's context window so that it can be sent at all.

Only notes that do not fit (llm_analysis.note_fits_context) come here; every other note is
sent as stored, exactly as before. Three steps are tried in order, each on the output of the
one before, and the first that brings the note under the limit ends the shortening:

  1. boilerplate removed: whitespace tidied, repeated template headers dropped, and sections
     that cannot hold a study answer (medications, allergies, histories, review of systems,
     vitals, labs, exam and its body systems, nutrition, ventilator and fluid tallies,
     consent, sedation) dropped, together with consent and sedation paragraphs that have no
     section header. A section or paragraph whose text (its header aside) mentions a study
     term (KEY_TERMS_RE) is always kept, so this step cannot lose an answer.
  2. relevant sections kept: when the note has section headers, the text before the first
     header and every section whose header names the procedure, findings, specimens,
     pathology or the staff, or whose text mentions a study term, are kept; the rest is
     dropped. Skipped when the note has fewer than two headers or no relevant section.
  3. keyword passages kept: the note is split into lines (long lines into sentences), and the
     lines that mention a procedure term (PROCEDURE_TERMS_RE) are kept, then their neighbours,
     then the lines that mention only a staff or specimen term (STAFF_TERMS_RE) and theirs
     (STEP3_ORDER), in the note's order, until the limit is reached.

The stored notes have no line breaks: the export that produced them turned every break into a
run of spaces. A run of four or more spaces, or of two or more before a header ("   FINDINGS:"),
is therefore read as a line break, and every other run as one space.

Where steps 2 and 3 leave a gap, the text sent has a line "[...]" in its place, so the model
is not led to read two passages as one. The shortening depends only on the note and the
context window, so every model with the same window (all study models use 8192) is sent the
same text, and the consensus compares answers to the same input. data_worker records what
was done in InputReduced and InputReduction.
"""
import re
from typing import NamedTuple

from llm_analysis import max_note_chars, note_fits_context

# A study term: anything the ten fields are about. Used to protect text in step 1 and to pick
# text in steps 2 and 3. Tuned on the 111 notes of the 5,000-note corpus that exceed the 8K
# window (Progress Notes, Consults, Discharge Summaries): a bare "present" matched 481 segments
# there ("present on admission", "represent"), so only "present" for or during something counts,
# and a duodenal site "D3" is not vitamin D3.
# The procedure itself: the upper endoscopy, the scope, biopsies and the duodenum, a colonoscopy.
PROCEDURE_TERMS_RE = re.compile(
    r"biops|duoden|\bbulb\b|(?<!vitamin )\bd\s?[123]\b|endoscop|\begd\b|esophagogastro|gastroscop|"
    r"colonoscop|\bscope|\bgif\b|olympus|pentax|fujinon|forceps",
    re.I,
)
# Who was there and what was sent to pathology: frequent in long notes for other reasons (lab
# specimens, the attending's attestation of a ward round), so step 3 takes them after the
# procedure terms.
STAFF_TERMS_RE = re.compile(
    r"\bfellows?\b|attending|supervis|attest|teaching|\b(was|were|not) present\b|"
    r"\bpresent (for|during|throughout|at|and)\b|immediately available|\bstaff (involved|only)\b|specimen|patholog",
    re.I,
)
KEY_TERMS_RE = re.compile(f"{PROCEDURE_TERMS_RE.pattern}|{STAFF_TERMS_RE.pattern}", re.I)

# A section header: a line that starts with a capital and a short name followed by a colon
# ("Findings:", "Specimens Removed: two jars"), or a short line in capitals ("IMPRESSION").
_HEADER_COLON_RE = re.compile(r"^(?P<name>[A-Z][A-Za-z0-9 /&,()'\-]{0,48}?)\s*:(?P<rest>.*)$")
_HEADER_CAPS_RE = re.compile(r"^(?P<name>[A-Z][A-Z0-9 /&,()'\-]{2,48})$")
_MAX_HEADER_WORDS = 6

# Sections that cannot hold a study answer (step 1), by header name. Besides the usual note
# sections, the long notes are mostly ward Progress Notes, whose exam lists one header per body
# system and whose tallies (ventilator, fluids, weights, nutrition) run to thousands of characters.
BOILERPLATE_HEADER_RE = re.compile(
    r"^((current|home|active|outpatient|inpatient|discharge|prior to admission|scheduled|prn|continuous)\s+)?"
    r"(medications?|meds|infusions)\b|"
    r"^allerg|"
    r"^(past\s+)?(medical|surgical|family|social|ob|obstetric|birth)\s+(history|hx)\b|^(pmh|psh|fhx?|shx?|pmhx)\b|"
    r"^history of present illness\b|^hpi\b|^interval history\b|"
    r"^review of systems\b|^ros\b|"
    r"^vitals?\b|^vital signs\b|^labs?\b|^laboratory\b|^(lab )?results reviewed\b|^lab results\b|"
    r"^no results\b|^collection time\b|^bmp\b|^cbc\b|^coagulation\b|^radiologic studies\b|^imaging\b|"
    r"^(physical\s+)?exam(ination)?\b|^objective\b|^general( appearance)?\b|^gen\b|^heent\b|^head\b|^eyes\b|"
    r"^ears\b|^nose\b|^mouth\b|^throat\b|^neck\b|^chest\b|^lungs\b|^resp(iratory)?\b|^heart\b|^cv\b|"
    r"^cardiovascular\b|^abd(omen)?\b|^ext(remities)?\b|^skin\b|^neuro(logic(al)?)?\b|^psych\b|^pulses\b|"
    r"^(current |admission |patient |birth )?weight\b|^weight (loss|review)\b|^diet\b|^energy intake\b|"
    r"^(lip documentation of |type of )?malnutrition\b|^dietitian\b|^nutrition-focused\b|^fluid\b|"
    r"^i/o\b|^in\b|^out\b|^net\b|^peep\b|^map\b|^bp\b|^bp/abp\b|^hr\b|^spo2\b|^vent\b|"
    r"^problem list\b|^immunizations?\b|^code status\b|^goals of care\b|^fax\b|"
    r"^(informed\s+)?consent\b|^sedation\b|^anesthesia\b|^anaesthesia\b",
    re.I,
)

# Paragraphs of consent and sedation text that come without a header (step 1).
BOILERPLATE_PARAGRAPH_RE = re.compile(
    r"^(after\s+)?(informed\s+)?consent\b|^the\s+risks,?\s+benefits|^risks,?\s+benefits|"
    r"^(moderate|conscious|deep)\s+sedation\b|^sedation\b|^the patient was (sedated|placed)|"
    r"^(monitored anesthesia care|mac)\b",
    re.I,
)

# Sections kept in step 2, by header name.
RELEVANT_HEADER_RE = re.compile(
    r"procedure|finding|impression|specimen|patholog|biops|attest|attending|fellow|staff|"
    r"physician|endoscopist|performed by|surgeon|provider|assistant|operator|participant|"
    r"personnel|instrument|scope|equipment|technique|description|intervention|teaching|"
    r"operat|endoscop|\begd\b|colonoscop|post-?op",
    re.I,
)

# Step 3: lines longer than this are split into sentences. Lines are taken in this order of
# (terms, distance from a line with them) until the limit: procedure lines and their direct
# neighbours first, then staff and specimen lines, then the farther neighbours.
MAX_UNIT_CHARS = 400
STEP3_ORDER = (
    (PROCEDURE_TERMS_RE, 0), (PROCEDURE_TERMS_RE, 1), (STAFF_TERMS_RE, 0),
    (PROCEDURE_TERMS_RE, 2), (STAFF_TERMS_RE, 1), (STAFF_TERMS_RE, 2),
)
_SENTENCE_END_RE = re.compile(r"(?<=[.;!?])\s+")

GAP = "[...]"

STEP_BOILERPLATE = "boilerplate removed"
STEP_SECTIONS = "relevant sections kept"
STEP_KEYWORDS = "keyword passages kept"


class Shortened(NamedTuple):
    """A note shortened to fit the window: the text to send and how it was made."""
    text: str
    steps: tuple[str, ...]   # the steps that changed the note, in order
    original_chars: int

    def describe(self) -> str:
        """InputReduction: the steps and the lengths, no note text."""
        return f"{', '.join(self.steps)}; {self.original_chars:,} to {len(self.text):,} characters"


def shorten_note(note: str, context_length: int) -> Shortened | None:
    """
    The note shortened until it fits context_length (see the module docstring), or None when
    even the last step cannot make it fit (the note mentions no study term). Call it only for
    a note that does not fit as it is.
    """
    text = note
    steps = []
    for name, step in (
        (STEP_BOILERPLATE, remove_boilerplate),
        (STEP_SECTIONS, keep_relevant_sections),
        (STEP_KEYWORDS, keep_keyword_passages),
    ):
        shorter = step(text, context_length)
        if shorter is None or shorter == text:
            continue
        text = shorter
        steps.append(name)
        if note_fits_context(text, context_length):
            return Shortened(text, tuple(steps), len(note))
    return None


def _header(line: str) -> tuple[str, str] | None:
    """(name, text after the colon) when line is a section header, else None."""
    match = _HEADER_COLON_RE.match(line) or _HEADER_CAPS_RE.match(line)
    if not match:
        return None
    name = match.group("name").strip()
    if not name or len(name.split()) > _MAX_HEADER_WORDS:
        return None
    rest = match.groupdict().get("rest") or ""
    return name, rest.strip()


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    """The note as (header name, lines) sections; the text before the first header has name None."""
    sections = [(None, [])]
    for line in text.split("\n"):
        header = _header(line)
        if header:
            sections.append((header[0], [line]))
        else:
            sections[-1][1].append(line)
    if not sections[0][1]:
        sections.pop(0)
    return sections


# A run of spaces the export left where the note had a line break: four or more, or two or
# more before something shaped like a header. A break restored in the wrong place costs nothing:
# the quote check ignores line breaks, and a line is only a header if _header says so.
_BREAK_RUN_RE = re.compile(r"[ \t\xa0]{4,}|[ \t\xa0]{2,}(?=[A-Z][A-Za-z0-9 /&,()'\-]{0,48}?:)")


def _tidy(text: str) -> str:
    """
    Line breaks restored where the export left a run of spaces (see the module docstring),
    other runs of spaces and tabs made one space, lines stripped, at most one blank line in a row.
    """
    text = _BREAK_RUN_RE.sub("\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [re.sub(r"[ \t\f\v\xa0]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _join(blocks: list[tuple[int, str]]) -> str:
    """Blocks (position, text) in order, with a GAP line where positions are not consecutive."""
    parts = []
    previous = None
    for position, block in blocks:
        if previous is not None and position != previous + 1:
            parts.append(GAP)
        elif previous is None and position != 0:
            parts.append(GAP)
        parts.append(block)
        previous = position
    return "\n".join(parts)


def remove_boilerplate(text: str, context_length: int | None = None) -> str:
    """Step 1: the note without whitespace runs, repeated headers and boilerplate without a study term."""
    kept = []
    seen_headers = set()
    for name, lines in _sections(_tidy(text)):
        # The header itself does not protect its section ("History of Present Illness").
        body = "\n".join([_header(lines[0])[1], *lines[1:]]) if name else "\n".join(lines)
        if name and BOILERPLATE_HEADER_RE.search(name) and not KEY_TERMS_RE.search(body):
            continue
        if name:
            key = name.lower()
            # A header repeated with nothing after it (a template printed twice) is dropped.
            if key in seen_headers and not _header(lines[0])[1]:
                lines = lines[1:]
            seen_headers.add(key)
        # Consent and sedation text without a header of its own is dropped line by line.
        lines = [
            line for line in lines
            if not (BOILERPLATE_PARAGRAPH_RE.search(line) and not KEY_TERMS_RE.search(line))
        ]
        block = "\n".join(lines).strip()
        if block:
            kept.append(block)
    return "\n".join(kept)


def keep_relevant_sections(text: str, context_length: int | None = None) -> str | None:
    """Step 2: the text before the first header and the relevant sections, or None without headers to go by."""
    sections = _sections(text)
    if sum(name is not None for name, _ in sections) < 2:
        return None
    kept = []
    relevant = 0
    for position, (name, lines) in enumerate(sections):
        block = "\n".join(lines).strip()
        if name is None:
            if block:
                kept.append((position, block))
        elif RELEVANT_HEADER_RE.search(name) or KEY_TERMS_RE.search(block):
            kept.append((position, block))
            relevant += 1
    if not relevant:
        return None
    return _join(kept)


def _units(text: str) -> list[str]:
    """The non-empty lines of text (GAP lines left out), each long line split into sentences."""
    units = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line == GAP:
            continue
        if len(line) > MAX_UNIT_CHARS:
            units.extend(s for s in _SENTENCE_END_RE.split(line) if s)
        else:
            units.append(line)
    return units


def keep_keyword_passages(text: str, context_length: int) -> str | None:
    """
    Step 3: the lines with a study term and their neighbours, taken in STEP3_ORDER and
    sent in the note's order, within the limit for context_length; None when no line has a study term.
    """
    units = _units(text)
    if not any(KEY_TERMS_RE.search(unit) for unit in units):
        return None
    limit = max_note_chars(context_length)
    chosen: set[int] = set()

    def render(indices) -> str:
        return _join([(i, units[i]) for i in sorted(indices)])

    for terms, distance in STEP3_ORDER:
        for i in (i for i, unit in enumerate(units) if terms.search(unit)):
            for j in sorted({i - distance, i + distance}):
                if 0 <= j < len(units) and j not in chosen:
                    if len(render(chosen | {j})) <= limit:
                        chosen.add(j)
    if not chosen:
        return None
    return render(chosen)

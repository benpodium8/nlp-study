import csv
import hashlib
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple


NOTES_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mrn TEXT,
    encounter TEXT,
    note_csn_id TEXT,
    note_date TEXT,
    note_type TEXT,
    note TEXT
);
"""

# The fields the LLM extracts, with their SQLite types, in storage order (export.py lays the
# export out in its own reading order, under plain-words titles).
# llm_analysis.EXPECTED_FIELDS validates the same set (plus the note id).
RESULT_FIELDS = (
    ("ScopeType", "TEXT"),
    ("Colonoscopy", "INTEGER"),
    ("ColonoscopyInformation", "TEXT"),
    ("Endoscopy", "INTEGER"),
    ("EndoscopyInformation", "TEXT"),
    ("NumberOfDuodenalBiopsies", "INTEGER"),
    ("DuodenalBiopsiesTaken", "INTEGER"),
    ("DuodenalBiopsiesInformation", "TEXT"),
    ("FellowPresent", "INTEGER"),
    ("FellowInformation", "TEXT"),
)

# Whether a human should look at the row before the study uses it, and why (review.py).
REVIEW_FIELDS = (
    ("ManualReviewRequired", "TEXT"),  # "true" or "false"
    ("ManualReviewReason", "TEXT"),    # every trigger, separated by "; ", when "true"
)

# Debugging columns data_worker records for every note next to the extracted fields.
DEBUG_FIELDS = (
    ("LLM_Failed", "INTEGER"),      # 1 when no attempt gave an answer the note supports (or the call failed); the extracted fields are then null
    ("LLM_Attempts", "INTEGER"),    # model calls made for this note
    ("LLM_Seconds", "REAL"),        # wall-clock seconds spent on this note, all attempts included
    ("LLM_Error", "TEXT"),          # why the note failed (the last attempt's rejection) when LLM_Failed is 1
    ("LLM_Rejections", "TEXT"),     # why each rejected attempt was rejected, one per line, for accepted and failed notes alike
    ("RawResponse", "TEXT"),        # verbatim text of the accepted model response; of the last rejected one when the note failed
)

# The columns one model produces for a note. The results table holds one copy of this block
# per model, and so does the export.
RESULTS_COLUMNS = RESULT_FIELDS + REVIEW_FIELDS + DEBUG_FIELDS

# The consensus block (consensus.py) consolidates the models' answers for a note. It is
# stored and exported under this label, "ScopeType (consensus)" and so on, with the ten
# fields, the review columns, and six columns that say what the consensus found and how it
# was reached (export.py gives every column its export title).
CONSENSUS_LABEL = "consensus"
CONSENSUS_FIELDS = (
    ("Summary", "TEXT"),      # what the consensus found, in one plain-words line ("Upper endoscopy with Olympus H190; 4 duodenal biopsies; fellow present"), or why there is none
    ("Outcome", "TEXT"),      # unanimous, agreed, majority, partial or none (consensus.OUTCOMES)
    ("Answered", "INTEGER"),  # how many of the models consulted gave an answer the note supports
    ("Models", "TEXT"),       # the model labels consulted, "; "-separated, each marked (failed) or (not analysed) when it gave no answer
    ("Disputed", "TEXT"),     # the voted fields the answering models did not all agree on, each marked "(majority answer given)" or "(no majority, left empty)"; null when they agreed on every field
    ("Agreement", "TEXT"),    # how many models answered and how they voted on each field they did not agree on
)
CONSENSUS_COLUMNS = RESULT_FIELDS + REVIEW_FIELDS + CONSENSUS_FIELDS

# Every model's block is labelled with the model's name and context window, as
# llm_analysis.ModelSpec.label gives it ("medgemma:27b@8192"): the column that holds a field
# for that model is named "<field> (<label>)", e.g. "ScopeType (medgemma:27b@8192)". Two
# models, or one model called with two window sizes, therefore never share a column. The
# names contain ':', '@' and spaces, so SQL must double-quote them (quoted()).
COLUMN_NAME_RE = re.compile(r"^(?P<field>\w+) \((?P<label>.+)\)$")


def column_name(field: str, label: str) -> str:
    """The results column that holds field for the model with the given label."""
    return f"{field} ({label})"


def column_label(column: str) -> str | None:
    """The label a results column name carries, or None for an unlabelled column."""
    match = COLUMN_NAME_RE.match(column)
    return match.group("label") if match else None


def quoted(identifier: str) -> str:
    """identifier as a double-quoted SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _col(field: str, label: str) -> str:
    """The quoted results column for field under label, ready to paste into SQL."""
    return quoted(column_name(field, label))


# The results table starts with the note id only; create_db adds a block of RESULTS_COLUMNS
# for each model label, so the table grows by one block whenever a model is added.
RESULTS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    FOREIGN KEY (id) REFERENCES notes(id)
);
"""

INSERT_NOTE_SQL = """
INSERT INTO notes (
    mrn,
    encounter,
    note_csn_id,
    note_date,
    note_type,
    note
) VALUES (?, ?, ?, ?, ?, ?);
"""

CHECK_DUPLICATE_NOTE_SQL = """
SELECT COUNT(*) FROM notes
WHERE mrn = ? AND encounter = ? AND note_csn_id = ?
  AND note_date = ? AND note_type = ? AND note = ?;
"""

# The csv module refuses a field longer than its default limit (128 KiB). A note can be
# longer than that, so the limit is raised as far as the platform allows.
_field_limit = sys.maxsize
while True:
    try:
        csv.field_size_limit(_field_limit)
        break
    except OverflowError:
        _field_limit //= 10

# The columns a notes CSV must carry. A header name is matched without regard to case,
# spaces, underscores, dashes or a byte-order mark, and the other spellings each column
# goes by in exports are accepted too (CSV_COLUMN_ALIASES, tried in order after the name
# itself); extra columns are ignored. So "EncounterID", "encounter_id" and "Encounter" all
# serve as Encounter, and a file need not be edited before it is ingested.
REQUIRED_CSV_COLUMNS = ("MRN", "Encounter", "NoteCsnID", "NoteDate", "NoteType", "Note")
CSV_COLUMN_ALIASES = {
    "MRN": ("patientmrn", "medicalrecordnumber"),
    "Encounter": ("encounterid", "encountercsn", "encountercsnid", "encid"),
    "NoteCsnID": ("notecsn", "csnid", "csn"),
    "NoteDate": ("dateofservice", "servicedate", "date"),
    "NoteType": ("type",),
    "Note": ("notetext", "text"),
}


def _normalize_header(name: str | None) -> str:
    """A header name with its case, spaces, underscores, dashes and any byte-order mark dropped."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_csv_columns(header) -> tuple[dict, list]:
    """
    Which column of a CSV header serves each of REQUIRED_CSV_COLUMNS: the name itself in any
    spelling first, then its CSV_COLUMN_ALIASES in order; a header column serves one required
    column at most. Returns ({required: header column}, [required columns not found]).
    """
    normalized = {}
    for name in header or []:
        normalized.setdefault(_normalize_header(name), name)  # the first column wins a repeated spelling
    mapping, missing, taken = {}, [], set()
    for required in REQUIRED_CSV_COLUMNS:
        for candidate in (_normalize_header(required), *CSV_COLUMN_ALIASES[required]):
            found = normalized.get(candidate)
            if found is not None and found not in taken:
                mapping[required] = found
                taken.add(found)
                break
        else:
            missing.append(required)
    return mapping, missing

COUNT_NOTES_SQL = "SELECT COUNT(*) FROM notes;"


def _table_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """(name, declared type) of every column of table, in table order."""
    return [(row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table});")]


def results_column_names(conn: sqlite3.Connection) -> set[str]:
    """The names of every column the results table has."""
    return {name for name, _ in _table_columns(conn, "results")}


def results_labels(conn: sqlite3.Connection) -> list[str]:
    """
    The model labels that have columns in the results table, in the order they were added.
    The consensus block is not a model's and is left out.
    """
    labels = []
    for name, _ in _table_columns(conn, "results"):
        label = column_label(name)
        if label is not None and label != CONSENSUS_LABEL and label not in labels:
            labels.append(label)
    return labels


def create_db(db_path: Path, labels=(), legacy_label: str | None = None):
    """
    Creates or connects to a SQLite database and initialises the notes and results tables,
    with a block of result columns for each model label in labels (llm_analysis.ModelSpec.label).

    A results table written before the columns carried labels is converted: its columns
    become the block of legacy_label, the first model of the default list (those answers
    came from that model, whatever model this run uses), so an existing data.db keeps its
    results. Any column a label's block lacks is added, and the review columns are filled
    for results stored before they existed. The consensus block is added the same way.

    Parameters:
        db_path: Path to the database file.
        labels: The labels of the models this run uses, first model first.
        legacy_label: The label unlabelled columns from an earlier version belong to;
            defaults to the first of labels.

    Returns:
        sqlite3.Connection: Database connection object.
    """
    legacy_label = legacy_label or (labels[0] if labels else None)
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(NOTES_TABLE_SCHEMA)
        # The ingest checks every row of a file against the stored notes; without the index
        # each check scans the whole table, and a large corpus takes hours to load.
        conn.execute("CREATE INDEX IF NOT EXISTS notes_identity ON notes (mrn, encounter, note_csn_id);")
        conn.execute(RESULTS_TABLE_SCHEMA)
        # A converted legacy block gets its review columns filled even when this run does not
        # use that model (the loop below only covers labels); a fresh database has no such block.
        if legacy_label and _label_legacy_columns(conn, legacy_label) and legacy_label not in labels:
            _backfill_manual_review(conn, legacy_label)
        for label in labels:
            _add_missing_result_columns(conn, label)
            _backfill_manual_review(conn, label)
        _add_missing_result_columns(conn, CONSENSUS_LABEL, CONSENSUS_COLUMNS)
    return conn


def _label_legacy_columns(conn: sqlite3.Connection, label: str) -> bool:
    """
    Moves the results of a table whose columns carry no label (the bare field names
    ScopeType, LLM_Failed, ...) under label, since those answers came from the first model.
    Columns that version lacked are added empty; columns already labelled are kept.
    SQLite's RENAME COLUMN needs 3.25, which the Argon Python may not have, so the table is
    rebuilt by copying instead. Returns whether there was anything to convert.
    """
    existing = _table_columns(conn, "results")
    names = [name for name, _ in existing]
    legacy = [field for field, _ in RESULTS_COLUMNS if field in names]
    if not legacy:
        return False

    kept = [(name, sqltype) for name, sqltype in existing if name != "id" and name not in legacy]
    new_columns = [(column_name(field, label), sqltype) for field, sqltype in RESULTS_COLUMNS] + kept
    select = (
        ["id"]
        + [quoted(field) if field in legacy else "NULL" for field, _ in RESULTS_COLUMNS]
        + [quoted(name) for name, _ in kept]
    )

    conn.execute("DROP TABLE IF EXISTS results_relabel;")
    conn.execute(
        "CREATE TABLE results_relabel (id INTEGER PRIMARY KEY, "
        + ", ".join(f"{quoted(name)} {sqltype}" for name, sqltype in new_columns)
        + ", FOREIGN KEY (id) REFERENCES notes(id));"
    )
    conn.execute(
        f"INSERT INTO results_relabel (id, {', '.join(quoted(name) for name, _ in new_columns)}) "
        f"SELECT {', '.join(select)} FROM results;"
    )
    conn.execute("DROP TABLE results;")
    conn.execute("ALTER TABLE results_relabel RENAME TO results;")

    count = conn.execute("SELECT COUNT(*) FROM results;").fetchone()[0]
    print(
        f"Labelled the {len(legacy)} unlabelled result column(s) of {count} existing result(s) "
        f"as {label!r} (they came from that model)."
    )
    return True


def _add_missing_result_columns(conn: sqlite3.Connection, label: str, columns=RESULTS_COLUMNS):
    """
    Adds every column of label's block (columns: a model's RESULTS_COLUMNS, or the
    CONSENSUS_COLUMNS) that the results table lacks: the whole block for a model that has
    not been run on this database, or a column introduced since.
    Existing rows get NULL in any column added here.
    """
    existing = {name for name, _ in _table_columns(conn, "results")}
    added = []
    for field, sqltype in columns:
        name = column_name(field, label)
        if name not in existing:
            conn.execute(f"ALTER TABLE results ADD COLUMN {quoted(name)} {sqltype};")
            added.append(name)
    if len(added) == len(columns):
        print(f"Added the result columns for {label!r} to the results table.")
    elif added:
        print(f"Added missing column(s) {', '.join(added)} to the results table.")


def _backfill_manual_review(conn: sqlite3.Connection, label: str):
    """
    Fills the review columns of every analysed result under label that lacks them (results
    stored before those columns existed), so an older database exports "true"/"false" too.
    """
    from review import manual_review  # imported here: review depends on llm_analysis only

    existing = {name for name, _ in _table_columns(conn, "results")}
    if any(column_name(field, label) not in existing for field, _ in RESULTS_COLUMNS):
        return   # no block for this label (nothing stored under it yet)
    rows = conn.execute(
        f"""
        SELECT results.id, notes.note, {", ".join(f"results.{_col(f, label)}" for f, _ in RESULT_FIELDS)},
               results.{_col("LLM_Failed", label)}, results.{_col("LLM_Attempts", label)},
               results.{_col("LLM_Error", label)}, results.{_col("LLM_Rejections", label)}
        FROM results JOIN notes ON notes.id = results.id
        WHERE results.{_col("LLM_Failed", label)} IS NOT NULL
          AND results.{_col("ManualReviewRequired", label)} IS NULL;
        """
    ).fetchall()
    context_length = label_context_length(label)
    for row in rows:
        note_id, note = row[0], row[1]
        extracted = dict(zip((name for name, _ in RESULT_FIELDS), row[2:2 + len(RESULT_FIELDS)]))
        llm_failed, attempts, error, rejections = row[2 + len(RESULT_FIELDS):]
        kwargs = {"context_length": context_length} if context_length else {}
        required, reason = manual_review(
            note, None if llm_failed else extracted, llm_failed or 0, attempts or 1, error, rejections, **kwargs
        )
        conn.execute(
            f"UPDATE results SET {_col('ManualReviewRequired', label)} = ?, "
            f"{_col('ManualReviewReason', label)} = ? WHERE id = ?;",
            ("true" if required else "false", reason, note_id),
        )
    if rows:
        print(f"Filled ManualReviewRequired for {len(rows)} existing result(s) of {label!r}.")


def label_context_length(label: str) -> int | None:
    """The context window a label records ("medgemma:27b@8192" gives 8192), or None."""
    _, _, ctx = label.rpartition("@")
    return int(ctx) if ctx.isdigit() else None


# How often ingest_csv reports its progress (rows checked against the database).
INGEST_PROGRESS_EVERY = 100


class IngestSummary(NamedTuple):
    """What ingest_csv read and added."""
    rows: int                    # rows in the file
    new: int                     # notes added
    duplicates: int              # rows identical to a stored note, skipped
    note_types: Counter          # NoteType -> rows in the file
    new_note_lengths: list       # characters of each note added
    seconds: float


# Rows are written in transactions of this many: an ingest holds one batch in memory, and an
# ingest that is interrupted keeps the batches it stored (the next run skips them as duplicates).
INGEST_BATCH = 1000


def _csv_lines(csv_path: Path, counts: dict):
    """
    The lines of csv_path for the csv module, read as they are needed, never the whole file
    at once: UTF-8 with a byte-order mark skipped, a byte that is not UTF-8 replaced with
    U+FFFD and a NUL byte dropped, each counted in counts ("undecodable", "nuls").
    """
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            if "\ufffd" in line:
                counts["undecodable"] += line.count("\ufffd")
            if "\x00" in line:
                counts["nuls"] += line.count("\x00")
                line = line.replace("\x00", "")
            yield line


def _count_csv_rows(csv_path: Path) -> int:
    """The data rows in csv_path (a quoted note that spans lines is one row): a first pass, so progress can say how many are left."""
    reader = csv.reader(_csv_lines(csv_path, {"undecodable": 0, "nuls": 0}))
    next(reader, None)
    return sum(1 for _ in reader)


def ingest_csv(csv_path: Path, conn: sqlite3.Connection, report=print, progress=None) -> IngestSummary:
    """
    Ingests CSV data into the database, skipping every row identical to a stored note or to
    an earlier row of the same file. The file is read twice, row by row, never whole: once
    to count the rows, then to store them in batches of INGEST_BATCH, so a corpus of any
    size takes the memory of one batch. It is read as UTF-8, skipping a byte-order mark; a
    byte that is not valid UTF-8 is replaced with U+FFFD and a NUL byte is dropped, each
    counted and reported, so a file from another system is read rather than refused (a quote
    that spans a replaced character may then not match the note, which the verifier reports
    on that note). The header is matched by match_csv_columns; a row with fewer cells than
    the header is stored with the missing cells empty. Raises ValueError, naming the header
    found, when a required column has no match. Calls report with a line for the header
    (when any name was taken for another or a byte-order mark skipped), a warning per
    problem found in the file, and one summary line at the end; and, when given,
    progress(checked, total, new, duplicates) every INGEST_PROGRESS_EVERY rows.

    Parameters:
        csv_path: Path to the CSV file to ingest.
        conn: Database connection object.
    """
    started = time.perf_counter()
    total = _count_csv_rows(csv_path)
    counts = {"undecodable": 0, "nuls": 0}
    reader = csv.DictReader(_csv_lines(csv_path, counts))
    found = reader.fieldnames or []
    mapping, missing = match_csv_columns(found)
    if missing:
        raise ValueError(
            f"CSV is missing the column(s) {', '.join(missing)}. "
            f"Required: {', '.join(REQUIRED_CSV_COLUMNS)} (any case; spaces, underscores and dashes are "
            f"ignored; EncounterID, note_csn_id and the like are accepted). "
            f"Header found: {', '.join(found) or '(empty)'}"
        )
    columns = [mapping[name] for name in REQUIRED_CSV_COLUMNS]

    checked = new = duplicates = short = empty = 0
    note_types = Counter()
    new_lengths = []
    seen = set()    # a digest of every row stored from this file: a row the file repeats is a duplicate too
    batch = []
    if progress is not None:
        progress(0, total, 0, 0)
    for row in reader:
        checked += 1
        values = [row.get(column) for column in columns]
        if any(value is None for value in values):
            short += 1  # fewer cells than the header: DictReader gives None for the rest
        values = tuple("" if value is None else value for value in values)
        note_types[values[4]] += 1
        digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).digest()
        if digest in seen or conn.execute(CHECK_DUPLICATE_NOTE_SQL, values).fetchone()[0]:
            duplicates += 1
        else:
            seen.add(digest)
            batch.append(values)
            new += 1
            new_lengths.append(len(values[5]))
            empty += not values[5].strip()
            if len(batch) >= INGEST_BATCH:
                with conn:
                    conn.executemany(INSERT_NOTE_SQL, batch)
                batch.clear()
        if progress is not None and (checked % INGEST_PROGRESS_EVERY == 0 or checked == total):
            progress(checked, total, new, duplicates)
    if batch:
        with conn:
            conn.executemany(INSERT_NOTE_SQL, batch)

    aliased = [f"{name!r} read as {required}" for required, name in mapping.items() if name != required]
    bom = csv_path.open("rb").read(3) == b"\xef\xbb\xbf"
    if bom or aliased:
        parts = (["byte-order mark skipped"] if bom else []) + aliased
        report(f"Header of {csv_path}: {'; '.join(parts)}.")
    if counts["undecodable"]:
        report(
            f"Warning: {counts['undecodable']} byte(s) in {csv_path} were not valid UTF-8 and were replaced with U+FFFD; "
            f"a quote that spans such a character will not match its note."
        )
    if counts["nuls"]:
        report(f"Warning: {counts['nuls']} NUL byte(s) in {csv_path} were dropped.")
    if short:
        report(f"Warning: {short} row(s) in {csv_path} had fewer cells than the header; the missing cells are stored empty.")
    if empty:
        report(f"Note: {empty} new note(s) are empty; each will be analysed and come out as no procedure documented.")

    seconds = time.perf_counter() - started
    report(
        f"Read {checked} row(s) from {csv_path} in {seconds:.1f}s: "
        f"{new} new note(s) added, {duplicates} duplicate(s) skipped."
    )
    return IngestSummary(checked, new, duplicates, note_types, new_lengths, seconds)


def pending_notes(conn: sqlite3.Connection, label: str) -> list[tuple[int, int]]:
    """
    (id, length of the note in characters) of every note that has no result under label
    yet, in id order. The text is not loaded here: a pass over a large corpus fetches each
    note when its turn comes (note_text), so the list costs a few bytes per note.
    """
    return conn.execute(
        f"""
        SELECT notes.id, LENGTH(notes.note)
        FROM notes
        LEFT JOIN results ON notes.id = results.id
        WHERE results.id IS NULL OR results.{_col("LLM_Failed", label)} IS NULL
        ORDER BY notes.id;
        """
    ).fetchall()


def note_text(conn: sqlite3.Connection, note_id: int) -> str:
    """The text of one note ("" for a note stored without text)."""
    row = conn.execute("SELECT note FROM notes WHERE id = ?;", (note_id,)).fetchone()
    return row[0] if row and row[0] is not None else ""


class ModelStatistics(NamedTuple):
    """What the results stored under one model's label add up to (model_statistics)."""
    analysed: int           # notes with a result
    failed: int             # of which LLM_Failed
    flagged: int            # of which ManualReviewRequired
    sent: int               # of which were sent to the model (LLM_Attempts > 0)
    retries: int            # rejected attempts over all notes
    seconds: list           # LLM_Seconds of each note sent, in id order
    fields: Counter         # egd, colonoscopy, biopsies, counted, fellow, scope: notes answering 1 (or naming a scope)

    @property
    def ok(self) -> int:
        return self.analysed - self.failed

    @property
    def mean_seconds(self) -> float | None:
        return sum(self.seconds) / len(self.seconds) if self.seconds else None

    @property
    def median_seconds(self) -> float | None:
        return sorted(self.seconds)[len(self.seconds) // 2] if self.seconds else None


def model_statistics(conn: sqlite3.Connection, label: str) -> ModelStatistics | None:
    """The ModelStatistics of label's stored results, or None when the table has no block for it."""
    existing = {name for name, _ in _table_columns(conn, "results")}
    if column_name("LLM_Failed", label) not in existing:
        return None
    columns = (
        "LLM_Failed", "LLM_Attempts", "LLM_Seconds", "LLM_Rejections", "ManualReviewRequired", "Endoscopy",
        "Colonoscopy", "DuodenalBiopsiesTaken", "NumberOfDuodenalBiopsies", "FellowPresent", "ScopeType",
    )
    select = ", ".join(_col(field, label) if column_name(field, label) in existing else "NULL" for field in columns)
    rows = conn.execute(f"SELECT {select} FROM results WHERE {_col('LLM_Failed', label)} IS NOT NULL ORDER BY id;").fetchall()
    stats = ModelStatistics(len(rows), 0, 0, 0, 0, [], Counter())
    failed = flagged = sent = retries = 0
    for llm_failed, attempts, seconds, rejections, review, egd, colonoscopy, taken, count, fellow, scope in rows:
        failed += llm_failed == 1
        flagged += review == "true"
        if attempts:
            sent += 1
            retries += len(rejections.split("\n")) if rejections else 0
            if seconds is not None:
                stats.seconds.append(seconds)
        if not llm_failed:
            stats.fields["egd"] += egd == 1
            stats.fields["colonoscopy"] += colonoscopy == 1
            stats.fields["biopsies"] += taken == 1
            stats.fields["counted"] += taken == 1 and count is not None
            stats.fields["fellow"] += fellow == 1
            stats.fields["scope"] += scope is not None
    return stats._replace(failed=failed, flagged=flagged, sent=sent, retries=retries)


def database_status(db_path: Path, labels=(), legacy_label: str | None = None) -> list[str]:
    """
    Lines about db_path for the no-argument help: how many notes it holds and, for each
    model label (the ones given, then any other found in the table), how many are analysed,
    failed, flagged for review and pending; or why it cannot be read. legacy_label is the
    label unlabelled columns from an earlier version will get (see create_db).
    Opens the file read-only and never creates it.
    """
    legacy_label = legacy_label or (labels[0] if labels else None)
    if not db_path.exists():
        return ["not found here; it is created on the first run"]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            notes = conn.execute(COUNT_NOTES_SQL).fetchone()[0]
            lines = [f"{notes} note(s)"]
            tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")}
            names = {name for name, _ in _table_columns(conn, "results")} if "results" in tables else set()
            found = results_labels(conn) if "results" in tables else []
            for label in list(labels) + [label for label in found if label not in labels]:
                if column_name("LLM_Failed", label) not in names:
                    lines.append(f"{label}: 0 analysed, {notes} pending")
                    continue
                done, failed = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM({_col('LLM_Failed', label)}), 0) FROM results "
                    f"WHERE {_col('LLM_Failed', label)} IS NOT NULL;"
                ).fetchone()
                if column_name("ManualReviewRequired", label) in names:
                    review = conn.execute(
                        f"SELECT COUNT(*) FROM results WHERE {_col('ManualReviewRequired', label)} = 'true';"
                    ).fetchone()[0]
                else:
                    review = "not yet computed"
                lines.append(
                    f"{label}: {done} analysed ({failed} failed, {review} flagged for manual review), "
                    f"{notes - done} pending"
                )
            if "LLM_Failed" in names:
                done = conn.execute("SELECT COUNT(*) FROM results WHERE LLM_Failed IS NOT NULL;").fetchone()[0]
                where = f"; the next run labels them {legacy_label!r}" if legacy_label else ""
                lines.append(f"{done} result(s) in unlabelled columns from an earlier version{where}")
            if column_name("ManualReviewRequired", CONSENSUS_LABEL) in names:
                built, review = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM({_col('ManualReviewRequired', CONSENSUS_LABEL)} = 'true'), 0) "
                    f"FROM results WHERE {_col('ManualReviewRequired', CONSENSUS_LABEL)} IS NOT NULL;"
                ).fetchone()
                lines.append(
                    f"{CONSENSUS_LABEL}: {built} consolidated ({review} flagged for manual review), "
                    f"{notes - built} not consolidated yet (--analyze rebuilds the consensus)"
                )
            else:
                lines.append(f"{CONSENSUS_LABEL}: not built yet (--analyze builds it after the models run)")
        finally:
            conn.close()
    except sqlite3.Error as e:
        return [f"exists but could not be read ({e})"]
    return lines


def insert_result(
    conn: sqlite3.Connection,
    id: int,
    label: str,
    result: dict | None,
    raw_response: str | None,
    llm_failed: int,
    attempts: int,
    seconds: float,
    error: str | None,
    rejections: str | None = None,
    review_required: bool = False,
    review_reason: str | None = None,
):
    """
    Stores one note's analysis by one model, with its review and debugging columns, in the
    results table under the model's label (creating the note's row if it has none).

    Parameters:
        conn: Database connection object.
        id: Note ID to associate with the result.
        label: The model's label (llm_analysis.ModelSpec.label); its block of columns is written.
        result: Dictionary of extracted fields, or None when the LLM failed.
        raw_response: Verbatim text of the accepted model response; of the last rejected
            one when the LLM failed; None when there was no response.
        llm_failed: 1 if the LLM analysis failed, 0 if it succeeded.
        attempts: Number of model calls made for this note.
        seconds: Wall-clock seconds spent on this note.
        error: The final error message when the analysis failed, else None.
        rejections: Why each rejected attempt was rejected, one per line, else None.
        review_required: Whether a human should look at this row (review.manual_review).
        review_reason: Why, when review_required; else None.
    """
    extracted = tuple(result.get(name) if result else None for name, _ in RESULT_FIELDS)
    review = ("true" if review_required else "false", review_reason)
    debug = {
        "LLM_Failed": llm_failed,
        "LLM_Attempts": attempts,
        "LLM_Seconds": seconds,
        "LLM_Error": error,
        "LLM_Rejections": rejections,
        "RawResponse": raw_response,
    }
    values = (*extracted, *review, *(debug[name] for name, _ in DEBUG_FIELDS))

    with conn:
        _write_block(conn, id, label, RESULTS_COLUMNS, values)


def _write_block(conn: sqlite3.Connection, id: int, label: str, columns, values):
    """
    Writes one block of columns (a model's RESULTS_COLUMNS or the CONSENSUS_COLUMNS, values
    in the same order) for note id, creating the note's results row if it has none. Commits
    nothing itself: the caller holds the transaction.
    """
    assignments = ", ".join(f"{_col(field, label)} = ?" for field, _ in columns)
    conn.execute("INSERT OR IGNORE INTO results (id) VALUES (?);", (id,))
    conn.execute(f"UPDATE results SET {assignments} WHERE id = ?;", (*values, id))


# How many notes model_answers reads from the database at a time.
ANSWERS_PAGE = 500


def model_answers(conn: sqlite3.Connection, labels):
    """
    Every note's answers from the models with the given labels, for the consensus, yielded
    one note at a time in id order as (note id, note_type, note text, [(label, LLM_Failed,
    LLM_Error, fields), ...]), with the models in label order and fields the dict of
    RESULT_FIELDS values. LLM_Failed is None (and the fields null) for a model that has not
    analysed the note, or whose block the table lacks. The note's type and text are there for
    the consensus to say what suggests a note without a consensus documents a procedure, and
    to check the agreed FellowPresent against the note's attestation (consensus.consensus).
    The notes are read in pages of ANSWERS_PAGE, each page fetched whole before any of it is
    yielded, so a corpus of any size takes the memory of one page and the caller may write
    to the results table between notes (no cursor is open while it does).
    """
    existing = {name for name, _ in _table_columns(conn, "results")}
    wanted = [(label, field) for label in labels for field in ("LLM_Failed", "LLM_Error", *(f for f, _ in RESULT_FIELDS))]
    select = ", ".join(
        f"results.{_col(field, label)}" if column_name(field, label) in existing else "NULL"
        for label, field in wanted
    )
    width = 2 + len(RESULT_FIELDS)
    last_id = 0
    while True:
        page = conn.execute(
            f"SELECT notes.id, notes.note_type, notes.note, {select} "
            f"FROM notes LEFT JOIN results ON notes.id = results.id "
            f"WHERE notes.id > ? ORDER BY notes.id LIMIT ?;",
            (last_id, ANSWERS_PAGE),
        ).fetchall()
        if not page:
            return
        for row in page:
            per_model = []
            for index, label in enumerate(labels):
                llm_failed, error, *values = row[3 + index * width : 3 + (index + 1) * width]
                fields = dict(zip((f for f, _ in RESULT_FIELDS), values))
                per_model.append((label, llm_failed, error, fields))
            yield (row[0], row[1], row[2], per_model)
        last_id = page[-1][0]


def store_consensus(conn: sqlite3.Connection, rows: dict[int, dict]):
    """
    Stores the consensus block for every note in rows ({note id: values}, values keyed by
    the CONSENSUS_COLUMNS field names, as consensus.consensus returns them), in one
    transaction, replacing whatever consensus the notes had.
    """
    with conn:
        for id, values in rows.items():
            _write_block(conn, id, CONSENSUS_LABEL, CONSENSUS_COLUMNS, tuple(values[f] for f, _ in CONSENSUS_COLUMNS))


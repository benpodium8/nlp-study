"""
Writes output/results.csv: the human-readable export of everything the database holds for
every note, one row per note, laid out for a researcher reading it in Google Sheets or Excel.

The database keeps the data under machine names ("Endoscopy (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)", 0/1 flags,
"true"/"false" review flags; database.py). The export presents the same data: every column
has a title in plain words with its source in parentheses, "(consensus)" or the model's
label; every yes/no value is written TRUE or FALSE, which both spreadsheets read as a
boolean, so the rows to review can be highlighted with one rule; the consensus block comes
first, its review flag in a fixed position (column G); each model's block follows, in the
same field order as the consensus; the bulky audit text (rejected attempts, raw model
responses) is kept to the far right, before the note itself. results-csv-guide.md explains
the file to its readers: keep the two in step when a column changes.

Nothing here changes a value: a cell is empty exactly where the database holds NULL, and the
one presentation change, a leading space before text that a spreadsheet would read as a
formula (cell), leaves the database text as it was.

A second file, results-review.csv, is written with results.csv from the same pass: the same
rows with only the identifiers, the consensus block and the note length (columns A to X as in
results.csv, then the length). It is about 4% of the size, so the full study corpus still
opens in Google Sheets; the researcher reads the review queue and the study values there and
opens results.csv for the evidence, the per-model blocks and the note text. The two files are
write-once together.
"""

import csv
import os
import time
from pathlib import Path
from typing import NamedTuple

from database import (
    CONSENSUS_COLUMNS, CONSENSUS_LABEL, RESULTS_COLUMNS, column_name, quoted, results_column_names,
    results_labels,
)
from llm_analysis import BOOL_FIELDS

EXPORT_NAME = "results.csv"
REVIEW_EXPORT_NAME = "results-review.csv"
NOTE_LENGTH_TITLE = "Note length (characters)"
PARTIAL_SUFFIX = ".partial"   # the name an export has while it is being written

# The note identifiers that open every row (columns A to F), titled as the ingest CSV names
# them (database.REQUIRED_CSV_COLUMNS), after the database id, which the run log and
# data.db use for the note.
ID_COLUMNS = (
    ("Note id", "id"),
    ("MRN", "mrn"),
    ("Encounter", "encounter"),
    ("NoteCsnID", "note_csn_id"),
    ("NoteDate", "note_date"),
    ("NoteType", "note_type"),
)

# The ten extracted fields in reading order (what was done, with what, what was taken, who
# took part), each with its title. database.RESULT_FIELDS stores them in the prompt's order;
# the field names themselves are what the review reasons and the vote text refer to.
FIELD_TITLES = {
    "Endoscopy": "Upper endoscopy performed",
    "ScopeType": "Scope type",
    "EndoscopyInformation": "Upper endoscopy evidence quote",
    "DuodenalBiopsiesTaken": "Duodenal biopsies taken",
    "NumberOfDuodenalBiopsies": "Number of duodenal biopsies",
    "DuodenalBiopsiesInformation": "Duodenal biopsies evidence quote",
    "Colonoscopy": "Colonoscopy performed",
    "ColonoscopyInformation": "Colonoscopy evidence quote",
    "FellowPresent": "Fellow present",
    "FellowInformation": "Fellow evidence quote",
}

# The consensus block, in column order (columns G to X): the review flag and its reasons
# first, the short status columns, the one-line summary, the ten fields, then the audit
# text that says how the consensus was reached.
CONSENSUS_LAYOUT = (
    ("ManualReviewRequired", "Needs human review"),
    ("ManualReviewReason", "Review reasons"),
    ("Outcome", "Outcome"),
    ("Answered", "Models answered"),
    ("Summary", "Summary"),
    *FIELD_TITLES.items(),
    ("Models", "Models consulted"),
    ("Disputed", "Disputed fields"),
    ("Agreement", "How the models voted"),
)

# One model's block: whether it failed first (its fields are then empty), the ten fields,
# its own review flag, and the short diagnostics. The long audit text comes later.
MODEL_LAYOUT = (
    ("LLM_Failed", "Failed"),
    *FIELD_TITLES.items(),
    ("ManualReviewRequired", "Needs review"),
    ("ManualReviewReason", "Review reasons"),
    ("InputReduced", "Input shortened"),
    ("InputReduction", "How the input was shortened"),
    ("LLM_Attempts", "Attempts"),
    ("LLM_Seconds", "Seconds"),
    ("LLM_Error", "Failure reason"),
)

# The audit text of every model, after all the model blocks: multi-line and long, so it does
# not sit between the columns a reader compares.
AUDIT_LAYOUT = (
    ("LLM_Rejections", "Rejected attempts"),
    ("RawResponse", "Raw model response"),
)

# Every stored column must have one title, so that a column added to the database cannot
# silently go missing from the export (or appear twice).
assert sorted(field for field, _ in CONSENSUS_LAYOUT) == sorted(field for field, _ in CONSENSUS_COLUMNS)
assert sorted(field for field, _ in MODEL_LAYOUT + AUDIT_LAYOUT) == sorted(field for field, _ in RESULTS_COLUMNS)

# The columns written TRUE or FALSE: the 0/1 flags, and the "true"/"false" review flags.
FLAG_FIELDS = BOOL_FIELDS | {"LLM_Failed"}
TEXT_FLAG_FIELDS = {"ManualReviewRequired", "InputReduced"}

# The title of the column a reader filters or highlights on (the consensus review flag).
REVIEW_COLUMN = f"Needs human review ({CONSENSUS_LABEL})"


class Column(NamedTuple):
    """One export column: its header, the SQL expression that yields it, and the field it holds."""
    title: str
    select: str
    field: str | None   # a result field name, for the value format; None for a notes column


# A spreadsheet reads a cell that starts with one of these as a formula: a quote that begins
# "- 4 biopsies were taken" shows as an error in Excel, and "=" or "@" can run as one. Such
# text is written with one leading space, which both spreadsheets show as text; the database
# holds the text as it was.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def cell(field: str | None, value):
    """
    The value as the export writes it: TRUE/FALSE for a flag, empty for NULL, text that a
    spreadsheet would take for a formula with a leading space (FORMULA_STARTS), else as stored.
    """
    if value is None:
        return ""
    if field in FLAG_FIELDS:
        return "TRUE" if value == 1 else "FALSE"
    if field in TEXT_FLAG_FIELDS:
        return "TRUE" if value == "true" else "FALSE"
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return " " + value
    return value


def column_letter(index: int) -> str:
    """The spreadsheet letter(s) of the 0-based column index: 0 is A, 25 is Z, 26 is AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def export_columns(conn) -> list[Column]:
    """
    The export's columns in order: the identifiers; the consensus block (when the table has
    it); one block per model label, in the order the labels were added to the table; every
    model's audit text; the note's length and text. A result column the table lacks is
    skipped, so an older database still exports.
    """
    existing = results_column_names(conn)
    columns = [Column(title, f"notes.{name}", None) for title, name in ID_COLUMNS]

    def block(label: str, layout):
        for field, title in layout:
            name = column_name(field, label)
            if name in existing:
                columns.append(Column(f"{title} ({label})", f"results.{quoted(name)}", field))

    block(CONSENSUS_LABEL, CONSENSUS_LAYOUT)
    labels = results_labels(conn)
    for label in labels:
        block(label, MODEL_LAYOUT)
    for label in labels:
        block(label, AUDIT_LAYOUT)
    columns.append(Column(NOTE_LENGTH_TITLE, "LENGTH(notes.note)", None))
    columns.append(Column("Note", "notes.note", None))

    titles = [column.title for column in columns]
    assert len(set(titles)) == len(titles), "export column titles must be unique"
    return columns


def review_columns(columns: list[Column]) -> list[Column]:
    """
    The columns of the review-sized file (REVIEW_EXPORT_NAME): the identifiers, the consensus
    block and the note length, in the order results.csv has them, so columns A to X are the
    same in both files and the note length follows.
    """
    keep = {title for title, _ in ID_COLUMNS} | {NOTE_LENGTH_TITLE}
    return [column for column in columns if column.title in keep or column.title.endswith(f" ({CONSENSUS_LABEL})")]


def export_results(conn, output_folder="output", report=print) -> Path | None:
    """
    Writes every note with its consensus block and each model's results to
    output_folder/results.csv, and the review-sized copy (review_columns) to
    output_folder/results-review.csv, from one pass over the database, creating the folder if
    needed. The pair is written only if neither file exists (write-once); otherwise both are
    left alone and a message names the file(s) in the way. Reports what was written, where the
    review flag sits and how many rows it marks (and how many rows have no consensus block at
    all, should there be any). Returns the path of results.csv, or None when nothing was written.
    """
    started = time.perf_counter()
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    output_file = output_path / EXPORT_NAME
    review_file = output_path / REVIEW_EXPORT_NAME
    existing = [path for path in (output_file, review_file) if path.exists()]
    if existing:
        report(
            f"File(s) {', '.join(repr(str(path)) for path in existing)} already exist. Export skipped; "
            f"move {EXPORT_NAME} and {REVIEW_EXPORT_NAME} aside and run --analyze again to regenerate "
            f"both from the database."
        )
        return None

    columns = export_columns(conn)
    cursor = conn.execute(
        f"SELECT {', '.join(column.select for column in columns)} "
        "FROM notes LEFT JOIN results ON notes.id = results.id ORDER BY notes.id;"
    )
    titles = [column.title for column in columns]
    review = titles.index(REVIEW_COLUMN) if REVIEW_COLUMN in titles else None
    lean = review_columns(columns)
    lean_indexes = [titles.index(column.title) for column in lean]

    # Both files are written under temporary names and renamed into place only when whole: an
    # export cut off by a stopped job leaves no truncated results.csv for write-once to keep.
    partial_file, partial_review = (path.with_name(path.name + PARTIAL_SUFFIX) for path in (output_file, review_file))
    row_count = flagged = unbuilt = 0
    with (
        partial_file.open(mode="w", newline="", encoding="utf-8") as full,
        partial_review.open(mode="w", newline="", encoding="utf-8") as small,
    ):
        writer = csv.writer(full)
        lean_writer = csv.writer(small)
        writer.writerow(titles)
        lean_writer.writerow([column.title for column in lean])
        for row in cursor:
            values = [cell(column.field, value) for column, value in zip(columns, row)]
            writer.writerow(values)
            lean_writer.writerow([values[index] for index in lean_indexes])
            row_count += 1
            if review is not None:
                if values[review] == "TRUE":
                    flagged += 1
                elif values[review] == "":
                    unbuilt += 1

        for f in (full, small):
            f.flush()
            os.fsync(f.fileno())
    os.replace(partial_review, review_file)
    os.replace(partial_file, output_file)

    size = output_file.stat().st_size
    lean_size = review_file.stat().st_size
    report(
        f"Wrote {row_count} row(s) and {len(titles)} columns to {output_file} "
        f"({size / 1e6:.1f} MB) in {time.perf_counter() - started:.1f}s."
    )
    report(
        f"Wrote the review-sized copy, {row_count} row(s) and {len(lean)} columns, to {review_file} "
        f"({lean_size / 1e6:.1f} MB): the identifiers, the consensus block and the note length, "
        f"columns A to {column_letter(len(lean) - 2)} as in {EXPORT_NAME}."
    )
    if review is not None:
        report(
            f'The review queue is the {flagged} row(s) with "{REVIEW_COLUMN}" TRUE '
            f"(column {column_letter(review)} of both files); results-csv-guide.md explains the columns."
        )
        if unbuilt:
            report(
                f'{unbuilt} row(s) have an empty "{REVIEW_COLUMN}": no consensus has been built for them, '
                f"so they are in neither half of the queue; run --analyze to build it."
            )
    else:
        report("No consensus block in the database yet; results-csv-guide.md explains the columns.")
    return output_file

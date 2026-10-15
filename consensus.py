"""
Consolidates the models' answers for each note into one consensus block, stored and exported
under the label "consensus" ("ScopeType (consensus)" and so on; database.CONSENSUS_COLUMNS).

The consensus is cautious, the way the study wants it: a wrong value is worse than an empty
one. A field gets a value only when at least two models gave an answer the note supports and
they agree on it. A strict majority (2 of 3) settles a disagreement, and the note is then
flagged for manual review; a field with no majority is left empty. The flag never changes a
value; it says where a human should look, and it is raised only where a human can improve
the row: the models disagree on a field; the agreed answer leaves a study field open (an
upper endoscopy without a scope name); the note's own attestation contradicts the agreed
FellowPresent; the agreed duodenal count is the sum of two counts in one sentence, a
reading the researcher has not yet confirmed; or a note that could hold a study value (a
procedure note type, upper endoscopy wording in the text, or a model's accepted answer that
a procedure was performed) got no consensus because fewer than two models answered. A model
that failed is an abstention, not a dissent: when the models that answered agree, the row is
not flagged, and the failure stays on record in Models, Agreement and Outcome. A note of
another type with no consensus, where no model found a procedure, is not flagged either,
since there is no study value to recover, and Agreement says so. Every row records which
models were consulted and how they voted (Models, Agreement, Outcome), so the consensus can
be audited against the per-model columns; Answered counts the models that answered, Disputed
names the fields they split on, and Summary says in one plain-words line what the consensus
found (export.py puts these columns under their export titles).

A model's own ManualReviewRequired flag (review.py) does not carry over: it describes how
that model reached its answer; the consensus is judged on whether the models agree.

The reasons are written for the reader of results.csv: where one points at another column
("see Failure reason", "see How the models voted") it uses that column's export title
(export.py), and it names the fields by their short names (FellowPresent), which
results-csv-guide.md maps to the column titles.
"""

import re
import time
from collections import Counter
from typing import NamedTuple, Sequence

from database import CONSENSUS_COLUMNS, RESULT_FIELDS, model_answers, store_consensus
from llm_analysis import EVIDENCE_FOR_FLAG, MODELS, ModelSpec, TOO_LONG_PREFIX, squash, summed_count_quote, supported_counts


class ModelAnswer(NamedTuple):
    """One model's stored result for a note, as database.model_answers returns it."""
    label: str              # the model's label (llm_analysis.ModelSpec.label)
    llm_failed: int | None  # 0 when it answered, 1 when it failed, None when it has not analysed the note
    error: str | None       # LLM_Error when it failed
    fields: dict            # the RESULT_FIELDS values; all None unless it answered

    @property
    def answered(self) -> bool:
        return self.llm_failed == 0


# The fields the models vote on, in the order the Agreement text lists them. The evidence
# fields are not voted on: each is the quote of a model that gave the agreed answer.
VOTED_FIELDS = (
    "Endoscopy", "Colonoscopy", "ScopeType", "DuodenalBiopsiesTaken", "NumberOfDuodenalBiopsies", "FellowPresent",
)

# What the Outcome column records.
OUTCOMES = {
    "unanimous": "every model answered and they agree on every field",
    "agreed": "every model that answered agrees on every field, but a model failed: the consensus rests on the others",
    "majority": "every field has a value, but a field was settled by a strict majority",
    "partial": "a field was left empty because the models could not agree on it",
    "none": "no consensus: fewer than two models answered, or a model has not run",
}

# A scope name shorter than this cannot be taken for a form of a longer one.
MIN_SCOPE_KEY_CHARS = 3

# The note's own attestation of who performed the procedure, checked against the agreed
# FellowPresent and never used to set it. The teaching-physician wording means a trainee
# performed under supervision (FellowPresent 1, prompt rule 7); a Staff Only or "Fellow:
# None" line means no trainee took part (0). "Was present for the entire procedure" needs a
# first-person subject ("I was present", "I, Dr X, was present"): the same words describe the
# sedation monitor in some notes.
TEACHING_ATTESTATION_RE = re.compile(
    r"teaching (physician )?statement|was present and actively participated"
    r"|\bi\b[^\n]{0,60}?\bwas present for the entire procedure",
    re.I,
)
NO_TRAINEE_LINE_RE = re.compile(r"staff only|(resident/)?fellows?\s*:?\s*none\b", re.I)

# What marks a note that could hold a study value, so that a missing consensus is worth a
# human's time: a procedure note type (Procedures, the Upper Endoscopy / PED Endoscopy / PEG
# Placement family, Brief Op Note, OR PostOp, OR Nursing), or upper endoscopy wording in the
# text (the scope passed or the esophagus intubated, an upper endoscope's brand or model).
# In the 5,000-note corpus that wording occurs in 353 of the 609 Procedures notes and in
# about twenty notes of other types; every note too long for the window is of another type.
PROCEDURE_NOTE_TYPE_RE = re.compile(
    r"procedure|endoscop|colonoscop|peg placement|brief op note|\bor (postop|nursing)\b", re.I
)
EGD_LANGUAGE_RE = re.compile(
    r"o?esophagus (was )?intubated|(endoscope|gastroscope) (was )?(advanced|passed)"
    r"|intubated with (an? )?(olympus|gif)|olympus|pentax|fuji|\bgif[- ]|\bh190|\bq180|\bxp190|esophagoscop",
    re.I,
)


class _Cluster(NamedTuple):
    """The models that gave one value for a field (for ScopeType, one scope in any form)."""
    value: object   # the value the cluster stands for; for ScopeType the form most models gave
    labels: list    # the models in it
    forms: list     # [(value as a model gave it, [labels])]; several entries only for scope names in different forms


class _Vote(NamedTuple):
    value: object       # the consensus value; None when agreed null or when left empty
    decided: bool       # False when the field was left empty for lack of a majority
    unanimous: bool
    text: str | None    # the Agreement line, when the models did not simply agree


def _show(value) -> str:
    """A value as the Agreement text writes it: null, a number, or a quoted name."""
    return "null" if value is None else repr(value) if isinstance(value, str) else str(value)


def _names(answers: Sequence[ModelAnswer]) -> str:
    return ", ".join(a.label for a in answers)


def _same_scope(key: str, other: str) -> bool:
    """
    Whether two squashed scope names are forms of the same scope: one is the other with the
    brand or spacing added ("gifh190" and "olympusgifh190"). "Olympus gastroscope" and
    "Olympus GIF-H190" are not: one names the model and the other does not.
    """
    return min(len(key), len(other)) >= MIN_SCOPE_KEY_CHARS and (key in other or other in key)


def _scope_clusters(votes) -> list[_Cluster]:
    """Groups scope names, with the forms of one scope together; the kept form is the one most models gave, the fullest on a tie."""
    forms = {}  # squashed name -> [the name as first given, labels]
    for label, value in votes:
        key = None if value is None else squash(value)
        forms.setdefault(key, [value, []])[1].append(label)

    groups = []  # lists of keys that name the same scope
    for key in sorted((k for k in forms if k is not None), key=len, reverse=True):
        for group in groups:
            if any(_same_scope(key, other) for other in group):
                group.append(key)
                break
        else:
            groups.append([key])

    clusters = []
    for keys in groups:
        kept = max(keys, key=lambda k: (len(forms[k][1]), len(k)))
        labels = [label for k in keys for label in forms[k][1]]
        clusters.append(_Cluster(forms[kept][0], labels, [(forms[k][0], forms[k][1]) for k in keys]))
    if None in forms:
        clusters.append(_Cluster(None, forms[None][1], [(None, forms[None][1])]))
    return clusters


def _clusters(field: str, votes) -> list[_Cluster]:
    """Groups the models by the value they gave for field; equal values together."""
    if field == "ScopeType":
        return _scope_clusters(votes)
    groups = {}
    for label, value in votes:
        groups.setdefault(value, []).append(label)
    return [_Cluster(value, labels, [(value, labels)]) for value, labels in groups.items()]


def _vote(field: str, answered: Sequence[ModelAnswer]) -> _Vote:
    """
    The models' consensus on one field: the value they all gave, else the value a strict
    majority gave, else none. The text names every model's value when they did not all give
    the same one.
    """
    clusters = sorted(_clusters(field, [(a.label, a.fields[field]) for a in answered]), key=lambda c: -len(c.labels))
    total = len(answered)
    top = clusters[0]
    tally = ", ".join(f"{_show(value)} ({', '.join(labels)})" for c in clusters for value, labels in c.forms)

    if len(top.labels) == total:
        if len(top.forms) == 1:
            return _Vote(top.value, True, True, None)
        return _Vote(top.value, True, True, f"{field}: {tally}: the same scope, {_show(top.value)} kept")
    if 2 * len(top.labels) > total:
        forms = " (the same scope in different forms)" if len(top.forms) > 1 else ""
        return _Vote(top.value, True, False, f"{field}: {tally}: {_show(top.value)} by {len(top.labels)} of {total}{forms}")
    return _Vote(None, False, False, f"{field}: {tally}: no majority, left empty")


def _evidence(flag: str, info: str, answered: Sequence[ModelAnswer], values: dict) -> str | None:
    """
    The quote for the consensus value of flag: null when the flag is not 1, else the quote
    most models that answered 1 gave (the first model's on a tie). For the biopsy sentence,
    models that also gave the consensus count are preferred, since the quote states it.
    """
    if values[flag] != 1:
        return None
    supporting = [a for a in answered if a.fields[flag] == 1 and a.fields[info]]
    if flag == "DuodenalBiopsiesTaken":
        same_count = [a for a in supporting if a.fields["NumberOfDuodenalBiopsies"] == values["NumberOfDuodenalBiopsies"]]
        supporting = same_count or supporting
    quotes = {}  # squashed quote -> [the quote as first given, how many models gave it]
    for a in supporting:
        quotes.setdefault(squash(a.fields[info]), [a.fields[info], 0])[1] += 1
    return max(quotes.values(), key=lambda q: q[1])[0] if quotes else None


def study_candidate(note_type: str | None, note: str | None) -> bool:
    """
    Whether a note could hold a study value, so that a missing consensus on it is worth a
    human's time: its type is a procedure type, or its text has upper endoscopy wording
    (PROCEDURE_NOTE_TYPE_RE, EGD_LANGUAGE_RE). True when neither is known, to stay cautious.
    """
    if note_type is None and note is None:
        return True
    return bool(PROCEDURE_NOTE_TYPE_RE.search(note_type or "")) or bool(EGD_LANGUAGE_RE.search(note or ""))


def found_procedure(answer: ModelAnswer) -> bool:
    """
    Whether a model's accepted answer says a procedure was performed (any flag 1, or a scope
    name). One such answer is not a consensus, but a row holding one is worth a human's time
    whatever the note's type, so it is never released unflagged.
    """
    return answer.answered and (
        any(answer.fields[flag] == 1 for flag in EVIDENCE_FOR_FLAG) or answer.fields["ScopeType"] is not None
    )


def attestation_conflicts(note: str, fellow) -> list[str]:
    """
    The review reasons where the note's own attestation contradicts the agreed FellowPresent
    (0, 1, or None when the field was left empty): a teaching attestation with FellowPresent
    not 1, a Staff Only or no-trainee line with FellowPresent not 0. A note carrying both
    wordings gets the reason for whichever the consensus contradicts (both when it is empty).
    The check only reads the note; it never sets the value. It is for a note whose consensus
    has a procedure: a teaching statement in a progress note is about the visit, not a
    procedure, and FellowPresent is rightly 0 there.
    """
    shown = "empty" if fellow is None else str(fellow)
    reasons = []
    if fellow != 1 and TEACHING_ATTESTATION_RE.search(note):
        reasons.append(f"note carries a teaching attestation but FellowPresent is {shown}")
    if fellow != 0 and NO_TRAINEE_LINE_RE.search(note):
        reasons.append(f"note carries a Staff Only or no-trainee line but FellowPresent is {shown}")
    return reasons


def consensus(
    answers: Sequence[ModelAnswer], votes: dict | None = None, note_type: str | None = None, note: str | None = None,
) -> dict:
    """
    The consensus block for one note from the models' answers (one ModelAnswer per model
    consulted, in run order): the ten fields, ManualReviewRequired ("true"/"false") with
    ManualReviewReason, and Outcome, Models and Agreement (database.CONSENSUS_COLUMNS).
    No text from the note goes into the reason or the agreement: only scope names, numbers
    and model labels.
    votes, when a dict is given, receives how each voted field went when at least two models
    answered: field -> (decided, unanimous, labels of the answering models, labels of those
    that gave the winning value), for the run's agreement tally.
    note_type and note, when given, decide whether a note without a consensus is flagged
    (study_candidate, unless a model's answer found a procedure: found_procedure) and let the
    agreed FellowPresent be checked against the note's own attestation
    (attestation_conflicts). Without them every note without a consensus is flagged and the
    attestation is not checked.
    """
    values = {field: None for field, _ in RESULT_FIELDS}
    answered = [a for a in answers if a.answered]
    failed = [a for a in answers if a.llm_failed == 1]
    pending = [a for a in answers if a.llm_failed is None]
    reasons = []
    outcome = "none"
    disputed = []       # the voted fields the answering models did not all agree on (Disputed)
    undecided = set()   # the fields left empty for lack of a majority or by a conflict (for the Summary)

    models = "; ".join(
        a.label + ("" if a.answered else " (failed)" if a.llm_failed == 1 else " (not analysed)") for a in answers
    )
    header = f"{len(answered)} of {len(answers)} models answered"
    gone = [f"{_names(failed)} failed"] if failed else []
    if pending:
        gone.append(f"{_names(pending)} not analysed")
    if gone:
        header += f" ({'; '.join(gone)})"

    if pending:
        reasons.append(f"{_names(pending)} has not analysed this note; run --analyze again")
        agreement = f"{header}; no consensus until every model has run"
        summary = "No consensus yet: a model has not analysed this note"
    elif len(answered) < 2:
        summary = f"No consensus: {len(answered)} of {len(answers)} models answered"
        if not answered:
            if failed and all(a.error and a.error.startswith(TOO_LONG_PREFIX) for a in failed):
                reasons.append("note too long for the context window, not sent to any model; see each model's Failure reason")
            else:
                reasons.append("no model gave an answer the note supports; see each model's Failure reason")
            agreement = f"{header}; no consensus"
        else:
            if failed:
                reasons.append(f"{_names(failed)} failed on this note; see Failure reason")
            reasons.append(
                f"only {answered[0].label} gave an answer the note supports, which is not a consensus; "
                f"its own columns hold that answer"
            )
            agreement = f"{header}; no consensus from one model"
        if not study_candidate(note_type, note) and not any(found_procedure(a) for a in answered):
            # An empty row for a note that cannot hold a study value loses the study nothing,
            # so no human is sent to it; the explanation stays here for the audit.
            agreement += (
                "; not flagged: not a procedure note type, no upper endoscopy wording in the note "
                "and no model found a procedure, so there is no study value to recover"
            )
            reasons = []
    else:
        lines, empty, by_majority = [], [], []
        for field in VOTED_FIELDS:
            vote = _vote(field, answered)
            if votes is not None:
                votes[field] = (vote.decided, vote.unanimous, [a.label for a in answered], _agreeing(field, vote, answered))
            values[field] = vote.value
            if vote.text:
                lines.append(vote.text)
            if not vote.decided:
                empty.append(field)
            elif not vote.unanimous:
                by_majority.append(field)

        # The fields must agree with each other, as verify_against_note demands of one model's
        # answer; separate majorities can contradict, and a contradicted field is left empty.
        conflicts = []
        undecided.update(empty)
        if values["FellowPresent"] == 1 and values["Endoscopy"] != 1 and values["Colonoscopy"] != 1:
            values["FellowPresent"] = None
            undecided.add("FellowPresent")
            conflicts.append("FellowPresent left empty: a fellow takes part in a procedure, and the consensus has none")
        if values["DuodenalBiopsiesTaken"] == 1 and values["Endoscopy"] != 1:
            values["DuodenalBiopsiesTaken"] = values["NumberOfDuodenalBiopsies"] = None
            undecided.update(("DuodenalBiopsiesTaken", "NumberOfDuodenalBiopsies"))
            conflicts.append("DuodenalBiopsiesTaken and NumberOfDuodenalBiopsies left empty: duodenal biopsies need an upper endoscopy, and the consensus has none")
        if values["ScopeType"] is not None and values["Endoscopy"] != 1:
            values["ScopeType"] = None
            undecided.add("ScopeType")
            conflicts.append("ScopeType left empty: the consensus has no upper endoscopy")

        disputed = [
            f"{field} ({'no majority, left empty' if field in empty else 'majority answer given'})"
            for field in VOTED_FIELDS if field in empty or field in by_majority
        ]
        if disputed:
            reasons.append(f"models disagree on {', '.join(disputed)}; see How the models voted")
        reasons.extend(conflicts)
        if values["Endoscopy"] == 1 and values["ScopeType"] is None and "ScopeType" not in empty:
            reasons.append("upper endoscopy documented but no scope name found")
        # A null count the models agree on is "taken, count not stated", which the study
        # accepts (whether duodenal biopsies were taken is the minimum it needs), so it is
        # not a reason for review.
        if note is not None and (values["Endoscopy"] == 1 or values["Colonoscopy"] == 1):
            reasons.extend(attestation_conflicts(note, values["FellowPresent"]))

        for flag, info in EVIDENCE_FOR_FLAG.items():
            values[info] = _evidence(flag, info, answered, values)

        # The verifier reads "4 biopsies of the duodenum and 2 from the bulb" as 6, the sum,
        # since the bulb is the duodenum (prompt rule 5). The researcher's examples confirm
        # only the part-of form ("6 including 2 from the bulb" is 6), so a count that rests on
        # a summed sentence is raised until that reading is confirmed; the value stays.
        count = values["NumberOfDuodenalBiopsies"]
        quote = values["DuodenalBiopsiesInformation"]
        if count and quote and summed_count_quote(quote):
            total = sum(supported_counts(quote))  # an additive quote supports its sum alone
            if count == total:
                reasons.append(
                    f"NumberOfDuodenalBiopsies {count} is the sum of the counts the quoted sentence gives for "
                    f"different parts of the duodenum; confirm the study counts them together"
                )
            else:
                reasons.append(
                    f"NumberOfDuodenalBiopsies {count} is one of the counts the quoted sentence gives for "
                    f"different parts of the duodenum, which add up to {total}; the verifier now accepts "
                    f"only the sum on a new run"
                )
        if failed and reasons:
            # A failed model is an abstention, not a dissent: its failure is worth saying only
            # when the row needs a look anyway, so the reviewer knows how many models the
            # consensus rests on.
            reasons.insert(
                0,
                f"{_names(failed)} failed on this note; the consensus rests on the other "
                f"{len(answered)} models; see Failure reason",
            )

        agreement = f"{header}; " + ("; ".join(lines) if lines else "they agree on every field")
        if empty or conflicts:
            outcome = "partial"
        elif by_majority:
            outcome = "majority"
        elif failed:
            outcome = "agreed"
        else:
            outcome = "unanimous"
        summary = _summary(values, undecided)

    values["ManualReviewRequired"] = "true" if reasons else "false"
    values["ManualReviewReason"] = "; ".join(reasons) or None
    values["Summary"] = summary
    values["Outcome"] = outcome
    values["Answered"] = len(answered)
    values["Models"] = models
    values["Disputed"] = "; ".join(disputed) or None
    values["Agreement"] = agreement
    assert set(values) == {field for field, _ in CONSENSUS_COLUMNS}
    return values


def _summary(values: dict, undecided: set) -> str:
    """
    What a consensus of two or more models found, in one plain-words line for the Summary
    column: the procedure(s) with the scope, the duodenal biopsies with their count, and the
    fellow, naming a field "undecided" where the models left it empty (undecided: the fields
    left empty for lack of a majority or by a conflict). Built from the consensus values
    only, so it says nothing the fields do not.
    """
    egd, colonoscopy = values["Endoscopy"], values["Colonoscopy"]
    parts = []
    if egd == 1:
        scope = values["ScopeType"]
        if scope is not None:
            parts.append(f"upper endoscopy with {scope}")
        elif "ScopeType" in undecided:
            parts.append("upper endoscopy, scope undecided")
        else:
            parts.append("upper endoscopy, scope not named")
    elif egd is None:
        parts.append("upper endoscopy undecided")

    taken, count = values["DuodenalBiopsiesTaken"], values["NumberOfDuodenalBiopsies"]
    if taken == 1:
        if count is None:
            how = "undecided" if "NumberOfDuodenalBiopsies" in undecided else "not stated"
            parts.append(f"duodenal biopsies taken, count {how}")
        else:
            parts.append(f"{count} duodenal {'biopsy' if count == 1 else 'biopsies'}")
    elif taken is None:
        parts.append("duodenal biopsies undecided")
    elif egd == 1:
        parts.append("no duodenal biopsies")

    if colonoscopy == 1:
        parts.append("colonoscopy")
    elif colonoscopy is None:
        parts.append("colonoscopy undecided")

    fellow = values["FellowPresent"]
    if fellow == 1:
        parts.append("fellow present")
    elif fellow is None:
        parts.append("fellow presence undecided")
    elif egd == 1 or colonoscopy == 1:
        parts.append("no fellow")

    if not parts:
        return "No procedure documented"
    text = "; ".join(parts)
    return text[0].upper() + text[1:]


def _agreeing(field: str, vote: _Vote, answered: Sequence[ModelAnswer]) -> list:
    """The labels of the models whose value for field is the consensus value (any form of a scope)."""
    if not vote.decided:
        return []
    if field == "ScopeType":
        for cluster in _scope_clusters([(a.label, a.fields[field]) for a in answered]):
            if cluster.value == vote.value:
                return list(cluster.labels)
        return []
    return [a.label for a in answered if a.fields[field] == vote.value]


# How often consolidate reports its progress (notes consolidated).
CONSENSUS_PROGRESS_EVERY = 100

# How a consensus review reason is counted in the summary: the first marker found in it.
REASON_KEYS = (
    ("has not analysed this note", "a model has not analysed the note"),
    ("not sent to any model", "note too long for the window"),
    ("no model gave an answer", "no model gave an answer the note supports"),
    ("which is not a consensus", "only one model answered"),
    ("failed on this note", "a model failed on the note"),
    ("models disagree", "the models disagree on a field"),
    ("left empty:", "fields in conflict, left empty"),
    ("no scope name found", "upper endoscopy but no scope name"),
    ("teaching attestation", "teaching attestation but FellowPresent not 1"),
    ("no-trainee line", "Staff Only or no-trainee line but FellowPresent not 0"),
    ("different parts of the duodenum", "duodenal count from a sentence that counts parts of the duodenum"),
)


class ConsensusSummary(NamedTuple):
    """What consolidate built, for the run's report."""
    notes: int
    outcomes: Counter          # Outcome -> notes
    flagged: int               # notes with ManualReviewRequired (consensus) true
    unflagged_none: int        # notes without consensus not flagged: not a procedure note, no upper endoscopy wording, no model found a procedure
    disputed: Counter          # field -> notes where the answering models did not all agree on it
    agreement: dict            # label -> (votes agreeing with the decided consensus value, votes cast)
    reasons: Counter           # review reason category -> notes
    seconds: float


def consolidate(conn, models: Sequence[ModelSpec] = MODELS, report=print, progress=None) -> ConsensusSummary:
    """
    Builds the consensus of the given models for every note in the database and stores it
    under the consensus label, replacing any earlier consensus (a model's answers may have
    been added since). Reports the summary lines through report (one line each) and, when
    given, calls progress(done, total) every CONSENSUS_PROGRESS_EVERY notes.
    """
    started = time.perf_counter()
    labels = [model.label for model in models]
    answers = model_answers(conn, labels)
    rows = {}
    outcomes = Counter()
    disputed = Counter()
    reasons = Counter()
    agreement = {label: [0, 0] for label in labels}
    flagged = 0
    unflagged_none = 0
    for done, (note_id, note_type, note, per_model) in enumerate(answers, start=1):
        votes = {}
        row = consensus([ModelAnswer(*answer) for answer in per_model], votes, note_type, note)
        rows[note_id] = row
        outcomes[row["Outcome"]] += 1
        flagged += row["ManualReviewRequired"] == "true"
        unflagged_none += row["Outcome"] == "none" and row["ManualReviewRequired"] == "false"
        for field, (decided, unanimous, voters, agreeing) in votes.items():
            if not unanimous:
                disputed[field] += 1
            if decided:
                for label in voters:
                    agreement[label][1] += 1
                    agreement[label][0] += label in agreeing
        for reason in (row["ManualReviewReason"] or "").split("; "):
            for marker, key in REASON_KEYS:
                if marker in reason:
                    reasons[key] += 1
                    break
        if progress is not None and (done % CONSENSUS_PROGRESS_EVERY == 0 or done == len(answers)):
            progress(done, len(answers))
    store_consensus(conn, rows)
    seconds = time.perf_counter() - started

    report(
        f"Consensus of {len(labels)} model(s) built for {len(rows)} note(s) in {seconds:.1f}s: "
        f"{outcomes['unanimous']} unanimous, {outcomes['agreed']} agreed with a model failed, "
        f"{outcomes['majority']} by majority, {outcomes['partial']} partial, {outcomes['none']} without consensus; "
        f"{flagged} flagged for manual review."
    )
    if unflagged_none:
        report(
            f"Not flagged although without consensus: {unflagged_none} note(s) of other types with no upper "
            f"endoscopy wording where no model found a procedure, so an empty row is no loss to the study (see How the models voted)."
        )
    if disputed:
        report("Fields the models did not all agree on: " + ", ".join(f"{field} {n}" for field, n in disputed.most_common()) + " note(s).")
    if any(total for _, total in agreement.values()):
        report("Agreement with the consensus, over the field votes where a consensus value exists: " + "; ".join(
            f"{label} {100 * agree / total:.1f}% ({agree:,} of {total:,})" if total else f"{label} no votes"
            for label, (agree, total) in agreement.items()
        ) + ".")
    if reasons:
        report("Review reasons: " + ", ".join(f"{key} {n}" for key, n in reasons.most_common()) + ".")
    return ConsensusSummary(
        len(rows), outcomes, flagged, unflagged_none, disputed,
        {label: tuple(pair) for label, pair in agreement.items()}, reasons, seconds,
    )

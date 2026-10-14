"""
Consolidates the models' answers for each note into one consensus block, stored and exported
under the label "consensus" ("ScopeType (consensus)" and so on; database.CONSENSUS_COLUMNS).

The consensus is cautious, the way the study wants it: a wrong value is worse than an empty
one. A field gets a value only when at least two models gave an answer the note supports and
they agree on it. A strict majority (2 of 3) settles a disagreement, and the note is then
flagged for manual review; a field with no majority is left empty. The note is also flagged
when a model failed or has not run, when only one model answered (one answer is not a
consensus), and when the agreed answer leaves a study field open (an upper endoscopy without
a scope name, biopsies taken without a count). The flag never changes a value; it says where
a human should look. Every row records which models were consulted and how they voted
(Models, Agreement, Outcome), so the consensus can be audited against the per-model columns.

A model's own ManualReviewRequired flag (review.py) does not carry over: it describes how
that model reached its answer; the consensus is judged on whether the models agree.
"""

from collections import Counter
from typing import NamedTuple, Sequence

from database import CONSENSUS_COLUMNS, RESULT_FIELDS, model_answers, store_consensus
from llm_analysis import EVIDENCE_FOR_FLAG, MODELS, ModelSpec, TOO_LONG_PREFIX, squash


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
    "majority": "every field has a value, but a field was settled by a strict majority or a model gave no answer",
    "partial": "a field was left empty because the models could not agree on it",
    "none": "no consensus: fewer than two models answered, or a model has not run",
}

# A scope name shorter than this cannot be taken for a form of a longer one.
MIN_SCOPE_KEY_CHARS = 3


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


def consensus(answers: Sequence[ModelAnswer]) -> dict:
    """
    The consensus block for one note from the models' answers (one ModelAnswer per model
    consulted, in run order): the ten fields, ManualReviewRequired ("true"/"false") with
    ManualReviewReason, and Outcome, Models and Agreement (database.CONSENSUS_COLUMNS).
    No text from the note goes into the reason or the agreement: only scope names, numbers
    and model labels.
    """
    values = {field: None for field, _ in RESULT_FIELDS}
    answered = [a for a in answers if a.answered]
    failed = [a for a in answers if a.llm_failed == 1]
    pending = [a for a in answers if a.llm_failed is None]
    reasons = []
    outcome = "none"

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
    elif not answered:
        if failed and all(a.error and a.error.startswith(TOO_LONG_PREFIX) for a in failed):
            reasons.append("note too long for the context window, not sent to any model; see LLM_Error")
        else:
            reasons.append("no model gave an answer the note supports; see each model's LLM_Error")
        agreement = f"{header}; no consensus"
    elif len(answered) == 1:
        if failed:
            reasons.append(f"{_names(failed)} failed on this note; see LLM_Error")
        reasons.append(
            f"only {answered[0].label} gave an answer the note supports, which is not a consensus; "
            f"its own columns hold that answer"
        )
        agreement = f"{header}; no consensus from one model"
    else:
        if failed:
            reasons.append(
                f"{_names(failed)} failed on this note; the consensus rests on the other "
                f"{len(answered)} models; see LLM_Error"
            )

        lines, empty, by_majority = [], [], []
        for field in VOTED_FIELDS:
            vote = _vote(field, answered)
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
        if values["FellowPresent"] == 1 and values["Endoscopy"] != 1 and values["Colonoscopy"] != 1:
            values["FellowPresent"] = None
            conflicts.append("FellowPresent left empty: a fellow takes part in a procedure, and the consensus has none")
        if values["DuodenalBiopsiesTaken"] == 1 and values["Endoscopy"] != 1:
            values["DuodenalBiopsiesTaken"] = values["NumberOfDuodenalBiopsies"] = None
            conflicts.append("DuodenalBiopsiesTaken and NumberOfDuodenalBiopsies left empty: duodenal biopsies need an upper endoscopy, and the consensus has none")
        if values["ScopeType"] is not None and values["Endoscopy"] != 1:
            values["ScopeType"] = None
            conflicts.append("ScopeType left empty: the consensus has no upper endoscopy")

        disputed = [
            f"{field} ({'no majority, left empty' if field in empty else 'majority answer given'})"
            for field in VOTED_FIELDS if field in empty or field in by_majority
        ]
        if disputed:
            reasons.append(f"models disagree on {', '.join(disputed)}; see Agreement")
        reasons.extend(conflicts)
        if values["Endoscopy"] == 1 and values["ScopeType"] is None and "ScopeType" not in empty:
            reasons.append("upper endoscopy documented but no scope name found")
        if (values["DuodenalBiopsiesTaken"] == 1 and values["NumberOfDuodenalBiopsies"] is None
                and "NumberOfDuodenalBiopsies" not in empty):
            reasons.append("duodenal biopsies taken but no count stated")

        for flag, info in EVIDENCE_FOR_FLAG.items():
            values[info] = _evidence(flag, info, answered, values)

        agreement = f"{header}; " + ("; ".join(lines) if lines else "they agree on every field")
        if empty or conflicts:
            outcome = "partial"
        elif failed or by_majority:
            outcome = "majority"
        else:
            outcome = "unanimous"

    values["ManualReviewRequired"] = "true" if reasons else "false"
    values["ManualReviewReason"] = "; ".join(reasons) or None
    values["Outcome"] = outcome
    values["Models"] = models
    values["Agreement"] = agreement
    assert set(values) == {field for field, _ in CONSENSUS_COLUMNS}
    return values


def consolidate(conn, models: Sequence[ModelSpec] = MODELS):
    """
    Builds the consensus of the given models for every note in the database and stores it
    under the consensus label, replacing any earlier consensus (a model's answers may have
    been added since). Prints one summary line.
    """
    labels = [model.label for model in models]
    rows = {}
    outcomes = Counter()
    flagged = 0
    for note_id, per_model in model_answers(conn, labels):
        row = consensus([ModelAnswer(*answer) for answer in per_model])
        rows[note_id] = row
        outcomes[row["Outcome"]] += 1
        flagged += row["ManualReviewRequired"] == "true"
    store_consensus(conn, rows)
    print(
        f"Consensus of {len(labels)} model(s) built for {len(rows)} note(s): "
        f"{outcomes['unanimous']} unanimous, {outcomes['majority']} by majority, "
        f"{outcomes['partial']} partial, {outcomes['none']} without consensus; "
        f"{flagged} flagged for manual review."
    )

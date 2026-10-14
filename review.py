"""
Decides whether a note's result needs a human to look at it before the study uses it.

The LLM checks (llm_analysis.verify_against_note) reject answers the note does not support.
The review flag covers what the checks cannot settle: a note with no accepted answer, an
answer the model only reached after rejected attempts, an answer that leaves a study field
open, and a note the model may not have seen in full.
"""

from llm_analysis import (
    CONTEXT_LENGTH, TOO_LONG_PREFIX, estimated_prompt_tokens, is_loop_rejection, note_fits_context,
)


def manual_review(
    note: str,
    result: dict | None,
    llm_failed: int,
    attempts: int,
    error: str | None,
    rejections: str | None = None,
    context_length: int = CONTEXT_LENGTH,
) -> tuple[bool, str | None]:
    """
    Returns (required, reason) for one note's result. The reason lists every trigger,
    separated by "; ", and is None when no review is needed. Reasons hold no note text.
    rejections is the LLM_Rejections text (one rejected attempt per line), used to tell
    rejections of the answer from repetition loops. context_length is the window the model
    was called with (ModelSpec.num_ctx).

    Triggers:
      - the note failed (no answer the note supports, or the model call failed)
      - the answer was accepted only after an attempt was rejected for its content (a
        quote not in the note, a scope name the note does not state, a count the quote
        does not support, and so on). A retry after a repetition loop is not counted: the
        model repeating itself says nothing about the answer it then gave
      - an upper endoscopy is documented but no scope name was found
      - duodenal biopsies were taken but the note states no count
      - the note was too long for the context window and was not sent to the model
        (results stored before that rule existed were sent, and may rest on a prompt that
        Ollama silently cut, so they are flagged by length)
    """
    reasons = []
    too_long = bool(error) and error.startswith(TOO_LONG_PREFIX)

    if llm_failed:
        if too_long:
            reasons.append("note too long for the context window, not sent to the model; see LLM_Error")
        elif error and error.startswith("LLM call failed"):
            reasons.append("the model call failed; see LLM_Error")
        else:
            reasons.append("no answer the note supports; see LLM_Error")
    elif attempts > 1:
        if rejections is None:
            reasons.append(
                f"accepted on attempt {attempts} after {attempts - 1} rejected answer(s) "
                f"(reasons not recorded)"
            )
        else:
            content = [line for line in rejections.split("\n") if line and not is_loop_rejection(line)]
            if content:
                reasons.append(
                    f"accepted on attempt {attempts} after {len(content)} rejected answer(s), "
                    f"repetition loops not counted; see LLM_Rejections"
                )

    if result:
        if result.get("Endoscopy") == 1 and result.get("ScopeType") is None:
            reasons.append("upper endoscopy documented but no scope name found")
        if result.get("DuodenalBiopsiesTaken") == 1 and result.get("NumberOfDuodenalBiopsies") is None:
            reasons.append("duodenal biopsies taken but no count stated")

    if not too_long and not note_fits_context(note, context_length):
        reasons.append(
            f"note is about {estimated_prompt_tokens(note)} prompt tokens, more than the "
            f"{context_length}-token context window allows; the model may not have seen all of it"
        )

    return (bool(reasons), "; ".join(reasons) or None)

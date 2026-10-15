# Reading results.csv and results-review.csv

This guide is for the researcher reading `output/results.csv`, the file the extraction program
writes. It explains what each column means, how to find the rows that need a human decision,
and how to read a row. No programming knowledge is needed.

The file contains protected health information: every note in full, and quotes from it. Keep
it wherever the notes themselves may be kept.

## 1. What the file is

One row per clinical note from the input CSV. For every note, three language models (run
locally, one after another) each read the note and answered the same ten questions about the
procedure it documents: was an upper endoscopy performed, with which scope, were duodenal
biopsies taken and how many, was a colonoscopy performed, did a fellow take part, and the
sentence from the note that justifies each answer.

Every answer was checked against the note's own text before it was accepted. An answer the
note does not support (a quoted sentence that is not in the note, a scope name the note never
states, a count the quoted sentence does not give) was rejected and the model was asked again,
up to five times. A model that never gave a supportable answer is recorded as **failed** for
that note; nothing is guessed.

A row then holds three kinds of information, from left to right:

1. **The consensus** (columns G to X): the study's answer, built from the models' answers. A
   field has a value only where at least two models answered and agree. Where the models split,
   a strict majority (2 of 3) is written and the row is flagged for review; where there is no
   majority the field is left empty. Where fewer than two models answered there is no
   consensus: every field is empty, and the row is flagged unless every model read the note
   and nothing in it suggests a procedure (section 4).
2. **Each model's own answers** (one block of 16 columns per model), with whether it failed,
   how many attempts it needed and why it failed. These are the evidence behind the consensus.
3. **Audit text** (each model's rejected attempts and raw response), the length of the note,
   and the note itself, in the last column.

A second file, `results-review.csv`, is written with it from the same pass: the same rows
with only the identifiers, the consensus block and the note length (columns A to X exactly as
in this file, then the length in column Y), about 4% of the size, so the whole study corpus
opens in Google Sheets. Read the review queue and the study values there, including the
evidence quotes behind each consensus value; open `results.csv` when you need a model's own
answers, the rejected attempts or the note text.

Both files are written once per run and never overwritten; a new run with the same output
folder leaves them alone until both are moved aside.

## 2. Open a file and highlight the review queue

Start with `results-review.csv`: it opens quickly whatever the size of the study, and columns A
to X are the same in both files, so everything below applies to either.

The one column to know is **G, "Needs human review (consensus)"**: TRUE on every row a human
should look at, FALSE on every other row. Both Google Sheets and Excel read TRUE and FALSE as
real yes/no values, so one formatting rule highlights the whole queue.

### Google Sheets

1. File, Import, Upload, choose the file. Separator type "Detect automatically". Leave
   "Convert text to numbers, dates and formulas" on, so TRUE and FALSE become yes/no values.
2. Freeze the header and the identifiers: View, Freeze, 1 row; then click the letter F, View,
   Freeze, Up to column F.
3. Highlight the queue: Format, Conditional formatting. In "Apply to range" type `A2:CB`
   (every column, from row 2 down; `A2:Y` in results-review.csv). Under "Format rules" choose "Custom formula is" and enter
   `=$G2=TRUE`. Pick a fill colour. Done.
4. Filter or sort: Data, Create a filter; on column G keep only TRUE. Or sort column G from Z to
   A, which puts TRUE first.

### Microsoft Excel

1. Data, From Text/CSV, choose the file. Check that the preview shows "File origin: UTF-8" and
   "Delimiter: Comma", then Load. (Double-clicking the file also works on most systems; if any
   accented character looks wrong, use the import.)
2. Freeze the header and the identifiers: click cell G2, View, Freeze Panes, Freeze Panes.
3. Highlight the queue: select everything from A2 to the last cell (click A2, then press
   Ctrl+Shift+End). Home, Conditional Formatting, New Rule, "Use a formula to determine which
   cells to format", enter `=$G2=TRUE`, Format, choose a fill, OK, OK.
4. Filter: click row 1, Data, Filter; on column G keep only TRUE.

If the yes/no values were imported as text (the import option to convert values was off), the
rule `=$G2="TRUE"` works instead; `=OR($G2=TRUE,$G2="TRUE")` works either way.

Two habits that keep the file usable:

- Record your decisions in **new columns at the far right**, after the last column ("Note",
  CB, in results.csv; the note length, Y, in results-review.csv), for
  example "Reviewer decision" and "Reviewer notes". Inserting or deleting columns in the middle
  changes the column letters this guide uses.
- A cell in Excel holds at most 32,767 characters, and a few notes are longer than that (the
  longest in the 5,000-note corpus is about 41,000 characters), so Excel shows those notes cut
  off or spilling. Google Sheets holds 50,000 characters per cell. The database behind the
  file has every note in full.

## 3. Column by column

### 3.1 Identifiers (columns A to F)

| Column | Title | Meaning |
| --- | --- | --- |
| A | Note id | The note's row number in the program's database. The run log refers to notes by this number. It is assigned when the notes are loaded, so it changes if the database is rebuilt; use NoteCsnID to tie a row back to the source file. |
| B | MRN | As in the input CSV. |
| C | Encounter | As in the input CSV. |
| D | NoteCsnID | As in the input CSV; the stable identifier of the note. |
| E | NoteDate | As in the input CSV (a year alone, if that is what the input held). |
| F | NoteType | As in the input CSV, for example `Procedures`, `Progress Notes`, `Consults`. |

### 3.2 The consensus (columns G to X)

Every title in this block ends in "(consensus)".

| Column | Title | Meaning and values |
| --- | --- | --- |
| G | Needs human review | TRUE where a human can improve the row (section 5 lists every reason), FALSE otherwise. This is the review queue. |
| H | Review reasons | Every reason the row was flagged, separated by "; ". Empty when G is FALSE. |
| I | Outcome | How the consensus was reached: `unanimous`, `agreed`, `majority`, `partial` or `none` (section 4). |
| J | Models answered | How many of the models gave an answer the note supports: 0 to 3. A consensus needs at least 2. |
| K | Summary | One line, in words, of what the consensus found, built from the consensus fields only: "No procedure documented", "Upper endoscopy with Olympus GIF-H190; 4 duodenal biopsies; fellow present", "Upper endoscopy, scope not named; duodenal biopsies taken, count not stated; no fellow", "No consensus: 1 of 3 models answered". A field the models left empty is called "undecided". |
| L | Upper endoscopy performed | TRUE, FALSE, or empty when there is no consensus. Section 3.3 has the definitions. |
| M | Scope type | The upper endoscope, written as the note spells it (section 3.3), or empty. |
| N | Upper endoscopy evidence quote | The sentence from the note that documents the upper endoscopy; empty when L is not TRUE. |
| O | Duodenal biopsies taken | TRUE, FALSE, or empty. |
| P | Number of duodenal biopsies | A number; 0 when none were taken; empty when biopsies were taken but the note states no count (the Summary then says "count not stated"), or when there is no consensus. |
| Q | Duodenal biopsies evidence quote | The sentence stating the duodenal biopsies; empty when O is not TRUE. |
| R | Colonoscopy performed | TRUE, FALSE, or empty. |
| S | Colonoscopy evidence quote | The sentence documenting the colonoscopy; empty when R is not TRUE. |
| T | Fellow present | TRUE, FALSE, or empty. |
| U | Fellow evidence quote | The attestation sentence or the line naming the trainee; empty when T is not TRUE. |
| V | Models consulted | The models, in run order, separated by "; ", each marked "(failed)" or "(not analysed)" when it gave no answer. |
| W | Disputed fields | The fields on which the answering models did not all agree, each marked "(majority answer given)" or "(no majority, left empty)". Empty when they agreed on every field. |
| X | How the models voted | "3 of 3 models answered; they agree on every field", or which models failed and, for every disputed field, each model's value: "FellowPresent: 1 (model A), 0 (model B): no majority, left empty". For a row without consensus it also says which case the row is: flagged because a model never read the note, flagged because something suggests the note documents a procedure (a procedure note type, upper endoscopy wording in the note, a model that found one), or released unflagged because every model read it and nothing does. When the scope name is the models' joined form rather than the note's own text (section 3.3) it says so: "ScopeType: 'Olympus GIF-H190' is the models' form, joined from parts the note states apart". |

### 3.3 What the ten fields mean

The same ten fields appear in the consensus block and in every model's block. The review
reasons and the vote text (columns H, W and X) refer to them by their short names, in the
second column of this table.

| Title | Short name | Values | Definition the models were given |
| --- | --- | --- | --- |
| Upper endoscopy performed | Endoscopy | TRUE, FALSE, empty | TRUE only if an upper endoscopy (EGD, gastroscopy, esophagogastroduodenoscopy) was performed as this note's procedure and the note documents it: the scope passed through the mouth, the esophagus intubated, the stomach or duodenum examined. A PEG tube placed with a gastroscope counts. History ("history of EGD"), recommendations, scheduling and patient instructions ("after your endoscopy") are FALSE. |
| Scope type | ScopeType | text, empty | The upper endoscope only, named as the note names it: brand and model when both are given ("Olympus GIF-H190"), the model alone ("GIF-H 190") or the brand alone ("Olympus gastroscope") when that is all the note gives. Colonoscopes and sigmoidoscopes are ignored. Empty when the note names no upper endoscope, when no upper endoscopy was performed, or when there is no consensus. Spellings of one scope ("GIF-H190", "GIF-H 190", "Olympus GIF H190") count as agreement. The value is the note's own spelling: once the models agree on a name, the program copies it from the first place the note states it, so a note that writes "GIF-H 190" gets "GIF-H 190" whatever the models typed, and the models' own spellings stay in their blocks. The one exception is a name the note states in parts, the brand in one place and the model number in another ("Olympus endoscope (GIF-H190)"): there is no single span to copy, so the models' joined form is kept ("Olympus GIF-H190") and column X says so. No list of scope names is used: the value is always the note's text or the models' joined form. |
| Upper endoscopy evidence quote | EndoscopyInformation | text, empty | The one sentence or line from the note, copied verbatim, that documents the upper endoscopy. Empty when the answer is FALSE. |
| Duodenal biopsies taken | DuodenalBiopsiesTaken | TRUE, FALSE, empty | TRUE if at least one biopsy was taken from the duodenum during this procedure. The duodenal bulb, D1, D2, D3 and the first to third portion are all the duodenum; biopsies of the esophagus, stomach, antrum, colon, terminal ileum or rectum are not. "Random duodenal biopsies were taken" counts without a number. "BIOPSY: None" or no mention is FALSE. |
| Number of duodenal biopsies | NumberOfDuodenalBiopsies | number, empty | The total number of biopsies taken from the duodenum when the note states it. 0 when none were taken. Empty when biopsies were taken but no count is stated, or when there is no consensus. The duodenum is counted once: "6 biopsies from the duodenum including 2 from the bulb" is 6; "4 biopsies from the second portion and 2 from the bulb" is 6, and such a sum is flagged for confirmation (section 5). |
| Duodenal biopsies evidence quote | DuodenalBiopsiesInformation | text, empty | The sentence stating the duodenal biopsies, verbatim. |
| Colonoscopy performed | Colonoscopy | TRUE, FALSE, empty | TRUE only if a colonoscopy was performed as part of this note's procedure (scope through the anus, cecum or colon examined). "No colonoscopy was performed", "colonoscopy tomorrow" and "prior colonoscopy" are FALSE. |
| Colonoscopy evidence quote | ColonoscopyInformation | text, empty | The sentence documenting the colonoscopy, verbatim. |
| Fellow present | FellowPresent | TRUE, FALSE, empty | TRUE if a fellow or other trainee took part in performing this note's procedure, judged from the attending's attestation and the provider lines, not from the word "fellow". A Teaching Statement or Teaching Physician Statement (the attending was present and actively participated while a trainee performed) is TRUE even when no one is called a fellow. "Staff Only", "Fellow: None" and "RESIDENT/FELLOW: None" are FALSE. A fellow in another role ("discussed with the fellow on call") is FALSE. No procedure in the note is FALSE. |
| Fellow evidence quote | FellowInformation | text, empty | The attestation sentence or the line naming the trainee, verbatim. |

In the consensus block, each evidence quote is the one most models gave for the agreed answer
(for the biopsy sentence, from a model that also gave the agreed count).

### 3.4 Each model's block (16 columns per model)

Every title in a model's block ends in the model's label in parentheses: the model's name and
the size of the text window it was given, in tokens, for example `(medgemma:27b@8192)`. The
blocks come in the order the models ran. With the three study models the first block is
columns Y to AN, the second AO to BD, the third BE to BT.

| Position | Title | Meaning |
| --- | --- | --- |
| 1 | Failed | TRUE when the model gave no answer the note supports after every attempt, when the note was too long to be sent to it, or when the call to the model failed. Its ten fields are then empty. FALSE when its answer was accepted. Empty when the model has not analysed the note (the run was interrupted). |
| 2 to 11 | The ten fields | This model's own answers, in the same order and with the same meanings as in the consensus (section 3.3). |
| 12 | Needs review | This model's own flag, about how it reached its answer (section 5.2). It does not feed column G: three models agreeing on a grounded answer outweigh one model's bumpy path to it. |
| 13 | Review reasons | Why, when Needs review is TRUE. |
| 14 | Attempts | How many times the model was asked, 1 to 5. 0 means the note was too long to send. |
| 15 | Seconds | Wall-clock time the model spent on the note, all attempts included. |
| 16 | Failure reason | Why the model failed, when it did: the reason its last answer was rejected, naming the field and the rejected value; or that the note was too long; or the error from the server. Empty when the model did not fail. |

### 3.5 Audit text (columns BU to BZ with three models)

| Title | Meaning |
| --- | --- |
| Rejected attempts (model) | One line per rejected attempt, "attempt 1: ...", for failed and accepted notes alike, so an answer accepted on the third attempt shows what the first two got wrong. Empty when no attempt was rejected. |
| Raw model response (model) | The model's text exactly as it returned it (a small block of JSON): the accepted attempt, or the last rejected one when the model failed. |

### 3.6 The note (columns CA and CB)

| Column | Title | Meaning |
| --- | --- | --- |
| CA | Note length (characters) | How long the note is. Notes over about 18,000 characters do not fit the models' text window and are recorded as failed for every model without being sent. |
| CB | Note | The note, in full, as it was in the input CSV. |

## 4. The Outcome column

| Value | Meaning | Flagged? |
| --- | --- | --- |
| `unanimous` | Every model answered and they agree on every field. | Not for agreement; it may still be flagged for another reason (section 5.1), such as an attestation in the note that contradicts the agreed Fellow present. |
| `agreed` | Every model that answered agrees on every field, but a model failed on this note, so the consensus rests on two models. | Not for the failure alone. A model's failure is treated as an abstention, not a dissent; the failure is recorded in Models consulted and How the models voted. |
| `majority` | Every field has a value, but at least one was settled 2 against 1. | Always. |
| `partial` | At least one field was left empty: the models split 1 against 1 (with the third failed), gave three different answers, or the agreed fields contradicted each other (for example a fellow present with no procedure). | Always. |
| `none` | No consensus: fewer than two models answered, or a model has not run on this note. Every field is empty. | Unless the row is a true empty. A note a model never read (too long to send, or the model call failed) is always flagged, whatever kind of note it is: it may hold study data nobody checked, so read it yourself. A note every model read is flagged when something suggests it documents a procedure (a procedure note type, upper endoscopy wording, or the one model that answered found one), and released unflagged when nothing does: the models were asked and the note refuted every claim, so the empty row is the answer for a note about something else. Review reasons says how the models came to give no answer, and How the models voted says which case the row is. |

## 5. Every review reason

### 5.1 Row-level reasons (column H)

| Reason, as written | What it means | What to do |
| --- | --- | --- |
| `<model> has not analysed this note; run --analyze again` | The run did not finish, so there is nothing to review yet. | Ask for the run to be completed, then regenerate the file. |
| `note too long for the context window, so no model read it in full; see each model's Failure reason` | The note exceeds what the models can read in one piece (about 18,000 characters), so it was not sent, or a model's own token count showed that the server had cut it: the row is empty because no model read the note in full, not because it holds nothing. | Read the note yourself. |
| `the model call failed for every model, so none read the note; see each model's Failure reason` | The server or a model returned an error for every model, so the note was never analysed. | Ask for the models' results on this note to be cleared and the run resumed, or read the note yourself. |
| `no model gave an answer the note supports; see each model's Failure reason` | Every model read the note and failed: each answer it gave was contradicted by the note's text. Raised only when something suggests the note documents a procedure (section 4); on a note of another type with no procedure wording the row is released instead. | Read the note; the Failure reason of each model shows what it tried to claim. |
| `no model answered: <models> failed on this note (<why>) and <models> (<why>); see each model's Failure reason` | The models failed in different ways: not sent the note, a failed call, or an answer the note did not support. | As the three rows above, model by model. |
| `<models> failed on this note (<why>); see Failure reason` together with `only <model> gave an answer the note supports, which is not a consensus; its own columns hold that answer` | One model answered; one answer is never written as consensus, so columns L to U are empty. The parenthesis says why the others gave none: "no answer the note supports", "the model call failed" or "not sent the note, too long for the context window". | Check that model's block against the note and decide. |
| `<model> failed on this note (<why>); the consensus rests on the other 2 models; see Failure reason` | Information only, added at the front of the reasons when the row is flagged for another reason, so you know how many models the answer rests on. | Nothing on its own. |
| `models disagree on <field> (majority answer given), <field> (no majority, left empty); see How the models voted` | The answering models split on the named fields. A majority answer was written where there was one; the field was left empty where there was none. | Read column X for each model's value, and the evidence quotes in the models' blocks; decide from the note. |
| `FellowPresent left empty: a fellow takes part in a procedure, and the consensus has none` | The per-field votes gave a fellow present with no procedure; the dependent field was emptied. | Decide from the note. |
| `DuodenalBiopsiesTaken and NumberOfDuodenalBiopsies left empty: duodenal biopsies need an upper endoscopy, and the consensus has none` | As above, for the biopsy fields. | Decide from the note. |
| `ScopeType left empty: the consensus has no upper endoscopy` | As above, for the scope. | Decide from the note. |
| `upper endoscopy documented but no scope name found` | The models agree on an upper endoscopy but none found a scope name the note states. | Look in the note for a brand or model the models missed; if there is none, the empty value is right. |
| `<field> is 1 but no model that gave it quoted the note, so <quote field> is empty` | An agreed TRUE without the sentence behind it. Only results stored before the program required the quote can produce this. | Find the sentence in the note, or correct the answer. |
| `note carries a teaching attestation but FellowPresent is 0` (or `is empty`) | The note has a Teaching Statement (or "was present and actively participated"), which means a trainee performed, but the consensus says no fellow. | Decide from the attestation in the note. Fellow present is the field the models most often miss. |
| `note carries a Staff Only or no-trainee line but FellowPresent is 1` (or `is empty`) | The note says Staff Only, "Fellow: None" or the like, but the consensus says a fellow was present. | Decide from the note. |
| `NumberOfDuodenalBiopsies <n> is the sum of the counts the quoted sentence gives for different parts of the duodenum; confirm the study counts them together` | The quoted sentence counts biopsies from two parts of the duodenum ("4 biopsies of the duodenum and 2 from the bulb") and the program wrote the sum. The study's examples confirm only the "6 including 2 from the bulb" form, so this reading waits for your confirmation; the value stays. | Confirm or correct the count. Once the reading is confirmed, this reason can be retired. |
| `NumberOfDuodenalBiopsies <n> is one of the counts the quoted sentence gives for different parts of the duodenum, which add up to <t>; the verifier now accepts only the sum on a new run` | The same sentence shape, from a run before the sum rule; a rerun would write the sum. | Decide which count the study wants. |

The check behind the two attestation reasons only reads the note; it never sets a value.

### 5.2 Model-level reasons (Review reasons in a model's block)

These describe how one model reached its answer. They do not flag the row.

| Reason, as written | What it means |
| --- | --- |
| `no answer the note supports; see Failure reason` | Every attempt was rejected because the note did not support it. |
| `the model call failed; see Failure reason` | The server or the model returned an error. |
| `note too long for the context window, so the model did not read it in full; see Failure reason` | The note was not sent at all, or it was sent and the server's token count showed it had been cut; Failure reason says which. |
| `accepted on attempt <n> after <k> rejected answer(s), repetition loops not counted; see Rejected attempts` | The model's first answers were rejected (a quote not in the note, an invented scope name, an unsupported count); the accepted answer passed every check, but only after correction. A retry caused by the model repeating itself is not counted. |
| `upper endoscopy documented but no scope name found` | This model's answer has an upper endoscopy and no scope. |
| `duodenal biopsies taken but no count stated` | This model's answer has biopsies taken and no count. |
| `note is about <n> prompt tokens, more than the <w>-token context window allows; the model may not have seen all of it` | Only on results from before the too-long rule existed: the note was sent although it may have been cut. |

Files from runs before this layout existed may say "see LLM_Error", "see LLM_Rejections" or
"see Agreement": those are the database names of Failure reason, Rejected attempts and How the
models voted.

### 5.3 Reading a Failure reason

A model's Failure reason is the message its last answer was rejected with. The common shapes:

| Failure reason begins | Meaning |
| --- | --- |
| `<field> is not a sentence from the note. ... The rejected value was '...'` | The model quoted a sentence that is not in the note (invented or paraphrased). |
| `<field> does not mention <an upper endoscopy / a colonoscopy / biopsies>` | The quote is about something else (a limb examination quoted for an endoscopy). |
| `<field> names an upper endoscopy but does not say it was carried out` | A bare mention: a history line, a plan, an instruction. |
| `<field> contains '<word>', so it reads as history, a plan, a recommendation or instructions` | The quote is about a past or future procedure, not this note's. |
| `ScopeType is not stated in the note` | The model named a scope the note does not name (often a brand added to a model number). |
| `NumberOfDuodenalBiopsies ... is not a number the quoted sentence states` | The count does not match the sentence. |
| `the note is too long for the model: about <n> prompt tokens ...; no model call was made.` | Not sent. |
| `the note is too long for the model: the server counted <n> prompt tokens and <m> answer tokens, more than the ... window holds ...; the answer was discarded.` | Sent, but the model's own token count showed the prompt did not fit together with the answer, so the model lost the instructions; the answer was discarded and the note counts as too long. |
| `the model repeated itself until Ollama stopped it` | A repetition loop on the last attempt. |
| `LLM call failed: ...` | The server or model failed; nothing about the note. |

## 6. Worked examples

The values below are invented to show the shapes; the model labels are the three study models.

**A row the study can use as it is.** G `FALSE`; H empty; I `unanimous`; J `3`; K "Upper
endoscopy with Olympus GIF-H190; 4 duodenal biopsies; fellow present"; L `TRUE`; M "Olympus
GIF-H190"; N "The patient's esophagus was easily intubated with an Olympus GIF-H190
endoscope."; O `TRUE`; P `4`; Q "4 biopsies were taken of the duodenum for histopathology.";
R `FALSE`; S empty; T `TRUE`; U "Teaching Statement: Dr. A was present and actively
participated in the entire procedure"; V "medgemma:27b@8192; gemma4:26b-a4b-it-q4_K_M@8192;
gpt-oss:20b@8192"; W empty; X "3 of 3 models answered; they agree on every field". Nothing to
decide.

**A flagged 2-against-1 split.** G `TRUE`; H "models disagree on FellowPresent (majority
answer given); see How the models voted; note carries a teaching attestation but FellowPresent
is 0"; I `majority`; J `3`; K "Upper endoscopy with GIF-H 190; 6 duodenal biopsies; no
fellow"; T `FALSE`; W "FellowPresent (majority answer given)"; X "3 of 3 models answered;
FellowPresent: 0 (medgemma:27b@8192, gpt-oss:20b@8192), 1 (gemma4:26b-a4b-it-q4_K_M@8192): 0
by 2 of 3". Two models said no fellow, one said yes, and the note carries a Teaching
Statement. Read the attestation in the note (column CB), or the Fellow evidence quote in the
second model's block, and decide.

**No consensus on a procedure note.** G `TRUE`; H "medgemma:27b@8192, gpt-oss:20b@8192 failed
on this note (no answer the note supports); see Failure reason; only
gemma4:26b-a4b-it-q4_K_M@8192 gave an answer the note supports, which is not a consensus; its
own columns hold that answer"; I `none`; J `1`; K "No consensus: 1 of 3 models answered"; L to
U empty; X "1 of 3 models answered (medgemma:27b@8192, gpt-oss:20b@8192 failed); no consensus
from one model; the note may document a procedure: a procedure note type, upper endoscopy
wording in the note, gemma4:26b-a4b-it-q4_K_M@8192 found a procedure". Read the second model's
block for its answer and the other two models' Failure reason for what they got wrong, then
decide from the note.

**No consensus, note too long for the window.** F `Progress Notes`; G `TRUE`; H "note too long for the
context window, so no model read it in full; see each model's Failure reason"; I `none`; J `0`;
K "No consensus: the note was too long for the context window"; L to U empty; X "0 of 3 models
answered (medgemma:27b@8192, gemma4:26b-a4b-it-q4_K_M@8192, gpt-oss:20b@8192 failed); no
consensus; nothing suggests the note documents a procedure, but medgemma:27b@8192,
gemma4:26b-a4b-it-q4_K_M@8192, gpt-oss:20b@8192 did not read it, so it is flagged". No model
read this note, so the empty row says nothing about it. Read it yourself; a progress note with
no procedure wording is usually quick to clear, but the program will not clear it for you,
because it cannot know what the note holds.

**No consensus, released.** F `Progress Notes`; G `FALSE`; I `none`; J `0`; K "No consensus: 0
of 3 models answered"; X "0 of 3 models answered (medgemma:27b@8192,
gemma4:26b-a4b-it-q4_K_M@8192, gpt-oss:20b@8192 failed); no consensus; not flagged: every model
read the note, and nothing suggests it documents a procedure: not a procedure note type, no
upper endoscopy wording in the note and no model found a procedure". Nothing to do: every
model read this note and each claimed a procedure the note's text refuted, and the note is a
progress note with no procedure wording, so the empty row is the answer.

## 7. Things to know

- **Empty is neither FALSE nor 0.** Empty means no answer: no consensus, a failed model, or a
  count the note does not state. FALSE means the models agreed the thing did not happen; 0 in
  Number of duodenal biopsies means none were taken.
- **A cell that begins with a space.** Text that starts with =, +, - or @ (a quote such as
  "- 4 biopsies were taken") is written with one leading space, because a spreadsheet would
  otherwise read it as a formula. The database holds the text without the space.
- **An empty consensus row is flagged unless it is a true empty.** A row on which fewer than
  two models answered has every field empty. It is G TRUE when a model never read the note
  (too long to send, or the model call failed): the note may hold study data nobody checked,
  so read it yourself. It is G FALSE only when every model read the note, no model found a
  procedure, and the note is of another type with no upper endoscopy wording: the models were
  asked, the note refuted every claim, and the empty row is the answer for a note about
  something else. On any unflagged row the other empty cells are the agreed ones: a count the
  note does not state, no quote behind a FALSE, no scope where there was no upper endoscopy.
- **"Duodenal biopsies taken" TRUE with an empty count means taken, count not stated.** The
  study accepts that; it is not flagged. The Summary spells it out.
- **Two models agreeing on "no scope named" is a consensus.** Scope type is then empty, and the
  row is flagged only because an upper endoscopy without a scope name leaves a study field
  open.
- **A model's "Failed" TRUE is not a program error.** It is the program refusing an answer the
  note does not support. Many notes in a mixed pool are not procedure notes, and a model will
  try to answer them anyway; the checks turn those guesses into recorded failures. Expect many
  on progress notes, consults and discharge documents.
- **A model's "Needs review" TRUE with column G FALSE is normal.** It says that model needed a
  correction or left a field open; the row is judged on whether the models agree.
- **The model label is the model's name and its text window.** `medgemma:27b@8192` is the
  model `medgemma:27b` reading up to 8,192 tokens at a time. Two models, or one model with a
  different window, never share a column.
- **The column letters in this guide assume the three study models in their run order and
  no columns inserted.** A run with a different number of models shifts every letter after X.
- **The two files are written once, together.** To refresh them after a further run, move
  both aside and run the program's analysis step again; the consensus is rebuilt from every
  model's stored answers.
- **Every row and every cell is protected health information**, including the quotes and the
  raw model responses.

## 8. Where the rules come from

The field definitions are the instructions the models were given (`llm_analysis.py`, the
`SYSTEM_PROMPT`), written from the study's review comments on ten example notes: only the
upper endoscope counts as the scope, bulb biopsies are duodenal and counted once, "taken but
no count stated" must stay distinct from "none taken", and fellow presence is read from the
attestation. The checks of an answer against the note are `verify_against_note` in the same
file; the review rules are `review.py` (one model) and `consensus.py` (the consensus); the
layout of this file is `export.py`. The README's sections on `consensus.py` and `review.py`
give the rules in full.

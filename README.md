# Clinical note extraction study with a local LLM

## Overview

This project is a **local, offline-first clinical note analysis pipeline** that extracts structured data from endoscopy procedure notes with a **local Large Language Model (LLM via Ollama)**.

Notes are loaded from a CSV into a local SQLite database, each note is sent once to each of the three default models (`medgemma:27b`, `gemma4:26b-a4b-it-q4_K_M` and `gpt-oss:20b`, one model at a time), every model's answers are stored next to the note in columns labelled with the model name and context window (`medgemma:27b@8192` and so on), the three answers are consolidated into a cautious consensus block labelled `(consensus)` (a value only where at least two models agree, a review flag wherever they do not), and the results are exported to CSV with the consensus first and every model's answers beside it.

> **Key design goals**
>
> - Run entirely on a hospital workstation
> - Never transmit PHI outside the machine
> - Provide auditability and error isolation
> - Prevent duplicate processing and data overwrites

> **History.** Earlier versions ran a rule-based spaCy extractor alongside the LLM and kept only the answers the two agreed on. That cross-check was built for a small early model that sometimes hallucinated; with the current model it discarded more good answers than bad ones, so it was removed on 2026-10-13. The LLM is now the only extractor.

---

## High-Level Architecture

```
CSV (Clinical Notes)
        │
        ▼
SQLite Database (notes)
        │
        ▼
┌───────────────────────────────┐
│ data_worker                   │
│  ├─ LLM analysis (Ollama)     │
│  └─ Store results             │
└───────────────────────────────┘
        │
        ▼
┌───────────────────────────────┐
│ consensus                     │
│  └─ Consolidate the models'   │
│     answers per note          │
└───────────────────────────────┘
        │
        ▼
CSV Export (output/results.csv)
```

---

## Windows Setup Instructions

### 1. Prerequisites

| Software          | Purpose                |
| ----------------- | ---------------------- |
| **Windows 10/11** | Supported OS           |
| **Python 3.12**   | Core runtime           |
| **PowerShell**    | Environment management |
| **Ollama**        | Local LLM runtime      |

---

### 2. Install Python

Download Python from:
https://www.python.org/downloads/windows/

During installation:

- ✅ Check **"Add Python to PATH"**
- ✅ Install `pip`

Verify:

```powershell
python --version
```

---

### 3. Clone or Copy Project

Place the project in a local directory such as:

```
C:\Users\<you>\Documents\nlp-study
```

---

### 4. PowerShell Execution Policy (One-Time)

If you see:

```
.venv\Scripts\Activate.ps1 cannot be loaded
```

Run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

### 5. Create & Activate Virtual Environment

On Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

On Mac or Linux:

```
python -m venv .venv
source .venv/bin/activate
```

---

### 6. Install Dependencies

```powershell
pip install -r requirements.txt
```

#### Dependency Breakdown

| Package  | Purpose                     |
| -------- | --------------------------- |
| `ollama` | Interface to local LLM      |
| `rich`   | Progress bar                |

---

### 7. Install and Start Ollama

Download Ollama:
https://ollama.com

Install Ollama on Linux or Mac:

```
curl -fsSL https://ollama.com/install.sh | sh
```

Pull the three models (about 49 GB in all):

```powershell
ollama pull medgemma:27b
ollama pull gemma4:26b-a4b-it-q4_K_M
ollama pull gpt-oss:20b
```

Every note goes through all three, one model at a time, and each model's answers land in
their own columns so the three can be compared:

- `medgemma:27b`: MedGemma 27B (Gemma 3 based, Q4_K_M, about 17.4 GB), called with thinking off (`think=False`).
- `gemma4:26b-a4b-it-q4_K_M`: Gemma 4 26B mixture of experts (3.8B active, Q4_K_M, about 18.0 GB), thinking off. This exact tag matters: the default `gemma4:26b` tag does not load on Ollama 0.24.0.
- `gpt-oss:20b`: OpenAI gpt-oss 20B mixture of experts (3.6B active, MXFP4, about 13.8 GB), a reasoning model called with `think="low"` (a boolean `think=False` makes it return nothing).

All three builds run on Ollama 0.24.0, the last release that can use the GPUs on the Argon
cluster (NVIDIA driver 535), and each fits a 24 GB card on its own at the 8192-token window.
To match Argon exactly, install Ollama 0.24.0:
`curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.24.0 sh`.

For a workstation test without the large models, `--location local` runs the same pipeline
with three small stand-ins instead (about 11 GB in all; these need a current Ollama, not 0.24.0):

```powershell
ollama pull gemma3:4b
ollama pull gemma4:e2b
ollama pull qwen3.5:4b
```

Ollama runs locally and does **not** require internet access after model download.

---

## Running the Program

### Basic Entry Point

```powershell
python app.py
```

With no options it prints what the program needs (the packages, Ollama and the model, the CSV header, where files go), the typical commands, and a status check of the current directory: whether Ollama is reachable and the model is pulled, how many notes `data.db` holds and how many are still pending, and whether `output/results.csv` already exists. There are four options, which can be combined:

| Option         | Effect                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------- |
| `--csv FILE`   | Ingest the notes in `FILE` into `data.db` (duplicates skipped); reports how many were added |
| `--analyze`    | Run each model on every note that has no result for it yet, rebuild the consensus of the models for every note, then write `output/results.csv` |
| `--clean`      | Delete `data.db` (and any SQLite journal files) and `output/results.csv` from earlier runs, before anything else. Discards every stored result |
| `--location`   | Which model set to run: `server` (the default) is the three study models; `local` is three small stand-ins (`gemma3:4b`, `gemma4:e2b`, `qwen3.5:4b`) for a workstation test. Either way each model gets its own labelled columns |

---

### Ingest CSV

```powershell
python app.py --csv path\\to\\endoscopy_notes.csv
```

**Required CSV columns:**

- MRN
- Encounter
- NoteCsnID
- NoteDate
- NoteType
- Note

Duplicates are automatically skipped.

---

### Run Full Analysis Pipeline

```powershell
python app.py --analyze --csv path\\to\\endoscopy_notes.csv
```

```bash
python app.py --analyze --csv path/to/endoscopy_notes.csv
```

For example, if the notes.csv is placed inside the same folder as the source code, the full analysis pipeline is:

```bash
python app.py --analyze --csv ./notes.csv
```

This will:

1. Ingest notes
2. Run the LLM extraction on every note without a result, one model at a time
3. Store each result, with the raw model response and per-note debugging columns, in the database
4. Build the consensus of the models for every note (see `consensus.py` below)
5. Export `output/results.csv`

Running `python app.py --analyze` again resumes: notes that already have a result for a model are skipped for that model. The consensus is rebuilt for every note on each run, so it always reflects every model's stored answers.

While it runs, the program reports what it is doing:

```
[15:48:30] Model: medgemma:27b@8192 (1 of 3)
[15:48:30] 330 note(s) in the database, 0 already analysed with medgemma:27b@8192, 330 to process.
[15:49:26] ok     note 1 (1/330) 56.2s, attempt 1: scope='Olympus H190' egd=1 colonoscopy=0 duodenal_biopsies=4 taken=1 fellow=1
[15:50:40] retry  note 2: attempt 1/5 did not return valid JSON
[15:51:55] ok     note 2 (2/330) 149.1s, attempt 2: scope=None egd=0 colonoscopy=0 duodenal_biopsies=0 taken=0 fellow=0
...
[16:02:10] FAILED note 7 (7/330) after 5 attempt(s), 301.4s: the output was not valid JSON (Expecting value: line 1 column 1 (char 0)).
[16:08:33] progress 10/330 done, 1 failed, 68.1s per note, 0:11:21 elapsed, about 6:03:12 remaining
...
[21:51:02] Finished 330 note(s) with medgemma:27b@8192 in 6:02:32: 326 ok, 4 failed, 31 flagged for manual review, 65.9s per note.
[21:51:02] Failed note id(s): 7, 118, 240, 301
[21:51:02] Model: gemma4:26b-a4b-it-q4_K_M@8192 (2 of 3)
[21:51:02] 330 note(s) in the database, 0 already analysed with gemma4:26b-a4b-it-q4_K_M@8192, 330 to process.
...
```

Only the structured fields are logged, never note text or the model's prose; the evidence sentences and raw responses are in the database and the export.

---

### Inspecting the Data

The program never prints note text to the terminal. Use `output/results.csv`, or query `data.db` directly. The result columns carry the model label (`LLM_Failed (medgemma:27b@8192)`), so they must be double-quoted in SQL, for example:

```bash
sqlite3 data.db 'select count(*) notes, (select count(*) from results where "LLM_Failed (medgemma:27b@8192)" is not null) done, (select count(*) from results where "LLM_Failed (medgemma:27b@8192)"=1) failed from notes;'
```

---

### Troubleshooting:

The `notes.csv` file may replace column names with underscores or dashes depending on client export/import differences in platforms. The program then stops with a message that names the columns it could not find and the header it did find, for example:

```
Error: CSV is missing the column(s) MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note. Required (case-sensitive): MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note. Header found: id, mrn, encounter, note_csn_id, note_date, note_type, note
```

Rename the header so the six required names match exactly (extra columns such as `id` are ignored):

```
id,MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note
```

A header found as `\ufeffMRN` means the file starts with a UTF-8 byte-order mark; save it as plain UTF-8.

---

## Detailed Component Walkthrough

### `app.py`

**Purpose:** Application entry point

- Delegates execution to the CLI
- No business logic

HIPAA:

- No data access
- Safe bootstrap layer

---

### `cli.py`

**Purpose:** Command-line interface & orchestration

Responsibilities:

- Argument parsing
- Safe ordering of pipeline steps (ingest, then analyze, then export)

HIPAA:

- No network access
- Controls execution boundaries
- Prevents accidental exports

---

### `database.py`

**Purpose:** Local persistence, data integrity and CSV export

Tables:

- `notes` – raw clinical notes
- `results` – one row per note, with one block of columns per model, each column named `<field> (<model>@<context>)`, for example `ScopeType (medgemma:27b@8192)`: the ten extracted fields, the review columns `ManualReviewRequired` and `ManualReviewReason`, and the debugging columns `LLM_Failed`, `LLM_Attempts`, `LLM_Seconds`, `LLM_Error`, `LLM_Rejections` and `RawResponse`. The label is the model's full name and the context window it was called with, so two models, or one model at two window sizes, never share a column. The same row also holds the consensus block, labelled `(consensus)`: the ten fields the models agree on, its own `ManualReviewRequired` and `ManualReviewReason`, and `Outcome`, `Models` and `Agreement` (see `consensus.py` below)

A `data.db` from before the columns were labelled is converted on startup: its unlabelled columns become the first model's block, since those answers came from that model. A column a block lacks is added on startup (existing rows get an empty value, except the review columns, which are computed for them), a model that has not yet run on the database gets a whole new block, and so does the consensus.

Safeguards:

- Duplicate detection on ingest
- Idempotent inserts
- Write-once exports

HIPAA Compliance:

- SQLite stored locally
- No encryption bypasses OS security
- No outbound connections
- Full audit trail retained

---

### `data_worker.py`

**Purpose:** Core processing engine

Workflow, one model at a time (so Ollama holds a single model loaded), per note:

1. Skip if this model already has a result for it
2. Run LLM analysis (with retries)
3. Store the result atomically under the model's label

Features:

- One log line per note (time taken, attempts, and the extracted flags), a running summary every ten notes with the projected time remaining, and a final summary that lists the failed note ids
- Progress bar with ETA when run in a terminal
- Retry logic for malformed LLM output and for answers the note does not support; every retry is logged (without note text) and every rejection reason is kept in `LLM_Rejections`
- Graceful failure isolation: a failed note is recorded with `LLM_Failed = 1`, its attempt count and the error message, and the run continues. A note the model cannot answer in a way the note supports is a failure, not a guess

HIPAA:

- Processes one note at a time in memory
- No caching outside DB
- Raw LLM output retained for audit

---

### `review.py`

**Purpose:** Decides which rows a human should look at before the study uses them

`manual_review` sets `ManualReviewRequired` to `true`, with every trigger listed in `ManualReviewReason`, when:

- the note failed (no answer the note supports, or the model call failed)
- the answer was accepted only after an attempt was rejected for its content (a quote not in the note, an invented scope name, an unsupported count). A retry after a repetition loop is not counted: the model repeating itself says nothing about the answer it then gave
- an upper endoscopy is documented but no scope name was found
- duodenal biopsies were taken but the note states no count
- the note was too long for the context window and was not sent to the model (see below)

Everything else is `false`. The flag never changes an answer; it only says where to look.

---

### `consensus.py`

**Purpose:** Consolidates the models' answers for each note into one consensus block

After every model has run, `consolidate` builds a consensus for every note from the answers of the run's models and stores it under the label `consensus` (`ScopeType (consensus)` and so on), rebuilding it in full on every `--analyze`. The rules prefer an empty value to a wrong one:

- A field gets a value only when at least two models gave an answer the note supports (`LLM_Failed` 0) and they agree on it. The models vote on `Endoscopy`, `Colonoscopy`, `ScopeType`, `DuodenalBiopsiesTaken`, `NumberOfDuodenalBiopsies` and `FellowPresent`; a null count ("taken, number not stated") and a null scope are answers the models can agree on.
- A strict majority (2 of 3) settles a disagreement, and the note is flagged for manual review. A field with no majority (one against one, or three different answers) is left empty, and the note is flagged.
- Scope names that differ only in form (`Olympus GIF-H190`, `GIF-H190`, `GIF-H 190`) count as the same scope: the form most models gave is kept (the fullest on a tie) and the difference is noted in `Agreement` without a flag. `Olympus gastroscope` against `Olympus GIF-H190` is a disagreement.
- The agreed fields must agree with each other, as one model's answer must: `FellowPresent` 1 without a procedure, duodenal biopsies without an upper endoscopy, or a scope name without one, is left empty.
- Each `...Information` column is the quote of a model that gave the agreed answer: the quote most of them gave, the first model's on a tie, and for the biopsy sentence one from a model that also gave the agreed count. It is null when the flag is not 1.
- `ManualReviewRequired (consensus)` is `true`, with every trigger in `ManualReviewReason (consensus)`, when a model has not analysed the note; when no model, or only one, gave an answer the note supports (one answer is not a consensus, so the fields stay empty and the reason points at that model's columns); when a model failed (the consensus rests on the others); when the models disagreed on a field; and when the agreed answer leaves a study field open (an upper endoscopy without a scope name, biopsies taken without a count). A model's own `ManualReviewRequired` does not carry over: it describes how that model reached its answer, and the consensus is judged on whether the models agree.
- `Outcome` is `unanimous` (every model answered and they agree on every field), `majority` (every field has a value, but one was settled by a majority or a model gave no answer), `partial` (a field was left empty) or `none` (no consensus). `Models` lists the models consulted, marking those that failed or have not run; `Agreement` says how many answered and how they voted on every field they did not simply agree on. Neither holds note text, only scope names, counts and model labels.

The consensus needs at least two models: a run with one model gets an empty block with `Outcome` `none` on every note.

**Notes too long for the context window are not sent to the model.** Ollama does not refuse an over-long prompt: it keeps a few leading tokens and the tail that fits, silently dropping the instructions and the start of the note, so any answer would be a guess. `data_worker` checks the length first (`llm_analysis.note_fits_context`, a conservative estimate of 3 characters per token plus the prompt overhead) and records such a note with `LLM_Failed = 1`, `LLM_Attempts = 0` and the reason in `LLM_Error`, without a model call. At the default 8192-token window the cut is about 18,000 characters of note; 7 of the 275 sample notes and 111 of the 5,000 corpus notes (about 2%) are longer and are recorded as failed. 8192 is the window at which medgemma:27b and its KV cache fit on Argon's A10; a larger `NLP_CONTEXT_LENGTH` raises the cut but spills the model to the CPU there.

---

### `llm_analysis.py`

**Purpose:** Semantic extraction using local LLM

The system prompt encodes the study's definitions (see `SYSTEM_PROMPT`): only the upper endoscope counts as the scope, bulb biopsies are duodenal and counted once, "biopsies taken but no count stated" is recorded as a null count rather than 0, fellow presence is read from the Teaching Statement or Staff Only attestation rather than the word "fellow", and only a procedure performed in the note (not history, plans or instructions) counts.

Key Safeguards:

- Strict JSON schema enforcement
- Type validation, with true/false and numeric strings coerced to integers
- JSON repair layer
- Every answer is checked against the note before it is accepted (`verify_against_note`).
  Accuracy is preferred over completeness: an answer the note does not support is rejected
  and retried, and a note that never gets a supported answer is recorded as failed with the
  reason rather than with a guess. The checks are:
  - each `...Information` quote must occur in the note (compared ignoring case, spacing and
    punctuation), so the model cannot paraphrase, stitch sentences together, or copy an
    example sentence from the prompt
  - every 1 needs a quote, and the quote must be about the right thing: an endoscopy or
    colonoscopy quote must mention one, must say it was carried out (a scope passed, advanced
    or withdrawn, something examined, a findings line) rather than just name it, and must not
    read as history, a plan or instructions;
    a duodenal-biopsy quote must mention biopsies and name the duodenum (bulb, D1 to D3,
    first to third portion, or "to rule out celiac disease") and must not say none were
    taken; a fellow quote must mention a trainee or a teaching attestation and must not be
    a Staff Only attestation
  - the flags must agree: no fellow and no duodenal biopsies without a procedure, no
    `ScopeType` without `Endoscopy = 1`
  - `ScopeType` must be a name the note states (brand and model may be joined when the
    note gives them apart), must look like an endoscope, and must not be a colonoscope
  - `NumberOfDuodenalBiopsies` must be a number the quoted sentence states, or the sum of
    its numbers when they are all duodenal sites ("4 from the second portion and 2 from the
    bulb" is 6); "6 including 2 from the bulb" supports 6, not 8; a count above 20 is
    rejected as some other number from the note (an eosinophil count, a CPT code)
- Output constrained to a JSON schema (`format=`): always a bare object with exactly the study
  fields, 0/1 flags, and text fields capped at 1000 characters
- Greedy decoding (temperature 0) so a note gives the same answer on every run
- Retries (up to 5 attempts) when the output is invalid JSON, fails validation, fails a check
  against the note, or the model loops (a text field reaches the 1000-character cap, or Ollama
  aborts the generation at its token repeat limit); the
  retry prompt says why the previous answer was rejected, and retries sample at temperature
  0.8 with a repeat penalty of 1.1 and the attempt number as the seed, since a greedy retry
  would repeat the loop
- Output capped at 2048 tokens (`num_predict`); context window `NLP_CONTEXT_LENGTH` tokens
  (default 8192, `num_ctx`) on every call, the same as an Argon job. 8192 is what medgemma:27b
  fits in on the A10 with its KV cache; anything larger spills to the CPU. Notes that do not
  fit the window are failed without a model call rather than silently truncated

Model:

- `MODELS` in `llm_analysis.py` lists the models every note is sent to, in run order, each with its context window and `think` setting: `medgemma:27b` (`LOCAL_MODEL`, MedGemma 27B, thinking off), `gemma4:26b-a4b-it-q4_K_M` (Gemma 4 26B A4B, thinking off) and `gpt-oss:20b` (reasoning at `"low"`). All three use the 8192-token window, so they see the same notes, and their columns are labelled `medgemma:27b@8192`, `gemma4:26b-a4b-it-q4_K_M@8192` and `gpt-oss:20b@8192`. To change the list, edit that block (one `ModelSpec` per line, the name as a quoted literal: `setup.sh` reads it) and pull the models. `LOCAL_MODELS` is the small set that `--location local` runs instead (`gemma3:4b`, `gemma4:e2b`, `qwen3.5:4b`, all with thinking off and the same window), for exercising the pipeline on a workstation; its columns are labelled `gemma3:4b@8192` and so on, so a local test never mixes with study results. Study runs use the default, `--location server`

HIPAA:

- Ollama runs locally
- No API calls
- No telemetry
- No prompt logging outside SQLite

---

## HIPAA Compliance Summary

| Requirement            | Implementation                |
| ---------------------- | ----------------------------- |
| Local processing       | ✅ All compute on workstation |
| No PHI exfiltration    | ✅ No network calls           |
| Auditability           | ✅ Raw + structured storage   |
| Minimal exposure       | ✅ Note text is never printed |
| Data integrity         | ✅ Idempotent inserts         |

⚠️ **Operational Notes**

- Ensure workstation disk encryption is enabled
- Restrict OS-level user access
- Treat CSV exports as PHI

---

## Output Files

Generated in `/output`:

- `results.csv` – one row per note

Columns, in order: the note identifiers (`id`, `mrn`, `encounter`, `note_csn_id`, `note_date`, `note_type`); then the consensus block, every column of it named `<field> (consensus)`: the ten fields (`ScopeType`, `Colonoscopy`, `ColonoscopyInformation`, `Endoscopy`, `EndoscopyInformation`, `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken`, `DuodenalBiopsiesInformation`, `FellowPresent`, `FellowInformation`), the review columns (`ManualReviewRequired`, `ManualReviewReason`) and `Outcome`, `Models`, `Agreement`; then one block per model, every column of it named `<field> (<model>@<context>)`, for example `ScopeType (medgemma:27b@8192)`: the same ten fields, the review columns and the debugging columns (`LLM_Failed`, `LLM_Attempts`, `LLM_Seconds`, `LLM_Error`, `LLM_Rejections`, `RawResponse`); and the note text last. The label is the model's full name and the context window it was called with, so answers from different models (or the same model at another window size) are never mixed.

The consensus block is the study's answer; the model blocks are the evidence behind it:

- A consensus field is filled only where at least two models agree (a strict majority on a disagreement); otherwise it is empty. `ManualReviewRequired (consensus)` is `true` wherever a model failed, has not run or disagreed, where only one model answered, or where the agreed answer leaves a field open; `ManualReviewReason (consensus)` lists why, and `Agreement (consensus)` shows every model's value for each disputed field. Filter on `ManualReviewRequired (consensus)` to get the review queue, and on `Outcome (consensus)` = `unanimous` for the rows every model agreed on. See `consensus.py` above for the rules.

Within each model's block:

- `ManualReviewRequired` is `true` when a human should look at the row before the study uses it, else `false`; `ManualReviewReason` lists why (see `review.py` above). Filter the export on this column to get the review queue.

- `LLM_Failed` is 1 when no attempt produced an answer the note supports (or the model call failed). The ten extracted fields are then empty: the note is skipped, not guessed.
- `LLM_Error` says why the note failed: the last attempt's rejection, naming the field and the rejected value (for example `ScopeType is not stated in the note ... The rejected value was 'Olympus gastroscope'`).
- `LLM_Rejections` lists every rejected attempt, one per line, for failed and accepted notes alike, so an answer accepted on the third attempt shows what the first two got wrong.
- `RawResponse` is the model text of the accepted attempt, or of the last rejected attempt when the note failed.

Expect failures. Many notes in a mixed pool are not procedure notes, and a small model will try to answer them anyway; the checks turn those guesses into failures with a stated reason instead of wrong rows.

The export is **write-once**: an existing `results.csv` is never overwritten. Move it aside and run `--analyze` again to regenerate it from the database.

---

## Disclaimer

This software assists with data extraction and research workflows.
It **does not replace clinical judgment** and must be validated per institutional policy.

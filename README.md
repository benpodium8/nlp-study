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
| `rich`   | Live panel and log lines    |

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

While it runs, the program reports what it is doing in two layers.

**The log.** Timestamped lines, the same whether stdout is a terminal, a pipe or an SGE job log, so `tail -f` on a job log always shows where the run is:

- a run header: the database and its note count, the Ollama version and address, each model with its parameter count, quantization and size, the steps to come, and the work left per model (pending notes, how many of them are too long to send, and the model's time per note so far when it has run before);
- a `== Step n of m ==` marker per phase; after an ingest, the note types read and how many of the new notes exceed the context window;
- per model: its details, what is pending, and a `load` line once it is in memory, with how long that took and where the server put it (`100% GPU`, or a CPU share: the first thing to look at when a model runs slowly);
- one `ok` or `FAILED` line per note with the time taken, the attempts, Ollama's own prompt and answer token counts with the generation speed, the structured fields, and the review reason when the row is flagged; a `retry` line with the reason and the attempt's token counts for every rejected attempt;
- a `progress` line every 10 notes (done, ok, failed, flagged, rejected attempts, seconds per note overall and over the last 10, elapsed, the time left for this model and for the rest of the run) followed by a `fields` line with the tally of the fields extracted so far and the most common rejection reasons;
- when stdout is not a terminal, a `...` heartbeat line every 60 seconds (`NLP_HEARTBEAT_SECONDS`) while a model call is in progress: whether the model is still reading the prompt or already answering, how many tokens it has produced at what speed, and how long the attempt and the note have taken;
- a `Finished` summary per model with the failed ids and the review and rejection reasons tallied; the consensus summary (outcomes, the fields the models disputed, each model's agreement with the consensus, the review reasons); the export line with rows, columns and size; and two closing tables of every model's stored results and field tallies, the outcome, and what to do next.

**The live panel.** In a terminal, a panel at the bottom of the screen (refreshed four times a second, with the log lines scrolling above it) shows the note and attempt in progress, what the model is doing right now (waiting for the first token while the server reads the prompt, thinking, or answering, with the token count and the tokens per second over the last five seconds; its spinner turns faster the more tokens a second arrive, and crawls while none do), the progress bar, the running totals and rates, the time left for the model and for the run, and the field tally. `NLP_PLAIN=1` turns the panel off and gives the heartbeat lines instead.

A workstation test (`--location local`, three small models, three notes, interrupted once and resumed) looks like this in a log:

```
[10:55:21] database  /home/ben/nlp-study/data.db · 0 notes
[10:55:21] Ollama    version 0.40.3 at http://127.0.0.1:11434
[10:55:21] models    3 (--location local): gemma3:4b@8192 (3.9B parameters, Q4_K_M, 3.4 GB on disk) · gemma4:e2b@8192 (4.6B parameters, Q4_K_M, 4.6 GB on disk) · qwen3.5:4b@8192 (4.2B parameters, Q4_K_M, 3.3 GB on disk)
[10:55:21] steps     1. ingest three.csv · 2. analyse with 3 models · 3. build the consensus · 4. write output/results.csv
[10:55:21] feedback  one line per note, one per rejected attempt, a progress summary every 10 notes, a heartbeat line every 5s during a long model call (NLP_HEARTBEAT_SECONDS)
[10:55:21] == Step 1 of 4: Ingest three.csv ==
[10:55:21] ingest    3/3 rows checked against the database: 3 new, 0 duplicates
[10:55:21] Read 3 row(s) from three.csv in 0.0s: 3 new note(s) added, 0 duplicate(s) skipped.
[10:55:21] types     Procedures 3
[10:55:21] == Step 2 of 4: Analyse with 3 models ==
[10:55:21] work      9 model calls at least: gemma3:4b@8192 3 pending · gemma4:e2b@8192 3 pending · qwen3.5:4b@8192 3 pending
[10:55:21] == Analyse · model 1 of 3: gemma3:4b@8192 ==
[10:55:21] model     gemma3:4b: 3.9B parameters, Q4_K_M, 3.4 GB on disk; context 8,192 tokens; think=False
[10:55:21] notes     3 note(s) in the database, 0 already analysed with gemma3:4b@8192, 3 to process.
[10:55:21] load      loading gemma3:4b into memory, context 8,192 tokens ...
[10:55:21] load      loaded in 0.0s: 100% GPU, 3.0 GB loaded
[10:55:27] ...    note 1 (1/3) attempt 1/5: answering · 9 tokens · 4.0 tok/s · 0:00:06 on this attempt, 0:00:06 on this note; 0 done, 0 failed, 0:00:06 elapsed
[10:55:42] retry  note 1 (1/3) attempt 1/5 rejected after 20.5s, 2,094 prompt + 154 answer tokens: EndoscopyInformation does not mention an upper endoscopy. Quote the sentence that documents the procedure being performed in this note, or answer 0.
...
[10:57:37] FAILED note 1 (1/3) after 5 attempts, 28.6s, 10,762 prompt + 686 answer tokens, 24.9 tok/s: EndoscopyInformation does not mention an upper endoscopy. Quote the sentence that documents the procedure being performed in this note, or answer 0.
[10:57:43] ...    note 2 (2/3) attempt 1/5: answering · 82 tokens · 25.0 tok/s · 0:00:05 on this attempt, 0:00:05 on this note; 1 done, 1 failed, 0:00:34 elapsed, about 0:00:52 left for this model
[10:57:54] Interrupted. gemma3:4b@8192 had stored 1 of 3 notes in this pass; note 2 was in progress and is not stored. Every stored result is kept; resume with: python app.py --analyze --location local
```

The resumed run (`python app.py --analyze --location local`) continues with note 2 and goes on to the other models, the consensus and the export:

```
[10:57:54] == Analyse · model 1 of 3: gemma3:4b@8192 ==
[10:57:54] notes     3 note(s) in the database, 1 already analysed with gemma3:4b@8192, 2 to process.
...
[10:58:38] ok     note 3 (2/2) 7.2s, attempt 2, 5,296 prompt + 375 answer tokens, 63.4 tok/s: scope='GIF-H 190' egd=1 colonoscopy=0 duodenal_biopsies=4 taken=1 fellow=0 review: accepted on attempt 2 after 1 rejected answer(s), repetition loops not counted; see LLM_Rejections
[10:58:38] Finished 2 notes with gemma3:4b@8192 in 0:00:44: 1 ok, 1 failed, 2 flagged for manual review, 6 rejected attempts; 22.2s per note (median 37.1s); 16,421 prompt and 1,727 answer tokens, 47.1 tok/s.
[10:58:38] fields   egd 1 · colonoscopy 0 · duodenal biopsies 1 (count stated 1) · fellow 0 · scope named 1
[10:58:38] Failed note id(s): 2
[10:58:38] Review reasons: no answer the note supports (1), accepted only after a rejected answer (1)
[10:58:38] Rejection reasons: FellowInformation is not a sentence from the note (4), the model repeated itself until Ollama stopped it (1), ...
...
[10:58:46] == Analyse · model 3 of 3: qwen3.5:4b@8192 ==
[10:58:46] load      loading qwen3.5:4b into memory, context 8,192 tokens ...
[10:59:01] load      loaded in 14.3s: 94%/6% CPU/GPU, 3.2 GB loaded
[10:59:06] ...    note 1 (1/3) attempt 1/5: waiting for the first token (the server is reading a prompt of about 2,267 tokens) · 0:00:05 on this attempt, 0:00:05 on this note; 0 done, 0 failed, 0:00:20 elapsed
[10:59:16] ok     note 1 (1/3) 15.4s, attempt 1, 2,211 prompt + 88 answer tokens, 10.4 tok/s: scope=None egd=0 colonoscopy=0 duodenal_biopsies=0 taken=0 fellow=0
...
[11:00:10] == Step 2 of 3: Build the consensus ==
[11:00:10] Consensus of 3 model(s) built for 3 note(s) in 0.0s: 1 unanimous, 2 by majority, 0 partial, 0 without consensus; 2 flagged for manual review.
[11:00:10] Agreement with the consensus, over the field votes where a consensus value exists: gemma3:4b@8192 100.0% (6 of 6); gemma4:e2b@8192 100.0% (18 of 18); qwen3.5:4b@8192 100.0% (18 of 18).
[11:00:10] Review reasons: a model failed on the note 2.
[11:00:10] == Step 3 of 3: Write output/results.csv ==
[11:00:10] Wrote 3 row(s) and 76 columns to output/results.csv (0.0 MB) in 0.0s.
Results stored per model (every run so far)
model           | analysed | ok | failed | flagged | rejected attempts | s/note | median | model time
----------------+----------+----+--------+---------+-------------------+--------+--------+-----------
gemma3:4b@8192  |        3 |  1 |      2 |       3 |                11 |   24.3 |   28.6 |    0:01:13
gemma4:e2b@8192 |        3 |  3 |      0 |       0 |                 0 |    1.7 |    1.6 |    0:00:05
qwen3.5:4b@8192 |        3 |  3 |      0 |       1 |                 1 |   23.1 |   20.4 |    0:01:09

Notes answered with 1 (or a scope name) per model
model           | egd | colonoscopy | duodenal biopsies | count stated | fellow | scope named
----------------+-----+-------------+-------------------+--------------+--------+------------
gemma3:4b@8192  |   1 |           0 |                 1 |            1 |      0 |           1
gemma4:e2b@8192 |   2 |           0 |                 2 |            2 |      0 |           2
qwen3.5:4b@8192 |   2 |           0 |                 2 |            2 |      0 |           2

[11:00:10] elapsed   0:02:16
[11:00:10] outcome   complete: 8 notes analysed in this run
[11:00:10] export    output/results.csv (write-once; move it aside to regenerate it)
[11:00:10] next      the review queue is every row with "ManualReviewRequired (consensus)" = true; "Outcome (consensus)" = unanimous marks the rows every model agreed on
```

The `load` line for `qwen3.5:4b` is the kind of thing to watch for: the server put 94% of it on the CPU and it ran at about 10 tokens per second, against 180 for `gemma4:e2b`, which sat entirely on the GPU.

Only the structured fields are logged, never note text or the model's prose; the evidence sentences and raw responses are in the database and the export. Rejection reasons are logged without the rejected value.

Before each model the program checks that Ollama is reachable (otherwise the run stops) and that the model is pulled (otherwise that model is skipped and its notes stay pending), and it stops a model's pass after three consecutive notes whose model call itself failed, since that means the server rather than the notes is the problem: the remaining notes stay pending for the next `--analyze`. The export is skipped when a run is incomplete, so a partial file never blocks the complete one (it is write-once). Ctrl-C stops the run cleanly: every stored result is kept, the note in progress is not stored, and `--analyze` resumes. Exit codes: 0 complete, 1 bad input, 2 incomplete (a model skipped or the run stopped early), 130 interrupted.

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
- Safe ordering of pipeline steps (ingest, then analyze, then consensus, then export), each reported as a numbered step with the run header, the work plan and the closing summary
- Exit codes: 0 complete, 1 bad input, 2 incomplete, 130 interrupted

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

- Checks before each model that Ollama is reachable and the model is pulled, then loads the model and reports where the server placed it (GPU or CPU share) and how long it took
- Streams every answer, so the reporter (`reporter.py`) can show what the model is doing while it works: one log line per note (time taken, attempts, prompt and answer tokens, speed, and the extracted flags), a running summary every ten notes with the projected time remaining for the model and the run, the tally of the fields so far, a final summary per model with the failed note ids and the review and rejection reasons, and in a terminal the live panel
- Retry logic for malformed LLM output and for answers the note does not support; every retry is logged (without note text) and every rejection reason is kept in `LLM_Rejections`
- Stops a model's pass after three consecutive notes whose model call failed (server down, model gone), leaving the rest pending, instead of recording every remaining note as failed
- Graceful failure isolation: a failed note is recorded with `LLM_Failed = 1`, its attempt count and the error message, and the run continues. A note the model cannot answer in a way the note supports is a failure, not a guess

HIPAA:

- Processes one note at a time in memory
- No caching outside DB
- Raw LLM output retained for audit

---

### `reporter.py`

**Purpose:** Everything the program shows while it runs

One `Reporter` receives every event from the CLI, the worker, the consensus and the export, and writes the timestamped log lines described under "Run Full Analysis Pipeline". When stdout is a terminal it also keeps the live panel (a `rich` live display, refreshed four times a second) fed from the streamed response of the note in progress: which attempt it is on, whether the server is still reading the prompt or the model is thinking or answering, tokens and tokens per second, the running totals, the time left for the model and the run, and the field tally. When stdout is not a terminal there is no panel; a heartbeat thread writes a `...` line every `NLP_HEARTBEAT_SECONDS` (default 60) while a call is in progress, so a job log always shows the program is alive and what it is waiting for. `NLP_PLAIN=1` forces the plain mode in a terminal.

HIPAA:

- Logs identifiers, counts, timings, token counts, the structured fields and rejection reasons without their values; never note text or model output

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
- `ManualReviewRequired (consensus)` is `true`, with every trigger in `ManualReviewReason (consensus)`, only where a human can improve the row:
  - the models disagreed on a field (a majority answer given, or no majority and the field left empty);
  - the agreed answer leaves a study field open: an upper endoscopy without a scope name. A null count two models agree on is "taken, count not stated", which the study accepts, and is not flagged;
  - the agreed duodenal count is the sum of two counts the quoted sentence gives for different parts of the duodenum ("4 biopsies of the duodenum and 2 from the bulb" is written as 6). The verifier reads such a sentence as its sum, as prompt rule 5 says, but the researcher's examples confirm only the part-of form ("6 including 2 from the bulb" is 6), so the row is raised until that reading is confirmed; the value stays;
  - the note's own attestation contradicts the agreed `FellowPresent`: a teaching attestation (`Teaching Statement`, `Teaching Physician Statement`, "was present and actively participated", "I ... was present for the entire procedure") with `FellowPresent` 0 or empty, or a `Staff Only`, `Fellow: None` or `RESIDENT/FELLOW: None` line with `FellowPresent` 1 or empty. The check only reads the note and never sets the value, and it runs only when the consensus has a procedure, since a teaching statement in a progress note is about the visit;
  - a model has not analysed the note (run `--analyze` again);
  - no model, or only one, gave an answer the note supports (one answer is not a consensus, so the fields stay empty and the reason points at that model's columns), or the note was too long for every model, provided the note could hold a study value: its type is a procedure type (`Procedures`, the `Upper Endoscopy` / `PED Endoscopy` / `PEG Placement` family, `Brief Op Note`, `OR PostOp`, `OR Nursing`) or its text has upper endoscopy wording (the esophagus intubated, the endoscope or gastroscope advanced or passed, an Olympus, Pentax or Fuji scope, `GIF-`, `H190`, `Q180`, `XP190`, esophagoscopy), or the one model that answered says a procedure was performed (a flag 1 or a scope name). A note of another type without a consensus, where no model found a procedure, is not flagged: an empty row there costs the study nothing, and `Agreement` says so.
- A model that failed is an abstention, not a dissent. When the models that answered agree on every field, the row is not flagged; the failure stays on record in `Models` (`(failed)`), in `Agreement` and in `Outcome` (`agreed`). The reason "X failed on this note; the consensus rests on the other N models" is added only to a row flagged for another reason, so the reviewer knows how many models the answer rests on. A model's own `ManualReviewRequired` does not carry over: it describes how that model reached its answer, and the consensus is judged on whether the models agree.
- `Outcome` is `unanimous` (every model answered and they agree on every field), `agreed` (every model that answered agrees on every field, but a model failed: the consensus rests on the others), `majority` (every field has a value, but one was settled by a strict majority), `partial` (a field was left empty) or `none` (no consensus). `Models` lists the models consulted, marking those that failed or have not run; `Agreement` says how many answered and how they voted on every field they did not simply agree on. Neither holds note text, only scope names, counts and model labels.

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
  - `NumberOfDuodenalBiopsies` must be a number the quoted sentence states. When the
    sentence adds up biopsies from two or more parts of the duodenum ("4 biopsies were taken
    of the duodenum and 3 additional from the bulb"), only the sum (7) is accepted, not a
    part; a sentence where one number is part of another ("6 including 2 from the bulb", "4
    biopsies (2 from the bulb)", "5 with 2 from the bulb") supports the numbers it states (6,
    never 8), and one that also counts another organ is never added up. A duodenal site's
    number (D2, the 2nd portion) is not a count. A count above 20 is rejected as some other
    number from the note (an eosinophil count, a CPT code)
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

- A consensus field is filled only where at least two models agree (a strict majority on a disagreement); otherwise it is empty. `ManualReviewRequired (consensus)` is `true` where the models disagreed, where the agreed answer leaves a field open or contradicts the note's own attestation of a trainee, where the agreed duodenal count is the sum of two counts in one sentence (a reading to confirm with the researcher), where a model has not run, and where a note that could hold a study value (a procedure note type, upper endoscopy wording in the text, or a model's answer that a procedure was performed) got fewer than two answers; a model's failure alone does not flag a row the other models agree on (`Outcome` `agreed`), and a note of another type with no consensus, where no model found a procedure, is not flagged either. `ManualReviewReason (consensus)` lists why, and `Agreement (consensus)` shows every model's value for each disputed field. Filter on `ManualReviewRequired (consensus)` to get the review queue, and on `Outcome (consensus)` = `unanimous` for the rows every model agreed on. See `consensus.py` above for the rules.

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

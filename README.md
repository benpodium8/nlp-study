# Clinical note extraction study with a local LLM

## Overview

This project is a **local, offline-first clinical note analysis pipeline** that extracts structured data from endoscopy procedure notes with a **local Large Language Model (LLM via Ollama)**.

Notes are loaded from a CSV into a local SQLite database, each note is sent once to the model, the model's answer is stored next to the note, and the results are exported to CSV for downstream analysis.

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
CSV Exports (results + full audit dump)
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

Pull the required model:

```powershell
ollama pull medgemma:27b
```

This is MedGemma 27B (Gemma 3 based, Q4_K_M, about 17.4 GB). It is called with thinking
off (`think=False`). Unlike the current `gemma3` and `gemma4` tags, this build runs on
Ollama 0.24.0, the last release that can use the GPUs on the Argon cluster (NVIDIA
driver 535). To match Argon exactly, install Ollama 0.24.0:
`curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.24.0 sh`.

Ollama runs locally and does **not** require internet access after model download.

---

## Running the Program

### Basic Entry Point

```powershell
python app.py
```

This displays CLI help. There are two options, which can be combined:

| Option         | Effect                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------- |
| `--csv FILE`   | Ingest the notes in `FILE` into `data.db` (duplicates skipped); prints how many were added |
| `--analyze`    | Run the LLM on every note that has no result yet, then write the CSV exports to `output/` |

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
2. Run the LLM extraction on every note without a result
3. Store each result, with the raw model response, in the database
4. Export CSV outputs

Running `python app.py --analyze` again resumes: notes that already have a result are skipped.

---

### Inspecting the Data

The program does not print notes or results to the terminal. Use the CSV exports in `output/`, or query `data.db` directly, for example:

```bash
sqlite3 data.db "select count(*) notes, (select count(*) from results) done, (select count(*) from results where LLM_Failed=1) failed from notes;"
```

---

### Troubleshooting:

The `notes.csv` file may replace column names with underscores or dashes depending on client export/import differences in platforms.
For example, if you see a message like this:

```
ValueError: CSV must contain columns: {'NoteType', 'MRN', 'Note', 'Encounter', 'NoteCsnID', 'NoteDate'}
```

Then the column names have been incorrectly coerced to something like:

```

id,mrn,encounter,note_csn_id,note_date,note_type,note

```

or similar.

To parse data, the column names must exactly match:

```

id,MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note

```

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
- `results` – the LLM's extracted fields and raw response, one row per note

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

Workflow per note:

1. Skip if already processed
2. Run LLM analysis (with retries)
3. Store the result atomically

Features:

- Progress bar with ETA
- Retry logic for malformed LLM output
- Graceful failure isolation: a failed note is recorded with `LLM_Failed = 1` and the run continues

HIPAA:

- Processes one note at a time in memory
- No caching outside DB
- Raw LLM output retained for audit

---

### `llm_analysis.py`

**Purpose:** Semantic extraction using local LLM

Key Safeguards:

- Strict JSON schema enforcement
- Type validation
- JSON repair layer

Model:

- `medgemma:27b` (MedGemma 27B, thinking off; set by `LOCAL_MODEL` in `llm_analysis.py`)

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

- `combined_results.csv` – every note with its extracted fields and `LLM_Failed` flag
- `full_combined_results.csv` – every table dumped in full, including raw LLM responses, for audit

Exports are **write-once** by default to prevent overwrites.

---

## Disclaimer

This software assists with data extraction and research workflows.
It **does not replace clinical judgment** and must be validated per institutional policy.

# Clinical NLP + LLM dual evaluation study for clinical notes

## Overview

This project is a **local, offline-first clinical note analysis pipeline** designed to extract structured data from endoscopy procedure notes using **two independent methods**:

1. **Rule-based NLP (spaCy)** for deterministic extraction
2. **Local Large Language Model (LLM via Ollama)** for semantic extraction and cross‑validation

The system compares results from both methods, stores intermediate and final results in a local SQLite database, and exports reconciled outputs to CSV for downstream analysis.

> **Key design goals**
>
> - Run entirely on a hospital workstation
> - Never transmit PHI outside the machine
> - Provide auditability, determinism, and error isolation
> - Prevent duplicate processing and data overwrites

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
│  ├─ NLP analysis (spaCy)      │
│  ├─ LLM analysis (Ollama)     │
│  └─ Store working_results     │
└───────────────────────────────┘
        │
        ▼
┌───────────────────────────────┐
│ Reconciliation Layer          │
│  ├─ Cross-check NLP vs LLM    │
│  └─ Write final_results       │
└───────────────────────────────┘
        │
        ▼
CSV Exports (final + audit)
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
| `spacy`  | Deterministic NLP parsing   |
| `ollama` | Interface to local LLM      |
| `rich`   | Progress bars & terminal UI |

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
ollama pull gemma4:e2b
```

Ollama runs locally and does **not** require internet access after model download.

---

## Running the Program

### Basic Entry Point

```powershell
python app.py
```

This displays CLI help.

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
2. Run NLP + LLM extraction
3. Store working results
4. Reconcile results
5. Export CSV outputs

---

### View Database Contents

```powershell
python app.py --print
python app.py --working_results
```

---

### Troubleshooting:

The `notes.csv` file may replace column names with underscores or dashes depending on client export/import differences in platforms.
For example, if you see a message like this:

```((.venv) ) ben@deb-developer-vm:~/Documents/GitHub/nlp-study$ python app.py --analyze --csv ./notes.csv
Traceback (most recent call last):
File "/home/ben/Documents/GitHub/nlp-study/app.py", line 4, in <module>
main()
File "/home/ben/Documents/GitHub/nlp-study/cli.py", line 118, in main
handle_data_worker_mode(conn, args.csv)
File "/home/ben/Documents/GitHub/nlp-study/cli.py", line 62, in handle_data_worker_mode
ingest_csv(csv_file, conn)
File "/home/ben/Documents/GitHub/nlp-study/database.py", line 155, in ingest_csv
raise ValueError(f"CSV must contain columns: {required_columns}")
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
- Mode selection
- Safe ordering of pipeline steps

HIPAA:

- No network access
- Controls execution boundaries
- Prevents accidental exports

---

### `database.py`

**Purpose:** Local persistence and data integrity

Tables:

- `notes` – raw clinical notes
- `working_results` – NLP & LLM outputs
- `final_results` – reconciled outputs

Safeguards:

- Duplicate detection on ingest
- Idempotent inserts
- No overwrites of final results

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
2. Run NLP analysis
3. Run LLM analysis (with retries)
4. Store results atomically

Features:

- Progress bar with ETA
- Retry logic for malformed LLM output
- Graceful failure isolation

HIPAA:

- Processes one note at a time in memory
- No caching outside DB
- Raw LLM output retained for audit

---

### `nlp_analysis.py`

**Purpose:** Deterministic rule-based extraction

Techniques:

- Sentence segmentation
- Regex-based entity detection
- Negation handling

Why it matters:

- Fully explainable
- Deterministic baseline

HIPAA:

- No ML training
- No model downloads
- No data persistence outside DB

---

### `llm_analysis.py`

**Purpose:** Semantic extraction using local LLM

Key Safeguards:

- Strict JSON schema enforcement
- Type validation
- Boolean normalization
- JSON repair layer

Model:

- `gemma4:e2b` (local only)

HIPAA:

- Ollama runs locally
- No API calls
- No telemetry
- No prompt logging outside SQLite

---

### `reconcile_working_results.py`

**Purpose:** Final result decision logic

Logic:

- Compare NLP vs LLM hard fields
- Require agreement
- Null-out ambiguous cases

Design philosophy:

> **When in doubt, discard**

HIPAA:

- Prevents silent corruption
- Ensures conservative outputs

---

### `display.py`

**Purpose:** Human-readable inspection

Features:

- Truncated PHI display
- Rich tables

HIPAA:

- Avoids full-note exposure by default
- Operator-controlled visibility

---

## HIPAA Compliance Summary

| Requirement            | Implementation                |
| ---------------------- | ----------------------------- |
| Local processing       | ✅ All compute on workstation |
| No PHI exfiltration    | ✅ No network calls           |
| Auditability           | ✅ Raw + structured storage   |
| Deterministic fallback | ✅ NLP baseline               |
| Minimal exposure       | ✅ Truncated display          |
| Data integrity         | ✅ Idempotent inserts         |

⚠️ **Operational Notes**

- Ensure workstation disk encryption is enabled
- Restrict OS-level user access
- Treat CSV exports as PHI

---

## Output Files

Generated in `/output`:

- `combined_results.csv`
- `all_tables_combined_results.csv`
- `full_combined_results.csv`

Exports are **write-once** by default to prevent overwrites.

---

## Disclaimer

This software assists with data extraction and research workflows.
It **does not replace clinical judgment** and must be validated per institutional policy.

```

```

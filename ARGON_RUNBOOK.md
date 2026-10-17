# nlp-study on the UIowa Argon HPC cluster: consolidated runbook

Last consolidated: 2026-10-13 (second pass). Covers commit `f8f76c0` ("create the runbook, the backlog and redo the setup script"), which holds the rewritten `setup.sh` (1100 lines, `ENV_VERSION=2`), `.python-version` relaxed to `3.12`, `BACKLOG.md`, and this document, plus the uncommitted working-tree changes present at 13:20 the same day: `setup.sh` at 1127 lines (a `mksquashfs` PATH wrapper replacing the ineffective `APPTAINER_MKSQUASHFS_ARGS` export, curl stall timeouts, `GIT_TERMINAL_PROMPT=0`) and a revised `BACKLOG.md` (B19 to B21 renumbered, B27 added). Also covers the Argon session logs from 2026-09-14 (HawkID `apkane`).

**Re-synced 2026-10-15** against the working tree of branch `nlp-study-2.0`: the three-model run and the consensus block, the two exports, the relaxed CSV contract, the token-count and timeout guards on the model call, the streaming ingest and consensus, and `tests/test_robustness.py`. Where a section describes a change by date, the date is when the code changed; the September logs and the rewrite of `setup.sh` are described as before. `migration-procedure.md` lists the differences between `main` and this branch.

**Re-synced 2026-10-16** against branch `nlp-study-v2.1`: `mistral-small3.2:24b-instruct-2506-q4_K_M` in place of `medgemma:27b`, long notes shortened before sending (`note_shortening.py`), the GPU request on by default in the job script, and the 2.0-to-2.1 migration (`migrate.sh` with `migrate_db.py`, section 5.6.1). `migration-procedure.md` is now the 2.0-to-2.1 procedure.

This document merges five artifacts from the 2026-09-14 Argon session, the project README and source, the sample data in `samples/`, and three reference pages:

| Source                                                                                                                 | What it is                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Setup transcript, 18:53 CDT                                                                                            | `./setup.sh` run on `argon-login-5` in `setup` mode (model still `gemma4:e2b`)                                                    |
| Ollama server log, 19:22 CDT                                                                                           | Server inside the Apptainer container on a compute node, loading `gemma4:e2b`                                                     |
| Ollama server log, 19:33 CDT                                                                                           | Second server start on the same kind of node, CPU-only                                                                            |
| Status transcript, 20:04 CDT                                                                                           | `./setup.sh --status` inside SGE job 6516164 on `argon-itf-bx55-01`; repo now wants `gemma3:12b`                                  |
| Generated job script, 20:11 CDT                                                                                        | `~/nlp-study.job`, queue `UI`, 8 slots, 24 h, model `gemma3:12b`                                                                  |
| [Argon: Software Installations](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513438/Software+Installations) | Central software policy, environment modules, licence review                                                                      |
| [Argon: Basic Job Submission](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513450/Basic+Job+Submission)     | SGE `qsub` options, queues, memory, output files, exit codes                                                                      |
| [github.com/benpodium8/nlp-study](https://github.com/benpodium8/nlp-study)                                             | The code, the README (workstation setup, HIPAA notes), `setup.sh`                                                                 |
| `samples/Upper Gl endoscopy Scope details.docx` (gitignored)                                                           | Ten example notes with the researcher's five review comments, which define what the fields must mean                              |
| `BACKLOG.md`                                                                                                           | Evaluation against the study goals, 26 bug fixes and 13 feature items. This runbook cross-references it and does not duplicate it |

**Two versions of `setup.sh` appear in this document.** The September logs were produced by the version committed in `3657d43` (the transcripts call it `setup_nlp_study.sh`). Commit `f8f76c0` replaced it with a rewrite. Sections 5, 6, 7 and the appendices describe the rewrite as the current script; where the September version behaved differently, the difference is called out as "September version". Until the Argon checkout is pulled and `./setup.sh` re-run there, Argon still has the September version (see [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite)).

---

## Contents

1. [Quick start on Argon](#1-quick-start-on-argon)
2. [What the program does](#2-what-the-program-does)
3. [The Argon environment as observed](#3-the-argon-environment-as-observed)
4. [Where everything lives](#4-where-everything-lives)
5. [setup.sh reference](#5-setupsh-reference)
6. [The preflight gate](#6-the-preflight-gate)
7. [Running on Argon](#7-running-on-argon)
8. [Running on a workstation](#8-running-on-a-workstation)
9. [SGE job submission reference (Argon)](#9-sge-job-submission-reference-argon)
10. [Argon software installation policy and why this project is unaffected](#10-argon-software-installation-policy-and-why-this-project-is-unaffected)
11. [Timeline of the 2026-09-14 session](#11-timeline-of-the-2026-09-14-session)
12. [Open items and known discrepancies](#12-open-items-and-known-discrepancies)
13. [Questions and answers](#13-questions-and-answers)
14. [Troubleshooting](#14-troubleshooting)
15. [Software development constraints](#15-software-development-constraints)
16. [Appendix A: environment variable reference](#appendix-a-environment-variable-reference)
17. [Appendix B: the generated scripts, annotated](#appendix-b-the-generated-scripts-annotated)
18. [Appendix C: Ollama server configuration as observed](#appendix-c-ollama-server-configuration-as-observed)
19. [Appendix D: database schema and output files](#appendix-d-database-schema-and-output-files)

---

## 1. Quick start on Argon

The sequence the installer prints: `[login] ./setup.sh  ->  [qlogin] ./setup.sh  ->  [login] qsub`. Each step runs on a different kind of node, and the order matters.

**Step 0. Stage the notes file** (PHI; scratch only, never git):

```bash
cp /path/to/notes.csv /nfsscratch/Users/apkane/nlp-study/repo/notes.csv
```

The header row must carry these six columns, in any case, with spaces, underscores, dashes and a byte-order mark ignored and the usual other spellings accepted (`EncounterID`, `note_csn_id`); extra columns such as `id` are ignored: `MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note`. See [Troubleshooting: CSV must contain columns](#csv-must-contain-columns).

**Step 1. Build or repair on a login node** (needs internet; never runs inference):

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
./setup.sh
```

Re-running is always safe. Healthy resources are skipped, broken ones rebuilt, interrupted downloads resumed. After a model change in `llm_analysis.py` this step is what downloads the new weights. It ends with a tier 0 (static) preflight and rewrites `~/nlp-study.job` and `~/nlp-study-env.sh`. Only one setup run per scratch tree can be active at a time (a lock directory enforces it).

**Step 2. Prove the LLM works, on a compute node** (interactive allocation):

```bash
qlogin -q UI-GPU -pe smp 8 -l ngpus=1
/nfsscratch/Users/apkane/nlp-study/repo/setup.sh
exit
```

Inside an SGE allocation the same `./setup.sh` builds anything still missing (if that node has internet) and then runs tier 1: real inference on a synthetic PHI-free note, with per-note latency, GPU placement, and a runtime projection for the real `notes.csv`. `./setup.sh --preflight` does the same check without building anything. Do not submit until this prints `PREFLIGHT PASSED`.

**Step 3. Submit the batch job from a login node:**

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

**Monitor:**

```bash
qstat -u apkane
```

```bash
tail -f /Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>
```

**Collect results** (scratch is purged on a schedule, so copy out promptly):

```bash
ls -l /nfsscratch/Users/apkane/nlp-study/repo/output/
```

**Interactive alternative to step 3** (same allocation as step 2):

```bash
qlogin -q UI-GPU -pe smp 8 -l ngpus=1
source ~/nlp-study-env.sh
nlp_start_ollama
nlp_preflight
python app.py --analyze --csv ./notes.csv
nlp_stop_ollama
```

---

## 2. What the program does

A local, offline clinical note pipeline for endoscopy procedure notes. A local LLM extracts the study fields from every note, and its answer is the result. Until 2026-10-13 a spaCy rule extractor ran alongside it and only answers the two agreed on were kept; that layer was removed because it held the LLM back, and the sections below describe the program as it is now.

```
notes.csv ──> SQLite notes table
                 │
                 ▼  data_worker.py, one note at a time
       ┌──────────────────────────────┐
       │ llm_analysis.py (Ollama LLM) │──> results (extracted fields + debugging columns + raw LLM text, one block per model)
       └──────────────────────────────┘
                 │
                 ▼  consensus.py, after every model has run
                     results (the consensus block: the fields the models agree on, its review flag)
                 │
                 ▼  export.py (write-once; plain-words titles, TRUE/FALSE flags)
                     output/results.csv + output/results-review.csv
```

### 2.1 Command-line interface (`cli.py`)

| Invocation                                 | Effect                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python app.py`                            | Prints what the program needs, typical use, and a status check of the current directory (Ollama reachable, each model pulled, notes in `data.db` and how many are pending, both exports present), then exits 0. On a login node the Ollama line says NOT reachable, which is expected there; the probe is a 3 s client request and starts nothing                                                       |
| `python app.py --csv FILE`                 | Ingests the CSV (duplicates skipped) and reports rows read, notes added and duplicates skipped                                                                                                                                                                                                                                                                                                          |
| `python app.py --analyze --csv FILE`       | Ingests, runs each of the three models on every note that lacks its result, rebuilds the consensus block for every note, writes `output/results.csv` and `output/results-review.csv`                                                                                                                                                                                                                    |
| `python app.py --analyze`                  | Same, without ingesting. Resumes an interrupted run                                                                                                                                                                                                                                                                                                                                                     |
| `python app.py --analyze --location local` | The same run with three small stand-ins (`ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`; `LOCAL_MODELS` in `llm_analysis.py`), each under its own labelled columns, for a workstation test. `--location server`, the default, is the study set                                                                                                                        |
| `python app.py --clean`                    | Deletes `data.db` (with any `-journal`/`-wal`/`-shm` file and its backup `data.db.backup`) and both exports (`output/results.csv`, `output/results-review.csv`), and `output/` if that leaves it empty. No prompt. Combined with `--csv`/`--analyze` it runs first, giving a fresh run; a missing `--csv` file aborts before anything is deleted. Refused (exit 3) while another run holds the database |
| `python app.py --analyze --stop-after 23h` | Stops starting new notes after that long (`23.5`, `23h30m`, `90m`, `5400s`; a bare number is hours; default `NLP_STOP_AFTER`, else no limit), finishes and stores the note in progress, rebuilds the consensus and exits 2. The job script sets it from the walltime (7.4)                                                                                                                              |
| `python app.py --work-left`                | Read-only: prints each model's pending notes and whether the exports are written, exits 0 when `--analyze` has work and 4 when it has none. The job script uses it to end a surplus chained job before starting Ollama                                                                                                                                                                                  |

`--clean` runs first if given, then `--csv` ingests; `--analyze` then runs if given. The database file is always `data.db` in the current working directory and `output/` is created there too, so run the program from the repository directory. There is no command that prints notes or results; use `output/results.csv` or `sqlite3` (7.3).

### 2.2 Extracted fields

`llm_analysis()` returns this dictionary, validated against `EXPECTED_FIELDS`:

| Field                         | Type            | Meaning                                                                                                                                                                                                |
| ----------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ScopeType`                   | string or null  | The upper endoscope as the note names it (brand and/or model, for example `Olympus H190`, `GIF-H 190`, `Olympus gastroscope`); colonoscopes are ignored; the consensus writes it as the note spells it |
| `Colonoscopy`                 | 0 or 1          | A colonoscopy was performed as this note's procedure (history, plans and instructions do not count)                                                                                                    |
| `ColonoscopyInformation`      | string or null  | Supporting sentence                                                                                                                                                                                    |
| `Endoscopy`                   | 0 or 1          | An upper endoscopy / EGD was performed as this note's procedure (PEG placement with a gastroscope counts)                                                                                              |
| `EndoscopyInformation`        | string or null  | Supporting sentence                                                                                                                                                                                    |
| `NumberOfDuodenalBiopsies`    | integer or null | Stated count of duodenal biopsies, the bulb included and counted once; null when biopsies were taken but no number is given; 0 when none were taken (15.5; BACKLOG B1)                                 |
| `DuodenalBiopsiesTaken`       | 0 or 1          | At least one duodenal biopsy (duodenum, bulb, D1 to D3), with or without a count                                                                                                                       |
| `DuodenalBiopsiesInformation` | string or null  | Supporting sentence                                                                                                                                                                                    |
| `FellowPresent`               | 0 or 1          | A fellow or other trainee took part in the procedure, read from the attestation: Teaching Statement is 1, Staff Only is 0                                                                              |
| `FellowInformation`           | string or null  | Supporting sentence                                                                                                                                                                                    |

What the researcher actually needs from these fields is in [15.5](#155-study-requirements-the-researchers-review-comments).

### 2.3 Failure handling in `data_worker.py`

- `RetryableLLMError` (invalid JSON, a text field that reached the 1000-character cap, a missing field, wrong type, unexpected field, flag not 0/1, negative count, or an answer the note does not support: a quote not in the note, evidence about the wrong thing, a scope name the note does not state, a count the quoted sentence does not give; `verify_against_note`): retried, up to 5 attempts total. Each retry is a complete generation with the rejection reason appended to the prompt (the rejected answer is not sent back), sampled at temperature 0.8 with `repeat_penalty` 1.1 and `seed` = attempt number. A note that never gets a supported answer is stored with `LLM_Failed = 1` and the last rejection as `LLM_Error`.
- `LLMAnalysisError` from the call itself (`LLM call failed: ...`: the server is down, or no byte came from it for `NLP_LLM_TIMEOUT` seconds) or any other exception: stored immediately with `LLM_Failed = 1`, no retry. Two more cases are stored without a retry and read as "too long" by the review and the consensus: a note that does not fit the context window is not sent at all (`LLM_Attempts` 0), and an answer whose prompt the server itself counted as larger than the window (the token counts arrive with the last chunk) is discarded (`PromptCutError`).
- Every note gets a `results` row, and every model its own block of columns in it (`<field> (<model>@<context>)`, Appendix D) with `LLM_Attempts`, `LLM_Seconds` and, on failure, `LLM_Error`. The log (`reporter.py`) carries a run header (database, Ollama version, each model's size and quantization, the steps, the work left per model), a `load` line per model saying how long the load took and where the server put the model (`100% GPU` or a CPU share), one timestamped line per note (`ok` or `FAILED`, with the time taken, the attempt count, Ollama's prompt and answer token counts with the speed, and the structured fields, never note text), a `retry` line with the reason for every retry, a `progress` and a `fields` line every 10 notes with the projected time remaining for the model and for the rest of the run and the tally of the fields so far, a `...` heartbeat line every 60 s (`NLP_HEARTBEAT_SECONDS`) while a model call is in progress (the job log is not a terminal, so there is no live panel), a `Finished` summary per model that lists the failed ids and tallies the review and rejection reasons, the consensus summary with each model's agreement, and two closing tables.
- Before each model the program checks that the server is reachable and the model is served; it stops a model's pass after 3 consecutive notes whose call failed (`LLM call failed`), after re-probing the server to say whether it died or the model is gone, and leaves the remaining notes pending. The export is skipped when the run is incomplete. Exit codes: 0 complete, 1 bad input or a damaged database, 2 incomplete (a model skipped, the `--stop-after` limit reached, or the run stopped early), 3 another run holds the database, 130 on SIGINT, 128 + the signal number on TERM, HUP, USR1 or USR2 (143 for SGE's TERM: the run stops as on Ctrl-C, logs where it stopped and releases its lock); SIGKILL (`h_rt`, `qdel` after its grace period) cannot be caught, and costs only the note in progress. The stored results are kept in every case. A run where every note fails on content still exits 0, which is why the preflight exists (section 6).

### 2.4 Idempotency

- Ingest skips a row only if all six fields match a stored note, or an earlier row of the same file, exactly.
- `data_worker` runs one model at a time and skips any note that already has a result under that model's label (its `LLM_Failed` column is not null).
- The consensus block (`consensus.py`) is rebuilt for every note on every `--analyze`, from the stored answers of the run's models, so it never goes stale after a resume.
- The export is skipped if `output/results.csv` or `output/results-review.csv` already exists; the two are written together, under `.partial` names that are renamed into place only when both are whole, so a job killed mid-export leaves no truncated `results.csv` for write-once to keep.
- Each note's result is committed on its own (SQLite rollback journal, never WAL, which needs shared memory NFS lacks), so a kill at any point loses at most the note in progress; a half-written transaction is rolled back on the next open.
- One run per database: every run that writes takes `data.db.lock` first (7.4). A second run waits for it to go stale, then refuses.
- Each run first checks `data.db` (`PRAGMA quick_check`) and copies it to `data.db.backup`; a damaged database is refused with the command that restores the backup.

---

## 3. The Argon environment as observed

### 3.1 Hosts

| Item                       | Login node (18:53)           | Compute node (20:04)                     |
| -------------------------- | ---------------------------- | ---------------------------------------- |
| Hostname                   | `argon-login-5.hpc`          | `argon-itf-bx55-01.hpc`                  |
| Node kind (per `setup.sh`) | login                        | job (SGE `JOB_ID=6516164`)               |
| OS                         | CentOS Linux 7.9.2009        | same                                     |
| Kernel                     | 3.10.0-1160.119.1.el7.x86_64 | same                                     |
| Bash                       | 4.2.46(2)                    | same                                     |
| glibc                      | 2.17                         | same                                     |
| `HOME`                     | `/Users/apkane`              | same                                     |
| Outbound HTTPS             | works                        | not checked; compute nodes often lack it |

### 3.2 Tooling

| Tool                 | Version                                                     | Notes                                                                                                                                                                                       |
| -------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| git                  | 1.8.3.1                                                     | Predates 1.8.5, so `git -C <path>` is an unknown option. `setup.sh` uses a subshell `cd` instead                                                                                            |
| curl                 | system                                                      | Health checks, the GitHub reachability test, and (rewrite) the model blob downloads                                                                                                         |
| sha256sum            | coreutils                                                   | Required by the rewrite to verify model blobs                                                                                                                                               |
| apptainer            | 1.3.2-1.el7                                                 | Runs the Ollama container unprivileged. Predates the 1.4.0 `--mksquashfs-args` option (see 3.3)                                                                                             |
| Environment Modules  | available                                                   | Only `python/3.8.8_*` modules exist on Argon (gcc-5.4.0, gcc-9.3.0, intel-2021.2.0 variants). No 3.12 module                                                                                |
| Python actually used | `/Users/apkane/temp/build_python/python3.12/bin/python3.12` | A user-built 3.12 in `$HOME`. The venv reports Python 3.12.14. `.python-version` pins `3.12.14` to match it; `setup.sh` reads only major.minor, so any 3.12.x still satisfies the installer |

### 3.3 Login node limits (the reason for the two-node workflow)

Argon caps each user on a login node at 100% of one logical CPU and 16 GB of memory, and kills the largest process when the cap is exceeded. A multi-gigabyte model asked for a single sentence under that cap is what hung an earlier installer. Therefore:

- Build (clone, pip, `apptainer pull`, model download) on a login node: these need internet and are light on CPU, with one exception below.
- Infer (preflight tier 1, `app.py --analyze`) only inside an SGE allocation (`qlogin` or `qsub`). The rewrite's `nlp_start_ollama` refuses to start on a hostname containing `login`, and refuses outside an SGE job unless `NLP_ALLOW_INFERENCE=1` is set (intended for off-cluster machines only).

**The exception: SquashFS conversion during `apptainer pull`.** The committed `setup.sh` (`f8f76c0`) exports `APPTAINER_MKSQUASHFS_ARGS="-processors ${NSLOTS:-1} -mem 2G"` and its comments say the conversion is pinned to one thread. It is not. That variable is the environment form of `apptainer build --mksquashfs-args`, which the Apptainer changelog lists as new in v1.4.0; `apptainer pull` does not read it, and Argon runs 1.3.2. So with that version the pull converts the image with mksquashfs's defaults (every core, a quarter of RAM) under the login-node limiter. The 2.8 GB pull succeeded on 2026-09-14 anyway, but it was exposed. The working-tree revision of 13:20 removes the export and adds `make_mksquashfs_wrapper`: before the pull it writes `nlp-study-assets/bin/mksquashfs`, a one-line wrapper that execs the real binary with `-processors ${NSLOTS:-1} -mem 2G`, and puts that directory first on `PATH` for the `apptainer pull` only. Apptainer resolves `mksquashfs` through `PATH` unless `apptainer.conf` pins `mksquashfs path`, in which case the script warns that the cap cannot be applied. The code reads correctly; it has not yet been exercised on Argon (BACKLOG B14 asks for a `./setup.sh --force sif` on a login node to confirm).

September version: the setup script started a temporary `ollama serve` on the login node to run `ollama pull` and `ollama list`. It worked on 2026-09-14 but is exactly the pattern the rewrite removes.

### 3.4 Scratch filesystems

| Mount           | Login node                                                           | Compute node          | Used for                                          |
| --------------- | -------------------------------------------------------------------- | --------------------- | ------------------------------------------------- |
| `/scratch`      | not mounted                                                          | not mounted           | nothing (first candidate in `setup.sh`, skipped)  |
| `/nfsscratch`   | 229 TB free of 284 TB (NFSv3 from `172.29.4.10:/dpool01/nfsscratch`) | same                  | repo, venv, container image, model weights, lock  |
| `/localscratch` | 3.1 TB free of 3.3 TB (local XFS)                                    | 818 GB free of 892 GB | not used by this project (node-local, not shared) |

Scratch is purged on a schedule. Results in `repo/output/` and the SQLite database must be copied to durable storage after each run.

### 3.5 GPU situation

Both Ollama server logs came from a node with an NVIDIA A10 (compute capability 8.6) and `CUDA_VISIBLE_DEVICES=0` set in the environment. Ollama 0.32.14 reported:

```
NVIDIA driver too old  device="NVIDIA A10" compute=8.6 driver=535 required_driver="550 or newer"
inference compute id=cpu library=cpu ... total="251.5 GiB" available="215.6 GiB"
vram-based default context total_vram="0 B" default_num_ctx=4096
```

So even on a GPU-equipped node the container fell back to CPU inference. The node driver (535) is older than what the bundled CUDA runtime in `ollama/ollama:latest` requires (550+). `setup.sh` now defaults to `ollama/ollama:0.24.0`, the last release whose CUDA build uses a driver-535 GPU (0.30.0 moved to kernels that need 550+), and re-pulls an existing image that came from a different tag. See [Open items](#12-open-items-and-known-discrepancies) and [Troubleshooting: NVIDIA driver too old](#nvidia-driver-too-old).

The `default_num_ctx=4096` line is also why the rewrite passes `OLLAMA_CONTEXT_LENGTH` (default 8192, `NLP_CONTEXT_LENGTH`) into the container.

### 3.6 Why Ollama runs in a container

Ollama's upstream `install.sh` binary needs `GLIBC_2.27+`. CentOS 7 ships glibc 2.17, so the README's `curl -fsSL https://ollama.com/install.sh | sh` instruction does not work on Argon (it is fine on a workstation, section 8). `setup.sh` pulls `docker://ollama/ollama:0.24.0` (overridable; the September pull of `latest` gave 2.8 GB, Ollama 0.32.14) into `ollama.sif`, and records the image reference in `ollama.sif.image` and runs it with Apptainer.

---

## 4. Where everything lives

| Purpose                                                                               | Path                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Repository checkout (cwd for all runs)                                                | `/nfsscratch/Users/apkane/nlp-study/repo`                                                                                                                                                                             |
| Virtual environment                                                                   | `/nfsscratch/Users/apkane/nlp-study/venv`                                                                                                                                                                             |
| Dependency stamp (rewrite): requirements hash plus `check`/`nocheck`                  | `/nfsscratch/Users/apkane/nlp-study/venv/.requirements.sha256`                                                                                                                                                        |
| Generated preflight script                                                            | `/nfsscratch/Users/apkane/nlp-study/nlp-preflight.py`                                                                                                                                                                 |
| Setup lock (rewrite): `owner` file holds host, pid, epoch                             | `/nfsscratch/Users/apkane/nlp-study/.setup.lock/`                                                                                                                                                                     |
| Ollama container image                                                                | `/nfsscratch/Users/apkane/nlp-study-assets/ollama.sif` (in-progress pull: `ollama.partial.sif`)                                                                                                                       |
| Model store (bound to `/models` inside the container)                                 | `/nfsscratch/Users/apkane/nlp-study-assets/ollama-models` with `manifests/<host>/<ns>/<name>/<tag>` (today `manifests/registry.ollama.ai/library/mistral-small3.2/24b-instruct-2506-q4_K_M`) and `blobs/sha256-<hex>` |
| Apptainer cache and tmp                                                               | `/nfsscratch/Users/apkane/nlp-study-assets/apptainer-cache`, `.../apptainer-tmp`                                                                                                                                      |
| Generated `mksquashfs` wrapper (working tree; on `PATH` only during `apptainer pull`) | `/nfsscratch/Users/apkane/nlp-study-assets/bin/mksquashfs`                                                                                                                                                            |
| Notes input (PHI)                                                                     | `/nfsscratch/Users/apkane/nlp-study/repo/notes.csv`                                                                                                                                                                   |
| SQLite database (PHI)                                                                 | `/nfsscratch/Users/apkane/nlp-study/repo/data.db`                                                                                                                                                                     |
| CSV exports (PHI)                                                                     | `/nfsscratch/Users/apkane/nlp-study/repo/output/`                                                                                                                                                                     |
| Preserved user data after `--clean` or a repo rebuild that could not restore it       | `/nfsscratch/Users/apkane/nlp-study/preserved/<timestamp>/`                                                                                                                                                           |
| Generated job script                                                                  | `/Users/apkane/nlp-study.job`                                                                                                                                                                                         |
| Generated interactive env script                                                      | `/Users/apkane/nlp-study-env.sh`                                                                                                                                                                                      |
| Job stdout+stderr                                                                     | `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>`                                                                                                                                                                    |
| Ollama server log, batch or interactive (rewrite)                                     | `/Users/apkane/nlp-study-logs/ollama-<JOB_ID>.log` (`ollama-<pid>.log` outside a job)                                                                                                                                 |
| Setup transcripts (rewrite; deleted after 30 days)                                    | `/Users/apkane/nlp-study-logs/setup-<YYYYMMDD-HHMMSS>.log`                                                                                                                                                            |

September version differences: setup transcripts were `~/nlp-study-setup-<timestamp>.log`; the setup-time Ollama log was `nlp-study-assets/ollama-setup.log`; the job-time Ollama log was `repo/ollama-<JOB_ID>.log`; the interactive one went to `$TMPDIR`; hand-edited generated scripts were backed up as `*.bak.<timestamp>` (the rewrite overwrites them atomically without backup).

---

## 5. setup.sh reference

### 5.1 Modes

| Command                     | Node  | Effect                                                                                                                                                                                                                                                                      |
| --------------------------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `./setup.sh`                | any   | Build or repair whatever is missing or broken (downloads need outbound HTTPS, so normally a login node), then run the preflight at the highest tier the node allows: tier 0 on a login node or outside SGE, tier 1 inside `qlogin`/`qsub`. Rewrites the two `$HOME` scripts |
| `./setup.sh --preflight`    | any   | Check an existing install; builds nothing. Same tier rule. Refuses if the env script is missing or from an older script version                                                                                                                                             |
| `./setup.sh --status`       | any   | Report repo, venv, sif and model as `healthy`, `broken` (with a reason) or `absent`; presence of the preflight, env and job scripts; whether a setup lock is held; whether `preserved/` holds data. Changes nothing                                                         |
| `./setup.sh --force <name>` | login | Rebuild one resource unconditionally. Names: `repo`, `venv`, `deps`, `sif`, `model`, `all`. Repeatable, or `--force=name`                                                                                                                                                   |
| `./setup.sh --clean [-y]`   | login | Move PHI aside to `preserved/`, then delete repo, venv, preflight script, sif, model store and Apptainer caches. Prompts for `yes`; `-y`/`--yes` for non-interactive                                                                                                        |
| `./setup.sh --help`         | any   | Prints the usage block                                                                                                                                                                                                                                                      |

September version also accepted `FORCE_VENV=1` and `FORCE_SIF=1`, had no lock, and could not run in a job at all without warnings.

### 5.2 Resource state machine

Every managed resource is verified before anything is done to it:

| State   | Action              |
| ------- | ------------------- |
| healthy | skipped             |
| broken  | removed and rebuilt |
| absent  | created             |

Health checks in the rewrite:

| Resource | Healthy when                                                                                                                                                                                                    |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| repo     | A git checkout with a valid `HEAD` and no tracked files missing from the working tree                                                                                                                           |
| venv     | `bin/python` runs, reports the same major.minor as the selected interpreter, and `bin/activate` exists                                                                                                          |
| deps     | The stamp matches the sha256 of `requirements.txt` and (unless the stamp says `nocheck`) every requirement imports by name. If they no longer import, the venv is rebuilt automatically                         |
| sif      | File exists, is at least 100 MB, and `apptainer sif list` can parse it (no execution, so safe on a login node)                                                                                                  |
| model    | The manifest for the exact `name:tag` the code wants exists under `manifests/<host>/`, every blob it lists exists in `blobs/` at the listed size, and its config does not require a newer Ollama than the image |

Building or updating repo, sif, model or deps requires outbound HTTPS. Without it the script dies with `[<name>] has to be built or updated, but this node has no outbound internet` before destroying anything, and a node that has everything healthy proceeds offline.

Before a repo directory is destroyed, `preserve_user_data` moves `*.csv`, `*.db`, `*.sqlite*` and `output/` into `preserved/<timestamp>/`. After a successful re-clone, `restore_user_data` moves them back into the fresh checkout, skipping any file the fresh checkout already has (that file stays in `preserved/` with a warning). `--clean` only preserves; it never restores.

September version: the model check was only "the models directory is non-empty", which is why the 20:04 status reported `model present` while the store held `gemma4:e2b` and the code wanted `gemma3:12b`. The rewrite reports `model absent` in that situation.

### 5.3 What a full run does, in order

1. Environment: mode, host, user (`$USER`, falling back to `id -un`), SGE job id. Requires `git`, `curl`, `apptainer`, `sha256sum`. Setup mode probes outbound HTTPS to GitHub; failure is a warning that switches the run to offline mode, not an error. Setup transcripts older than 30 days are deleted. The working tree also exports `GIT_TERMINAL_PROMPT=0` so a clone or pull can never hang on a credential prompt.
2. Scratch layout: probes `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` for writability, separately for the fast root (repo, venv) and bulk root (image, model). Acquires `.setup.lock` (setup and clean modes); a lock whose owner is dead on this host, or older than six hours, is taken over. Sets Apptainer cache/tmp (the committed version also exports the ineffective `APPTAINER_MKSQUASHFS_ARGS`; the working tree drops it, 3.3). Sweeps Apptainer tmp entries older than an hour. On a login node, kills orphaned `ollama serve` processes left by older script versions. Reads `LOCAL_MODEL` from `llm_analysis.py` if the repo already exists.
3. Repository: clone (then restore preserved user data), or `pull --ff-only --quiet` if healthy, clean and online. A dirty working tree is left alone with a warning. Warns if `preserved/` holds data from earlier runs. Dies if `LOCAL_MODEL` cannot be read and `MODEL` is not set.
4. Python: tries `module load python/<wanted>` and a few variants (harmless if none exists), then probes `$NLP_PYTHON`, the interpreter recorded in an existing `venv/pyvenv.cfg` (so re-runs need no `NLP_PYTHON`), `python3.12`, `python3.13`, `python3.11`, `python3.10`, `python3`. Needs 3.10+. `<wanted>` is the major.minor of `.python-version`.
5. Virtual environment and dependencies, as in 5.2. pip runs with `--only-binary=:all:` by default and needs the network; a failed pip self-upgrade is only a warning.
6. Container image: `apptainer pull ollama.partial.sif <image>`, renamed to `ollama.sif` on success, so an existing `.sif` is always complete. Working tree: the generated `mksquashfs` wrapper is placed first on `PATH` for this command (3.3).
7. Model: first, every stored manifest whose config declares `requires` newer than the image's Ollama version is deleted (`remove_incompatible_models`) and its unshared blobs are pruned. Then the repo's model is fetched with `curl` straight from its registry (`registry.ollama.ai`, or `hf.co` for `hf.co/...` names) into Ollama's on-disk layout. The config blob is fetched first and checked against the image's Ollama version; if the model needs a newer Ollama, the script stops before downloading the weights. The manifest is downloaded to a `.partial` name; each blob is downloaded single-threaded with resume (`curl -C -`; the working tree adds `--connect-timeout 30 --speed-limit 1024 --speed-time 60`, so a transfer under 1 KB/s for 60 s fails and is retried instead of hanging, and the manifest fetch gets `--max-time 120`), up to 5 attempts, progress printed every 30 s, size checked, sha256 verified (a mismatch deletes the blob and retries within the 5 attempts), then renamed. The manifest is moved into place last, so its presence means every blob is complete. Existing complete blobs are reused; `--force model` wipes `blobs/`. Afterwards `prune_model_store` deletes blobs that no manifest references (left over from a model change).
8. Runtime scripts: writes `~/nlp-study-env.sh` (with `NLP_ENV_VERSION=2`) and `~/nlp-study.job` atomically (temp file plus rename, so a job sourcing the file never sees it half-written). Both are regenerated on every run; do not hand-edit.
9. Preflight at the highest tier the node allows, by sourcing the env script and, inside SGE (or with `NLP_ALLOW_INFERENCE=1`), starting Ollama for tier 1. Failure stops the run but leaves the build intact.
10. Summary with the next step, which differs by tier: after tier 1 the next step is `qsub`; after tier 0 it is `qlogin` and `./setup.sh` again.

Traps: Ctrl-C or TERM prints `Interrupted. Re-run to resume; partial downloads continue where they stopped.` and exits 130. Exit always kills a running curl, stops any Ollama the script started, and releases the lock.

Every run is transcribed to `~/nlp-study-logs/setup-<timestamp>.log`.

September version: the model step started a temporary Ollama server on the login node and ran `ollama pull`; pip was skipped when every requirement was already importable rather than on a hash stamp; the image was verified by executing `ollama --version` inside it; the preflight ran before the runtime scripts were written; there was no lock, no restore, no offline mode, no pruning.

### 5.4 Model name resolution

`setup.sh` does not hardcode the models. `detect_repo_models()` reads the first line matching `^LOCAL_MODEL *= *"..."` in `llm_analysis.py` (no leading whitespace allowed) and then the `MODELS = (` ... `)` block, taking the double-quoted name from every `ModelSpec("..."` line in it (the block's first line names `LOCAL_MODEL` itself); the result is the list the program calls, `LOCAL_MODEL` first. `MODEL="name:tag [name:tag ...]" ./setup.sh` overrides the list but warns if it disagrees with the repo, because the program will call whatever the repo says. If neither source yields a name the script dies. Every model in the list is fetched, verified and pruned around in turn (`MODEL` is the one the model functions act on). There is no `gemma4:e2b` fallback any more. Names parse as Ollama does: `mistral-small3.2:24b-instruct-2506-q4_K_M` is `registry.ollama.ai/library/mistral-small3.2:24b-instruct-2506-q4_K_M`, and `hf.co/<user>/<repo>:<quant>` is fetched from Hugging Face, which serves the same registry protocol.

From 2026-10-13 to 2026-10-16 the first model was `medgemma:27b` (MedGemma 27B, Gemma 3 based, Q4_K_M, 17.4 GB, model blob `sha256:4b97da6b21a8...`), called with `think=False`. Its registry config declares no `requires` and it ships no separate projector layer, so it passes `setup.sh`'s check and loads on 0.24.0. Ollama 0.24.0 rejects `think` only when it is true for a model without thinking support, so `think=False` is safe. It replaced `hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M` (Gemma 3 12B instruct, Q4_K_M, about 7.8 GB, published March 2025 by the llama.cpp maintainers). Every `gemma3` and `gemma4` tag on `registry.ollama.ai` now declares `requires` 0.30.0 or newer in its config. The image is pinned to Ollama 0.24.0 for the driver-535 GPUs. Ollama 0.24.0 does not check `requires` itself, the registry checks it on `ollama pull`, and curl skips that check, so `setup.sh` reads the field and checks it itself. The Hugging Face build declares no requirement.

Since 2026-10-14 every note also goes to `gemma4:26b-a4b-it-q4_K_M` (Gemma 4 26B mixture of experts, 3.8B active, Q4_K_M, 18.0 GB, config requires Ollama 0.20.0, blob `sha256:7121486771cb...`, `think=False`; the default `gemma4:26b` tag does not load on 0.24.0) and `gpt-oss:20b` (gpt-oss 20B mixture of experts, MXFP4, 13.8 GB, config blob `sha256:e103573196b6...`; a reasoning model called with `think="low"`, because a boolean `think=False` returns empty content from it on 0.24.0; its config declares `requires` 0.30.0, which 0.24.0 ignores and which the load test of 2026-10-13 disproved, so `setup.sh` lists it in `NLP_REQUIRES_VERIFIED` and only logs the mismatch), in that order after the first model, each at the same 8192-token window so all three see the same notes, and each with its own block of result columns (Appendix D) for a comparison across the models. `setup.sh` fetches all three, the preflight checks and exercises each, and `nlp_start_ollama` sets `OLLAMA_MAX_LOADED_MODELS=1`: each model fits the A10 alone, no two together, and `data_worker` runs them one after another.

Since 2026-10-16 the first model is `mistral-small3.2:24b-instruct-2506-q4_K_M` (Mistral Small 3.2 24B instruct, dense, Q4_K_M, 15.2 GB, Apache 2.0, model blob `sha256:41a5b0c36a28...`, `think=False`; about 17.7 GB on the A10 with the 8K cache, 1.3 GB of it KV) in place of `medgemma:27b`, which `setup.sh` prunes on its next login-node run. Its registry config declares no `requires` and the manifest has no separate projector (vision is merged into the GGUF), and the tag was published in June 2025 for Ollama 0.9 and never re-published, so it passes `setup.sh`'s check and loads on 0.24.0's engine (the `mistral3` architecture, which that engine has run since 0.6). It has not yet been loaded on a 0.24.0 build: the tier 1 preflight is the first test, and its placement line should read 100% on the GPU. The reason for the change is in `model_candidates.md` section 8: on the 275-note sample of 2026-10-15 medgemma failed 28 notes and had 75 flagged (gemma4: 9 and 26), with 157 rejected attempts, most of them quoting rules broken, and took 14.4 s per note against 2.7; Mistral Small 3.2's release notes report halved infinite generations and better instruction following over 3.1. The lighter alternative, if the dense 24B is too slow or only a 16 GB card is available, is `ministral-3:14b-instruct-2512-q4_K_M` (9.1 GB, about 11.6 GB loaded). The stand-ins for `--location local` changed the same day to `ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`, one per study-model family, each fitting an 8 GB GPU with its 8K cache; `qwen3.5:4b` left because the study excludes models from non-US labs. An existing `data.db` keeps its `medgemma:27b@8192` block and gets the new model's block beside it on the next `--analyze`; the consensus is rebuilt from the current three models, so every note is flagged until the new model has answered it.

### 5.5 Installed package set (venv, 2026-09-14)

spacy 3.8.14, thinc 8.3.13, blis 1.3.3, numpy 2.2.6, ollama 0.6.2, httpx 0.28.1, pydantic 2.13.4, rich 15.0.0, click 8.4.2, typer 0.27.1, requests 2.34.2, tqdm 4.70.0, pip 26.2.1, setuptools 84.0.0, wheel 0.48.0, plus their transitive dependencies. `requirements.txt` pinned exactly these versions (except pip, setuptools and wheel) until 2026-10-13; since the spaCy extractor was removed it lists only `rich`, `ollama`, `httpx` and `pydantic`, and the unrecorded transitive dependencies are left to pip. A venv built from the old file keeps the spaCy tree until it is rebuilt, which is harmless. The local workstation venv is built from the same file on Python 3.12.14.

### 5.6 Upgrading Argon from the September script to the rewrite

This was the move to 2.0, done before the 2026-10-15 sample run; the script it describes is `migrate.sh` at commit `4e50269` (`git show 4e50269:migrate.sh`). Since 2.1 `migrate.sh` does the 2.0-to-2.1 move instead (5.6.1) and refuses a September install, printing the command for the 2.0 script. The 2.0 `migrate.sh` did the one-time clean-up between the two layouts. It runs nothing but `git`, `find`, `mv` and `rm` (no Ollama, no `apptainer exec`), prints a plan and asks for `yes`; `--dry-run` only prints the plan. It never deletes anything that could be PHI: `notes.csv`, `data.db`, `output/` and every untracked file it does not recognise go to `preserved/migrate-<timestamp>/`, and it prints the `mv` that puts `notes.csv` back. Pass `--keep-data` to leave them in place.

1. Push the branch that carries the rewrite. As of 2026-10-13 that is `nlp-study-2.0`; `main` on GitHub (`957c6b8`) still has the September `setup.sh`. Either merge `nlp-study-2.0` into `main` and push, or deploy the branch directly with `--branch` in step 3.
2. On a login node, pull so the migration script is in the checkout (if `git pull` refuses because of local changes, `git stash` first, or let step 3 handle it with `--reset`):

   ```bash
   cd /nfsscratch/Users/apkane/nlp-study/repo && git pull --ff-only
   ```

3. Run the migration. It deletes build junk from the checkout, moves PHI aside, removes the `gemma4:e2b` manifest and the 6.5 GB of blobs nothing references, empties the Apptainer caches, removes the September `~/nlp-study.job`, `~/nlp-study-env.sh`, their `.bak` copies and the `nlp-preflight.py`, moves the `~/nlp-study-setup-*.log` transcripts into `~/nlp-study-logs/`, kills an orphaned login-node `ollama serve`, then fast-forwards the checkout (or switches it with `--branch`) and refuses to finish on a branch whose `setup.sh` is still the September one. The venv and the user-built Python are not touched; the September `ollama.sif` is left for `setup.sh` to replace (it has no `ollama.sif.image` stamp, so the next online run re-pulls the pinned image).

   ```bash
   ./migrate.sh --branch nlp-study-2.0      # or plain ./migrate.sh once main carries the rewrite
   ```

4. Run `./setup.sh`. It writes the venv's dependency stamp (one pip run against the pinned `requirements.txt`, no downloads when the pins are already installed), pulls the pinned image (the first login-node exercise of the `mksquashfs` wrapper, BACKLOG B14), removes stored models that need a newer Ollama than the image, re-verifies the model store against the manifests of every model in `MODELS` and downloads whatever is missing (about 47 GB on a fresh store), rewrites both `$HOME` scripts with `NLP_ENV_VERSION=2`, and runs tier 0.
5. Put `notes.csv` back from `preserved/migrate-<timestamp>/` (the migration prints the command), then `qlogin -q UI-GPU -pe smp 8 -l ngpus=1` and `./setup.sh` again (or `./setup.sh --preflight`). The rewrite refuses `--preflight` if the env script lacks `NLP_ENV_VERSION=2`, so step 4 is mandatory first.

#### 5.6.1 Upgrading Argon from 2.0 to 2.1

`migrate.sh` (2.1) moves an install that runs 2.0 (`main` at `4e50269`, `ENV_VERSION=2`) to 2.1 and keeps the 2.0 results that are still valid, so the first 2.1 job only analyses what 2.1 changed: every note with the new first model, and the notes 2.0 failed as too long. `migration-procedure.md` is the step-by-step; in short, push 2.1 to `main`, then on a login node `git pull --ff-only`, `./migrate.sh --dry-run`, `./migrate.sh`, `./setup.sh`, the GPU preflight, `qsub`. Like the 2.0 script it runs no Ollama and no inference, prints a plan, asks for `yes`, and refuses while an `nlp-study` job is queued or running.

- **Checkout.** Fast-forwarded to `origin/<branch>` (default the current branch), which must contain `note_shortening.py`; with `--branch nlp-study-v2.1` it deploys the branch unmerged. Untracked files stay in place (`notes.csv`, `data.db`), except `output/` and any path 2.1 now tracks, which move to `preserved/migrate-2.1-<timestamp>/`.
- **Database** (`migrate_db.py`, run with the venv's Python from the commit being deployed). Plans read-only; applies under `data.db.lock` (the pipeline's own run lock, `run_lock.py`) after copying `data.db` whole to the `preserved/` directory and checking the copy (`PRAGMA quick_check`, row counts), in one transaction: removes the block of every label that is not a study model (`medgemma:27b@8192`); clears the gemma4 and gpt-oss results whose `LLM_Error` begins `the note is too long for the model` with `LLM_Attempts` 0 and no `InputReduced` (2.0's not-sent notes; 7 per model on the sample), so 2.1 shortens and sends them; clears the consensus block; and rebuilds `results` in the order a fresh 2.1 database has (the study models in `MODELS` order, each with `InputReduced` and `InputReduction`, then the consensus), because the export writes model blocks in table order and the new model's block would otherwise come last, after column BH. It moves the stale 2.0 `data.db.backup` into the same directory. A study model found under another window (an `NLP_CONTEXT_LENGTH` mismatch) is left alone, not dropped. `--keep-retired` keeps the medgemma block (after the study blocks); `--keep-data` skips the step.
- **Model store.** Removes manifests for models `MODELS` does not list (`medgemma:27b`) and the blobs no remaining manifest references. `setup.sh` alone would not: it prunes only unreferenced blobs and keeps every manifest whose model loads on the image.
- **`$HOME`.** Removes a 2.0 `~/nlp-study.job` without `#$ -l ngpus=`, so a CPU job cannot be submitted before `setup.sh` writes the 2.1 one; `~/nlp-study-env.sh` is left for `setup.sh` (`ENV_VERSION` is still 2).
- **Left alone.** The image (both versions pin `ollama/ollama:0.24.0`) and the venv (same `requirements.txt`).

Re-running is harmless: a migrated database plans nothing, including after 2.1 has run on it (a note 2.1 itself could not shorten carries `InputReduced` and is not requeued, and the consensus is cleared only together with a change to the models' data). Verified on 2026-10-16 against a rehearsal install on a workstation (a 2.0 checkout, a `data.db` written by the 2.0 code from the 275-note sample input, a fake model store): the plan matched the sample run (the same 7 note ids), the migrated database exported 86 columns in the guide's order after a simulated 2.1 run, the refusals (2.0 still on origin, run lock held) fired, and `tests/test_robustness.py` covers the database step. Not yet run on Argon itself.

### 5.7 September version versus rewrite, at a glance

| Topic                       | September version (`3657d43`, in the logs)        | Rewrite (`f8f76c0`)                                                                                                                                    |
| --------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ollama on login node        | Started for `ollama pull` and tier 0              | Never; blobs via curl, tier 0 reads the manifest                                                                                                       |
| Model health check          | Directory non-empty                               | Manifest for the exact tag plus every blob at its size                                                                                                 |
| Image health check          | `apptainer exec ... ollama --version`             | `apptainer sif list` (no execution), 100 MB minimum                                                                                                    |
| Running inside a job        | Warned; `setup` mode discouraged                  | First-class; builds if online, uses what exists if not                                                                                                 |
| Concurrency                 | None                                              | `.setup.lock` with stale takeover                                                                                                                      |
| PHI during repo rebuild     | Moved to `preserved/`                             | Moved aside and restored into the new checkout                                                                                                         |
| Dependency check            | Importability of each requirement                 | Hash stamp plus import test; auto venv rebuild                                                                                                         |
| Context length              | Ollama default (4096 on CPU)                      | `OLLAMA_CONTEXT_LENGTH=8192` (`NLP_CONTEXT_LENGTH`)                                                                                                    |
| Job preflight budget        | 900 s                                             | 600 s (preflight default)                                                                                                                              |
| GPU directive               | `ngpus=1` and `gpu_p100`                          | `ngpus=1` by default since 2026-10-16 (`NLP_JOB_GPU=0` drops it); a card type only through `NLP_JOB_GPU_TYPE=<complex>`                                                                                                                                         |
| Logs                        | `~/nlp-study-setup-*.log`, Ollama logs on scratch | Everything under `~/nlp-study-logs/`, 30-day rotation for setup logs                                                                                   |
| Generated scripts           | Backed up as `.bak` when they differed            | Overwritten atomically, no backup                                                                                                                      |
| Preflight row count         | `wc -l` (wrong for multi-line notes)              | `csv` module                                                                                                                                           |
| Image tag                   | `latest`, hardcoded                               | `0.24.0` (last to use driver 535), overridable with `NLP_OLLAMA_IMAGE`; a different tag from the one recorded in `ollama.sif.image` triggers a re-pull |
| Repo URL                    | hardcoded                                         | `NLP_REPO_URL` override                                                                                                                                |
| SquashFS conversion on pull | mksquashfs defaults                               | Committed: ineffective `APPTAINER_MKSQUASHFS_ARGS`; working tree: PATH wrapper with `-processors ${NSLOTS:-1} -mem 2G`                                 |
| Stalled downloads           | hang until killed                                 | Working tree: curl stall timeouts, git credential prompt disabled                                                                                      |

---

## 6. The preflight gate

### 6.1 Why it exists

`data_worker.py` catches every LLM exception per note, records `llm_failed = 1` and continues. A wrong endpoint, a missing model, or a model that will not emit schema-clean JSON therefore does not crash the job. It runs for its full walltime and hands back a complete-looking table with every LLM column null, exit status 0. The preflight is the only thing that turns that into a loud, early failure.

### 6.2 Tiers (rewrite)

| Tier | Where                                                       | What it checks                                                                                                                                                                                                                                                                                                                                                                                    |
| ---- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | anywhere, including login nodes; seconds; no Ollama process | Imports of `llm_analysis`, `data_worker`, `database`, `cli`, `ollama`, `rich`. `LOCAL_MODEL` and the names in `MODELS` equal the models setup fetched. Every model's manifest exists and every blob is present at the right size. Repo dir and `output/` are writable                                                                                                                             |
| 1    | inside an SGE job, after `nlp_start_ollama`                 | Tier 0, then: `OLLAMA_HOST` set, `/api/tags` responds, every model is served, the module-level `ollama` client points at that host. Then, for each model in turn: real `llm_analysis()` calls on a synthetic PHI-free note under a `SIGALRM` budget, model placement via `/api/ps` (percentage of the model in VRAM) and extraction spot checks. Latency per model and a projection for all three |

Tier 1 expectations on the synthetic note:

- Hard (failure): `Endoscopy == 1`, `Colonoscopy == 0`. The synthetic note says "No colonoscopy was performed", and since 2026-10-13 the prompt defines `Colonoscopy` as a colonoscopy performed in the note, so the gate and the prompt agree (BACKLOG B11).
- Soft (warning): `DuodenalBiopsiesTaken == 1`, `NumberOfDuodenalBiopsies == 4`, `FellowPresent == 1`, `ScopeType` containing `H190`.
- Warns if the raw output is not bare JSON (the repair layer is doing work; `data_worker` retries up to 5x on JSON errors).
- Placement: PASS at 100% on GPU, PASS on a node with no `nvidia-smi`, WARN otherwise ("partly or fully on CPU, expect it to be slow").
- Prints seconds per note and projected hours for 100, 1000 and the real row count of `notes.csv` (counted with the `csv` module, so multi-line notes are counted correctly). Warns above 60 s per note.

September version: tier 0 also probed the endpoint (so it needed a running server, hence the login-node Ollama); the row count came from `wc -l`; placement was shown by printing `ollama ps`.

### 6.3 Defaults

| Context                                                                 | Reps                     | Per-call budget                                                                           |
| ----------------------------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------- |
| `./setup.sh` inside `qlogin`, `./setup.sh --preflight`, `nlp_preflight` | 3 (`NLP_PREFLIGHT_REPS`) | 600 s (`NLP_PREFLIGHT_BUDGET`)                                                            |
| Inside `~/nlp-study.job`                                                | 1                        | 600 s unless `NLP_PREFLIGHT_BUDGET` is exported at `qsub` time (September version: 900 s) |

### 6.4 Direct invocation

```bash
python /nfsscratch/Users/apkane/nlp-study/nlp-preflight.py --repo /nfsscratch/Users/apkane/nlp-study/repo --model mistral-small3.2:24b-instruct-2506-q4_K_M --model gemma4:26b-a4b-it-q4_K_M --model gpt-oss:20b --models-dir /nfsscratch/Users/apkane/nlp-study-assets/ollama-models --tier 1 --reps 3 --budget 600 --notes 5000
```

`--models-dir` is required in the rewrite. For tier 1, `OLLAMA_HOST` must already be exported and a server listening; `nlp_start_ollama` from `~/nlp-study-env.sh` does both. `NLP_COLOR=1` forces colour when stdout is not a TTY.

---

## 7. Running on Argon

### 7.1 Batch (recommended)

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

Everything after the script name is forwarded to `app.py` verbatim (`"$@"`). `./notes.csv` is resolved after the env script does `cd "$NLP_REPO_DIR"`, so it means `repo/notes.csv`.

What the job does (full script in Appendix B):

1. Sources `~/nlp-study-env.sh` quietly: exports the `NLP_*` paths, defines the helper functions, `cd`s into the repo, activates the venv.
2. `nlp_start_ollama`: picks a free localhost port, exports `OLLAMA_HOST`, starts `ollama serve` in the container (with `--nv` only if `nvidia-smi -L` succeeds, `OLLAMA_CONTEXT_LENGTH` 8192 by default, `OLLAMA_MAX_LOADED_MODELS=1` so one model is resident at a time), logs to `~/nlp-study-logs/ollama-<JOB_ID>.log`, waits up to 180 s for `/api/tags`. Traps `nlp_stop_ollama` on exit (TERM, then KILL after 10 s).
   Before that, unless the arguments include `--csv` or `--clean` (or `NLP_ALWAYS_RUN=1`), it runs `python app.py --work-left`: a job with nothing left to do (a surplus job in a chain) ends there with exit 0, without starting Ollama.
3. Runs the tier 1 preflight with 1 rep per model unless `NLP_SKIP_PREFLIGHT` is set. Non-zero exit aborts the job before `app.py`.
4. Runs `python app.py --stop-after <left> "$@"`, where `<left>` is the job's walltime (its own `h_rt` from `qstat -j`, so a `qsub -l h_rt=` override counts; else the baked `NLP_JOB_HRT`) minus `NLP_STOP_MARGIN_MINUTES` (default 45) minus the time already spent starting Ollama and the preflight. An exported `NLP_STOP_AFTER`, or a `--stop-after` among the arguments, takes precedence. `app.py` runs in the background so a TERM reaching the script is passed on and the script waits for it to stop. The script exits with `app.py`'s status and a line saying what it means (0 complete, 2 work left, 3 database in use, 143 stopped by TERM or the memory limit).

Resource request baked in: `-N nlp-study`, `-q UI-GPU`, `-pe smp 8`, `-l h_rt=24:00:00`, `-l ngpus=1`, `-j y`, `-o /Users/apkane/nlp-study-logs/`, `-m ea`. Since 2026-10-16 the job requests one GPU by default; `NLP_JOB_GPU=0` with `NLP_JOB_QUEUE=UI` makes a deliberate CPU-only job, and `NLP_JOB_GPU_TYPE=<complex>` pins the card type (the study models need a 24 GB card). Change them with `NLP_JOB_QUEUE`, `NLP_JOB_SLOTS`, `NLP_JOB_HRT`, `NLP_JOB_GPU`, `NLP_JOB_GPU_TYPE` and re-run `./setup.sh`, or override at submission time (command-line options supersede `#$` directives):

```bash
qsub -pe smp 16 -l h_rt=48:00:00 ~/nlp-study.job --analyze --csv ./notes.csv
```

Environment variables exported in the submitting shell reach the job because `-V` is on by default. For example `NLP_PREFLIGHT_BUDGET=1200 qsub ~/nlp-study.job --analyze`.

### 7.2 Interactive

```bash
qlogin -q UI-GPU -pe smp 8 -l ngpus=1
source ~/nlp-study-env.sh      # cd to repo, activate venv, define helpers
nlp_start_ollama               # refuses on login nodes and outside SGE
nlp_preflight                  # tier 1 (pass 0 for tier 0)
python app.py --analyze --csv ./notes.csv
nlp_stop_ollama
```

`nlp_start_ollama` is idempotent: if a server it started is still alive it reports the existing `OLLAMA_HOST` and returns.

### 7.3 Monitoring

```bash
qstat -u apkane
```

```bash
qstat -j <JOB_ID>
```

```bash
tail -f /Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>
```

```bash
qdel <JOB_ID>
```

After the job ends, `qacct -j <JOB_ID>` shows exit status, wallclock, and `maxvmem`. The job log (`tail -f` above) carries one line per note with the time taken, the token counts and the extracted flags, a `...` heartbeat line every 60 s while a call is in progress (what the model is doing, tokens so far, time on the attempt), a `progress` line every 10 notes with the projected time remaining for the model and for the rest of the run, and a final summary with the failed ids (section 2.3). Checking progress in the database while a job is running is also safe for reads:

```bash
sqlite3 /nfsscratch/Users/apkane/nlp-study/repo/data.db 'select count(*) notes, (select count(*) from results where "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)" is not null) done, (select count(*) from results where "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)"=1) llm_failed from notes;'
```

### 7.4 Resuming, and running the full corpus (about 100,000 notes)

**Resuming.** Submit again without `--csv`. Notes that already have a result for a model are skipped for that model; the rest are processed.

```bash
qsub ~/nlp-study.job --analyze
```

The exports are write-once and written only by a complete run: if `output/results.csv` or `output/results-review.csv` exists, both are skipped with a message, so after a resume the files on disk are from the first complete run. Move both aside to regenerate them from the complete database (BACKLOG B17).

**How long the full corpus takes.** 100,000 notes are 300,000 model calls (three models). One job of the default 24 h walltime gives about 23 h of model time (Ollama start and the preflight take some minutes, the stop margin 45). The tier 1 preflight (section 6) prints each model's time per note and a projection for the real row count of `notes.csv`; divide that projection by 23 h for the number of jobs:

| Time per note, three models together                   | Model time for 100,000 notes | 24 h jobs |
| ------------------------------------------------------ | ---------------------------- | --------- |
| 12 s (3 to 5 s per call, the stand-ins' pace on a GPU) | about 14 days                | about 15  |
| 17 s (what fits 5,000 notes in one job)                | about 20 days                | about 21  |
| 60 s (the preflight's warning threshold)               | about 70 days                | about 73  |

At the CPU pace of a dense 24 B model (minutes per note) the corpus does not finish in any useful time: make sure the job got its GPU (it requests one by default since 2026-10-16; the preflight's placement line must say 100% GPU for each model, and a 16 GB card does not hold them) or set a longer walltime, or cut the work with the relevance gate (BACKLOG F3). The models run one at a time over every note, so `mistral-small3.2` finishes the whole corpus before `gemma4` starts, and the consensus is complete only after the last job.

The program itself handles the size: ingest, analysis, consensus and export stream, and peak memory stays under 100 MB. Measured on 2026-10-15 with 100,000 synthetic notes of the real length distribution (mean 4.0 KB) and the model calls faked: ingest 6 s, the integrity check and backup of the 0.5 GB database under 1 s on a local disk, consensus 5.5 s, export 6 s (443 MB and 23 MB), peak 83 MB. The run was stopped five times along the way (TERM, KILL, two time limits, a lock taken over) and every note still ended with exactly one result per model. On `/nfsscratch` expect the check and backup to take a minute or two per job. Disk: about 0.8 GB for `data.db` and as much for `data.db.backup`, about 670 MB for `results.csv`.

`start-resume-stop.md` in the repository is the step-by-step version of this section for the person running the jobs.

**Submit the whole chain at once** from a login node. Each job waits for the one before it (`-hold_jid`, which releases on any exit status), resumes where it stopped, and stops starting notes 45 minutes before its walltime:

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
prev=$(qsub -terse ~/nlp-study.job --analyze --csv ./notes.csv)
for i in $(seq 2 27); do prev=$(qsub -terse -hold_jid "$prev" ~/nlp-study.job --analyze); done
qstat -u apkane
```

Size the chain at the table's job count plus a quarter (27 for the 17 s row). Surplus jobs end within seconds (`Nothing left to do; the job ends without starting Ollama.`), so over-provisioning costs little; delete them anyway once a job log says `complete` (`qdel <JOB_ID> ...`). Each job mails at its end (`-m ea`); add `-m a` to the `qsub` lines for abort mail only. Check in between with `python app.py` (status, including the run lock and the last backup) or `python app.py --work-left`, both safe while a job runs. A job that fails its preflight or hits trouble ends and the next one in the chain tries again; read the job logs if several in a row end early.

**What keeps a weeks-long run safe.**

- _Clean stops._ With `--stop-after` set by the job script, a job finishes the note in progress, rebuilds the consensus and exits 2 before SGE's `h_rt` kill. TERM (eviction from `all.q`, `qdel`) stops it the same way at once (exit 143). The note in progress is not stored and is analysed again by the next job.
- _Kills._ SIGKILL (`h_rt` reached anyway, the node lost) cannot be caught. Every note is committed on its own, so at most the note in progress is lost; SQLite rolls back a half-written transaction on the next open. The lock file stays behind and the next job takes it over (below).
- _One run per database._ Every run that writes holds `data.db.lock`, which names its host, pid, SGE job and start time and is refreshed every 30 s. A second run (an overlapping job, an interactive run, `--clean`) waits for it and is refused with exit 3 if it is still fresh after 3.5 minutes. A lock not refreshed for 3 minutes, or whose owner is a dead process on the same host, is stale and taken over (`Taking over the run lock ...`), so a job chained after a killed one goes ahead. A run that loses its lock (suspended by SGE for over 3 minutes while another run took over) stops before its next note and writes nothing more. If you are sure no run is active and a lock still blocks you, delete `data.db.lock`.
- _Integrity check and backup._ Each run first runs `PRAGMA quick_check` on `data.db` and, when it passes, copies it to `data.db.backup` (written under a temporary name, so the backup is always whole). A damaged database is refused (exit 1) with the commands to set it aside and restore the backup, which loses at most the previous job's work. Copy `data.db` (or the backup) to durable storage every few days: scratch is purged on a schedule.
- _Exports._ Written under `.partial` names and renamed into place only when whole, so a kill mid-export never leaves a truncated `results.csv` behind.
- _Call failures._ A note whose model call failed (Ollama died) is stored as failed and not retried on resume (2.3, 14). After a job that stopped with `STOPPED ... model calls in a row failed`, clear those notes' `LLM_Failed` to have the next job retry them.

### 7.5 Collecting results

Two files: `repo/output/results.csv` (columns in Appendix D; `results-csv-guide.md` in the repository explains them to the researcher, with the steps to highlight the review queue in Google Sheets or Excel), and `repo/output/results-review.csv`, the same rows with only the identifiers, the consensus block and the note length (columns A to X as in `results.csv`), about 4% of the size and the one to open in Google Sheets. Both contain PHI; `results.csv` already includes the raw LLM responses. Copy it, and `data.db`, to durable storage. Scratch is purged on a schedule and `preserved/` is still scratch.

### 7.6 Compute nodes without internet

`./setup.sh` inside a job first probes HTTPS. Without it, the run prints `No outbound HTTPS from this node: existing resources are used as they are; nothing can be downloaded.` and continues with what is on scratch. If anything is absent or broken it stops with `[<name>] has to be built or updated, but this node has no outbound internet`; build it on a login node and come back.

---

## 8. Running on a workstation

The README describes a workstation deployment (Windows 10/11, with Mac and Linux variants). Everything in sections 2 and 15 applies there too; this section is what differs from Argon.

### 8.1 Prerequisites

| Software                        | Purpose                                                                   |
| ------------------------------- | ------------------------------------------------------------------------- |
| Windows 10/11, macOS or Linux   | Supported OS                                                              |
| Python 3.12                     | Core runtime (the installer accepts 3.10+; `.python-version` says `3.12`) |
| PowerShell (Windows) or a shell | Environment management                                                    |
| Ollama                          | Local LLM runtime                                                         |

### 8.2 Setup

1. Install Python from https://www.python.org/downloads/windows/ (or your package manager). On Windows tick "Add Python to PATH" and install pip. Verify with `python --version`.
2. Clone or copy the project, for example to `C:\Users\<you>\Documents\nlp-study`.
3. Windows only, one time: if activating the venv fails with `.venv\Scripts\Activate.ps1 cannot be loaded`, run

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

4. Create and activate the virtual environment.

   Windows (PowerShell):

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

   Mac or Linux:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   ```

   `.vscode/settings.json` already points VS Code at `.venv` and auto-activates it in terminals.

5. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

   `ollama` (client for the local LLM) and `rich` (progress bar).

6. Install Ollama from https://ollama.com. On Linux or Mac the one-liner works on a workstation (it does not on Argon, see 3.6):

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

7. Pull the models the code asks for. `MODELS` in `llm_analysis.py` is the authority: `mistral-small3.2:24b-instruct-2506-q4_K_M`, `gemma4:26b-a4b-it-q4_K_M` and `gpt-oss:20b` (about 47 GB in all; see 5.4). To match Argon exactly, install Ollama 0.24.0 (`OLLAMA_VERSION=0.24.0` on the one-liner in step 6):

   ```bash
   ollama pull mistral-small3.2:24b-instruct-2506-q4_K_M && ollama pull gemma4:26b-a4b-it-q4_K_M && ollama pull gpt-oss:20b
   ```

   For a test without the large models, `--location local` runs the same pipeline with three small stand-ins (`ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`, about 12 GB, each fitting an 8 GB GPU with its 8K cache; they need a current Ollama, 0.30.5 or newer, not 0.24.0):

   ```bash
   ollama pull ministral-3:3b-instruct-2512-q4_K_M && ollama pull gemma4:e4b-it-qat && ollama pull nemotron-3-nano:4b
   ```

   Ollama needs internet only for this download. Afterwards it serves on `127.0.0.1:11434`, which is the `ollama` client's default, so `OLLAMA_HOST` need not be set.

### 8.3 Running

Same CLI as section 2.1, from the project directory:

```bash
python app.py --analyze --csv ./notes.csv
```

PowerShell paths use backslashes (`--csv path\to\endoscopy_notes.csv`). `data.db` and `output/` are created in the current directory.

### 8.4 Differences from Argon to keep in mind

- There is no preflight gate on a workstation unless you run the generated `nlp-preflight.py` yourself, and no job script. A wrong model name still produces a complete-looking run with `LLM_Failed = 1` on every row; check the counts in 7.3 before trusting a run.
- A desktop Ollama with a GPU picks its context length from VRAM; a CPU-only machine gets 4096 tokens, and an over-long prompt is truncated server-side with no client-visible error (BACKLOG B10, fixed 2026-10-14). `llm_analysis.py` passes `num_ctx` (`NLP_CONTEXT_LENGTH`, default 8192) on every call, so the server's own default no longer applies, and `data_worker.py` fails a note that would not fit the window without calling the model. A larger window needs more VRAM and pushes layers to the CPU on the A10, which the preflight's placement check reports; 8192 is the study's window, and every model fits the A10 with it (it was also the largest window medgemma:27b, replaced on 2026-10-16, fitted in).
- `requirements.txt` pins the Argon versions (ollama 0.6.2, rich 15.0.0, see 5.5), so a workstation venv matches Argon's. Use Python 3.12.14 (`.python-version`) for an exact match.
- The same CSV header rules apply (14, "CSV must contain columns").
- With Apptainer installed, the Argon env script also works off-cluster: `NLP_ALLOW_INFERENCE=1` lets `nlp_start_ollama` run outside SGE.

### 8.5 HIPAA operational notes and disclaimer (from the README)

- All compute stays on the machine: no network calls, no telemetry, no prompt logging outside SQLite. Ollama listens on loopback.
- Enable disk encryption on the workstation, restrict OS-level user access, and treat CSV exports (and `data.db`) as PHI.
- The program never prints note text or model output to the terminal; the per-note log lines carry only the structured fields. Raw LLM output is kept in the database and in `results.csv` for audit.
- The software assists with data extraction and research workflows. It does not replace clinical judgment and must be validated per institutional policy.

---

## 9. SGE job submission reference (Argon)

Condensed from the Basic Job Submission page (updated 2021-07-02). `man qsub` is the authoritative source.

### 9.1 Job scripts and submission

- Prefer a job script over `qsub -b y <program>`. The script is an ordinary shell script that SGE runs for you once it finds a node.
- Scripts written on Windows need `dos2unix script` before submission.
- `qsub myscript.job` submits with defaults. `qsub -pe smp 12 myscript.job` requests a parallel environment with 12 slots.
- Options can live in the script as `#$` directives. A command-line option supersedes the same option in the script.

### 9.2 Queues

| Queue    | Notes from the page                                                                                                                                                                                                                                 |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `UI`     | Default queue                                                                                                                                                                                                                                       |
| `UI-HM`  | High-memory queue for large-memory jobs                                                                                                                                                                                                             |
| `all.q`  | No limit, but subordinate to the other queues. Jobs are evicted (TERM signal) when a higher-priority queue needs the node. Suggested for many single-processor jobs                                                                                 |
| `UI-GPU` | Not on this page. The default queue of the generated job since 2026-10-16, with `#$ -l ngpus=1`. The September version pinned `#$ -l gpu_p100`; the rewrite pins a card type only when `NLP_JOB_GPU_TYPE=<complex>` is set (`qconf -sc \| grep -i gpu` lists the complexes; the study models need a 24 GB card such as the A10) |

### 9.3 Commonly used `qsub` options

| Option                               | Description                                                                                                                                                                                  |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-V`                                 | Import the current environment into the job. On by default                                                                                                                                   |
| `-N name`                            | Job name                                                                                                                                                                                     |
| `-l h_rt=hr:min:sec`                 | Maximum walltime                                                                                                                                                                             |
| `-l h_vmem=bytes` (for example `2G`) | Virtual memory limit. An appropriate value is set automatically if an entire node is not requested                                                                                           |
| `-l mf=nG` or `-l mf=nM`             | The `mem_free` request: free memory at scheduling time. Not a limit; only affects where the job starts                                                                                       |
| `-cwd`                               | Run from the current working directory. Otherwise the job starts in `$HOME`                                                                                                                  |
| `-S shell`                           | Shell for interpreting the job script                                                                                                                                                        |
| `-o path`, `-e path`                 | Standard output / error file or directory. A directory gets the default filenames inside it                                                                                                  |
| `-j y`                               | Merge stderr into stdout                                                                                                                                                                     |
| `-pe name n`                         | Parallel environment and slot count                                                                                                                                                          |
| `-M addr[,addr]`                     | Email recipients                                                                                                                                                                             |
| `-m b\|e\|a\|s\|n`                   | Mail at begin, end, abort, suspend, never. The suspend option does not work. For multi-node jobs the mail's CPU and memory accounting covers only the primary host; use `qacct` for the rest |
| `-t n[-m[:s]]`                       | Array job with index range and step; tasks see `SGE_TASK_ID`                                                                                                                                 |

### 9.4 Output files

Default names are `$JOB_NAME.o$JOB_ID` and `$JOB_NAME.e$JOB_ID`. Streams are appended, so reusing a file keeps previous contents. Shell redirection inside the script (`command > file`) overrides `-o`. For this project, with `-j y` and `-o /Users/apkane/nlp-study-logs/`, the single log is `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>`.

### 9.5 Exit status

| Code         | Meaning                                                                                                                                                                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 to 127     | Exit status of the computation itself. `app.py` returns 0 even when every note's LLM step failed                                                                                                                                                                    |
| 128 + signal | Job did not complete normally                                                                                                                                                                                                                                       |
| 143          | 128 + TERM (15). Eviction from `all.q`, `qdel`, or the memory limit. `app.py` handles TERM: the job log then ends with `Stopped by SIGTERM.` and where it stopped. Without that line, outside `all.q`, it is almost always memory. Confirm with `qacct -j <JOB_ID>` |
| 137          | 128 + KILL (9). `h_rt` reached (the job script's `--stop-after` normally ends the job before that), or a follow-up kill after TERM. At most the note in progress is lost                                                                                            |
| 2            | `app.py` stopped with work left: the `--stop-after` limit (normal in a chain), a model not pulled, or the run stopped early. The next job resumes                                                                                                                   |
| 3            | `app.py` did not start: another run holds `data.db.lock`                                                                                                                                                                                                            |

---

## 10. Argon software installation policy and why this project is unaffected

From the Software Installations page (updated March 2026):

- Central software is installed from RPMs or built from source and exposed through environment modules. The Argon Software List shows versions. Loading a module sets up dependencies; after loading, `$ROOT_PACKAGE` (module name upper-cased) gives the install path.
- Perl, Ruby, R and Python modules come with a curated set of extensions.
- Centrally installed software must comply with University of Iowa software licensing policy. Review can add days to weeks:
  - Open source under an approved licence: no further review.
  - Free or open source with a non-standard licence: review required, possibly with access restrictions (for example registration-gated downloads).
  - Purchased software: signed agreement review and an access-control scheme approved by General Counsel.
  - Already-reviewed software (on the List of Reviewed Agreements): still verify the licence permits cluster installation.
- Contact the HPC team to discuss software needs (`research-computing@uiowa.edu` is the address `setup.sh` prints).

Implications for nlp-study:

- Nothing in this project is installed centrally. Python 3.12 is a user build in `$HOME`, the venv and all pip packages are on scratch, Ollama runs from an Apptainer image on scratch, and the model weights are on scratch. No review is triggered and no module needs to exist.
- The only Argon Python modules are 3.8.8 variants, which is why a user-built 3.12 was needed. The rewrite still tries `module load python/3.12` first in case one appears, then falls back to `$NLP_PYTHON`, the existing venv's base interpreter, and the PATH.
- If a central install were ever requested: Ollama is MIT-licensed (approved category), and the three models' weights are Apache 2.0 (Mistral Small 3.2, Gemma 4, gpt-oss). The MedGemma weights (`medgemma:27b`, replaced on 2026-10-16) were under Google's Health AI Developer Foundations Terms of Use, a non-standard licence that would have required review before central deployment.

---

## 11. Timeline of the 2026-09-14 session

All of this was produced by the September version of `setup.sh`.

| Time (CDT) | Host                             | Event                                                                                                                                                                                                                                                                        |
| ---------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 18:53:40   | `argon-login-5`                  | `setup.sh` in setup mode. All prerequisites OK. `/scratch` not mounted; `/nfsscratch` chosen. Repo already healthy, fast-forwarded `332ad3c..3657d43` (adds `setup.sh`). Model read from repo: `gemma4:e2b`                                                                  |
| 18:53:58   |                                  | Python selected: `/Users/apkane/temp/build_python/python3.12/bin/python3.12`. Venv healthy (3.12.14). All deps already satisfied; pip skipped                                                                                                                                |
| 18:54:15   |                                  | `ollama.sif` healthy, 2.8 GB, Ollama client 0.32.14                                                                                                                                                                                                                          |
| 18:54:17   |                                  | No GPU on login node. Temporary Ollama on `127.0.0.1:37310`, API up after ~10 s. `gemma4:e2b` already present (ID `7fbdbf8f5e45`, 7.2 GB listed, 6.5 GB store on disk)                                                                                                       |
| 18:54:40   |                                  | Preflight tier 0 (login node; tier 1 refused on purpose). All static checks PASS                                                                                                                                                                                             |
| 18:54:45   |                                  | Wrote `~/nlp-study-env.sh` and `~/nlp-study.job`. Setup complete                                                                                                                                                                                                             |
| 19:22:16   | compute node with A10            | Ollama server started inside a job (`OLLAMA_HOST=127.0.0.1:59103`, `CUDA_VISIBLE_DEVICES=0`). Driver 535 too old for CUDA runtime (needs 550). CPU inference, 251.5 GiB RAM. At 19:22:56 loaded `gemma4:e2b` (a tier 1 preflight or interactive run; result not in the logs) |
| 19:33:59   | same kind of node                | Fresh Ollama server start, same port, same driver warning, CPU-only. Model list cache: 1 model                                                                                                                                                                               |
| 20:04:37   | `argon-itf-bx55-01`, job 6516164 | `setup.sh --status`. Repo, venv, sif healthy. Model store "present" at 6.5 GB. Warning: repo has moved off `gemma4:e2b`; `llm_analysis.py` now requests `gemma3:12b`                                                                                                         |
| 20:11:43   |                                  | `~/nlp-study.job` regenerated with `MODEL="gemma3:12b"`, queue `UI`, 8 slots, 24 h, no GPU                                                                                                                                                                                   |

Between 18:54 and 20:04 the repository advanced to commit `1052cdd` ("change to gemma3"). Whether `gemma3:12b` was subsequently pulled into the model store is not shown in any of the five artifacts. The rewrite's `--status` will answer that definitively (`model healthy` or `model absent`).

---

## 12. Open items and known discrepancies

Bug-level items are tracked in `BACKLOG.md` (B1 to B26) with proposed fixes; this list is about the deployment state.

1. **`gemma3:12b` may not be on Argon yet.** The 20:04 status showed a 6.5 GB store, which matches `gemma4:e2b` alone. The code wants `gemma3:12b` (roughly 8 GB more). Under the September script, `--status` cannot tell and the job's tier 1 preflight is the first thing that would notice. Under the rewrite, `--status` reports `model absent` and `./setup.sh` on a login node downloads the missing blobs with resume and checksum, then prunes the old `gemma4:e2b` blobs. Either way: run `./setup.sh` on a login node before the next submission. Since 2026-10-13 the code wants `medgemma:27b` instead (17.4 GB from `registry.ollama.ai`; its config declares no `requires`, see 5.4), and `setup.sh` deletes any stored `gemma3`/`gemma4` from `registry.ollama.ai` whose config requires Ollama newer than 0.24.0.

2. **Argon still runs the September script.** The rewrite is committed locally in `f8f76c0` but Argon's checkout only changes when `main` is pushed and pulled. See [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite) for the order. After the upgrade, a full `./setup.sh` run is required before `--preflight` will accept the env script (`NLP_ENV_VERSION=2` check).

3. **The login-node image pull was never thread-limited in the committed script** (`APPTAINER_MKSQUASHFS_ARGS` is a no-op on Apptainer 1.3.2, 3.3). The working tree fixes this with a `mksquashfs` PATH wrapper, which still needs one confirming run on Argon: `./setup.sh --force sif` on a login node, then check the transcript shows the capped pull surviving (BACKLOG B14). BACKLOG B27 tracks runbook statements that drift from the script; this pass resolves the ones it lists.

4. **README was behind the code** (resolved 2026-10-13). The README said `gemma4:e2b` in the Ollama pull command and in the `llm_analysis.py` section while the code said `gemma3:12b`; both now say `medgemma:27b`, and the README's description of value coercion matches `normalize_llm_result`. Its "curl install.sh" instruction is for workstations; Argon uses the container (3.6). (BACKLOG B24.)

5. **GPU nodes with driver 535 run on CPU.** The A10 node seen on 2026-09-14 could not be used by `ollama/ollama:latest`. `setup.sh` now pins `ollama/ollama:0.24.0`, which uses the A10 on driver 535; the next `./setup.sh` on a login node replaces the old `latest` image automatically (unconfirmed on Argon until a GPU job's placement line says GPU). Always read the placement line the preflight prints.

6. **Sample CSVs in the local `samples/` folder ingest as they are since 2026-10-15** (BACKLOG B7 fixed): the ingest skips a byte-order mark, matches header names in any case with spaces, underscores and dashes ignored, and accepts the usual other spellings (`EncounterID`, `note_csn_id`), logging which name it read as which column. Before that, `samples/notes.csv` (header `id,mrn,encounter,note_csn_id,note_date,note_type,note`) and `samples/sample_5000_deidentified.csv` (byte-order mark `EF BB BF`, second column `EncounterID`) were both refused; verified ingesting as-is on 2026-10-15 (5,000 rows in 0.3 s). `samples/notes.csv` still has a note-boundary error: note 9's CPT line sits at the head of note 10 (BACKLOG B8). `samples/` and `environment-notes/` are in `.gitignore` and must stay out of git (names and MRNs).

7. **Runtime versus walltime.** 5000 notes at 24 h walltime allows about 17 s per note for all three models together, including retries; the tier 1 preflight prints each model's per-note time and their sum. CPU inference on a dense 24 B model (`mistral-small3.2` is 15 GB of weights; its predecessor medgemma:27b decoded at about 3 tokens per second on 16 cores) cannot meet that on its own. Plan on multiple resubmissions (resume is automatic) or set `NLP_JOB_HRT` higher, and read the tier 1 projection, which counts the real rows in `notes.csv`. Most of the 5000-note corpus is not procedure notes, so a relevance gate before the LLM (BACKLOG F3) would cut the work by roughly ten times. At the full study corpus of about 100,000 notes the arithmetic is 300,000 model calls: at the local stand-ins' measured 3 to 5 s per call about 13 days of continuous model time, at the 17 s per note budget above about 20 days, so plan on that many 24 h resubmissions (resume is automatic and keeps every stored result) or on the gate. The program itself scales: the ingest, the analysis pass, the consensus and the export all stream, measured on 2026-10-15 at a flat 50 to 76 MB peak memory and seconds per phase from 20,000 to 100,000 synthetic notes (at 100,000: ingest 3.6 s, consensus 2.7 s, export 3.3 s; the real notes are about 2.4 times longer, which scales the seconds, not the memory; `tests/test_robustness.py` covers the streaming paths). What needs planning is the output: `results.csv` is about 6.7 KB per row (71% the note text, 19% the raw model responses and rejected attempts, 4% the identifiers and the consensus), so about 670 MB at 100,000 rows, beyond Google Sheets' 100 MB import limit and slow in Excel, while `results-review.csv` (the identifiers, the consensus block and the note length) is about 4% of that, some 30 MB, and is the file to open in Sheets; `data.db` about 0.8 GB; and the export is written only by a complete run, so nothing is visible until the last resubmission (BACKLOG B17). Since 2026-10-15 a chain of jobs runs the whole corpus unattended, each stopping cleanly before its walltime; 7.4 has the commands and the safeguards.

8. **Container tag pinned to 0.24.0.** Before 2026-10-13 the default was `latest` (0.32.14 when pulled), which needs driver 550+. Do not move the default past 0.24.x until Argon's GPU nodes run driver 550 or newer (BACKLOG B25).

9. **Job preflight budget dropped from 900 s to 600 s** in the rewrite (it inherits the preflight script's default). On a CPU node a 27 B model can exceed 600 s for one call. Export `NLP_PREFLIGHT_BUDGET` at `qsub` time if the gate times out (BACKLOG B26).

10. **The study's definitions are implemented in the prompt, unconfirmed on Argon.** The researcher's comments (15.5) require "biopsies taken, count unknown" to be representable and colonoscopes to be ignored; as of 2026-10-13 the prompt implements them (null count when taken but not stated, scope from the upper endoscope only, fellow from the attestation; BACKLOG B1 and B11), measured against the ten annotated notes on a workstation. Not yet confirmed on Argon: run the tier 1 preflight after pulling.

11. **The first study model changed on 2026-10-16 and has not run on Argon.** `mistral-small3.2:24b-instruct-2506-q4_K_M` replaced `medgemma:27b` (5.4; `model_candidates.md` section 8). Still to do: `./migrate.sh` then `./setup.sh` on a login node (5.6.1: the migration removes medgemma's manifest and 17.4 GB of blobs, which `setup.sh` alone keeps, and `setup.sh` fetches the 15.2 GB of the new model), the tier 1 preflight inside a GPU job (the first load of this tag on a 0.24.0 build; the placement line should read 100% GPU at about 17.7 GB), and the walltime arithmetic for the new per-note time. The 2.0 `data.db` is migrated in place (5.6.1): the medgemma block is removed and the table laid out as a fresh 2.1 one, so `results.csv` keeps the guide's column letters; without the migration the new model's block would come after the old ones. The new stand-ins for `--location local` have not been run on a workstation yet either.

---

## 13. Questions and answers

### Getting oriented

**Q: Which machine do I run which command on?**
`./setup.sh` runs anywhere and does what the node allows. Downloads need outbound HTTPS, which login nodes have and compute nodes usually do not, so build on a login node. Inference (tier 1, `app.py --analyze`) happens only inside an SGE allocation; on a login node the 1-CPU/16 GB limiter would kill it, and `nlp_start_ollama` refuses anyway.

**Q: Do I still need `--preflight`?**
Not necessarily. Plain `./setup.sh` inside `qlogin` runs the tier 1 check after confirming the build. `--preflight` is the variant that builds nothing, useful when you want to be certain nothing is downloaded or rewritten.

**Q: Where is the code I am supposed to edit?**
The Argon checkout is `/nfsscratch/Users/apkane/nlp-study/repo`, a clone of `github.com/benpodium8/nlp-study`. Develop locally, push to `main`, then run `./setup.sh` on a login node, which fast-forwards the checkout. A dirty working tree on Argon blocks the pull with a warning. `NLP_REPO_URL` points the clone at a fork or mirror.

**Q: What does `--status` tell me?**
Rewrite: whether repo, venv, sif and the exact model the code wants are `healthy`, `broken` (with a reason) or `absent`; whether the preflight, env and job scripts exist; whether another setup run holds the lock; whether `preserved/` holds user data. It runs no model and starts no server. September version: the model line only said whether the directory was non-empty.

**Q: Why does the preflight matter so much?**
Because `data_worker.py` turns every LLM error into `llm_failed = 1` and keeps going. Without the gate a broken setup yields a full-walltime run, exit 0, and a table with no LLM data.

**Q: Is re-running `setup.sh` dangerous?**
No. Healthy resources are skipped, broken ones rebuilt, interrupted downloads resume, and a second concurrent run is refused by the lock. PHI files are moved aside before a repo directory is destroyed and moved back into the new checkout. `--clean` asks for a typed `yes`.

**Q: It says another setup run is active. I am sure it is not.**
The lock is `.setup.lock/` under the project root, with host, pid and start time in `owner`. A lock from a dead process on the same host, or older than six hours, is taken over automatically. Otherwise, if you are certain, `rm -rf /nfsscratch/Users/apkane/nlp-study/.setup.lock` and re-run.

**Q: I edited `~/nlp-study.job` by hand and it was overwritten.**
Expected. Both generated scripts are rewritten on every setup run (the September version kept a `.bak`; the rewrite does not). Use `NLP_JOB_QUEUE`, `NLP_JOB_SLOTS`, `NLP_JOB_HRT`, `NLP_JOB_GPU`, `NLP_JOB_GPU_TYPE`, or pass `qsub` options on the command line.

**Q: Where is the list of known bugs and planned work?**
`BACKLOG.md` in the repo: an evaluation of the pipeline against the researcher's comments and the 5000-note corpus, 27 bug fixes and 14 features, each with file and line references and a status.

**Q: How do I run the tests?**
From the repo directory inside the venv, on any node: `python -m unittest discover -s tests`. The 27 cases in `tests/test_robustness.py` need no model or server; they cover the CSV formats the ingest accepts, the checks against the note, the export conventions and the streaming paths.

### Data

**Q: What must `notes.csv` look like?**
UTF-8 (a byte-order mark is skipped; a byte that is not UTF-8 is replaced and counted in the log), header row containing `MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note` in any case, spaces, underscores and dashes ignored, the usual other spellings accepted (`EncounterID`, `note_csn_id`, `note_text`); extra columns such as `id` are ignored. Notes may contain embedded newlines if quoted and may be any length (the csv field limit is raised). CRLF line endings are fine.

**Q: Where do I put it?**
`/nfsscratch/Users/apkane/nlp-study/repo/notes.csv`, then refer to it as `./notes.csv` because the job `cd`s into the repo.

**Q: What happens if I ingest the same file twice?**
Rows identical in all six fields are skipped with a count message. Any change to any field creates a new note with a new id.

**Q: Can I ingest without analysing?**
Yes: `python app.py --csv ./notes.csv` ingests and reports how many notes were added. This is cheap and safe on a login node since no model is involved.

**Q: Where do results go?**
`data.db` (both tables, including raw LLM text), `output/results.csv` and `output/results-review.csv` in the repo directory. All are PHI. Copy them off scratch.

**Q: Which notes did the LLM fail on?**
The job log ends with `Failed note id(s): ...`, and every note has `Failed`, `Attempts` and `Failure reason` per model in `results.csv` (the database columns `LLM_Failed`, `LLM_Attempts` and `LLM_Error`; or `sqlite3 data.db 'select id, "LLM_Error (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)" from results where "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)" = 1;'`; the result columns carry the model label and need double quotes in SQL). Those rows keep every extracted field null and are not retried on resume; see 14, "Job finished quickly, exit 0, LLM columns all empty" for reprocessing them.

**Q: Where are the researcher's requirements written down?**
As five review comments by Alexander P. Kane in `samples/Upper Gl endoscopy Scope details.docx` (also rendered in the margins of the PDF next to it). They are quoted in [15.5](#155-study-requirements-the-researchers-review-comments) and analysed in `BACKLOG.md` section 1. The README does not mention them.

### Running

**Q: How long will the run take?**
Run tier 1 with `notes.csv` staged and read the projection line for its row count. Multiply by up to 5 if the preflight warned that the raw output is not bare JSON (retries). Compare to `h_rt` (24 h by default, `NLP_JOB_HRT`).

**Q: The job ran out of walltime. Did I lose everything?**
No. Every processed note is already in `results`. Resubmit with `qsub ~/nlp-study.job --analyze` (no `--csv`). Delete or move the old `output/*.csv` so the exports regenerate.

**Q: How do I get more CPU for inference?**
`NLP_JOB_SLOTS=16 ./setup.sh` and resubmit, or `qsub -pe smp 16 ~/nlp-study.job --analyze ...`. Ollama uses the cores it sees for CPU inference; nothing currently limits it to the requested slots (BACKLOG B26). Returns diminish; measure with the preflight.

**Q: How do I use a GPU?**
Since 2026-10-16 the generated job asks for one by default: queue `UI-GPU` and `#$ -l ngpus=1` (`NLP_JOB_GPU=1`). It pins no card type, and the study models need a 24 GB card (the A10 holds each of them; a 16 GB P100 does not, and the job then runs on the CPU at minutes per note): to pin one, regenerate with `NLP_JOB_GPU_TYPE=<complex>` after reading the complex names from `qconf -sc | grep -i gpu` on a login node (the September version used `gpu_p100` for the P100 nodes). An interactive session must request the GPU too: `qlogin -q UI-GPU -pe smp 8 -l ngpus=1`, which is what `setup.sh` prints as the next step. Then confirm the preflight's placement line says 100% on GPU for every model. On 2026-09-14 an A10 node fell back to CPU because its driver (535) was older than the container's CUDA runtime required (550); the image is pinned to 0.24.0 for that. Requesting a GPU on a non-GPU queue leaves the job pending forever, and `setup.sh` warns at generation time when `NLP_JOB_QUEUE` names one; `NLP_JOB_GPU=0 NLP_JOB_QUEUE=UI` makes a deliberate CPU-only job.

**Q: Can I skip the preflight in the job?**
`NLP_SKIP_PREFLIGHT=1 qsub ~/nlp-study.job ...` bypasses it. Not recommended; it is the only thing standing between you and a silent all-null run.

**Q: How do I watch progress?**
`qstat -u apkane` for state, `tail -f ~/nlp-study-logs/nlp-study.o<JOB_ID>` for the log (one line per note, a `...` heartbeat every 60 s during a call, and a `progress` line with the projected time remaining every 10 notes; the `load` line says whether the model landed on the GPU), `~/nlp-study-logs/ollama-<JOB_ID>.log` for the server, and a read-only `sqlite3` count on `data.db` for notes done versus failed.

**Q: The job log shows `exit 143`. Bug?**
On any queue other than `all.q`, 143 usually means the memory limit was hit. `qacct -j <JOB_ID>` shows `maxvmem`. Request more slots or the `UI-HM` queue. On `all.q`, 143 means eviction by a higher-priority queue.

**Q: Why does the job script not use `-cwd`?**
The env script does `cd "$NLP_REPO_DIR"`, so the job is independent of where `qsub` was run. That is also why `./notes.csv` works from anywhere.

**Q: Does my shell environment leak into the job?**
Yes, `-V` is on by default. That is how `NLP_PREFLIGHT_REPS`, `NLP_PREFLIGHT_BUDGET`, `NLP_CONTEXT_LENGTH` and `NLP_SKIP_PREFLIGHT` reach the job. The job sets its own `OLLAMA_HOST`, overriding any inherited one.

**Q: Where does the email go?**
`-m ea` sends at end and abort. No `-M` is set, so it goes to the account default for your HawkID.

**Q: The compute node has no internet. Can I still run `setup.sh` there?**
Yes. It warns, skips the pull, and uses whatever is already on scratch. If something is missing it stops and tells you to build on a login node first (7.6).

**Q: Can I run this on my own machine?**
Yes, that is the README's deployment; see section 8. The Argon env script also works off-cluster with `NLP_ALLOW_INFERENCE=1` if Apptainer is installed.

### Environment

**Q: Why is Ollama in a container?**
Ollama's binary needs glibc 2.27 or newer; CentOS 7 has 2.17. The Apptainer image carries its own userland.

**Q: Why is `OLLAMA_MODELS=/models` set explicitly?**
The image defaults to `/root/.ollama/models`, which is not writable for an unprivileged Apptainer user. The host directory is bind-mounted at `/models`.

**Q: How can the model be downloaded without running Ollama?**
The rewrite talks to the model's registry directly (`registry.ollama.ai`, or `hf.co` for `hf.co/...` names): it fetches the manifest for `name:tag`, then each blob listed in it, into `manifests/<host>/<ns>/<name>/<tag>` and `blobs/sha256-<hex>`. That is exactly the layout `ollama serve` reads, so the server sees the model on first start.

**Q: Why is Python 3.12 not loaded with `module load`?**
Argon only publishes 3.8.8 modules. A user-built 3.12 lives in `/Users/apkane/temp/build_python/python3.12`. If that directory moves, the venv is detected as broken and rebuilt by `setup.sh` (needs a login node). Re-runs find the interpreter through the venv's `pyvenv.cfg`, so `NLP_PYTHON` is only needed the first time.

**Q: Why does pip refuse to build from source?**
`--only-binary=:all:` is the default so that a missing wheel fails fast with the package name instead of a ten-minute failed compile on GCC 4.8. New dependencies must ship `manylinux2014` (glibc 2.17) wheels.

**Q: I installed a package by hand in the venv and setup does not notice.**
The rewrite re-runs pip only when `requirements.txt` changes (sha256 stamp), when the requirements stop importing, or with `--force deps`. Put the dependency in `requirements.txt`.

**Q: Is `/localscratch` useful?**
It is node-local and not shared between the login node and the compute node, so the project does not use it. Everything is on `/nfsscratch`, which both see.

**Q: Why did the Ollama log say `default_num_ctx=4096`?**
With no VRAM detected, Ollama picks a 4096-token default context. The rewrite passes `OLLAMA_CONTEXT_LENGTH=8192` (`NLP_CONTEXT_LENGTH`) into the container so long notes plus the prompt overhead (about 1,700 tokens, measured with the Gemma 3 tokenizer) fit. 8192 is the study's window; every model fits the A10 with it (it was also the largest window medgemma:27b fitted in before it was replaced on 2026-10-16), and a larger window needs more VRAM for every model. The 5000-note corpus has notes up to about 41,000 characters (roughly 13,000 tokens), and about 2% of notes (over roughly 18,000 characters) do not fit; `data_worker.py` fails those without a model call (BACKLOG B10).

**Q: Is the image pull really single-threaded on the login node?**
With the committed script, no: the variable it exports is only honoured by `apptainer build` on Apptainer 1.4.0 and later, and Argon has 1.3.2. The working-tree script caps it through a `mksquashfs` wrapper placed first on `PATH`; that fix has not yet been run on Argon. See 3.3 and BACKLOG B14.

### Policy

**Q: Do I need to ask HPC staff to install anything?**
No. Everything is user-space on scratch and in `$HOME`. The central-install licence review in the Software Installations page does not apply.

**Q: Is any of this PHI leaving the machine?**
No network calls are made by the pipeline. Ollama listens on `127.0.0.1` only. The only outbound traffic is `setup.sh` on a login node pulling code, wheels, the image and the weights.

---

## 14. Troubleshooting

Symptoms are grouped by where they appear. Each entry gives the cause and the fix.

### Ingest

#### CSV must contain columns

```
Error: CSV is missing the column(s) NoteDate. Required: MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note (any case; spaces, underscores and dashes are ignored; EncounterID, note_csn_id and the like are accepted). Header found: MRN, EncounterID, NoteCsnID, NoteType, Note
```

Cause: a required column is absent from the header. The message lists the names it could not find and the header it did find (`REQUIRED_CSV_COLUMNS` and `CSV_COLUMN_ALIASES` in `database.py`). Since 2026-10-15 the three variants that used to need a `sed` on the file ingest as they are: a lower-case snake_case header from a database export (`id,mrn,encounter,note_csn_id,note_date,note_type,note`), a UTF-8 byte-order mark at the start of the file (Excel "CSV UTF-8" export), and `EncounterID` for `Encounter`. The ingest log says what it did (`byte-order mark skipped; 'EncounterID' read as Encounter`). If the file really lacks a column, add it. To see the header bytes:

```bash
head -c 80 notes.csv | xxd
```

#### `Error: CSV file not found`

The path is resolved relative to the current directory. In the job that is the repo dir after `cd`. Interactively, run from the repo dir or give an absolute path.

#### `Read N row(s) from ...: 0 new note(s) added, N duplicate(s) skipped.`

Not an error. Identical rows already exist in `notes`.

#### `Header of notes.csv: byte-order mark skipped; 'EncounterID' read as Encounter.`

Informational. The file was read as it came; the line says which header name served which column.

#### `Warning: N byte(s) in notes.csv were not valid UTF-8 ...`, `... NUL byte(s) ... were dropped`, `... row(s) ... had fewer cells than the header`

The file was read anyway; the count says how much was affected. A quote that spans a replaced character cannot match its note, so that note's answers may be rejected and the note flagged; if the count is large, re-export the file as UTF-8.

#### `Nothing to do: every note already has a result from <model>.`

Not an error: that model has a stored result for every note, and resume skips them. To recompute some notes with a model, clear its `LLM_Failed` column for those notes (see "Job finished quickly, exit 0, LLM columns all empty" and 15.3 item 26). An empty `notes` table shows as `0 notes` in the run header: ingest first, or pass `--csv`.

### setup.sh

#### `No outbound HTTPS from this node: existing resources are used as they are; nothing can be downloaded.`

A warning, not an error. You are probably on a compute node. The run continues offline and only fails if something must be built (next entry).

#### `[repo] has to be built or updated, but this node has no outbound internet. Run ... on a login node first.`

(Also `[sif]`, `[model]`, `[deps]`.) Something is absent or broken and cannot be fetched here. Run `./setup.sh` on a login node, then come back.

#### `Another setup run is active on <host> (pid N, since <date>). Wait for it, or if it is dead:  rm -rf .../.setup.lock`

A second run was refused by the lock. If the first run is really gone (different host, or less than six hours old so the stale rule did not fire), remove the lock directory as the message says.

#### `Taking over a stale setup lock (host ..., pid ...).`

Informational. The previous owner is dead on this host or the lock is older than six hours.

#### `'sha256sum' not on PATH`

The rewrite needs coreutils `sha256sum` for blob verification. It is present on CentOS 7; this only appears on a stripped-down host.

#### `git clone failed. On an old TLS stack try: git config --global http.sslVersion tlsv1.2`

Old git/curl/NSS stack on CentOS 7 failing to negotiate TLS with GitHub. Run the suggested command and re-run.

#### `git: unknown option -C`

Expected on git 1.8.3.1. The script does not use `-C`. If you are typing it by hand, use `(cd <dir> && git ...)`.

#### `[repo] local modifications present; not pulling`

Someone edited files in the Argon checkout. `cd` there, `git status`, then stash or commit before re-running.

#### `[repo] pull failed (offline or diverged); keeping the current checkout`

Either the node is offline or `main` was force-pushed. Resolve in the checkout by hand (`git fetch && git status`).

#### `[repo] not restoring X: the fresh checkout has one; yours stays in .../preserved/<timestamp>`

After a repo rebuild, a preserved PHI file has the same name as something in the new checkout (for example a committed `notes.csv`, which must never happen). Your copy is safe in `preserved/`; merge by hand.

#### `Earlier runs left user data under .../preserved/ (from --clean or an interrupted rebuild); review it.`

Previous PHI stashes exist on scratch. Copy them to durable storage or delete them; they are subject to the scratch purge.

#### `LOCAL_MODEL not found in llm_analysis.py; set MODEL=name:tag and re-run.`

The rewrite's parser needs a line starting exactly with `LOCAL_MODEL = "name:tag"` (no indentation). Restore that form or pass `MODEL=`.

#### `No Python >= 3.10 found (Argon's modules stop at 3.8)`

`module load` offers only 3.8.8. If a venv already exists, its base interpreter (from `pyvenv.cfg`) is tried automatically. Otherwise point at the user build:

```bash
NLP_PYTHON=/Users/apkane/temp/build_python/python3.12/bin/python3.12 ./setup.sh
```

#### `[venv] unusable (bin/python does not run ...); removing the existing copy and rebuilding`

The venv symlinks to the user-built Python. Restore that directory or let the script rebuild the venv (login node).

#### `[deps] were installed earlier but no longer import (scratch purge?); rebuilding the venv`

The stamp matched but the packages are gone or broken. The venv is recreated and pip runs again, which needs internet.

#### `[deps] installed, but some module name differs from its package name; later runs trust pip instead of importing`

A requirement's import name is not its package name (for example a package whose module is `yaml`). The stamp is written as `nocheck`, so future runs skip the import test for all requirements. Harmless, but the "no longer import" safety net above is off until the names line up.

#### `pip failed. With --only-binary this means no wheel exists for this platform and Python`

Wheels-only mode found no `manylinux2014` wheel for some package. Identify it:

```bash
source /nfsscratch/Users/apkane/nlp-study/venv/bin/activate && pip download --only-binary=:all: --no-deps -d /tmp/wt <package>
```

Then pin an older version with a compatible wheel, or allow a source build for that one package with `PIP_INSTALL_ARGS='' ./setup.sh --force deps` (slow, usually fails on GCC 4.8).

#### `[sif] unusable (not a valid SIF image); removing the existing copy and rebuilding`

Truncated or corrupt image. The script deletes it and the `.partial`, clears the Apptainer layer cache and re-pulls. With the committed script the conversion uses every core for a few minutes (3.3); the working-tree script caps it through the `mksquashfs` wrapper and logs `mksquashfs capped to N thread(s)`.

#### `[sif] mksquashfs not found; the pull will use Apptainer's default, uncapped`

The wrapper could not locate a real `mksquashfs` on `PATH` or in the usual `sbin`/`bin` directories. The pull proceeds without the cap; on a login node prefer pulling from a `qlogin` session that has internet.

#### `[sif] apptainer.conf pins mksquashfs; its CPU and memory use cannot be capped here`

The site configuration sets `mksquashfs path`, so Apptainer ignores `PATH` and the wrapper. Same advice as above.

#### `<host> has no '<model>' (or it is gated). Check LOCAL_MODEL in llm_analysis.py (or MODEL=...).`

The `name:tag` does not exist on the registry, or (on `hf.co`) the repository needs a Hugging Face login, which `setup.sh` does not support. Check spelling and tag, or pass `MODEL=`.

#### `<model> requires Ollama X, but the image has 0.24.0 ...`

The model's config declares a minimum Ollama version newer than the pinned image. Every `gemma3`/`gemma4` tag on `registry.ollama.ai` does this now. Nothing large was downloaded. Use a build without that requirement in `LOCAL_MODEL` or the `MODELS` block (`mistral-small3.2:24b-instruct-2506-q4_K_M` declares none; a GGUF from `hf.co` is the other option), or move the image up with `NLP_OLLAMA_IMAGE` and accept CPU inference on driver-535 nodes. The field is advisory: a model that is known to load on the image despite it (today `gpt-oss:20b`, whose config says 0.30.0) is listed in `NLP_REQUIRES_VERIFIED`, which turns this stop into the log line `[model] <name>: its config says it requires Ollama X, but it is known to load on 0.24.0`.

#### `[model] removing <host>/<ns>/<name>/<tag>: requires Ollama X, the image has 0.24.0`

Informational. A stored model the pinned Ollama cannot run was deleted (for example the registry `gemma3:12b` or `gemma4:e2b`); its blobs are pruned on the next line. The check runs only when the image tag carries a version number.

#### `[model] download interrupted (attempt N/5); resuming in 10 s`

Network trouble, or (working tree) a stall: curl now gives up when the rate stays under 1 KB/s for 60 s instead of hanging. It resumes from the `.partial`. Only a problem if all five attempts fail.

#### `[model] checksum mismatch on ... (attempt N/5); downloading it again`

A corrupted blob was detected by sha256, deleted, and is re-downloaded within the same five attempts.

#### `[model] blob ... could not be downloaded intact after 5 attempts. Re-run to try again.`

Five attempts exhausted. Re-run `./setup.sh`; completed blobs are kept, the failing one resumes.

#### `[model] pruned N blob(s) that no manifest references`

Informational. Weights for a model the code no longer asks for were deleted (for example `gemma4:e2b` after the move to `gemma3:12b`, or the Hugging Face Gemma 3 12B build after the move to `medgemma:27b`, or `medgemma:27b` itself after the move to `mistral-small3.2` on 2026-10-16).

#### `Killing orphaned Ollama server(s) on this login node`

A September-version run left `ollama serve` behind. The rewrite kills it so it stops eating the login-node memory budget. Harmless.

#### `Interrupted. Re-run to resume; partial downloads continue where they stopped.`

You pressed Ctrl-C or the shell was terminated. The lock is released and curl is killed. Re-run.

#### `Non-interactive; pass --yes to confirm.`

`--clean` from a non-TTY needs `--yes`.

#### Process killed on a login node with no clear error

The login-node limiter killed the largest process (1 CPU, 16 GB per user). Either you ran inference where you should not have (use `qlogin`), or, with the committed script, the image pull's SquashFS conversion exceeded the cap (3.3; the working-tree wrapper is the fix; meanwhile re-run, or pull from a `qlogin` session with internet).

### Preflight

#### `~/nlp-study-env.sh is missing or from an older setup.sh. Re-run ... first (on a login node if anything needs downloading).`

`--preflight` requires an env script with `NLP_ENV_VERSION=2`. Run a full `./setup.sh`, then retry.

#### `Login node: static checks only; inference waits for a compute node.`

Tier 0 ran. Repeat `./setup.sh` (or `--preflight`) inside `qlogin`.

#### `Not inside an SGE job: static checks only (NLP_ALLOW_INFERENCE=1 overrides this off-cluster).`

A non-login host without SGE variables. Use `qlogin`; off-cluster only, set the variable.

#### `llm_analysis.LOCAL_MODEL is 'mistral-small3.2:24b-instruct-2506-q4_K_M' but setup fetched 'medgemma:27b'`

The repo changed models after setup, or `MODEL=` was given. Run `./setup.sh` on a login node; it follows the repo.

#### `model blobs missing or truncated: [...]` or `no usable manifest at ...`

Tier 0 found the on-disk model store incomplete for the model the code wants. Run `./setup.sh` on a login node; it resumes the download. This is the on-disk form of [open item 1](#12-open-items-and-known-discrepancies).

#### `'mistral-small3.2:24b-instruct-2506-q4_K_M' is not served (server has: [...])`

The server is running but does not see the model: `OLLAMA_MODELS` inside the container does not point at the fetched blobs, or the blobs are for a different tag. Check the bind mount (`--bind <models_dir>:/models --env OLLAMA_MODELS=/models`).

#### `OLLAMA_HOST is not set`

`llm_analysis.py` uses the module-level `ollama` client, which reads `OLLAMA_HOST` when `import ollama` runs. Run `nlp_start_ollama` first; it exports the variable and starts the server.

#### `cannot reach http://127.0.0.1:<port>/api/tags`

No server on that port. Run `nlp_start_ollama` (interactive) or read `~/nlp-study-logs/ollama-<JOB_ID>.log` (batch).

#### `ollama default client -> ..., not ...`

`OLLAMA_HOST` was set after `import ollama`. Export it, then start Python.

#### `nlp_start_ollama: refusing on a login node (1 CPU / 16 GB cap). Use qlogin.`

Intended. Get an allocation.

#### `nlp_start_ollama: not inside an SGE job.`

You are on a compute-class host without SGE variables. Use `qlogin`/`qsub`. Off-cluster only: `NLP_ALLOW_INFERENCE=1`.

#### `Ollama not answering after 180 s` or `Ollama died on startup; log follows` or `Ollama did not start; see the log above.`

Read the log printed (`~/nlp-study-logs/ollama-*.log`). Common causes: bad bind mount, unwritable `OLLAMA_MODELS`, a container that cannot start under this Apptainer version.

#### `call 1: no response within 600s`

The model is generating on CPU when a GPU was expected, is emitting a very long output, or the node is contended. Read the placement line. Raise `NLP_PREFLIGHT_BUDGET` only if CPU inference is intended.

#### `Endoscopy == 0, expected 1` or `Colonoscopy == 1, expected 0` (hard failure)

The model misread the synthetic note. For `Colonoscopy`, be aware the production prompt's trigger-word rule invites exactly this error on "No colonoscopy was performed" (BACKLOG B11). Do not submit until the prompt and the gate agree.

#### `output is not bare JSON, so the repair layer is doing work`

Warning only, but each JSON failure costs a full regeneration and `data_worker` retries up to 5 times. Expect up to 5x runtime. Every saved LLM response in `samples/working_results.csv` begins with a markdown fence, so expect this warning until the call uses `format=` (BACKLOG B9).

#### `partly or fully on CPU, expect it to be slow`

`/api/ps` reports less than 100% of the model in VRAM on a node that has `nvidia-smi`. Usually the driver-too-old case; see below.

### Batch job

#### Job stays in `qw` indefinitely

Most likely the GPU request (`#$ -l ngpus=1`, on by default) on a queue without GPU nodes, from `NLP_JOB_QUEUE=UI` set without `NLP_JOB_GPU=0` (`setup.sh` warns at generation time), or a card-type complex (`NLP_JOB_GPU_TYPE`) that no node offers. Regenerate with the default queue, or submit with `-q UI-GPU` on the command line. `qstat -j <JOB_ID>` shows scheduling messages.

#### `Preflight failed; not starting app.py.`

Intended fail-fast. The lines above it name the problem; see the Preflight section.

#### `app.py exited 143 ...`

If the log shows `Stopped by SIGTERM.`, the job was evicted (`all.q`) or deleted; resubmit, the run resumes. Otherwise, outside `all.q`: memory limit. Check `qacct -j <JOB_ID>` for `maxvmem`, then add slots (`NLP_JOB_SLOTS=16` or `-pe smp 16`) or use `-q UI-HM`.

#### `app.py exited 137 ...`

SGE sent KILL: `h_rt` was reached before the `--stop-after` limit (a note that ran very long; raise `NLP_STOP_MARGIN_MINUTES`), or TERM did not stop it. At most the note in progress is lost; the next job resumes.

#### `app.py stopped ... with work left`

Normal for a job in a chain: the `--stop-after` limit was reached (`outcome   paused`). The next job, or `qsub ~/nlp-study.job --analyze`, resumes.

#### `Error: another run is using this database (...)` / `app.py did not start: another run holds data.db`

Another run holds `data.db.lock` and refreshed it within the last 3 minutes: an overlapping job (check `qstat -u apkane`) or an interactive run. Let it finish. If you are certain none is running (the host and pid in the message are gone), delete `data.db.lock` and resubmit.

#### `Taking over the run lock data.db.lock: ...`

Informational. The previous run was killed (or its host is gone) without releasing the lock.

#### `STOPPED ... this run lost the run lock`

The run was paused (SGE suspends with SIGSTOP) long enough for another run to take the database over; it stopped writing. Check that only one job runs on the database.

#### `data.db failed its integrity check: ...`

The database is damaged (a node or filesystem failure mid-write). The message gives the commands: move `data.db` aside and copy `data.db.backup` over it (the copy taken, after the same check, at the start of the previous run), then resubmit. The notes analysed since then are analysed again.

#### Job finished quickly, exit 0, LLM columns all empty

Something started failing after the preflight passed (for example the Ollama server died mid-run). The job log has a `FAILED` line per note with the error, the final summary lists the failed ids, and `LLM_Error` in `results` keeps the message. Count failures:

```bash
sqlite3 data.db 'select count(*) from results where "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)"=1;'
```

Those notes will not be redone on resume because the model's `LLM_Failed` column is set. Clear it to reprocess them with that model (the next run overwrites the model's whole block for those notes):

```bash
sqlite3 data.db 'update results set "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)" = null where "LLM_Failed (mistral-small3.2:24b-instruct-2506-q4_K_M@8192)" = 1;'
```

Then resubmit with `--analyze` and regenerate the export. (A `--reprocess-failed` flag is BACKLOG B21.)

#### `LLM call failed: ... ReadTimeout` (or `timed out`)

No byte came from the server for `NLP_LLM_TIMEOUT` seconds (default 1800) during one call. The note is recorded as a failed call; three in a row stop the pass after the server is re-probed. Read `~/nlp-study-logs/ollama-<JOB_ID>.log`; raise the variable only for a model known to be that slow.

#### `Failure reason` says `the note is too long for the model: the server counted N prompt tokens ...`

The length estimate let the note through, but the model's own tokenizer needed more tokens than the window holds together with the answer, so the server had cut the prompt; the answer was discarded and the note counts as too long, flagged for a human to read. Expected now and then for notes near the cut and for a model whose tokenizer is denser than Gemma 3's.

#### `File(s) 'output/results.csv' already exist. Export skipped; ...`

Write-once protection for the pair `results.csv` and `results-review.csv`: if either exists, neither is written. Move both aside and re-run `--analyze` (the export is cheap when nothing new is pending).

#### NVIDIA driver too old

```
level=WARN source=cuda_compat.go msg="NVIDIA driver too old" device="NVIDIA A10" compute=8.6 driver=535 required_driver="550 or newer"
```

The container's CUDA runtime is newer than the node driver. Ollama silently uses CPU (0.30.11 and later) or crashes with `device kernel image is invalid` (0.30.0 to 0.30.10). The default image is now `0.24.0`, the last release that works with driver 535; if you see this warning, the image was overridden or is the old one. Re-pull the default on a login node:

```bash
./setup.sh --force sif
```

Then re-run the preflight and read the placement line.

#### `user overrode visible devices CUDA_VISIBLE_DEVICES=0`

Informational. The SGE GPU allocation sets `CUDA_VISIBLE_DEVICES`. Only matters if a GPU is expected and not found; then unset it and retry as the log suggests.

#### Where did my results go after a few weeks?

Scratch purge. Copy `output/` and `data.db` to durable storage after every run. `preserved/` is also scratch. If the venv vanished too, `./setup.sh` notices the dependencies no longer import and rebuilds it.

### Upgrading from the September script

#### I pushed the rewrite but Argon still behaves like the old script

The checkout runs whatever is on disk. Pull first (`git pull --ff-only` in the repo), then run `./setup.sh`. See [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite).

#### The old `ollama-setup.log` and `~/nlp-study-setup-*.log` files stopped appearing

Expected. The rewrite writes everything under `~/nlp-study-logs/` and deletes setup transcripts after 30 days.

---

## 15. Software development constraints

Things to hold in mind while changing the code, grouped by the layer they affect. Where a constraint corresponds to a known defect, the BACKLOG item is named.

### 15.1 Platform

1. **Target is CentOS 7, glibc 2.17, GCC 4.8, Bash 4.2, git 1.8.3.1, Apptainer 1.3.2.** No source builds of C extensions. Every new Python dependency must publish a `manylinux2014`-compatible wheel for CPython 3.12 x86_64. `manylinux_2_28` wheels will not install. Check with `pip download --only-binary=:all:` before adding to `requirements.txt`.
2. **Python 3.12 is a user build, not a module.** `.python-version` pins `3.12.14`, the version of the Argon venv. `setup.sh` reads only major.minor from the file. Keep the code compatible with 3.10+ (the installer accepts 3.10 through 3.13) unless you are willing to also tighten `check_python` in `setup.sh`.
3. **Compute nodes may have no internet.** Anything that downloads at runtime (a Hugging Face model, `ollama pull`) must happen in `setup.sh` on a login node. Today nothing in the code downloads at runtime; if you add a dependency that does, give `setup.sh` a step that fetches it.
4. **Login nodes: 1 CPU, 16 GB, process killer.** Nothing that touches the model may run there. Tier 0 checks are the ceiling, and the rewrite enforces this in `nlp_start_ollama` by hostname and SGE variables. Keep the hostname check if you touch that function; `NLP_ALLOW_INFERENCE` deliberately does not override it.
5. **Apptainer 1.3 has no pull-time mksquashfs option.** `APPTAINER_MKSQUASHFS_ARGS` is the environment form of `apptainer build --mksquashfs-args` (new in 1.4.0) and `pull` never reads it; the committed script's export of it did nothing. The working tree caps the conversion with a generated `mksquashfs` wrapper placed first on `PATH` during the pull (BACKLOG B14). That only works while Apptainer resolves `mksquashfs` through `PATH`, so if Argon's `apptainer.conf` ever pins `mksquashfs path` the cap silently disappears (the script warns). Confirm on Argon with `./setup.sh --force sif` before relying on it.
6. **`setup.sh` edits must stay Bash 4.2-safe.** No `"${arr[@]}"` on a possibly-empty array under `set -u` (use `${arr[@]+"${arr[@]}"}` as the pip call does), no `git -C`, mind `${VAR-default}` versus `${VAR:-default}` (`PIP_INSTALL_ARGS=""` must mean "no flags"). `set -Eeuo pipefail` is on; `. activate` and `module` need `set +u` around them, as the script already does. Outside the venv, `python` on CentOS 7 is Python 2.7, so helper code must call `python3` or the venv interpreter (as `manifest_blobs` does). The script carries shellcheck directives; keep it shellcheck-clean.
7. **`NLP_SETUP_LIB_ONLY=1 source setup.sh`** loads the functions and returns before `main`; use it for shell tests instead of running the script.
8. **Bump `ENV_VERSION`** whenever the shape of the generated env or job script changes, because `--preflight` refuses an env script with a different version.
9. **Only one setup run per scratch tree.** The lock is a `mkdir` under the project root, with host, pid and epoch in `owner`; stale after six hours or when the owner pid is dead on the same host. Any new long-running step belongs inside the locked region, and anything that writes files a job may be sourcing must go through `atomic_write`.
10. **The dependency stamp assumes import name equals package name.** `deps_importable` imports each requirement by its distribution name. A package whose module differs (for example `PyYAML`/`yaml`) flips the stamp to `nocheck` and disables the "deps vanished, rebuild venv" safety net for everything. Prefer packages where the names match, or extend the check.
11. **Scratch is ephemeral.** Never treat `/nfsscratch` as the only copy of PHI results. Never write PHI under git-tracked paths; `.gitignore` covers `data.db`, `output/`, `notes.csv`, `samples`, `environment-notes`, `.venv`, `__pycache__`. `setup.sh` restores preserved PHI into a fresh checkout only if the checkout has no file of the same name, so never commit a `notes.csv` or `data.db`.

### 15.2 Model and Ollama

12. **`LOCAL_MODEL` in `llm_analysis.py` is parsed by `sed`.** Keep it a single line of the form `LOCAL_MODEL = "name:tag"` at column 0. Any model change requires `./setup.sh` on a login node to fetch the weights (and it prunes the old ones), and the README should be updated in the same commit (BACKLOG B24). The models a run sends each note to are `MODELS` in `llm_analysis.py` (three: `mistral-small3.2:24b-instruct-2506-q4_K_M`, `gemma4:26b-a4b-it-q4_K_M`, `gpt-oss:20b`); each writes its own block of result columns labelled `<name>@<num_ctx>` (Appendix D), so adding a model adds a block without touching existing results. `setup.sh` and `migrate.sh` read the block with `sed` (`detect_repo_models`): keep one `ModelSpec` per line, the first with `LOCAL_MODEL` and every other with its name as a double-quoted literal first argument. The preflight checks and exercises every model. The small stand-ins `--location local` runs instead are `LOCAL_MODELS` (`ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`), under their own labels, so a workstation test never mixes with study results; `setup.sh` does not fetch them.
13. **Model size budget.** `mistral-small3.2:24b-instruct-2506-q4_K_M` is 15.2 GB of Q4_K_M weights and needs about 17.7 GB of VRAM once the 8192-token KV cache (1.3 GB: 40 layers, 8 KV heads, full attention) and compute buffers are allocated (`model_candidates.md` section 8; its predecessor `medgemma:27b` needed about 20 GB), so the three models together still need a 24 GB card: the A10 holds each of them, the P100 (16 GB) does not hold gemma4 and is within a gigabyte for gpt-oss, and a larger `NLP_CONTEXT_LENGTH` spills gemma4 first. The September job script pinned `gpu_p100` for the 7 to 8 GB models of that time (`gemma4:e2b`, then Gemma 3 12B); the rewrite requests only `ngpus=1`, so pin the A10 or a larger card with `#$ -l <complex>`, or a smaller card will silently fall back to CPU. CPU inference of the dense 24 B model needs about 18 GB of RAM plus headroom and decodes at a few tokens per second (medgemma:27b did 3.2 on 16 cores). The other two models: `gemma4:26b-a4b-it-q4_K_M` is 18.0 GB (about 20 GB loaded) and `gpt-oss:20b` 13.8 GB (about 15 GB loaded); each fits the A10 alone, no two together, hence `OLLAMA_MAX_LOADED_MODELS=1` and the model-by-model run order.
14. **The module-level `ollama` client is wired by `OLLAMA_HOST` at import time.** Do not create `ollama.Client()` with a hard-coded host or port, and do not import `ollama` before the host is exported. The port is random per job to avoid collisions with other users on the node. Since 2026-10-15 `llm_analysis()` builds `ollama.Client(timeout=LLM_TIMEOUT)` for each call and each model load (B15); it reads `OLLAMA_HOST` at call time, which keeps this rule. `ollama_status()` in `llm_analysis.py` follows that rule: the no-argument `python app.py` builds a short-lived `ollama.Client(timeout=3)` that reads `OLLAMA_HOST` at call time and only lists models.
15. **Decoding is greedy and schema-constrained.** `ollama.chat()` passes `format=RESPONSE_SCHEMA` (the JSON schema of the ten study fields: 0/1 enums, nullable strings with `maxLength` 1000) and `GENERATION_OPTIONS` (`temperature` 0, `seed` 0, `num_ctx` from `NLP_CONTEXT_LENGTH`, `num_predict` 2048), so the reply is always a bare JSON object and the same note gives the same answer on the same model build; Ollama's server default temperature would be 0.8. Greedy decoding can lock into a repetition loop (seen with gemma3:4b), which the schema cuts off at 1000 characters; a value that reaches the cap raises `RetryableLLMError`, as do invalid JSON and any validation failure, and the retry prompt says why the previous answer was rejected. A greedy retry would repeat the loop, so `generation_options()` makes attempts 2+ sample at temperature 0.8 with `repeat_penalty` 1.1 and `seed` = attempt number (still reproducible). Schema-constrained output needs Ollama 0.5 or newer; confirm the pinned 0.24.0 honours `maxLength` with the preflight. Re-run the preflight after any change here.
16. **Context window.** The rewrite sets `OLLAMA_CONTEXT_LENGTH=8192` on the container (`NLP_CONTEXT_LENGTH`), and `llm_analysis.py` passes the same value as `num_ctx` on every call, so a workstation run uses the same window as a job. The September script and a workstation install without it get Ollama's default, 4096 on CPU. The prompt overhead is about 1,700 tokens; the 5000-note corpus has notes up to about 41,000 characters (roughly 13,000 tokens). Ollama truncates an over-long prompt server-side, keeping 5 leading tokens plus the tail so the system prompt is lost, and the client sees a normal response; that is why `data_worker.py` fails any note that `llm_analysis.note_fits_context` says would not fit, without calling the model (BACKLOG B10, fixed 2026-10-14). The default 8192 is the study's window, and every model fits the A10 with it; a larger `NLP_CONTEXT_LENGTH` moves the cut up but needs more VRAM for every model (gemma4:26b-a4b, the largest loaded, has about 3.5 GB to spare), and the preflight prints both the cut and the placement. Since 2026-10-15 the server's own token counts are checked after every call as well: prompt plus answer tokens over `num_ctx` means the context was shifted or cut, and that answer is discarded and the note recorded as too long (`PromptCutError`).
17. **`OLLAMA_KEEP_ALIVE=-1` keeps the model loaded for the whole job.** Keep it; reloading a 17 GB model per note would dominate runtime. `OLLAMA_MAX_LOADED_MODELS=1` keeps a single model resident: the three models run one after another and no two of them fit the A10 together. `OLLAMA_NUM_PARALLEL=1` matches the single-threaded `data_worker`. Nothing limits llama.cpp's threads to the requested slots (BACKLOG B26).
18. **The container tag is pinned.** Now pinned to `0.24.0`, the last release that uses a driver-535 GPU; a newer tag needs driver 550+ (BACKLOG B25). The Python packages, including `ollama` 0.6.2, are pinned in `requirements.txt` to the Argon versions.
19. **GPU use requires three things to line up:** a `*-GPU` queue, `#$ -l ngpus=1` (and a card-type complex if you need one), and a node driver the container's CUDA runtime accepts. `nlp_start_ollama` only passes `--nv` when `nvidia-smi -L` works. Since 2026-10-16 the generated job supplies the first two by default (`NLP_JOB_QUEUE=UI-GPU`, `NLP_JOB_GPU=1`); the card type is `NLP_JOB_GPU_TYPE`. Verify with the preflight placement line every time.
20. **The on-disk model store is Ollama's own layout.** The rewrite writes `manifests/<host>/<ns>/<name>/<tag>` and `blobs/sha256-<hex>` with curl, removes models whose config `requires` a newer Ollama than the image, and prunes unreferenced blobs. If Ollama changes that layout, `create_model`, `verify_model`, `prune_model_store` and the preflight's tier 0 model check all need updating together.

### 15.3 Pipeline semantics

21. **LLM failures are swallowed by design.** Any change to the schema, prompt or model must be followed by a tier 1 preflight; nothing else will tell you it broke. If you add a field, update all of: the prompt schema text, `EXPECTED_FIELDS`, `BOOL_FIELDS` if boolean, `normalize_llm_result` if integer, `RESULT_FIELDS` in `database.py` (the table schema, the insert and the export query are all built from it, and `create_db` adds the new column to an existing database), the layout tables in `export.py` (the module asserts that every stored column has a title), `VOTED_FIELDS` or `EVIDENCE_FOR_FLAG` in `consensus.py`, `results-csv-guide.md`, and `HARD`/`SOFT` plus the synthetic note in `setup.sh`.
22. **Keep `except Exception` in `llm_analysis()`, not `except BaseException`.** The preflight's timeout is a `BaseException` subclass precisely so that wrapper cannot swallow it.
23. **Validation rejects extra fields and non-0/1 booleans, and every rejection is retried.** `format=RESPONSE_SCHEMA` keeps the reply to the ten fields; `normalize_llm_result` then coerces `true`/`false` and numeric strings to integers, fills null booleans with 0, treats a non-numeric count as "not stated" (null) and keeps the count and the taken flag consistent; a value that still fails validation, like an answer the note does not support (`verify_against_note`), raises `RetryableLLMError` and is retried with the reason in the prompt (BACKLOG B9, fixed 2026-10-13).
24. **Retries cover invalid JSON, validation failures, answers the note does not support and repetition loops**, up to 5 attempts, each a full generation (attempts 2+ sampled, item 15). A failed call, a note too long for the window and a prompt the server counted as cut are not retried. Budget runtime accordingly and prefer fixing the prompt or the checks over raising the retry count.
25. **`CREATE TABLE IF NOT EXISTS` does not migrate.** `create_db` adds any `results` column missing from an existing `data.db` (`ALTER TABLE ... ADD COLUMN`; existing rows get null), which covers adding a field. Anything else (renaming or retyping a column, changing `notes`) still needs a fresh database or a hand-written migration (BACKLOG B19). The 2.0-to-2.1 move has one: `migrate_db.py` (5.6.1) removes a retired model's block and reorders the table by rebuilding it, since SQLite before 3.35 cannot drop a column and no version can reorder one.
26. **Resume logic keys on a model's block being filled, not on freshness.** A note is pending for a model while that model's `LLM_Failed` column is null. Changing the prompt does not reprocess notes a model has answered; clear the model's `LLM_Failed` for those notes (14, "Job finished quickly, exit 0, LLM columns all empty") to recompute them with that model. A new model name gets a new block and processes every note.
27. **Relative paths everywhere.** `data.db` and `output/` are resolved from the current directory. Run from the repo directory, or refactor to pass a path (BACKLOG B18).
28. **The export is write-once.** `output/results.csv` and `output/results-review.csv` are written together and skipped together if either exists. After a resume the file on disk is stale (BACKLOG B17).
29. **Ingest dedup is whole-row equality**, against the database and within the file, on an index of MRN, encounter and CSN since 2026-10-15 (5,000 rows in 0.3 s). A trailing space in one field creates a second note; identical notes under different CSN ids are analysed twice (BACKLOG B16).
30. **CSV contract** (`REQUIRED_CSV_COLUMNS` and `CSV_COLUMN_ALIASES` in `database.py`): header names are matched in any case with spaces, underscores, dashes and a byte-order mark ignored, and the usual other spellings are accepted; the error names the missing columns and the header found. Bytes that are not UTF-8 are replaced with U+FFFD and NUL bytes dropped, each counted in the log; the csv field limit is raised for long notes; a row with fewer cells than the header is stored with the rest empty (BACKLOG B7, fixed 2026-10-15; `tests/test_robustness.py`).
31. **The consensus block is derived, never edited.** `consensus.py` rebuilds it for every note from the model blocks on each `--analyze`, over the models of that run (`--location`), so a hand edit in `data.db` is lost on the next run, and a run with a different model set replaces the consensus with that set's; `Models (consensus)` records which labels built each row. A consensus value needs two agreeing models, so a one-model run leaves the block empty with `Outcome` `none`. If you add a field, add it to the vote (`VOTED_FIELDS`) or to the evidence rule (`EVIDENCE_FOR_FLAG`) as well.

### 15.4 Operations and HIPAA

32. **Logs are not PHI-free by construction.** `reporter.py` logs one line per note with the note id, timing, token counts and the structured fields (scope name, flags, counts), never the evidence sentences, note text or raw output, and a rejection is logged in its `brief` form, without the rejected value; the full message with the value goes only to the `LLM_Error` and `LLM_Rejections` columns in `data.db` and the export (BACKLOG F11). Do not add debug prints of note content, raw LLM output or CSV rows. Ollama at `OLLAMA_DEBUG=INFO` does not log prompts; the Ollama logs now also live in `~/nlp-study-logs/`.
33. **The job emails on end and abort** (`-m ea`). Do not put identifiers in the job name or in anything that reaches stdout at the end.
34. **Everything binds to `127.0.0.1`.** Keep it that way; never expose the Ollama port on the node's interfaces.
35. **Walltime versus volume.** 24 h at 8 slots is the default (`NLP_JOB_HRT`, `NLP_JOB_SLOTS`). Projected runtime comes from tier 1, which counts the real rows. Raise `h_rt` or plan resubmissions; the pipeline is resume-safe. Gating non-procedure notes before the LLM (BACKLOG F3) is the big lever.
36. **Windows line endings** in any script copied to Argon need `dos2unix`. Shebangs with `\r` fail with confusing "no such file" errors.

### 15.5 Study requirements (the researcher's review comments)

The ten example notes in `samples/` carry five review comments by Alexander P. Kane dated 2025-12-13 (in `word/comments.xml` of the `.docx`, and rendered in the PDF's margin), plus highlights marking the expected answers. Together they are the only written statement of what the fields must mean; the README and code do not record them. The comments were verified against both files on 2026-10-13.

| #   | Attached to                                                                                            | Comment (verbatim)                                                                                                | Requirement                                                                                                                                  |
| --- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Note 2, "Random biopsies were taken to rule out celiac disease"                                        | "Ideally they say the number of biopsies but lacking that just whether they were taken would be good to know"     | Presence of duodenal biopsies is the minimum deliverable; the count is optional. "Count unknown" must be representable and must not become 0 |
| 2   | Note 4, "intubated with an Olympus Q 180 endoscope" (the same note also uses a PCF H180AL colonoscope) | "I only need the scopes used in upper endoscopy"                                                                  | `ScopeType` is the EGD scope only; a colonoscope must never win                                                                              |
| 3   | Note 5, "6 biopsies ... including 2 from bulb"                                                         | "I am looking for biopsies from the duodenum and the bulb is a part of that and another word used to describe it" | Bulb biopsies are duodenal biopsies; count them once (6 here, not 2 and not 8)                                                               |
| 4   | Note 6, PEG placement, "BIOPSY: None"                                                                  | "This one no biopsies were taken"                                                                                 | A true zero, distinct from "unknown"; PEG notes are still in scope for the scope field                                                       |
| 5   | Note 9, "An Olympus PCF H180AL colonoscope was lubricated"                                             | "Don't care about this one since it was for a colonoscopy"                                                        | Same as 2                                                                                                                                    |

The `.docx` also carries 36 yellow highlights marking the expected answer in each note (count and marker phrases verified on 2026-10-13; the per-note table below is `BACKLOG.md` section 1's reading of them; the `.md` conversion in `samples/` lost both comments and highlights and must not be used as the reference). Two things they settle:

- **Acceptable scope answers include brand-only and model-only forms**: "Olympus gastroscope" is the highlighted answer for note 2, "GIF-H 190" for notes 8 and 10.
- **`FellowPresent` is decided by the attending's attestation, not by the word "fellow"**: a "Teaching Statement" (the attending attests that a trainee performed under supervision) means 1; "Staff Only: I was present for and participated in the entire procedure" means 0. Notes 6 and 9 never contain the word "fellow", yet the gold is 1; prompt rule 9 ("ONLY if a fellow is explicitly mentioned") makes the LLM return 0 there.

| Note | ScopeType (highlighted) | Duodenal biopsies        | FellowPresent (marker)                    |
| ---- | ----------------------- | ------------------------ | ----------------------------------------- |
| 1    | Olympus H190            | 4                        | 1 (fellow signature + Teaching Statement) |
| 2    | Olympus gastroscope     | taken, count not stated  | 0 (RESIDENT/FELLOW None)                  |
| 3    | Olympus Q 180           | 4, one from bulb         | 1                                         |
| 4    | Olympus Q 180           | 4                        | 1                                         |
| 5    | Olympus H190            | 6, including 2 from bulb | 0 (Staff Only)                            |
| 6    | Olympus GIF XP190N      | 0, "BIOPSY: None"        | 1 (named fellow + Teaching Statement)     |
| 7    | Olympus H190            | 6                        | 0 (attending signs alone, no attestation) |
| 8    | GIF-H 190               | 6                        | 0 (Staff Only)                            |
| 9    | Olympus H190            | 4                        | 1 (named fellow + Teaching Statement)     |
| 10   | GIF-H 190               | 4                        | 0 (Staff Only)                            |

Where the current code falls short of these, with the BACKLOG item that fixes it:

Done in the prompt on 2026-10-13 (`SYSTEM_PROMPT` in `llm_analysis.py`): `NumberOfDuodenalBiopsies` is null when biopsies were taken without a stated count and 0 when none were taken (B1); `FellowPresent` is read from the attestation, Teaching Statement 1 and Staff Only 0, with named trainees and "Fellow: None" handled (B11); bulb biopsies are duodenal and counted once (comment 3, F6); `ScopeType` is the upper endoscope as written, brand-only and model-only accepted, colonoscopes ignored (comment 2); `Endoscopy` and `Colonoscopy` mean performed in this note, so history, plans and instructions are 0 (B11). Still open:

- Nothing canonicalises the scope strings the model returns, so one scope can appear under several spellings ("Olympus H190", "GIF-H 190", "Olympus GIF-H190") (F5).
- The real input is mixed note types: per BACKLOG section 1, only about 609 of the 5000 deidentified rows are `Procedures` and about 330 are EGDs. The prompt now tells the model that history and plans do not count, but every note is still sent to the model; a relevance gate would cut the run roughly tenfold (F3).

When changing the prompt or the model, check behaviour on all ten notes of `samples/notes.csv` against the gold table (after fixing its header and the note 9/10 boundary, B7 and B8). A gold-label fixture and `--evaluate` command are BACKLOG F1.

---

## Appendix A: environment variable reference

### Read by `setup.sh` (rewrite)

| Variable                         | Default                                               | Purpose                                                                                                                               |
| -------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `MODEL`                          | read from `llm_analysis.py`                           | Override the model to fetch (warns on disagreement)                                                                                   |
| `NLP_REPO_URL`                   | `https://github.com/benpodium8/nlp-study.git`         | Clone a fork or mirror instead                                                                                                        |
| `NLP_PYTHON`                     | auto-detect                                           | Path to a Python 3.10+ interpreter; tried first                                                                                       |
| `NLP_FAST_ROOT`, `NLP_BULK_ROOT` | `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` | Force the roots for repo+venv and image+model                                                                                         |
| `NLP_OLLAMA_IMAGE`               | `docker://ollama/ollama:0.24.0`                       | Container image; 0.24.0 is the last that uses a driver-535 GPU                                                                        |
| `NLP_REQUIRES_VERIFIED`          | `gpt-oss:20b`                                         | Models fetched although their config `requires` a newer Ollama than the image, because they are known to load on it (space-separated) |
| `PIP_INSTALL_ARGS`               | `--only-binary=:all:`                                 | Extra pip flags; empty string permits source builds                                                                                   |
| `NLP_JOB_QUEUE`                  | `UI-GPU`                                              | Queue baked into the job script                                                                                                       |
| `NLP_JOB_SLOTS`                  | `8`                                                   | `-pe smp` slots baked into the job script                                                                                             |
| `NLP_JOB_HRT`                    | `24:00:00`                                            | `-l h_rt` baked into the job script                                                                                                   |
| `NLP_JOB_GPU`                    | `1`                                                   | Bake `#$ -l ngpus=1` into the job script and `-l ngpus=1` into the printed `qlogin` command; `0` (or empty) for a CPU-only job        |
| `NLP_JOB_GPU_TYPE`               | unset                                                 | Card-type complex to pin as well (`#$ -l <complex>`, names from `qconf -sc`); the study models need a 24 GB card                       |
| `NLP_STOP_MARGIN_MINUTES`        | `45`                                                  | (job, at `qsub` time) how long before the walltime the job stops starting notes                                                       |
| `NLP_STOP_AFTER`                 | unset                                                 | (`app.py`) default for `--stop-after`; when exported, the job script passes no limit of its own                                       |
| `NLP_ALWAYS_RUN`                 | unset                                                 | (job) skip the `--work-left` check, start Ollama even with nothing pending                                                            |
| `NLP_SETUP_LIB_ONLY`             | unset                                                 | Source the script for its functions only                                                                                              |
| `USER`                           | set by the shell/SGE                                  | HawkID for scratch paths; falls back to `id -un`                                                                                      |

Exported by the committed `setup.sh` but ineffective on Argon, and removed in the working tree: `APPTAINER_MKSQUASHFS_ARGS` (3.3). The working tree exports `GIT_TERMINAL_PROMPT=0` instead.

September-only variables, no longer read: `SKIP_SMOKE_TEST`, `FORCE_SMOKE_TEST`, `NLP_NOTE_ESTIMATE`, `FORCE_VENV`, `FORCE_SIF`.

### Read at run time by `~/nlp-study-env.sh`, the preflight and the job (inherited via `-V`)

| Variable                | Default                     | Purpose                                                                                                                                                                               |
| ----------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NLP_CONTEXT_LENGTH`    | `8192`                      | `OLLAMA_CONTEXT_LENGTH` passed into the container                                                                                                                                     |
| `NLP_LLM_TIMEOUT`       | `1800`                      | Longest silence (seconds) from Ollama during one model call or model load before it counts as a failed call; the answer is streamed, so a slow model is never cut off while producing |
| `NLP_HEARTBEAT_SECONDS` | `60`                        | Seconds between the `...` heartbeat lines the job log gets during a long model call                                                                                                   |
| `NLP_PLAIN`             | unset                       | `1` turns off the live panel in a terminal and gives the heartbeat lines instead                                                                                                      |
| `NLP_PREFLIGHT_REPS`    | 3 (job forces 1 unless set) | Tier 1 repetitions                                                                                                                                                                    |
| `NLP_PREFLIGHT_BUDGET`  | 600                         | Per-call timeout in seconds                                                                                                                                                           |
| `NLP_SKIP_PREFLIGHT`    | unset                       | Bypass the gate in the job                                                                                                                                                            |
| `NLP_ALLOW_INFERENCE`   | unset                       | Let `nlp_start_ollama` and `setup.sh` run tier 1 outside SGE (never on a login node)                                                                                                  |
| `NLP_QUIET`             | unset (job and setup set 1) | Suppress the env script's ready banner                                                                                                                                                |
| `NLP_COLOR`             | unset                       | `1` forces colour in the preflight output                                                                                                                                             |

### Exported by `~/nlp-study-env.sh`

`NLP_ENV_VERSION=2`, `NLP_REPO_DIR`, `NLP_VENV_DIR`, `NLP_SIF`, `NLP_MODELS_DIR`, `NLP_MODEL` (the first model), `NLP_MODELS` (all of them, space-separated), `NLP_PREFLIGHT`, `NLP_LOG_DIR`, `APPTAINER_CACHEDIR`, `APPTAINER_TMPDIR`; after `nlp_start_ollama`: `OLLAMA_HOST` (random port), `NLP_OLLAMA_PID`, `NLP_OLLAMA_LOG`. Functions: `nlp_has_gpu`, `nlp_start_ollama`, `nlp_stop_ollama`, `nlp_preflight [tier]`.

### Set inside the container

`OLLAMA_MODELS=/models`, `OLLAMA_HOST=127.0.0.1:<port>`, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_CONTEXT_LENGTH=<NLP_CONTEXT_LENGTH>`.

### Set by SGE inside a job

`JOB_ID`, `JOB_NAME`, `NSLOTS`, `PE_HOSTFILE`, `SGE_TASK_ID` (array jobs), `TMPDIR`. `setup.sh` and the env script use `JOB_ID` or `PE_HOSTFILE` to recognise that they are inside an allocation.

---

## Appendix B: the generated scripts, annotated

### B.1 `~/nlp-study.job` (rewrite)

```bash
#!/bin/bash
# Generated by setup.sh; regenerated on every setup run. Tune through NLP_JOB_* and re-run setup.sh.
#$ -N nlp-study                        # job name -> log file nlp-study.o<JOB_ID>
#$ -q UI-GPU                           # NLP_JOB_QUEUE
#$ -pe smp 8                           # NLP_JOB_SLOTS; Ollama uses them for CPU inference
#$ -l h_rt=24:00:00                    # NLP_JOB_HRT
#$ -j y                                # merge stderr into stdout
#$ -o /Users/apkane/nlp-study-logs/    # directory -> default filename inside it
#$ -m ea                               # mail at end and abort (account default address)
#$ -l ngpus=1                          # NLP_JOB_GPU (1 by default; 0 drops the line)
# Any card on the queue; NLP_JOB_GPU_TYPE=<complex> adds "#$ -l <complex>" to pin a 24 GB card
set -eo pipefail
export NLP_QUIET=1
. /Users/apkane/nlp-study-env.sh       # paths, helpers, cd repo, activate venv
JOB_HRT_DEFAULT="24:00:00"             # NLP_JOB_HRT, the fallback when qstat cannot tell the job's h_rt
set -u
job_started=$(date +%s)
echo "nlp-study job ${JOB_ID:-?} on $(hostname), $(date); slots ${NSLOTS:-?}; models ${NLP_MODELS}; args: $*"
case " $* " in                         # a chained job with nothing left ends before starting Ollama
    *" --csv"*|*" --clean "*) ;;
    *)
        if [ -z "${NLP_ALWAYS_RUN:-}" ]; then
            left=0; python app.py "$@" --work-left || left=$?
            if [ "$left" -eq 4 ]; then echo "Nothing left to do; the job ends without starting Ollama."; exit 0; fi
        fi ;;
esac
nlp_hrt_seconds() { ... }              # h_rt from `qstat -j $JOB_ID`, else JOB_HRT_DEFAULT, in seconds
nlp_start_ollama                       # random port, --nv if a GPU is visible, context 8192, log in ~/nlp-study-logs
trap nlp_stop_ollama EXIT
if [ -z "${NLP_SKIP_PREFLIGHT:-}" ]; then
    NLP_PREFLIGHT_REPS="${NLP_PREFLIGHT_REPS:-1}" nlp_preflight 1 \
        || { echo "Preflight failed; not starting app.py." >&2; exit 1; }
fi
stop_args=()                           # stop starting notes NLP_STOP_MARGIN_MINUTES before the walltime
if [ -z "${NLP_STOP_AFTER:-}" ]; then
    left_s=$(( $(nlp_hrt_seconds) - ${NLP_STOP_MARGIN_MINUTES:-45} * 60 - ($(date +%s) - job_started) ))
    [ "$left_s" -gt 60 ] || left_s=60
    stop_args=(--stop-after "${left_s}s")
fi
python app.py ${stop_args[@]+"${stop_args[@]}"} "$@" &     # in the background, so TERM is passed on
app_pid=$!
trap 'kill -TERM "$app_pid" 2>/dev/null || true' TERM HUP USR1 USR2
rc=0; wait "$app_pid" || rc=$?
while kill -0 "$app_pid" 2>/dev/null; do rc=0; wait "$app_pid" || rc=$?; done
trap - TERM HUP USR1 USR2
case "$rc" in ... esac                 # one line saying what the exit status means (0, 2, 3, other)
exit "$rc"
```

Written atomically (temp file plus rename), so a job that sources the env script mid-rewrite never sees a half-written file.

### B.2 `~/nlp-study-env.sh` (rewrite), key parts

```bash
export NLP_ENV_VERSION=2                       # checked by setup.sh --preflight
export NLP_REPO_DIR=... NLP_VENV_DIR=... NLP_SIF=... NLP_MODELS_DIR=... NLP_MODEL=... NLP_MODELS="... ... ..." NLP_PREFLIGHT=... NLP_LOG_DIR=...
export APPTAINER_CACHEDIR=... APPTAINER_TMPDIR=...

nlp_start_ollama() {
    # refuses if hostname contains "login"; refuses outside SGE unless NLP_ALLOW_INFERENCE=1
    # no-op if a server it started is still alive
    export OLLAMA_HOST="127.0.0.1:<free port>"
    apptainer exec [--nv] --bind "$NLP_MODELS_DIR:/models" \
        --env OLLAMA_MODELS=/models --env OLLAMA_HOST="$OLLAMA_HOST" \
        --env OLLAMA_KEEP_ALIVE=-1 --env OLLAMA_MAX_LOADED_MODELS=1 \
        --env OLLAMA_CONTEXT_LENGTH="${NLP_CONTEXT_LENGTH:-8192}" \
        "$NLP_SIF" ollama serve > "$NLP_LOG_DIR/ollama-${JOB_ID:-$$}.log" 2>&1 &
    # wait up to 90 x 2 s for /api/tags; on failure print the log and return 1
}
nlp_stop_ollama() { kill; wait up to 10 s; kill -9; }
nlp_preflight()   { python "$NLP_PREFLIGHT" --repo "$NLP_REPO_DIR" --model <each name in $NLP_MODELS> --models-dir "$NLP_MODELS_DIR" --tier "${1:-1}"; }

cd "$NLP_REPO_DIR" && . "$NLP_VENV_DIR/bin/activate"
```

### B.3 September version of the job script (what produced the 2026-09-14 artifacts)

Same `#$` directives, except that `NLP_JOB_GPU=1` also pinned `#$ -l gpu_p100`. Other differences: it did not source the env script; it inlined the venv activation, port selection and `apptainer exec ... ollama serve` (log to `repo/ollama-<JOB_ID>.log`, no `OLLAMA_CONTEXT_LENGTH`); it ran the preflight with `--tier 1`, reps 1 and a 900 s budget, then printed `ollama ps`; and it embedded `MODEL="gemma3:12b"` as a literal copied from `llm_analysis.py` at generation time.

---

## Appendix C: Ollama server configuration as observed

From the 19:22 and 19:33 server logs, Ollama 0.32.14 inside the container on a compute node (September version launcher):

| Setting                    | Value                                           | Note                                                                                  |
| -------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| `OLLAMA_HOST`              | `http://127.0.0.1:59103`                        | Loopback only, random port                                                            |
| `OLLAMA_MODELS`            | `/models`                                       | Bind mount of the scratch model store                                                 |
| `OLLAMA_KEEP_ALIVE`        | `2562047h47m16s`                                | The `-1` setting; model stays loaded                                                  |
| `OLLAMA_NUM_PARALLEL`      | 1                                               | One request at a time                                                                 |
| `OLLAMA_MAX_LOADED_MODELS` | 0                                               | Auto                                                                                  |
| `OLLAMA_CONTEXT_LENGTH`    | 0                                               | Auto; resolved to `default_num_ctx=4096` with no VRAM. The rewrite sets 8192          |
| `OLLAMA_FLASH_ATTENTION`   | false                                           |                                                                                       |
| `OLLAMA_LOAD_TIMEOUT`      | 5m                                              |                                                                                       |
| `OLLAMA_DEBUG`             | INFO                                            | Prompts are not logged                                                                |
| `OLLAMA_NO_CLOUD`          | false                                           | Cloud disabled: false, but no outbound call is made by the pipeline; models are local |
| `CUDA_VISIBLE_DEVICES`     | 0                                               | Set by the allocation; Ollama warns that visible devices were overridden              |
| GPU discovery              | NVIDIA A10, compute 8.6, driver 535             | Rejected: requires 550 or newer                                                       |
| Inference compute          | cpu, 251.5 GiB total, ~215 to 220 GiB available |                                                                                       |
| Blobs                      | 4 total, 0 unused removed                       | One model (`gemma4:e2b`) at the time                                                  |

---

## Appendix D: database schema and output files

### Tables in `data.db`

**notes**: `id` (autoincrement), `mrn`, `encounter`, `note_csn_id`, `note_date`, `note_type`, `note`, with the index `notes_identity` on (`mrn`, `encounter`, `note_csn_id`) for the ingest's duplicate check.

**results** (one row per note id, one block of columns per model): every column is named `<field> (<model>@<context>)`, the label being the model's full name and the context window it was called with (`llm_analysis.ModelSpec.label`, today `mistral-small3.2:24b-instruct-2506-q4_K_M@8192`), so two models, or one model at two window sizes, never share a column, and SQL must double-quote the names. A block holds the ten extracted fields (`ScopeType`, `Colonoscopy`, `ColonoscopyInformation`, `Endoscopy`, `EndoscopyInformation`, `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken`, `DuodenalBiopsiesInformation`, `FellowPresent`, `FellowInformation`), the review columns `ManualReviewRequired` and `ManualReviewReason`, and six debugging columns: `LLM_Failed` (1 when no attempt gave an answer the note supports, or the call failed; the extracted fields are then null; null means the model has not run on the note), `LLM_Attempts` (model calls made for the note), `LLM_Seconds` (wall-clock time over all attempts), `LLM_Error` (the final error message when it failed), `LLM_Rejections` (why each rejected attempt was rejected) and `RawResponse` (verbatim text of the accepted model response, for audit). It carries no `note_csn_id`, so selecting the EGD cohort by CSN needs a join with `notes` (BACKLOG B20); `results.csv` is that join. `create_db` converts a table from before the labels (bare field names) into the first model's block, adds any column a block lacks, and adds a whole block for a model that has not run on that `data.db`.

The same row holds the **consensus block**, labelled `(consensus)` (`ScopeType (consensus)` and so on; `database.CONSENSUS_COLUMNS`): the ten fields the models agree on, `ManualReviewRequired` and `ManualReviewReason` for the consensus, and `Summary` (one line in words of what the consensus found), `Outcome` (`unanimous`, `agreed`, `majority`, `partial` or `none`), `Answered` (how many models answered), `Models` (the labels consulted, marking those that failed or have not run), `Disputed` (the fields the answering models split on) and `Agreement` (how the models voted on each disputed field). `consensus.py` rebuilds it for every note after the models have run: a field is filled only where at least two models that answered agree (a strict majority settles a 2-against-1 split and flags the note; no majority leaves the field empty), and the note is flagged where the models disagreed, where the agreed answer leaves a field open, contradicts the note's own attestation of a trainee or sums two biopsy counts from one sentence, where a model has not run, and where a procedure note (or one with upper endoscopy wording, or where a model's answer found a procedure) got fewer than two answers; a model's failure alone does not flag a row the others agree on (`Outcome` `agreed`). The README's `consensus.py` section has the full rules.

Until 2026-10-13 the tables were `working_results` (NLP and LLM answers side by side) and `final_results` (the reconciled subset). A `data.db` from before then still holds those two tables and no `results` table: `CREATE TABLE IF NOT EXISTS` adds `results` beside them, every note is analysed again, and the old tables are ignored. Start from a fresh `data.db` unless you want that.

### Files in `output/`

| File                 | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `results.csv`        | One row per note, under plain-words titles (`export.py`; `results-csv-guide.md` explains them): `Note id`, `MRN`, `Encounter`, `NoteCsnID`, `NoteDate`, `NoteType` (columns A to F); the consensus block, each column titled `<what> (consensus)` (columns G to X): `Needs human review` (TRUE/FALSE, the review queue), `Review reasons`, `Outcome`, `Models answered`, `Summary`, the ten fields (`Upper endoscopy performed`, `Scope type`, `Upper endoscopy evidence quote`, `Duodenal biopsies taken`, `Number of duodenal biopsies`, `Duodenal biopsies evidence quote`, `Colonoscopy performed`, `Colonoscopy evidence quote`, `Fellow present`, `Fellow evidence quote`), `Models consulted`, `Disputed fields`, `How the models voted`; then one block of 18 columns per model, each titled `<what> (<model>@<context>)`: `Failed`, the ten fields, `Needs review`, `Review reasons`, `Input shortened`, `How the input was shortened`, `Attempts`, `Seconds`, `Failure reason`; then every model's `Rejected attempts` and `Raw model response`; then `Note length (characters)` and `Note`. Yes/no values are TRUE or FALSE; a cell is empty where the database holds NULL |
| `results-review.csv` | The same rows with only the identifiers (A to F), the consensus block (G to X) and `Note length (characters)` (Y), written from the same pass as `results.csv` and write-once together with it; about 4% of its size, for Google Sheets.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

Both are written once, together, and never overwritten. Both contain PHI. While being written they are named `results.csv.partial` and `results-review.csv.partial`; a leftover `.partial` is from an export that was cut short and is overwritten by the next one.

### Files beside `data.db`

| File              | Content                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `data.db.lock`    | Present while a run uses the database: host, pid, SGE job id, start time and a token, refreshed every 30 s (7.4)               |
| `data.db.backup`  | A copy of `data.db` taken after a passing integrity check at the start of the latest run (PHI). `--clean` deletes it           |
| `data.db-journal` | SQLite's rollback journal, present during a write, and after a kill until the next open rolls it back. Never delete it by hand |

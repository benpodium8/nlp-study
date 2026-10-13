# nlp-study on the UIowa Argon HPC cluster: consolidated runbook

Last consolidated: 2026-10-13. Covers the repository at commit `957c6b8` plus two uncommitted working-tree changes: the rewrite of `setup.sh` (file dated 2026-10-13 11:19, 955 lines, `ENV_VERSION=2`) and `.python-version` relaxed from `3.12.13` to `3.12`. Also covers the Argon session logs from 2026-09-14 (HawkID `apkane`).

This document merges five artifacts from the 2026-09-14 Argon session, the project README and source, and three reference pages:

| Source | What it is |
| --- | --- |
| Setup transcript, 18:53 CDT | `./setup.sh` run on `argon-login-5` in `setup` mode (model still `gemma4:e2b`) |
| Ollama server log, 19:22 CDT | Server inside the Apptainer container on a compute node, loading `gemma4:e2b` |
| Ollama server log, 19:33 CDT | Second server start on the same kind of node, CPU-only |
| Status transcript, 20:04 CDT | `./setup.sh --status` inside SGE job 6516164 on `argon-itf-bx55-01`; repo now wants `gemma3:12b` |
| Generated job script, 20:11 CDT | `~/nlp-study.job`, queue `UI`, 8 slots, 24 h, model `gemma3:12b` |
| [Argon: Software Installations](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513438/Software+Installations) | Central software policy, environment modules, licence review |
| [Argon: Basic Job Submission](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513450/Basic+Job+Submission) | SGE `qsub` options, queues, memory, output files, exit codes |
| [github.com/benpodium8/nlp-study](https://github.com/benpodium8/nlp-study) | The code, the README, `setup.sh` |

**Two versions of `setup.sh` appear in this document.** The September logs were produced by the version committed in `3657d43` (the transcripts call it `setup_nlp_study.sh`). The working tree holds a rewrite that is not yet committed or pushed. Sections 5, 6, 7 and the appendices describe the rewrite as the current script; where the September version behaved differently, the difference is called out as "September version". Until the rewrite is pushed and re-run, Argon still has the September version.

---

## Contents

1. [Quick start](#1-quick-start)
2. [What the program does](#2-what-the-program-does)
3. [The Argon environment as observed](#3-the-argon-environment-as-observed)
4. [Where everything lives](#4-where-everything-lives)
5. [setup.sh reference](#5-setupsh-reference)
6. [The preflight gate](#6-the-preflight-gate)
7. [Running the pipeline](#7-running-the-pipeline)
8. [SGE job submission reference (Argon)](#8-sge-job-submission-reference-argon)
9. [Argon software installation policy and why this project is unaffected](#9-argon-software-installation-policy-and-why-this-project-is-unaffected)
10. [Timeline of the 2026-09-14 session](#10-timeline-of-the-2026-09-14-session)
11. [Open items and known discrepancies](#11-open-items-and-known-discrepancies)
12. [Questions and answers](#12-questions-and-answers)
13. [Troubleshooting](#13-troubleshooting)
14. [Software development constraints](#14-software-development-constraints)
15. [Appendix A: environment variable reference](#appendix-a-environment-variable-reference)
16. [Appendix B: the generated scripts, annotated](#appendix-b-the-generated-scripts-annotated)
17. [Appendix C: Ollama server configuration as observed](#appendix-c-ollama-server-configuration-as-observed)
18. [Appendix D: database schema and output files](#appendix-d-database-schema-and-output-files)

---

## 1. Quick start

The three-step sequence the installer prints. Each step runs on a different kind of node, and the order matters.

**Step 0. Stage the notes file** (PHI; scratch only, never git):

```bash
cp /path/to/notes.csv /nfsscratch/Users/apkane/nlp-study/repo/notes.csv
```

The header row must contain exactly these six names (an extra `id` column is tolerated and ignored): `MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note`. See [Troubleshooting: CSV must contain columns](#csv-must-contain-columns).

**Step 1. Build or repair on a login node** (needs internet; never runs inference):

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
./setup.sh
```

Re-running is always safe. Healthy resources are skipped, broken ones rebuilt. After a model change in `llm_analysis.py` this step is what downloads the new weights. It ends with a tier 0 preflight and rewrites `~/nlp-study.job` and `~/nlp-study-env.sh`.

**Step 2. Prove the LLM works, on a compute node** (interactive allocation):

```bash
qlogin -q UI -pe smp 8
/nfsscratch/Users/apkane/nlp-study/repo/setup.sh --preflight
exit
```

Tier 1 runs real inference on a synthetic PHI-free note and prints a per-note latency, GPU placement, and a runtime projection for the real `notes.csv`. Do not submit until this prints `PREFLIGHT PASSED`.

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
qlogin -q UI -pe smp 8
source ~/nlp-study-env.sh
nlp_start_ollama
nlp_preflight
python app.py --analyze --csv ./notes.csv
nlp_stop_ollama
```

---

## 2. What the program does

A local, offline clinical note pipeline for endoscopy procedure notes. Two independent extractors run on every note and their answers are reconciled conservatively.

```
notes.csv ──> SQLite notes table
                 │
                 ▼  data_worker.py, one note at a time
       ┌──────────────────────────────┐
       │ nlp_analysis.py (spaCy rules)│──┐
       │ llm_analysis.py (Ollama LLM) │──┤──> working_results (both answers + raw LLM text)
       └──────────────────────────────┘  │
                                         ▼  reconcile_working_results.py
                                   final_results (agreement only, else all null)
                                         │
                                         ▼  database.py exports (write-once)
                     output/combined_results.csv
                     output/all_tables_combined_results.csv
                     output/full_combined_results.csv
```

### 2.1 Command-line interface (`cli.py`)

| Invocation | Effect |
| --- | --- |
| `python app.py` | Prints help and exits 0 |
| `python app.py --csv FILE` | Ingests the CSV (duplicates skipped), then prints the notes table without note text |
| `python app.py --analyze --csv FILE` | Ingests, runs NLP + LLM on every unprocessed note, reconciles, exports CSVs |
| `python app.py --analyze` | Same, without ingesting. Resumes an interrupted run |
| `python app.py --print` | Prints the notes table with note text truncated to 30 characters |
| `python app.py --working_results` | Prints the working_results table (raw LLM text and Information fields truncated) |

Precedence inside `main()`: `--analyze` wins, then `--print`, then `--working_results`, then `--csv` alone. The database file is always `data.db` in the current working directory. The export functions open `"data.db"` by relative path as well, so the program must be run from the repository directory.

### 2.2 Extracted fields

Both extractors return the same dictionary:

| Field | Type | Meaning |
| --- | --- | --- |
| `ScopeType` | string or null | Brand and model of the upper endoscope, for example `Olympus H190` |
| `Colonoscopy` | 0 or 1 | A colonoscopy was performed |
| `ColonoscopyInformation` | string or null | Supporting sentence |
| `Endoscopy` | 0 or 1 | An upper endoscopy / EGD was performed |
| `EndoscopyInformation` | string or null | Supporting sentence |
| `NumberOfDuodenalBiopsies` | integer | Explicitly stated count, else 0 |
| `DuodenalBiopsiesTaken` | 0 or 1 | At least one duodenal biopsy |
| `DuodenalBiopsiesInformation` | string or null | Supporting sentence |
| `FellowPresent` | 0 or 1 | A fellow is explicitly mentioned |
| `FellowInformation` | string or null | Supporting sentence |

### 2.3 Reconciliation rule (`reconcile_working_results.py`)

A `final_results` row carries real values only when all of the following hold. Otherwise the row is inserted with every value null ("when in doubt, discard").

- `AllHardDataInAgreement` is 1 (set at insert time when NLP and LLM agree on `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken` and `FellowPresent`).
- `NLP_Failed` and `LLM_Failed` are both 0.
- The three numeric fields match again at reconcile time.
- `reconcile_scope_type()` returns a non-empty scope. Only one side having a scope is accepted. Both sides having one is accepted if the normalised strings match or the digit-bearing tokens match.

Consequence: a note with no scope mentioned anywhere ends up with an all-null final row even when every other field agrees. `final_results` carries only four fields: `ScopeType`, `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken`, `FellowPresent`. The colonoscopy and endoscopy flags live only in `working_results`.

### 2.4 Failure handling in `data_worker.py`

- NLP exception: the default all-zero result is stored with `NLP_Failed = 1`.
- LLM `LLMAnalysisError` whose message contains `Invalid JSON` or `JSONDecodeError`: retried, up to 5 attempts total. Each retry is a complete generation.
- Any other `LLMAnalysisError` (missing field, wrong type, unexpected field, boolean not 0/1) or any other exception: stored immediately with `LLM_Failed = 1`, no retry.
- The loop never raises. The process exits 0 even when every note failed. This is why the preflight exists (section 6).

### 2.5 Idempotency

- Ingest skips a row only if all six fields match an existing row exactly.
- `data_worker` skips any note id that already has a `working_results` row.
- `reconcile` skips any id that already has a `final_results` row.
- Exports are skipped if the target CSV already exists.

---

## 3. The Argon environment as observed

### 3.1 Hosts

| Item | Login node (18:53) | Compute node (20:04) |
| --- | --- | --- |
| Hostname | `argon-login-5.hpc` | `argon-itf-bx55-01.hpc` |
| Node kind (per `setup.sh`) | login | job (SGE `JOB_ID=6516164`) |
| OS | CentOS Linux 7.9.2009 | same |
| Kernel | 3.10.0-1160.119.1.el7.x86_64 | same |
| Bash | 4.2.46(2) | same |
| glibc | 2.17 | same |
| `HOME` | `/Users/apkane` | same |
| Outbound HTTPS | works | not checked; compute nodes often lack it |

### 3.2 Tooling

| Tool | Version | Notes |
| --- | --- | --- |
| git | 1.8.3.1 | Predates 1.8.5, so `git -C <path>` is an unknown option. `setup.sh` uses a subshell `cd` instead |
| curl | system | Health checks, the GitHub reachability test, and (rewrite) the model blob downloads |
| sha256sum | coreutils | Required by the rewrite to verify model blobs |
| apptainer | 1.3.2-1.el7 | Runs the Ollama container unprivileged |
| Environment Modules | available | Only `python/3.8.8_*` modules exist on Argon (gcc-5.4.0, gcc-9.3.0, intel-2021.2.0 variants). No 3.12 module |
| Python actually used | `/Users/apkane/temp/build_python/python3.12/bin/python3.12` | A user-built 3.12 in `$HOME`. The venv reports Python 3.12.14. `.python-version` pinned 3.12.13 at commit `957c6b8`; the working tree now pins `3.12`, so any 3.12.x satisfies it |

### 3.3 Login node limits (the reason for the two-node workflow)

Argon caps each user on a login node at 100% of one logical CPU and 16 GB of memory, and kills the largest process when the cap is exceeded. A multi-gigabyte model asked for a single sentence under that cap is what hung an earlier installer. Therefore:

- Build (clone, pip, `apptainer pull`, model download) on a login node: these need internet and are light on CPU. The rewrite additionally pins `mksquashfs` to one thread and 2 GB (`APPTAINER_MKSQUASHFS_ARGS="-processors 1 -mem 2G"`) so the image conversion stays under the cap.
- Infer (preflight tier 1, `app.py --analyze`) only inside an SGE allocation (`qlogin` or `qsub`). The rewrite's `nlp_start_ollama` refuses to start on a hostname containing `login`, and refuses outside an SGE job unless `NLP_ALLOW_INFERENCE=1` is set (intended for off-cluster machines only).

September version: the setup script started a temporary `ollama serve` on the login node to run `ollama pull` and `ollama list`. It worked on 2026-09-14 but is exactly the pattern the rewrite removes.

### 3.4 Scratch filesystems

| Mount | Login node | Compute node | Used for |
| --- | --- | --- | --- |
| `/scratch` | not mounted | not mounted | nothing (first candidate in `setup.sh`, skipped) |
| `/nfsscratch` | 229 TB free of 284 TB (NFSv3 from `172.29.4.10:/dpool01/nfsscratch`) | same | repo, venv, container image, model weights |
| `/localscratch` | 3.1 TB free of 3.3 TB (local XFS) | 818 GB free of 892 GB | not used by this project (node-local, not shared) |

Scratch is purged on a schedule. Results in `repo/output/` and the SQLite database must be copied to durable storage after each run.

### 3.5 GPU situation

Both Ollama server logs came from a node with an NVIDIA A10 (compute capability 8.6) and `CUDA_VISIBLE_DEVICES=0` set in the environment. Ollama 0.32.14 reported:

```
NVIDIA driver too old  device="NVIDIA A10" compute=8.6 driver=535 required_driver="550 or newer"
inference compute id=cpu library=cpu ... total="251.5 GiB" available="215.6 GiB"
vram-based default context total_vram="0 B" default_num_ctx=4096
```

So even on a GPU-equipped node the container fell back to CPU inference. The node driver (535) is older than what the bundled CUDA runtime in `ollama/ollama:latest` requires (550+). The rewrite lets you pin an older image with `NLP_OLLAMA_IMAGE`. See [Open items](#11-open-items-and-known-discrepancies) and [Troubleshooting: NVIDIA driver too old](#nvidia-driver-too-old).

The `default_num_ctx=4096` line is also why the rewrite passes `OLLAMA_CONTEXT_LENGTH` (default 8192, `NLP_CONTEXT_LENGTH`) into the container.

### 3.6 Why Ollama runs in a container

Ollama's upstream `install.sh` binary needs `GLIBC_2.27+`. CentOS 7 ships glibc 2.17, so the README's `curl -fsSL https://ollama.com/install.sh | sh` instruction does not work on Argon. `setup.sh` pulls `docker://ollama/ollama:latest` (overridable) into `ollama.sif` (2.8 GB, Ollama 0.32.14 at the time) and runs it with Apptainer.

---

## 4. Where everything lives

| Purpose | Path |
| --- | --- |
| Repository checkout (cwd for all runs) | `/nfsscratch/Users/apkane/nlp-study/repo` |
| Virtual environment | `/nfsscratch/Users/apkane/nlp-study/venv` |
| Dependency stamp (rewrite) | `/nfsscratch/Users/apkane/nlp-study/venv/.requirements.sha256` |
| Generated preflight script | `/nfsscratch/Users/apkane/nlp-study/nlp-preflight.py` |
| Ollama container image | `/nfsscratch/Users/apkane/nlp-study-assets/ollama.sif` (in-progress pull: `ollama.partial.sif`) |
| Model store (bound to `/models` inside the container) | `/nfsscratch/Users/apkane/nlp-study-assets/ollama-models` with `manifests/registry.ollama.ai/<ns>/<name>/<tag>` and `blobs/sha256-<hex>` |
| Apptainer cache and tmp | `/nfsscratch/Users/apkane/nlp-study-assets/apptainer-cache`, `.../apptainer-tmp` |
| Notes input (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/notes.csv` |
| SQLite database (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/data.db` |
| CSV exports (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/output/` |
| Preserved user data after `--clean` or repo rebuild | `/nfsscratch/Users/apkane/nlp-study/preserved/<timestamp>/` |
| Generated job script | `/Users/apkane/nlp-study.job` |
| Generated interactive env script | `/Users/apkane/nlp-study-env.sh` |
| Job stdout+stderr | `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>` |
| Ollama server log, batch or interactive (rewrite) | `/Users/apkane/nlp-study-logs/ollama-<JOB_ID>.log` (`ollama-<pid>.log` outside a job) |
| Setup transcripts (rewrite) | `/Users/apkane/nlp-study-logs/setup-<YYYYMMDD-HHMMSS>.log` |

September version differences: setup transcripts were `~/nlp-study-setup-<timestamp>.log`; the setup-time Ollama log was `nlp-study-assets/ollama-setup.log`; the job-time Ollama log was `repo/ollama-<JOB_ID>.log`; the interactive one went to `$TMPDIR`; hand-edited generated scripts were backed up as `*.bak.<timestamp>` (the rewrite overwrites them without backup).

---

## 5. setup.sh reference

### 5.1 Modes

| Command | Node | Effect |
| --- | --- | --- |
| `./setup.sh` | login | Build or repair: verify each resource, skip healthy, rebuild broken, create absent. Fast-forwards the repo. Runs preflight tier 0. Writes the two `$HOME` scripts |
| `./setup.sh --status` | any | Report state of repo, venv, sif and model (each `healthy`, `broken` or `absent` with a reason), plus presence of the preflight, env and job scripts. Changes nothing |
| `./setup.sh --preflight` | compute | Check an existing install; builds nothing. Tier 1 inside an SGE job, tier 0 on a login node or outside SGE |
| `./setup.sh --force <name>` | login | Rebuild one resource unconditionally. Names: `repo`, `venv`, `deps`, `sif`, `model`, `all`. Repeatable, or `--force=name` |
| `./setup.sh --clean [-y]` | login | Move PHI aside to `preserved/`, then delete repo, venv, preflight script, sif, model store and Apptainer caches. Prompts for `yes`; `-y`/`--yes` for non-interactive |
| `./setup.sh --help` | any | Prints the usage block |

September version also accepted `FORCE_VENV=1` and `FORCE_SIF=1`; the rewrite does not.

### 5.2 Resource state machine

Every managed resource is verified before anything is done to it:

| State | Action |
| --- | --- |
| healthy | skipped |
| broken | removed and rebuilt |
| absent | created |

Health checks in the rewrite:

| Resource | Healthy when |
| --- | --- |
| repo | A git checkout containing `app.py`, `cli.py`, `llm_analysis.py`, `requirements.txt` |
| venv | `bin/python` runs and reports the same major.minor as the selected interpreter |
| sif | File exists, is at least 100 MB, and `apptainer sif list` can parse it (no execution, so safe on a login node) |
| model | The manifest for the exact `name:tag` the code wants exists under `manifests/registry.ollama.ai/`, and every blob it lists exists in `blobs/` at the listed size |

Before a repo directory is destroyed, `preserve_user_data` moves `*.csv`, `*.db`, `*.sqlite*` and `output/` into `preserved/<timestamp>/`. That location is still on scratch.

September version: the model check was only "the models directory is non-empty", which is why the 20:04 status reported `model present` while the store held `gemma4:e2b` and the code wanted `gemma3:12b`. The rewrite would report `model absent` in that situation.

### 5.3 What a full run does, in order

1. Environment: mode, host, user, SGE job id. Login node in setup mode is confirmed as correct; inside a job it warns that clone/pull may fail. Requires `git`, `curl`, `apptainer`, `sha256sum`. Setup mode requires outbound HTTPS to GitHub.
2. Scratch layout: probes `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` for writability, separately for the fast root (repo, venv) and bulk root (image, model). Sets Apptainer cache/tmp and the single-thread `mksquashfs` limit. Sweeps Apptainer tmp entries older than an hour. On a login node, kills orphaned `ollama serve` processes left by older script versions. Reads `LOCAL_MODEL` from `llm_analysis.py` if the repo already exists.
3. Repository: clone, or `pull --ff-only --quiet` if healthy and clean. A dirty working tree is left alone with a warning. Dies if `LOCAL_MODEL` cannot be read and `MODEL` is not set.
4. Python: tries `module load python/<wanted>` and a few variants (harmless if none exists), then probes `$NLP_PYTHON`, the interpreter recorded in an existing `venv/pyvenv.cfg` (so re-runs need no `NLP_PYTHON`), `python3.12`, `python3.13`, `python3.11`, `python3.10`, `python3`. Needs 3.10+. `<wanted>` is the major.minor of `.python-version`.
5. Virtual environment and dependencies. pip runs only when the sha256 of `requirements.txt` differs from the stamp in `venv/.requirements.sha256`, or `--force deps` is given. Flags default to `--only-binary=:all:`. Then scans the repo for `spacy.load("<name>")` and downloads any named pipeline (none today).
6. Container image: `apptainer pull ollama.partial.sif <image>`, renamed to `ollama.sif` on success, so an existing `.sif` is always complete.
7. Model: fetched with `curl` straight from `registry.ollama.ai` into Ollama's on-disk layout. The manifest is downloaded to a `.partial` name, each blob is downloaded single-threaded with resume (`curl -C -`), up to 5 attempts, progress printed every 30 s, size checked, sha256 verified, then renamed. The manifest is moved into place last, so its presence means every blob is complete. Existing complete blobs are reused; `--force model` wipes `blobs/`.
8. Preflight tier 0 (static). Failure stops the run but leaves the build intact.
9. Runtime scripts: writes `~/nlp-study-env.sh` (with `NLP_ENV_VERSION=2`) and `~/nlp-study.job`. Both are regenerated on every run; do not hand-edit.
10. Summary with the next steps.

Every run is transcribed to `~/nlp-study-logs/setup-<timestamp>.log`.

September version: the model step started a temporary Ollama server on the login node and ran `ollama pull`; pip was skipped when every requirement was already importable rather than on a hash stamp; the image was verified by executing `ollama --version` inside it.

### 5.4 Model name resolution

`setup.sh` does not hardcode the model. `detect_repo_model()` reads the first line matching `^LOCAL_MODEL *= *"..."` in `llm_analysis.py` (no leading whitespace allowed). `MODEL=name:tag ./setup.sh` overrides but warns if it disagrees with the repo, because the program will call whatever the repo says. If neither source yields a name the script dies. There is no `gemma4:e2b` fallback any more.

### 5.5 Installed package set (venv, 2026-09-14)

spacy 3.8.14, thinc 8.3.13, blis 1.3.3, numpy 2.2.6, ollama 0.6.2, httpx 0.28.1, pydantic 2.13.4, rich 15.0.0, click 8.4.2, typer 0.27.1, requests 2.34.2, tqdm 4.70.0, pip 26.2.1, setuptools 84.0.0, wheel 0.48.0, plus their transitive dependencies. `requirements.txt` lists only `rich`, `click`, `spacy`, `ollama`, unpinned.

### 5.6 Upgrading Argon from the September script to the rewrite

1. Commit and push the rewrite to `main`.
2. On a login node, update the checkout first so the new script runs, not the old one:

   ```bash
   cd /nfsscratch/Users/apkane/nlp-study/repo && git pull --ff-only
   ```

3. Run `./setup.sh`. It will re-verify the model store against the manifest for `gemma3:12b` and download whatever is missing, verify the existing `.sif` without executing it, rewrite both `$HOME` scripts with `NLP_ENV_VERSION=2`, and run tier 0.
4. `./setup.sh --preflight` inside `qlogin`. The rewrite refuses `--preflight` if the env script is missing or lacks `NLP_ENV_VERSION=2`, so step 3 is mandatory first.

---

## 6. The preflight gate

### 6.1 Why it exists

`data_worker.py` catches every LLM exception per note, records `llm_failed = 1` and continues. A wrong endpoint, a missing model, or a model that will not emit schema-clean JSON therefore does not crash the job. It runs for its full walltime and hands back a complete-looking table with every LLM column null, exit status 0. The preflight is the only thing that turns that into a loud, early failure.

### 6.2 Tiers (rewrite)

| Tier | Where | What it checks |
| --- | --- | --- |
| 0 | anywhere, including login nodes; seconds; no Ollama process | Imports of `llm_analysis`, `nlp_analysis`, `data_worker`, `database`, `cli`, `ollama`, `spacy`, `rich`, `click`. `LOCAL_MODEL` equals the model setup fetched. The model manifest exists and every blob is present at the right size. Repo dir and `output/` are writable |
| 1 | inside an SGE job, after `nlp_start_ollama` | Tier 0, then: `OLLAMA_HOST` set, `/api/tags` responds, the model is served, the module-level `ollama` client points at that host. Then real `llm_analysis()` calls on a synthetic PHI-free note under a `SIGALRM` budget. Model placement via `/api/ps` (percentage of the model in VRAM). Extraction spot checks. Latency and projection |

Tier 1 expectations on the synthetic note:

- Hard (failure): `Endoscopy == 1`, `Colonoscopy == 0`.
- Soft (warning): `DuodenalBiopsiesTaken == 1`, `NumberOfDuodenalBiopsies == 4`, `FellowPresent == 1`, `ScopeType` containing `H190`.
- Warns if the raw output is not bare JSON (the repair layer is doing work; `data_worker` retries up to 5x on JSON errors).
- Placement: PASS at 100% on GPU, PASS on a node with no `nvidia-smi`, WARN otherwise ("partly or fully on CPU, expect it to be slow").
- Prints seconds per note and projected hours for 100, 1000 and the real row count of `notes.csv` (counted with the `csv` module, so multi-line notes are counted correctly). Warns above 60 s per note.

September version: tier 0 also probed the endpoint (so it needed a running server, hence the login-node Ollama); the row count came from `wc -l`, which overcounts multi-line notes; placement was shown by printing `ollama ps`.

### 6.3 Defaults

| Context | Reps | Per-call budget |
| --- | --- | --- |
| `./setup.sh --preflight` and `nlp_preflight` | 3 (`NLP_PREFLIGHT_REPS`) | 600 s (`NLP_PREFLIGHT_BUDGET`) |
| Inside `~/nlp-study.job` | 1 | 600 s unless `NLP_PREFLIGHT_BUDGET` is exported at `qsub` time (September version: 900 s) |

### 6.4 Direct invocation

```bash
python /nfsscratch/Users/apkane/nlp-study/nlp-preflight.py --repo /nfsscratch/Users/apkane/nlp-study/repo --model gemma3:12b --models-dir /nfsscratch/Users/apkane/nlp-study-assets/ollama-models --tier 1 --reps 3 --budget 600 --notes 5000
```

`--models-dir` is required in the rewrite. For tier 1, `OLLAMA_HOST` must already be exported and a server listening; `nlp_start_ollama` from `~/nlp-study-env.sh` does both. `NLP_COLOR=1` forces colour when stdout is not a TTY.

---

## 7. Running the pipeline

### 7.1 Batch (recommended)

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

Everything after the script name is forwarded to `app.py` verbatim (`"$@"`). `./notes.csv` is resolved after the env script does `cd "$NLP_REPO_DIR"`, so it means `repo/notes.csv`.

What the job does (full script in Appendix B):

1. Sources `~/nlp-study-env.sh` quietly: exports the `NLP_*` paths, defines the helper functions, `cd`s into the repo, activates the venv.
2. `nlp_start_ollama`: picks a free localhost port, exports `OLLAMA_HOST`, starts `ollama serve` in the container (with `--nv` only if `nvidia-smi -L` succeeds, `OLLAMA_CONTEXT_LENGTH` 8192 by default), logs to `~/nlp-study-logs/ollama-<JOB_ID>.log`, waits up to 180 s for `/api/tags`. Traps `nlp_stop_ollama` on exit (TERM, then KILL after 10 s).
3. Runs the tier 1 preflight with 1 rep unless `NLP_SKIP_PREFLIGHT` is set. Non-zero exit aborts the job before `app.py`.
4. Runs `python app.py "$@"` and exits with its status, printing a reminder that 143 outside `all.q` usually means the memory limit.

Resource request baked in: `-N nlp-study`, `-q UI`, `-pe smp 8`, `-l h_rt=24:00:00`, `-j y`, `-o /Users/apkane/nlp-study-logs/`, `-m ea`. No GPU. Change them with `NLP_JOB_QUEUE`, `NLP_JOB_SLOTS`, `NLP_JOB_HRT`, `NLP_JOB_GPU` and re-run `./setup.sh`, or override at submission time (command-line options supersede `#$` directives):

```bash
qsub -pe smp 16 -l h_rt=48:00:00 ~/nlp-study.job --analyze --csv ./notes.csv
```

Environment variables exported in the submitting shell reach the job because `-V` is on by default. For example `NLP_PREFLIGHT_BUDGET=1200 qsub ~/nlp-study.job --analyze`.

### 7.2 Interactive

```bash
qlogin -q UI -pe smp 8
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

After the job ends, `qacct -j <JOB_ID>` shows exit status, wallclock, and `maxvmem`. Checking progress in the database while a job is running is safe for reads:

```bash
sqlite3 /nfsscratch/Users/apkane/nlp-study/repo/data.db "select count(*) notes, (select count(*) from working_results) done, (select count(*) from working_results where LLM_Failed=1) llm_failed from notes;"
```

### 7.4 Resuming an interrupted run

Submit again without `--csv`. Notes with a `working_results` row are skipped; the rest are processed; reconciliation covers the new rows.

```bash
qsub ~/nlp-study.job --analyze
```

Exports are write-once. If `output/combined_results.csv` or either of the other two files exists, the export is skipped with a message. Move or delete the old files first to regenerate them from the complete database.

### 7.5 Collecting results

Three files in `repo/output/` (details in Appendix D). All contain PHI. Copy them, and `data.db` if you want the raw LLM responses for audit, to durable storage. Scratch is purged on a schedule and `preserved/` is still scratch.

---

## 8. SGE job submission reference (Argon)

Condensed from the Basic Job Submission page (updated 2021-07-02). `man qsub` is the authoritative source.

### 8.1 Job scripts and submission

- Prefer a job script over `qsub -b y <program>`. The script is an ordinary shell script that SGE runs for you once it finds a node.
- Scripts written on Windows need `dos2unix script` before submission.
- `qsub myscript.job` submits with defaults. `qsub -pe smp 12 myscript.job` requests a parallel environment with 12 slots.
- Options can live in the script as `#$` directives. A command-line option supersedes the same option in the script.

### 8.2 Queues

| Queue | Notes from the page |
| --- | --- |
| `UI` | Default queue |
| `UI-HM` | High-memory queue for large-memory jobs |
| `all.q` | No limit, but subordinate to the other queues. Jobs are evicted (TERM signal) when a higher-priority queue needs the node. Suggested for many single-processor jobs |
| `UI-GPU` | Not on this page. Referenced by `setup.sh`: `NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 ./setup.sh` bakes in `#$ -l ngpus=1`. The September version also pinned `#$ -l gpu_p100`; the rewrite leaves the card type to you (`qconf -sc` lists the complexes) |

### 8.3 Commonly used `qsub` options

| Option | Description |
| --- | --- |
| `-V` | Import the current environment into the job. On by default |
| `-N name` | Job name |
| `-l h_rt=hr:min:sec` | Maximum walltime |
| `-l h_vmem=bytes` (for example `2G`) | Virtual memory limit. An appropriate value is set automatically if an entire node is not requested |
| `-l mf=nG` or `-l mf=nM` | Free memory at scheduling time. Not a limit; only affects where the job starts |
| `-cwd` | Run from the current working directory. Otherwise the job starts in `$HOME` |
| `-S shell` | Shell for interpreting the job script |
| `-o path`, `-e path` | Standard output / error file or directory. A directory gets the default filenames inside it |
| `-j y` | Merge stderr into stdout |
| `-pe name n` | Parallel environment and slot count |
| `-M addr[,addr]` | Email recipients |
| `-m b\|e\|a\|s\|n` | Mail at begin, end, abort, suspend, never. The suspend option does not work. For multi-node jobs the mail's CPU and memory accounting covers only the primary host; use `qacct` for the rest |
| `-t n[-m[:s]]` | Array job with index range and step; tasks see `SGE_TASK_ID` |

### 8.4 Output files

Default names are `$JOB_NAME.o$JOB_ID` and `$JOB_NAME.e$JOB_ID`. Streams are appended, so reusing a file keeps previous contents. Shell redirection inside the script (`command > file`) overrides `-o`. For this project, with `-j y` and `-o /Users/apkane/nlp-study-logs/`, the single log is `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>`.

### 8.5 Exit status

| Code | Meaning |
| --- | --- |
| 0 to 127 | Exit status of the computation itself. `app.py` returns 0 even when every note's LLM step failed |
| 128 + signal | Job did not complete normally |
| 143 | 128 + TERM (15). Eviction from `all.q`, or the memory limit was hit. Outside `all.q` it almost always means memory. Confirm with `qacct -j <JOB_ID>` |
| 137 | 128 + KILL (9). Follow-up kill after TERM was not enough |

---

## 9. Argon software installation policy and why this project is unaffected

From the Software Installations page (updated March 2026):

- Central software is installed from RPMs or built from source and exposed through environment modules. The Argon Software List shows versions. Loading a module sets up dependencies; after loading, `$ROOT_PACKAGE` (module name upper-cased) gives the install path.
- Perl, Ruby, R and Python modules come with a curated set of extensions.
- Centrally installed software must comply with University of Iowa software licensing policy. Review can add days to weeks:
  - Open source under an approved licence: no further review.
  - Free or open source with a non-standard licence: review required, possibly with access restrictions (for example registration-gated downloads).
  - Purchased software: signed agreement review and an access-control scheme approved by General Counsel.
  - Already-reviewed software: still verify the licence permits cluster installation.
- Contact the HPC team to discuss software needs (`research-computing@uiowa.edu` is the address `setup.sh` prints).

Implications for nlp-study:

- Nothing in this project is installed centrally. Python 3.12 is a user build in `$HOME`, the venv and all pip packages are on scratch, Ollama runs from an Apptainer image on scratch, and the model weights are on scratch. No review is triggered and no module needs to exist.
- The only Argon Python modules are 3.8.8 variants, which is why a user-built 3.12 was needed. The rewrite still tries `module load python/3.12` first in case one appears, then falls back to `$NLP_PYTHON` and the PATH.
- If a central install were ever requested: Ollama is MIT-licensed (approved category). The Gemma model weights are under Google's Gemma Terms of Use, a non-standard licence, which would require review before central deployment.

---

## 10. Timeline of the 2026-09-14 session

All of this was produced by the September version of `setup.sh`.

| Time (CDT) | Host | Event |
| --- | --- | --- |
| 18:53:40 | `argon-login-5` | `setup.sh` in setup mode. All prerequisites OK. `/scratch` not mounted; `/nfsscratch` chosen. Repo already healthy, fast-forwarded `332ad3c..3657d43` (adds `setup.sh`). Model read from repo: `gemma4:e2b` |
| 18:53:58 | | Python selected: `/Users/apkane/temp/build_python/python3.12/bin/python3.12`. Venv healthy (3.12.14). All deps already satisfied; pip skipped |
| 18:54:15 | | `ollama.sif` healthy, 2.8 GB, Ollama client 0.32.14 |
| 18:54:17 | | No GPU on login node. Temporary Ollama on `127.0.0.1:37310`, API up after ~10 s. `gemma4:e2b` already present (ID `7fbdbf8f5e45`, 7.2 GB listed, 6.5 GB store on disk) |
| 18:54:40 | | Preflight tier 0 (login node; tier 1 refused on purpose). All static checks PASS |
| 18:54:45 | | Wrote `~/nlp-study-env.sh` and `~/nlp-study.job`. Setup complete |
| 19:22:16 | compute node with A10 | Ollama server started inside a job (`OLLAMA_HOST=127.0.0.1:59103`, `CUDA_VISIBLE_DEVICES=0`). Driver 535 too old for CUDA runtime (needs 550). CPU inference, 251.5 GiB RAM. At 19:22:56 loaded `gemma4:e2b` (a tier 1 preflight or interactive run; result not in the logs) |
| 19:33:59 | same kind of node | Fresh Ollama server start, same port, same driver warning, CPU-only. Model list cache: 1 model |
| 20:04:37 | `argon-itf-bx55-01`, job 6516164 | `setup.sh --status`. Repo, venv, sif healthy. Model store "present" at 6.5 GB. Warning: repo has moved off `gemma4:e2b`; `llm_analysis.py` now requests `gemma3:12b` |
| 20:11:43 | | `~/nlp-study.job` regenerated with `MODEL="gemma3:12b"`, queue `UI`, 8 slots, 24 h, no GPU |

Between 18:54 and 20:04 the repository advanced to commit `1052cdd` ("change to gemma3"). Whether `gemma3:12b` was subsequently pulled into the model store is not shown in any of the five artifacts. The rewrite's `--status` will answer that definitively (`model healthy` or `model absent`).

---

## 11. Open items and known discrepancies

1. **`gemma3:12b` may not be on Argon yet.** The 20:04 status showed a 6.5 GB store, which matches `gemma4:e2b` alone. The code wants `gemma3:12b` (roughly 8 GB more). Under the September script, `--status` cannot tell (it only checks that the directory is non-empty) and the job's tier 1 preflight is the first thing that would notice. Under the rewrite, `--status` reports `model absent` and `./setup.sh` on a login node downloads the missing blobs with resume and checksum. Either way: run `./setup.sh` on a login node before the next submission.

2. **The rewrite is uncommitted.** The working tree `setup.sh` (2026-10-13 11:19) and the relaxed `.python-version` are not in `main`, so Argon still runs the September version. See [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite) for the upgrade order. After the upgrade, a full `./setup.sh` run is required before `--preflight` will accept the env script (`NLP_ENV_VERSION=2` check).

3. **README is behind the code.** The README still says `gemma4:e2b` in the Ollama pull command and in the `llm_analysis.py` section. The code says `gemma3:12b`. The README's "curl install.sh" instruction also does not apply to Argon (glibc). Update when convenient.

4. **GPU nodes with driver 535 run on CPU.** The A10 node seen on 2026-09-14 could not be used by `ollama/ollama:latest`. Requesting `UI-GPU` will not help on such nodes until the driver is updated or an older image is pinned with `NLP_OLLAMA_IMAGE=docker://ollama/ollama:<tag>` followed by `./setup.sh --force sif` on a login node. Always read the placement line the preflight prints.

5. **Sample CSVs in the local `samples/` folder will not ingest as-is.** Verified on 2026-10-13:
   - `samples/notes.csv` header is `id,mrn,encounter,note_csn_id,note_date,note_type,note` (lower-case, underscores). Required names are case-sensitive.
   - `samples/sample_5000_deidentified.csv` starts with a UTF-8 byte-order mark (`EF BB BF`) and names the second column `EncounterID`. With `encoding="utf-8"` the first header parses as `﻿MRN`, and `EncounterID` is not `Encounter`. Two failures.
   Fixes are in [Troubleshooting](#csv-must-contain-columns). `samples/` and `environment-notes/` are in `.gitignore` (commit `957c6b8`) and must stay out of git.

6. **Runtime versus walltime.** 5000 notes at 24 h walltime allows about 17 s per note including retries. CPU inference on a 12 B model is unlikely to meet that. Plan on multiple resubmissions (resume is automatic) or set `NLP_JOB_HRT` higher, and read the tier 1 projection, which now counts the real rows in `notes.csv`.

7. **Unpinned container tag by default.** `docker://ollama/ollama:latest` means every `--force sif` can change the Ollama version and its CUDA requirement. The observed version was 0.32.14. The rewrite's `NLP_OLLAMA_IMAGE` exists to pin it; consider making a pinned tag the default.

8. **Job preflight budget dropped from 900 s to 600 s** in the rewrite (it now inherits the preflight script's default). On a CPU node a 12 B model can exceed 600 s for one call. Export `NLP_PREFLIGHT_BUDGET` at `qsub` time if the gate times out.

---

## 12. Questions and answers

### Getting oriented

**Q: Which machine do I run which command on?**
Build on a login node (`./setup.sh`, anything that downloads). Infer inside an SGE allocation (`qlogin` for `--preflight` and interactive runs, `qsub` for batch). Never run `app.py --analyze` or tier 1 on a login node; the 1-CPU/16 GB limiter kills it, and the rewrite's `nlp_start_ollama` refuses anyway.

**Q: Where is the code I am supposed to edit?**
The Argon checkout is `/nfsscratch/Users/apkane/nlp-study/repo`, a clone of `github.com/benpodium8/nlp-study`. Develop locally, push to `main`, then run `./setup.sh` on a login node, which fast-forwards the checkout. A dirty working tree on Argon blocks the pull with a warning.

**Q: What does `--status` tell me?**
Rewrite: whether repo, venv, sif and the exact model the code wants are `healthy`, `broken` (with a reason) or `absent`, and whether the preflight, env and job scripts exist. It runs no model and starts no server. September version: the model line only said whether the directory was non-empty.

**Q: Why does the preflight matter so much?**
Because `data_worker.py` turns every LLM error into `llm_failed = 1` and keeps going. Without the gate a broken setup yields a full-walltime run, exit 0, and a table with no LLM data.

**Q: Is re-running `setup.sh` dangerous?**
No. Healthy resources are skipped, broken ones rebuilt, interrupted downloads resume. PHI files are moved to `preserved/` before a repo directory is destroyed. `--clean` asks for a typed `yes`.

**Q: I edited `~/nlp-study.job` by hand and it was overwritten.**
Expected. Both generated scripts are rewritten on every setup run (the September version kept a `.bak`; the rewrite does not). Use `NLP_JOB_QUEUE`, `NLP_JOB_SLOTS`, `NLP_JOB_HRT`, `NLP_JOB_GPU`, or pass `qsub` options on the command line.

### Data

**Q: What must `notes.csv` look like?**
UTF-8 without a byte-order mark, header row containing exactly `MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note` (extra columns such as `id` are ignored). Notes may contain embedded newlines if quoted. CRLF line endings are fine.

**Q: Where do I put it?**
`/nfsscratch/Users/apkane/nlp-study/repo/notes.csv`, then refer to it as `./notes.csv` because the job `cd`s into the repo.

**Q: What happens if I ingest the same file twice?**
Rows identical in all six fields are skipped with a count message. Any change to any field creates a new note with a new id.

**Q: Can I ingest without analysing?**
Yes: `python app.py --csv ./notes.csv` ingests and prints the notes table without note text. This is cheap and safe on a login node since no model is involved.

**Q: Where do results go?**
`data.db` (all three tables, including raw LLM text) and `output/` (three CSVs) in the repo directory. All are PHI. Copy them off scratch.

**Q: Why are so many `final_results` rows all null?**
Any disagreement between NLP and LLM on the three numeric fields, any failure flag, or a missing scope type nulls the row. A note that never names the endoscope will be null regardless. Look at `working_results` for the individual answers.

### Running

**Q: How long will the run take?**
Run tier 1 with `notes.csv` staged and read the projection line for its row count. Multiply by up to 5 if the preflight warned that the raw output is not bare JSON (retries). Compare to `h_rt` (24 h by default, `NLP_JOB_HRT`).

**Q: The job ran out of walltime. Did I lose everything?**
No. Every processed note is already in `working_results`. Resubmit with `qsub ~/nlp-study.job --analyze` (no `--csv`). Delete or move the old `output/*.csv` so the exports regenerate.

**Q: How do I get more CPU for inference?**
`NLP_JOB_SLOTS=16 ./setup.sh` and resubmit, or `qsub -pe smp 16 ~/nlp-study.job --analyze ...`. Ollama uses the available cores for CPU inference. Returns diminish; measure with the preflight.

**Q: How do I use a GPU?**
Regenerate with `NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 ./setup.sh`, which bakes in `#$ -l ngpus=1`. To pin a card type add a `#$ -l <complex>` line yourself (`qconf -sc` lists the complexes; the September version used `gpu_p100`). Then confirm the preflight's placement line says 100% on GPU. On 2026-09-14 an A10 node fell back to CPU because its driver (535) was older than the container's CUDA runtime required (550). Requesting a GPU on a non-GPU queue leaves the job pending forever.

**Q: Can I skip the preflight in the job?**
`NLP_SKIP_PREFLIGHT=1 qsub ~/nlp-study.job ...` bypasses it. Not recommended; it is the only thing standing between you and a silent all-null run.

**Q: How do I watch progress?**
`qstat -u apkane` for state, `tail -f ~/nlp-study-logs/nlp-study.o<JOB_ID>` for the log, `~/nlp-study-logs/ollama-<JOB_ID>.log` for the server, and a read-only `sqlite3` count on `data.db` for notes done versus failed.

**Q: The job log shows `exit 143`. Bug?**
On any queue other than `all.q`, 143 usually means the memory limit was hit. `qacct -j <JOB_ID>` shows `maxvmem`. Request more slots or the `UI-HM` queue. On `all.q`, 143 means eviction by a higher-priority queue.

**Q: Why does the job script not use `-cwd`?**
The env script does `cd "$NLP_REPO_DIR"`, so the job is independent of where `qsub` was run. That is also why `./notes.csv` works from anywhere.

**Q: Does my shell environment leak into the job?**
Yes, `-V` is on by default. That is how `NLP_PREFLIGHT_REPS`, `NLP_PREFLIGHT_BUDGET`, `NLP_CONTEXT_LENGTH` and `NLP_SKIP_PREFLIGHT` reach the job. The job sets its own `OLLAMA_HOST`, overriding any inherited one.

**Q: Where does the email go?**
`-m ea` sends at end and abort. No `-M` is set, so it goes to the account default for your HawkID.

**Q: Can I run this on my own Linux box with the same scripts?**
The env script's `nlp_start_ollama` refuses outside SGE unless `NLP_ALLOW_INFERENCE=1`. With that set, and Apptainer installed, the same flow works off-cluster. Never set it on a login node.

### Environment

**Q: Why is Ollama in a container?**
Ollama's binary needs glibc 2.27 or newer; CentOS 7 has 2.17. The Apptainer image carries its own userland.

**Q: Why is `OLLAMA_MODELS=/models` set explicitly?**
The image defaults to `/root/.ollama/models`, which is not writable for an unprivileged Apptainer user. The host directory is bind-mounted at `/models`.

**Q: How can the model be downloaded without running Ollama?**
The rewrite talks to `registry.ollama.ai` directly: it fetches the manifest for `name:tag`, then each blob listed in it, into `manifests/registry.ollama.ai/<ns>/<name>/<tag>` and `blobs/sha256-<hex>`. That is exactly the layout `ollama serve` reads, so the server sees the model on first start.

**Q: Why is Python 3.12 not loaded with `module load`?**
Argon only publishes 3.8.8 modules. A user-built 3.12 lives in `/Users/apkane/temp/build_python/python3.12`. If that directory moves, the venv is detected as broken and rebuilt by `setup.sh` (needs a login node).

**Q: Why does pip refuse to build from source?**
`--only-binary=:all:` is the default so that a missing wheel fails fast with the package name instead of a ten-minute failed compile on GCC 4.8. New dependencies must ship `manylinux2014` (glibc 2.17) wheels.

**Q: I installed a package by hand in the venv and setup does not notice.**
The rewrite re-runs pip only when `requirements.txt` changes (sha256 stamp) or with `--force deps`. Put the dependency in `requirements.txt`.

**Q: Is `/localscratch` useful?**
It is node-local and not shared between the login node and the compute node, so the project does not use it. Everything is on `/nfsscratch`, which both see.

**Q: Why did the Ollama log say `default_num_ctx=4096`?**
With no VRAM detected, Ollama picks a 4096-token default context. The rewrite passes `OLLAMA_CONTEXT_LENGTH=8192` (`NLP_CONTEXT_LENGTH`) into the container so long notes plus the roughly 700-token system prompt are not truncated.

### Policy

**Q: Do I need to ask HPC staff to install anything?**
No. Everything is user-space on scratch and in `$HOME`. The central-install licence review in the Software Installations page does not apply.

**Q: Is any of this PHI leaving the machine?**
No network calls are made by the pipeline. Ollama listens on `127.0.0.1` only. The only outbound traffic is `setup.sh` on a login node pulling code, wheels, the image and the weights.

---

## 13. Troubleshooting

Symptoms are grouped by where they appear. Each entry gives the cause and the fix.

### Ingest

#### CSV must contain columns

```
ValueError: CSV must contain columns: {'NoteType', 'MRN', 'Note', 'Encounter', 'NoteCsnID', 'NoteDate'}
```

Cause: the header does not contain the six names exactly. Three variants seen:

1. Lower-case snake_case header from a database export: `id,mrn,encounter,note_csn_id,note_date,note_type,note`.

   ```bash
   sed -i '1s/.*/id,MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note/' notes.csv
   ```

2. UTF-8 byte-order mark at the start of the file (Excel "CSV UTF-8" export). The first header becomes `﻿MRN`.

   ```bash
   sed -i '1s/^\xEF\xBB\xBF//' notes.csv
   ```

3. `EncounterID` instead of `Encounter`.

   ```bash
   sed -i '1s/EncounterID/Encounter/' notes.csv
   ```

Check the header bytes before and after:

```bash
head -c 80 notes.csv | xxd
```

#### `Error: CSV file not found`

The path is resolved relative to the current directory. In the job that is the repo dir after `cd`. Interactively, run from the repo dir or give an absolute path.

#### `Skipped N duplicate row(s) during import.`

Not an error. Identical rows already exist in `notes`.

#### `No notes to process.`

The `notes` table is empty. Ingest first, or pass `--csv`.

### setup.sh

#### `No outbound HTTPS. Run this on a login node.`

Compute nodes often have no outbound internet. Build on a login node.

#### `'sha256sum' not on PATH`

The rewrite needs coreutils `sha256sum` for blob verification. It is present on CentOS 7; this only appears on a stripped-down host.

#### `git clone failed. On an old TLS stack try: git config --global http.sslVersion tlsv1.2`

Old git/curl/NSS stack on CentOS 7 failing to negotiate TLS with GitHub. Run the suggested command and re-run.

#### `git: unknown option -C`

Expected on git 1.8.3.1. The script does not use `-C`. If you are typing it by hand, use `(cd <dir> && git ...)`.

#### `[repo] local modifications present; not pulling`

Someone edited files in the Argon checkout. `cd` there, `git status`, then stash or commit before re-running.

#### `LOCAL_MODEL not found in llm_analysis.py; set MODEL=name:tag and re-run.`

The rewrite's parser needs a line starting exactly with `LOCAL_MODEL = "name:tag"` (no indentation). Restore that form or pass `MODEL=`.

#### `No Python >= 3.10 found (Argon's modules stop at 3.8)`

`module load` offers only 3.8.8. If a venv already exists, its base interpreter (from `pyvenv.cfg`) is tried automatically. Otherwise point at the user build:

```bash
NLP_PYTHON=/Users/apkane/temp/build_python/python3.12/bin/python3.12 ./setup.sh
```

#### `[venv] unusable (bin/python does not run ...); rebuilding`

The venv symlinks to the user-built Python. Restore that directory or let the script rebuild the venv (login node).

#### `pip failed. With --only-binary this means no wheel exists for this platform and Python`

Wheels-only mode found no `manylinux2014` wheel for some package. Identify it:

```bash
source /nfsscratch/Users/apkane/nlp-study/venv/bin/activate && pip download --only-binary=:all: --no-deps -d /tmp/wt <package>
```

Then pin an older version with a compatible wheel (the September script suggested `spacy==3.7.5` as the usual culprit), or allow a source build for that one package with `PIP_INSTALL_ARGS='' ./setup.sh --force deps` (slow, usually fails on GCC 4.8).

#### `[sif] unusable (not a valid SIF image); rebuilding`

Truncated or corrupt image. The script deletes it and the `.partial`, clears the Apptainer layer cache and re-pulls. Takes a few minutes; `mksquashfs` is pinned to one thread.

#### `registry.ollama.ai has no '<model>'`

The `name:tag` in `LOCAL_MODEL` does not exist on the registry. Check spelling and tag (for example `gemma3:12b`), or pass `MODEL=`.

#### `[model] blob ... incomplete after 5 attempts ... Re-run to resume.`

Network trouble on the login node. Re-run `./setup.sh`; the `.partial` blob resumes where it stopped.

#### `[model] checksum mismatch on ...; removed it. Re-run to download again.`

A corrupted blob was detected by sha256 and deleted. Re-run to fetch it again.

#### `Killing orphaned Ollama server(s) on this login node`

A September-version run left `ollama serve` behind. The rewrite kills it so it stops eating the login-node memory budget. Harmless.

#### `Non-interactive; pass --yes to confirm.`

`--clean` from a non-TTY needs `--yes`.

#### Process killed on a login node with no clear error

The login-node limiter killed the largest process (1 CPU, 16 GB per user). You ran inference where you should not have. Use `qlogin`.

### Preflight

#### `~/nlp-study-env.sh is missing or from an older setup.sh. Re-run ... on a login node first.`

The rewrite's `--preflight` requires an env script with `NLP_ENV_VERSION=2`. Run a full `./setup.sh` on a login node, then retry.

#### `llm_analysis.LOCAL_MODEL is 'gemma3:12b' but setup fetched 'gemma4:e2b'`

The repo changed models after setup, or `MODEL=` was given. Run `./setup.sh` on a login node; it follows the repo.

#### `model blobs missing or truncated: [...]` or `no usable manifest at ...`

Tier 0 found the on-disk model store incomplete for the model the code wants. Run `./setup.sh` on a login node; it resumes the download. This is the on-disk form of [open item 1](#11-open-items-and-known-discrepancies).

#### `'gemma3:12b' is not served (server has: [...])`

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

#### `Ollama not answering after 180 s` or `Ollama died on startup; log follows`

Read the log printed (`~/nlp-study-logs/ollama-*.log`). Common causes: bad bind mount, unwritable `OLLAMA_MODELS`, a container that cannot start under this Apptainer version.

#### `call 1: no response within 600s`

The model is generating on CPU when a GPU was expected, is emitting a very long output, or the node is contended. Read the placement line. Raise `NLP_PREFLIGHT_BUDGET` only if CPU inference is intended.

#### `Endoscopy == 0, expected 1` (hard failure)

The model misread an unambiguous synthetic note. Do not submit. Usually the model ignored the schema or returned prose.

#### `output is not bare JSON, so the repair layer is doing work`

Warning only, but each JSON failure costs a full regeneration and `data_worker` retries up to 5 times. Expect up to 5x runtime. Consider the prompt or decoding changes in section 14.

#### `partly or fully on CPU, expect it to be slow`

`/api/ps` reports less than 100% of the model in VRAM on a node that has `nvidia-smi`. Usually the driver-too-old case; see below.

#### `Login node: static checks only. Inference needs a compute node.`

You are on a login node. Tier 0 ran. Repeat `--preflight` inside `qlogin`.

### Batch job

#### Job stays in `qw` indefinitely

Most likely GPU directives on a queue without GPU nodes (`NLP_JOB_GPU=1` with `-q UI`). Regenerate with `NLP_JOB_QUEUE=UI-GPU`, or submit with `-q UI-GPU` on the command line. `qstat -j <JOB_ID>` shows scheduling messages.

#### `Preflight failed; not starting app.py.`

Intended fail-fast. The lines above it name the problem; see the Preflight section.

#### `app.py exited 143 (143 outside all.q usually means the memory limit)`

Outside `all.q`: memory limit. Check `qacct -j <JOB_ID>` for `maxvmem`, then add slots (`NLP_JOB_SLOTS=16` or `-pe smp 16`) or use `-q UI-HM`. On `all.q`: eviction; resubmit, the run resumes.

#### `app.py exited 137`

TERM did not stop it and SGE sent KILL. Same causes as 143.

#### Job finished quickly, exit 0, LLM columns all empty

Something started failing after the preflight passed (for example the Ollama server died mid-run). Count failures:

```bash
sqlite3 data.db "select count(*) from working_results where LLM_Failed=1;"
```

Those rows will not be redone on resume because a `working_results` row exists. Delete them to reprocess:

```bash
sqlite3 data.db "delete from final_results where id in (select id from working_results where LLM_Failed=1); delete from working_results where LLM_Failed=1;"
```

Then resubmit with `--analyze` and regenerate the exports.

#### `File 'output/combined_results.csv' already exists. Export skipped.`

Write-once protection. Move the old files aside and re-run `--analyze` (reconcile and export are cheap when nothing new is pending).

#### NVIDIA driver too old

```
level=WARN source=cuda_compat.go msg="NVIDIA driver too old" device="NVIDIA A10" compute=8.6 driver=535 required_driver="550 or newer"
```

The container's CUDA runtime is newer than the node driver. Ollama silently uses CPU. Options: accept CPU and budget time accordingly; request a different GPU node type; or pin an older image whose CUDA runtime accepts driver 535:

```bash
NLP_OLLAMA_IMAGE=docker://ollama/ollama:<older-tag> ./setup.sh --force sif
```

Then re-run `--preflight` and read the placement line.

#### `user overrode visible devices CUDA_VISIBLE_DEVICES=0`

Informational. The SGE GPU allocation sets `CUDA_VISIBLE_DEVICES`. Only matters if a GPU is expected and not found; then unset it and retry as the log suggests.

#### Where did my results go after a few weeks?

Scratch purge. Copy `output/` and `data.db` to durable storage after every run. `preserved/` is also scratch.

### Upgrading from the September script

#### I pushed the rewrite but Argon still behaves like the old script

The checkout runs whatever is on disk. Pull first (`git pull --ff-only` in the repo), then run `./setup.sh`. See [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite).

#### The old `ollama-setup.log` and `~/nlp-study-setup-*.log` files stopped appearing

Expected. The rewrite writes everything under `~/nlp-study-logs/`.

---

## 14. Software development constraints

Things to hold in mind while changing the code, grouped by the layer they affect.

### Platform

1. **Target is CentOS 7, glibc 2.17, GCC 4.8, Bash 4.2, git 1.8.3.1.** No source builds of C extensions. Every new Python dependency must publish a `manylinux2014`-compatible wheel for CPython 3.12 x86_64. `manylinux_2_28` wheels will not install. Check with `pip download --only-binary=:all:` before adding to `requirements.txt`.
2. **Python 3.12 is a user build, not a module.** `.python-version` pins `3.12` in the working tree (3.12.13 at commit `957c6b8`); the venv has 3.12.14. `setup.sh` reads only major.minor from the file. Keep the code compatible with 3.10+ (the installer accepts 3.10 through 3.13) unless you are willing to also tighten `check_python` in `setup.sh`.
3. **Compute nodes may have no internet.** Anything that downloads at runtime (`spacy.load("en_core_web_sm")`, Hugging Face, `ollama pull`) must happen in `setup.sh` on a login node. `setup.sh` scans for `spacy.load("<name>")` with a literal quoted name and pre-downloads; keep that call pattern literal if you add one.
4. **Login nodes: 1 CPU, 16 GB, process killer.** Nothing that touches the model may run there. Tier 0 checks are the ceiling, and the rewrite enforces this in `nlp_start_ollama` by hostname and SGE variables. Keep the hostname check if you touch that function; `NLP_ALLOW_INFERENCE` deliberately does not override it.
5. **`setup.sh` edits must stay Bash 4.2-safe.** No `"${arr[@]}"` on a possibly-empty array under `set -u` (use `${arr[@]+"${arr[@]}"}` as the pip call does), no `git -C`, mind `${VAR-default}` versus `${VAR:-default}` (`PIP_INSTALL_ARGS=""` must mean "no flags"). `set -Eeuo pipefail` is on; `. activate` and `module` need `set +u` around them, as the script already does. Outside the venv, `python` on CentOS 7 is Python 2.7, so helper code must call `python3` or the venv interpreter (as `manifest_blobs` does). The script carries shellcheck directives; keep it shellcheck-clean.
6. **`NLP_SETUP_LIB_ONLY=1 source setup.sh`** loads the functions and returns before `main`; use it for shell tests instead of running the script.
7. **Bump `ENV_VERSION`** whenever the shape of the generated env or job script changes, because `--preflight` refuses an env script with a different version.
8. **Scratch is ephemeral.** Never treat `/nfsscratch` as the only copy of PHI results. Never write PHI under git-tracked paths; `.gitignore` covers `data.db`, `output/`, `notes.csv`, `samples`, `environment-notes`, `.venv`, `__pycache__`.

### Model and Ollama

9. **`LOCAL_MODEL` in `llm_analysis.py` is parsed by `sed`.** Keep it a single line of the form `LOCAL_MODEL = "name:tag"` at column 0. Any model change requires `./setup.sh` on a login node to fetch the weights, and the README should be updated in the same commit.
10. **Model size budget.** `gemma4:e2b` is 5.12 B parameters at Q4_K_M, about 7.2 GB, and is a reasoning model that emits a thinking trace by default. `gemma3:12b` is roughly 8 GB. Budget 8 to 10 GB of VRAM once the KV cache is allocated. The September job script pinned `gpu_p100` (16 GB) for that reason; the rewrite requests only `ngpus=1`, so pin a card type with `#$ -l <complex>` if a small card would silently fall back to CPU. CPU inference needs that much RAM plus headroom.
11. **The module-level `ollama` client is wired by `OLLAMA_HOST` at import time.** Do not create `ollama.Client()` with a hard-coded host or port, and do not import `ollama` before the host is exported. The port is random per job to avoid collisions with other users on the node.
12. **Ollama is not deterministic by default.** `ollama.chat()` is called without `options`, so the server default temperature (0.8) applies despite the prompt calling itself deterministic. For reproducibility add `options={"temperature": 0, "seed": <int>}` and consider `format="json"`, which constrains decoding to JSON and would reduce the repair-and-retry load. Re-run the preflight after any change here.
13. **Context window.** The rewrite sets `OLLAMA_CONTEXT_LENGTH=8192` on the container (`NLP_CONTEXT_LENGTH`). The September script left the default, which was 4096 on CPU nodes. The system prompt is roughly 700 tokens; if the application should be portable to other Ollama hosts, also pass `options={"num_ctx": 8192}` in the chat call so the window does not depend on the launcher.
14. **`OLLAMA_KEEP_ALIVE=-1` keeps the model loaded for the whole job.** Keep it; reloading an 8 GB model per note would dominate runtime. `OLLAMA_NUM_PARALLEL=1` matches the single-threaded `data_worker`.
15. **The container tag defaults to `latest`.** Any `--force sif` may change Ollama's version, CUDA requirement and client-API behaviour. The Python `ollama` package (0.6.2) is also unpinned. Pin with `NLP_OLLAMA_IMAGE` and a version specifier in `requirements.txt` when stability matters.
16. **GPU use requires three things to line up:** a `*-GPU` queue, `#$ -l ngpus=1` (and a card-type complex if you need one), and a node driver the container's CUDA runtime accepts. `nlp_start_ollama` only passes `--nv` when `nvidia-smi -L` works. Verify with the preflight placement line every time.
17. **The on-disk model store is Ollama's own layout.** The rewrite writes `manifests/registry.ollama.ai/<ns>/<name>/<tag>` and `blobs/sha256-<hex>` with curl. If Ollama changes that layout, `create_model`/`verify_model` and the preflight's tier 0 model check all need updating together.

### Pipeline semantics

18. **LLM failures are swallowed by design.** Any change to the schema, prompt or model must be followed by a tier 1 preflight; nothing else will tell you it broke. If you add a field, update all of: the prompt schema text, `EXPECTED_FIELDS`, `BOOL_FIELDS` if boolean, `normalize_llm_result` if integer, `default_nlp_result` in `data_worker.py`, `WORKING_RESULTS_TABLE_SCHEMA`, `INSERT_WORKING_RESULT_SQL` and its `values` tuple, `SELECT_ALL_WORKING_RESULTS_SQL` and the positional unpack in `reconcile_working_results`, the export queries, `display.py` truncation rules, and `HARD`/`SOFT` plus the synthetic note in `setup.sh`.
19. **Keep `except Exception` in `llm_analysis()`, not `except BaseException`.** The preflight's timeout is a `BaseException` subclass precisely so that wrapper cannot swallow it.
20. **Validation rejects extra fields and non-0/1 booleans.** A model that adds commentary keys or returns `true`/`false` fails validation and is not retried (only JSON parse errors are). The prompt is the only defence; `format="json"` with a schema would be stronger.
21. **Retries are only for JSON decode errors**, up to 5 attempts, each a full generation. Budget runtime accordingly and prefer fixing the prompt or decoding over raising the retry count.
22. **`CREATE TABLE IF NOT EXISTS` does not migrate.** Changing a schema does nothing to an existing `data.db`. Delete it (after copying out) or write a migration.
23. **Resume logic keys on row existence, not freshness.** Changing the prompt, model or NLP rules does not reprocess notes that already have `working_results`. Delete those rows (and their `final_results`) to recompute.
24. **Relative paths everywhere.** `data.db` and `output/` are resolved from the current directory, and the export functions open `"data.db"` by name rather than reusing the connection. Run from the repo directory, or refactor to pass a path.
25. **Exports are write-once and interlocked.** If any of the three CSVs exists, the corresponding export is skipped. The second export writes two files and skips both if either exists.
26. **Reconciliation nulls the row when no scope is found**, even if everything else agrees. If that is not the desired study behaviour, change `insert_null_row` deliberately and document it; it is the "when in doubt, discard" rule.
27. **NLP rule quirks to know before "fixing" disagreements:** `FellowPresent` only fires if the sentence starts after 70% of the document (signature-block heuristic). `ScopeType` needs the word `endoscope` in the sentence and a brand in `Olympus|Pentax|Fujifilm`; the LLM is asked for any brand, so other manufacturers produce one-sided scopes, which reconciliation accepts. Biopsy counts are tied to duodenum sentences within two sentences.
28. **Ingest dedup is whole-row equality.** A trailing space in one field creates a second note. Clean exports upstream.
29. **CSV contract is strict and case-sensitive**, opened with `encoding="utf-8"`. Switching to `encoding="utf-8-sig"` would make BOM-prefixed exports work. `EncounterID` would still need renaming unless the required set is widened.

### Operations and HIPAA

30. **Logs are not PHI-free by construction.** `data_worker` logs exceptions per note id, not note text, and Ollama at `OLLAMA_DEBUG=INFO` does not log prompts. Keep it that way: do not add debug prints of note content, raw LLM output or CSV rows. `~/nlp-study-logs/` is in `$HOME`, not scratch, and now also holds the Ollama server logs.
31. **The job emails on end and abort** (`-m ea`). Do not put identifiers in the job name or in anything that reaches stdout at the end.
32. **Everything binds to `127.0.0.1`.** Keep it that way; never expose the Ollama port on the node's interfaces.
33. **Walltime versus volume.** 24 h at 8 slots is the default (`NLP_JOB_HRT`, `NLP_JOB_SLOTS`). Projected runtime comes from tier 1, which counts the real rows. Raise `h_rt` or plan resubmissions; the pipeline is resume-safe.
34. **Windows line endings** in any script copied to Argon need `dos2unix`. Shebangs with `\r` fail with confusing "no such file" errors.

---

## Appendix A: environment variable reference

### Read by `setup.sh` (rewrite)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL` | read from `llm_analysis.py` | Override the model to fetch (warns on disagreement) |
| `NLP_PYTHON` | auto-detect | Path to a Python 3.10+ interpreter; tried first |
| `NLP_FAST_ROOT`, `NLP_BULK_ROOT` | `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` | Force the roots for repo+venv and image+model |
| `NLP_OLLAMA_IMAGE` | `docker://ollama/ollama:latest` | Pin the container image |
| `PIP_INSTALL_ARGS` | `--only-binary=:all:` | Extra pip flags; empty string permits source builds |
| `NLP_JOB_QUEUE` | `UI` | Queue baked into the job script |
| `NLP_JOB_SLOTS` | `8` | `-pe smp` slots baked into the job script |
| `NLP_JOB_HRT` | `24:00:00` | `-l h_rt` baked into the job script |
| `NLP_JOB_GPU` | unset | Bake `#$ -l ngpus=1` into the job script (no card type; add `#$ -l <complex>` by hand if needed) |
| `NLP_SETUP_LIB_ONLY` | unset | Source the script for its functions only |

September-only variables, no longer read: `SKIP_SMOKE_TEST`, `FORCE_SMOKE_TEST`, `NLP_NOTE_ESTIMATE`, `FORCE_VENV`, `FORCE_SIF`.

### Read at run time by `~/nlp-study-env.sh`, `--preflight` and the job (inherited via `-V`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `NLP_CONTEXT_LENGTH` | `8192` | `OLLAMA_CONTEXT_LENGTH` passed into the container |
| `NLP_PREFLIGHT_REPS` | 3 (job forces 1 unless set) | Tier 1 repetitions |
| `NLP_PREFLIGHT_BUDGET` | 600 | Per-call timeout in seconds |
| `NLP_SKIP_PREFLIGHT` | unset | Bypass the gate in the job |
| `NLP_ALLOW_INFERENCE` | unset | Let `nlp_start_ollama` run outside SGE (never on a login node) |
| `NLP_QUIET` | unset (job sets 1) | Suppress the env script's ready banner |
| `NLP_COLOR` | unset | `1` forces colour in the preflight output |

### Exported by `~/nlp-study-env.sh`

`NLP_ENV_VERSION=2`, `NLP_REPO_DIR`, `NLP_VENV_DIR`, `NLP_SIF`, `NLP_MODELS_DIR`, `NLP_MODEL`, `NLP_PREFLIGHT`, `NLP_LOG_DIR`, `APPTAINER_CACHEDIR`, `APPTAINER_TMPDIR`; after `nlp_start_ollama`: `OLLAMA_HOST` (random port), `NLP_OLLAMA_PID`, `NLP_OLLAMA_LOG`. Functions: `nlp_has_gpu`, `nlp_start_ollama`, `nlp_stop_ollama`, `nlp_preflight [tier]`.

### Set inside the container

`OLLAMA_MODELS=/models`, `OLLAMA_HOST=127.0.0.1:<port>`, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_CONTEXT_LENGTH=<NLP_CONTEXT_LENGTH>`.

### Set by SGE inside a job

`JOB_ID`, `JOB_NAME`, `NSLOTS`, `PE_HOSTFILE`, `SGE_TASK_ID` (array jobs), `TMPDIR`. `setup.sh` and the env script use `JOB_ID` or `PE_HOSTFILE` to recognise that they are inside an allocation.

---

## Appendix B: the generated scripts, annotated

### B.1 `~/nlp-study.job` (rewrite)

```bash
#!/bin/bash
# Generated by setup.sh; regenerated on every setup run. Tune through NLP_JOB_* and re-run setup.sh.
#$ -N nlp-study                        # job name -> log file nlp-study.o<JOB_ID>
#$ -q UI                               # NLP_JOB_QUEUE
#$ -pe smp 8                           # NLP_JOB_SLOTS; Ollama uses them for CPU inference
#$ -l h_rt=24:00:00                    # NLP_JOB_HRT
#$ -j y                                # merge stderr into stdout
#$ -o /Users/apkane/nlp-study-logs/    # directory -> default filename inside it
#$ -m ea                               # mail at end and abort (account default address)
# No GPU requested. For one, regenerate with: NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 setup.sh
set -eo pipefail
export NLP_QUIET=1
. /Users/apkane/nlp-study-env.sh       # paths, helpers, cd repo, activate venv
set -u
echo "nlp-study job ${JOB_ID:-?} on $(hostname), $(date); slots ${NSLOTS:-?}; model ${NLP_MODEL}; args: $*"
nlp_start_ollama                       # random port, --nv if a GPU is visible, context 8192, log in ~/nlp-study-logs
trap nlp_stop_ollama EXIT
if [ -z "${NLP_SKIP_PREFLIGHT:-}" ]; then
    NLP_PREFLIGHT_REPS="${NLP_PREFLIGHT_REPS:-1}" nlp_preflight 1 \
        || { echo "Preflight failed; not starting app.py." >&2; exit 1; }
fi
python app.py "$@" || { rc=$?; echo "app.py exited ${rc} (143 outside all.q usually means the memory limit)" >&2; exit "$rc"; }
echo "app.py finished at $(date)"
```

### B.2 `~/nlp-study-env.sh` (rewrite), key parts

```bash
export NLP_ENV_VERSION=2                       # checked by setup.sh --preflight
export NLP_REPO_DIR=... NLP_VENV_DIR=... NLP_SIF=... NLP_MODELS_DIR=... NLP_MODEL=... NLP_PREFLIGHT=... NLP_LOG_DIR=...
export APPTAINER_CACHEDIR=... APPTAINER_TMPDIR=...

nlp_start_ollama() {
    # refuses if hostname contains "login"; refuses outside SGE unless NLP_ALLOW_INFERENCE=1
    # no-op if a server it started is still alive
    export OLLAMA_HOST="127.0.0.1:<free port>"
    apptainer exec [--nv] --bind "$NLP_MODELS_DIR:/models" \
        --env OLLAMA_MODELS=/models --env OLLAMA_HOST="$OLLAMA_HOST" \
        --env OLLAMA_KEEP_ALIVE=-1 --env OLLAMA_CONTEXT_LENGTH="${NLP_CONTEXT_LENGTH:-8192}" \
        "$NLP_SIF" ollama serve > "$NLP_LOG_DIR/ollama-${JOB_ID:-$$}.log" 2>&1 &
    # wait up to 90 x 2 s for /api/tags; on failure print the log and return 1
}
nlp_stop_ollama() { kill; wait up to 10 s; kill -9; }
nlp_preflight()   { python "$NLP_PREFLIGHT" --repo "$NLP_REPO_DIR" --model "$NLP_MODEL" --models-dir "$NLP_MODELS_DIR" --tier "${1:-1}"; }

cd "$NLP_REPO_DIR" && . "$NLP_VENV_DIR/bin/activate"
```

### B.3 September version of the job script (what produced the 2026-09-14 artifacts)

Same `#$` directives, except that `NLP_JOB_GPU=1` also pinned `#$ -l gpu_p100`. Other differences: it did not source the env script; it inlined the venv activation, port selection and `apptainer exec ... ollama serve` (log to `repo/ollama-<JOB_ID>.log`, no `OLLAMA_CONTEXT_LENGTH`); it ran the preflight with `--tier 1`, reps 1 and a 900 s budget, then printed `ollama ps`; and it embedded `MODEL="gemma3:12b"` as a literal copied from `llm_analysis.py` at generation time.

---

## Appendix C: Ollama server configuration as observed

From the 19:22 and 19:33 server logs, Ollama 0.32.14 inside the container on a compute node (September version launcher):

| Setting | Value | Note |
| --- | --- | --- |
| `OLLAMA_HOST` | `http://127.0.0.1:59103` | Loopback only, random port |
| `OLLAMA_MODELS` | `/models` | Bind mount of the scratch model store |
| `OLLAMA_KEEP_ALIVE` | `2562047h47m16s` | The `-1` setting; model stays loaded |
| `OLLAMA_NUM_PARALLEL` | 1 | One request at a time |
| `OLLAMA_MAX_LOADED_MODELS` | 0 | Auto |
| `OLLAMA_CONTEXT_LENGTH` | 0 | Auto; resolved to `default_num_ctx=4096` with no VRAM. The rewrite sets 8192 |
| `OLLAMA_FLASH_ATTENTION` | false | |
| `OLLAMA_LOAD_TIMEOUT` | 5m | |
| `OLLAMA_DEBUG` | INFO | Prompts are not logged |
| `OLLAMA_NO_CLOUD` | false | Cloud disabled: false, but no outbound call is made by the pipeline; models are local |
| `CUDA_VISIBLE_DEVICES` | 0 | Set by the allocation; Ollama warns that visible devices were overridden |
| GPU discovery | NVIDIA A10, compute 8.6, driver 535 | Rejected: requires 550 or newer |
| Inference compute | cpu, 251.5 GiB total, ~215 to 220 GiB available | |
| Blobs | 4 total, 0 unused removed | One model (`gemma4:e2b`) at the time |

---

## Appendix D: database schema and output files

### Tables in `data.db`

**notes**: `id` (autoincrement), `mrn`, `encounter`, `note_csn_id`, `note_date`, `note_type`, `note`.

**working_results** (one row per note id): the ten extracted fields twice, suffixed `_NLP` and `_LLM`; `RawResponse_LLM` (verbatim model text, for audit); `AllHardDataInAgreement` (1, 0, or null when the LLM failed); `NLP_Failed`; `LLM_Failed`.

**final_results**: `id`, `ScopeType`, `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken`, `FellowPresent`. All null when reconciliation discards the note.

### Files in `output/`

| File | Content |
| --- | --- |
| `combined_results.csv` | `notes` left-joined with `final_results` |
| `all_tables_combined_results.csv` | `notes` left-joined with the numeric and scope columns of `working_results` (both sides) and `final_results` (prefixed `Final_`) |
| `full_combined_results.csv` | Every table dumped sequentially with a `Table: <name>` header line between them, including raw LLM responses |

All three are written once and never overwritten. All three contain PHI.

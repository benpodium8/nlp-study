# nlp-study on the UIowa Argon HPC cluster: consolidated runbook

Last consolidated: 2026-10-13 (second pass). Covers commit `f8f76c0` ("create the runbook, the backlog and redo the setup script"), which holds the rewritten `setup.sh` (1100 lines, `ENV_VERSION=2`), `.python-version` relaxed to `3.12`, `BACKLOG.md`, and this document, plus the uncommitted working-tree changes present at 13:20 the same day: `setup.sh` at 1127 lines (a `mksquashfs` PATH wrapper replacing the ineffective `APPTAINER_MKSQUASHFS_ARGS` export, curl stall timeouts, `GIT_TERMINAL_PROMPT=0`) and a revised `BACKLOG.md` (B19 to B21 renumbered, B27 added). Also covers the Argon session logs from 2026-09-14 (HawkID `apkane`).

This document merges five artifacts from the 2026-09-14 Argon session, the project README and source, the sample data in `samples/`, and three reference pages:

| Source | What it is |
| --- | --- |
| Setup transcript, 18:53 CDT | `./setup.sh` run on `argon-login-5` in `setup` mode (model still `gemma4:e2b`) |
| Ollama server log, 19:22 CDT | Server inside the Apptainer container on a compute node, loading `gemma4:e2b` |
| Ollama server log, 19:33 CDT | Second server start on the same kind of node, CPU-only |
| Status transcript, 20:04 CDT | `./setup.sh --status` inside SGE job 6516164 on `argon-itf-bx55-01`; repo now wants `gemma3:12b` |
| Generated job script, 20:11 CDT | `~/nlp-study.job`, queue `UI`, 8 slots, 24 h, model `gemma3:12b` |
| [Argon: Software Installations](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513438/Software+Installations) | Central software policy, environment modules, licence review |
| [Argon: Basic Job Submission](https://uiowa.atlassian.net/wiki/spaces/hpcdocs/pages/76513450/Basic+Job+Submission) | SGE `qsub` options, queues, memory, output files, exit codes |
| [github.com/benpodium8/nlp-study](https://github.com/benpodium8/nlp-study) | The code, the README (workstation setup, HIPAA notes), `setup.sh` |
| `samples/Upper Gl endoscopy Scope details.docx` (gitignored) | Ten example notes with the researcher's five review comments, which define what the fields must mean |
| `BACKLOG.md` | Evaluation against the study goals, 26 bug fixes and 13 feature items. This runbook cross-references it and does not duplicate it |

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

The header row must contain exactly these six names (an extra `id` column is tolerated and ignored): `MRN, Encounter, NoteCsnID, NoteDate, NoteType, Note`. See [Troubleshooting: CSV must contain columns](#csv-must-contain-columns).

**Step 1. Build or repair on a login node** (needs internet; never runs inference):

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
./setup.sh
```

Re-running is always safe. Healthy resources are skipped, broken ones rebuilt, interrupted downloads resumed. After a model change in `llm_analysis.py` this step is what downloads the new weights. It ends with a tier 0 (static) preflight and rewrites `~/nlp-study.job` and `~/nlp-study-env.sh`. Only one setup run per scratch tree can be active at a time (a lock directory enforces it).

**Step 2. Prove the LLM works, on a compute node** (interactive allocation):

```bash
qlogin -q UI -pe smp 8
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

Precedence inside `main()`: `--analyze` wins, then `--print`, then `--working_results`, then `--csv` alone (so `--print --csv x` does not ingest; BACKLOG B22). The database file is always `data.db` in the current working directory. The export functions open `"data.db"` by relative path as well, so the program must be run from the repository directory.

### 2.2 Extracted fields

Both extractors return the same dictionary:

| Field | Type | Meaning |
| --- | --- | --- |
| `ScopeType` | string or null | Brand and model of the upper endoscope, for example `Olympus H190` |
| `Colonoscopy` | 0 or 1 | A colonoscopy was performed |
| `ColonoscopyInformation` | string or null | Supporting sentence |
| `Endoscopy` | 0 or 1 | An upper endoscopy / EGD was performed |
| `EndoscopyInformation` | string or null | Supporting sentence |
| `NumberOfDuodenalBiopsies` | integer | Explicitly stated count, else 0. This makes "count unknown" and "none" the same value, which the study needs to keep apart (15.5; BACKLOG B1) |
| `DuodenalBiopsiesTaken` | 0 or 1 | At least one duodenal biopsy |
| `DuodenalBiopsiesInformation` | string or null | Supporting sentence |
| `FellowPresent` | 0 or 1 | A fellow is explicitly mentioned |
| `FellowInformation` | string or null | Supporting sentence |

What the researcher actually needs from these fields is in [15.5](#155-study-requirements-the-researchers-review-comments).

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
| apptainer | 1.3.2-1.el7 | Runs the Ollama container unprivileged. Predates the 1.4.0 `--mksquashfs-args` option (see 3.3) |
| Environment Modules | available | Only `python/3.8.8_*` modules exist on Argon (gcc-5.4.0, gcc-9.3.0, intel-2021.2.0 variants). No 3.12 module |
| Python actually used | `/Users/apkane/temp/build_python/python3.12/bin/python3.12` | A user-built 3.12 in `$HOME`. The venv reports Python 3.12.14. `.python-version` pins `3.12.14` to match it; `setup.sh` reads only major.minor, so any 3.12.x still satisfies the installer |

### 3.3 Login node limits (the reason for the two-node workflow)

Argon caps each user on a login node at 100% of one logical CPU and 16 GB of memory, and kills the largest process when the cap is exceeded. A multi-gigabyte model asked for a single sentence under that cap is what hung an earlier installer. Therefore:

- Build (clone, pip, `apptainer pull`, model download) on a login node: these need internet and are light on CPU, with one exception below.
- Infer (preflight tier 1, `app.py --analyze`) only inside an SGE allocation (`qlogin` or `qsub`). The rewrite's `nlp_start_ollama` refuses to start on a hostname containing `login`, and refuses outside an SGE job unless `NLP_ALLOW_INFERENCE=1` is set (intended for off-cluster machines only).

**The exception: SquashFS conversion during `apptainer pull`.** The committed `setup.sh` (`f8f76c0`) exports `APPTAINER_MKSQUASHFS_ARGS="-processors ${NSLOTS:-1} -mem 2G"` and its comments say the conversion is pinned to one thread. It is not. That variable is the environment form of `apptainer build --mksquashfs-args`, which the Apptainer changelog lists as new in v1.4.0; `apptainer pull` does not read it, and Argon runs 1.3.2. So with that version the pull converts the image with mksquashfs's defaults (every core, a quarter of RAM) under the login-node limiter. The 2.8 GB pull succeeded on 2026-09-14 anyway, but it was exposed. The working-tree revision of 13:20 removes the export and adds `make_mksquashfs_wrapper`: before the pull it writes `nlp-study-assets/bin/mksquashfs`, a one-line wrapper that execs the real binary with `-processors ${NSLOTS:-1} -mem 2G`, and puts that directory first on `PATH` for the `apptainer pull` only. Apptainer resolves `mksquashfs` through `PATH` unless `apptainer.conf` pins `mksquashfs path`, in which case the script warns that the cap cannot be applied. The code reads correctly; it has not yet been exercised on Argon (BACKLOG B14 asks for a `./setup.sh --force sif` on a login node to confirm).

September version: the setup script started a temporary `ollama serve` on the login node to run `ollama pull` and `ollama list`. It worked on 2026-09-14 but is exactly the pattern the rewrite removes.

### 3.4 Scratch filesystems

| Mount | Login node | Compute node | Used for |
| --- | --- | --- | --- |
| `/scratch` | not mounted | not mounted | nothing (first candidate in `setup.sh`, skipped) |
| `/nfsscratch` | 229 TB free of 284 TB (NFSv3 from `172.29.4.10:/dpool01/nfsscratch`) | same | repo, venv, container image, model weights, lock |
| `/localscratch` | 3.1 TB free of 3.3 TB (local XFS) | 818 GB free of 892 GB | not used by this project (node-local, not shared) |

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

| Purpose | Path |
| --- | --- |
| Repository checkout (cwd for all runs) | `/nfsscratch/Users/apkane/nlp-study/repo` |
| Virtual environment | `/nfsscratch/Users/apkane/nlp-study/venv` |
| Dependency stamp (rewrite): requirements hash plus `check`/`nocheck` | `/nfsscratch/Users/apkane/nlp-study/venv/.requirements.sha256` |
| Generated preflight script | `/nfsscratch/Users/apkane/nlp-study/nlp-preflight.py` |
| Setup lock (rewrite): `owner` file holds host, pid, epoch | `/nfsscratch/Users/apkane/nlp-study/.setup.lock/` |
| Ollama container image | `/nfsscratch/Users/apkane/nlp-study-assets/ollama.sif` (in-progress pull: `ollama.partial.sif`) |
| Model store (bound to `/models` inside the container) | `/nfsscratch/Users/apkane/nlp-study-assets/ollama-models` with `manifests/<host>/<ns>/<name>/<tag>` (today `manifests/hf.co/ggml-org/gemma-3-12b-it-GGUF/Q4_K_M`) and `blobs/sha256-<hex>` |
| Apptainer cache and tmp | `/nfsscratch/Users/apkane/nlp-study-assets/apptainer-cache`, `.../apptainer-tmp` |
| Generated `mksquashfs` wrapper (working tree; on `PATH` only during `apptainer pull`) | `/nfsscratch/Users/apkane/nlp-study-assets/bin/mksquashfs` |
| Notes input (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/notes.csv` |
| SQLite database (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/data.db` |
| CSV exports (PHI) | `/nfsscratch/Users/apkane/nlp-study/repo/output/` |
| Preserved user data after `--clean` or a repo rebuild that could not restore it | `/nfsscratch/Users/apkane/nlp-study/preserved/<timestamp>/` |
| Generated job script | `/Users/apkane/nlp-study.job` |
| Generated interactive env script | `/Users/apkane/nlp-study-env.sh` |
| Job stdout+stderr | `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>` |
| Ollama server log, batch or interactive (rewrite) | `/Users/apkane/nlp-study-logs/ollama-<JOB_ID>.log` (`ollama-<pid>.log` outside a job) |
| Setup transcripts (rewrite; deleted after 30 days) | `/Users/apkane/nlp-study-logs/setup-<YYYYMMDD-HHMMSS>.log` |

September version differences: setup transcripts were `~/nlp-study-setup-<timestamp>.log`; the setup-time Ollama log was `nlp-study-assets/ollama-setup.log`; the job-time Ollama log was `repo/ollama-<JOB_ID>.log`; the interactive one went to `$TMPDIR`; hand-edited generated scripts were backed up as `*.bak.<timestamp>` (the rewrite overwrites them atomically without backup).

---

## 5. setup.sh reference

### 5.1 Modes

| Command | Node | Effect |
| --- | --- | --- |
| `./setup.sh` | any | Build or repair whatever is missing or broken (downloads need outbound HTTPS, so normally a login node), then run the preflight at the highest tier the node allows: tier 0 on a login node or outside SGE, tier 1 inside `qlogin`/`qsub`. Rewrites the two `$HOME` scripts |
| `./setup.sh --preflight` | any | Check an existing install; builds nothing. Same tier rule. Refuses if the env script is missing or from an older script version |
| `./setup.sh --status` | any | Report repo, venv, sif and model as `healthy`, `broken` (with a reason) or `absent`; presence of the preflight, env and job scripts; whether a setup lock is held; whether `preserved/` holds data. Changes nothing |
| `./setup.sh --force <name>` | login | Rebuild one resource unconditionally. Names: `repo`, `venv`, `deps`, `sif`, `model`, `all`. Repeatable, or `--force=name` |
| `./setup.sh --clean [-y]` | login | Move PHI aside to `preserved/`, then delete repo, venv, preflight script, sif, model store and Apptainer caches. Prompts for `yes`; `-y`/`--yes` for non-interactive |
| `./setup.sh --help` | any | Prints the usage block |

September version also accepted `FORCE_VENV=1` and `FORCE_SIF=1`, had no lock, and could not run in a job at all without warnings.

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
| repo | A git checkout with a valid `HEAD` and no tracked files missing from the working tree |
| venv | `bin/python` runs, reports the same major.minor as the selected interpreter, and `bin/activate` exists |
| deps | The stamp matches the sha256 of `requirements.txt` and (unless the stamp says `nocheck`) every requirement imports by name. If they no longer import, the venv is rebuilt automatically |
| sif | File exists, is at least 100 MB, and `apptainer sif list` can parse it (no execution, so safe on a login node) |
| model | The manifest for the exact `name:tag` the code wants exists under `manifests/<host>/`, every blob it lists exists in `blobs/` at the listed size, and its config does not require a newer Ollama than the image |

Building or updating repo, sif, model or deps requires outbound HTTPS. Without it the script dies with `[<name>] has to be built or updated, but this node has no outbound internet` before destroying anything, and a node that has everything healthy proceeds offline.

Before a repo directory is destroyed, `preserve_user_data` moves `*.csv`, `*.db`, `*.sqlite*` and `output/` into `preserved/<timestamp>/`. After a successful re-clone, `restore_user_data` moves them back into the fresh checkout, skipping any file the fresh checkout already has (that file stays in `preserved/` with a warning). `--clean` only preserves; it never restores.

September version: the model check was only "the models directory is non-empty", which is why the 20:04 status reported `model present` while the store held `gemma4:e2b` and the code wanted `gemma3:12b`. The rewrite reports `model absent` in that situation.

### 5.3 What a full run does, in order

1. Environment: mode, host, user (`$USER`, falling back to `id -un`), SGE job id. Requires `git`, `curl`, `apptainer`, `sha256sum`. Setup mode probes outbound HTTPS to GitHub; failure is a warning that switches the run to offline mode, not an error. Setup transcripts older than 30 days are deleted. The working tree also exports `GIT_TERMINAL_PROMPT=0` so a clone or pull can never hang on a credential prompt.
2. Scratch layout: probes `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` for writability, separately for the fast root (repo, venv) and bulk root (image, model). Acquires `.setup.lock` (setup and clean modes); a lock whose owner is dead on this host, or older than six hours, is taken over. Sets Apptainer cache/tmp (the committed version also exports the ineffective `APPTAINER_MKSQUASHFS_ARGS`; the working tree drops it, 3.3). Sweeps Apptainer tmp entries older than an hour. On a login node, kills orphaned `ollama serve` processes left by older script versions. Reads `LOCAL_MODEL` from `llm_analysis.py` if the repo already exists.
3. Repository: clone (then restore preserved user data), or `pull --ff-only --quiet` if healthy, clean and online. A dirty working tree is left alone with a warning. Warns if `preserved/` holds data from earlier runs. Dies if `LOCAL_MODEL` cannot be read and `MODEL` is not set.
4. Python: tries `module load python/<wanted>` and a few variants (harmless if none exists), then probes `$NLP_PYTHON`, the interpreter recorded in an existing `venv/pyvenv.cfg` (so re-runs need no `NLP_PYTHON`), `python3.12`, `python3.13`, `python3.11`, `python3.10`, `python3`. Needs 3.10+. `<wanted>` is the major.minor of `.python-version`.
5. Virtual environment and dependencies, as in 5.2. pip runs with `--only-binary=:all:` by default and needs the network; a failed pip self-upgrade is only a warning. Then scans the repo for `spacy.load("<name>")` and downloads any named pipeline. None today: `nlp_analysis.py` uses `spacy.blank("en")` plus the `sentencizer`, so no trained pipeline is needed.
6. Container image: `apptainer pull ollama.partial.sif <image>`, renamed to `ollama.sif` on success, so an existing `.sif` is always complete. Working tree: the generated `mksquashfs` wrapper is placed first on `PATH` for this command (3.3).
7. Model: first, every stored manifest whose config declares `requires` newer than the image's Ollama version is deleted (`remove_incompatible_models`) and its unshared blobs are pruned. Then the repo's model is fetched with `curl` straight from its registry (`registry.ollama.ai`, or `hf.co` for `hf.co/...` names) into Ollama's on-disk layout. The config blob is fetched first and checked against the image's Ollama version; if the model needs a newer Ollama, the script stops before downloading the weights. The manifest is downloaded to a `.partial` name; each blob is downloaded single-threaded with resume (`curl -C -`; the working tree adds `--connect-timeout 30 --speed-limit 1024 --speed-time 60`, so a transfer under 1 KB/s for 60 s fails and is retried instead of hanging, and the manifest fetch gets `--max-time 120`), up to 5 attempts, progress printed every 30 s, size checked, sha256 verified (a mismatch deletes the blob and retries within the 5 attempts), then renamed. The manifest is moved into place last, so its presence means every blob is complete. Existing complete blobs are reused; `--force model` wipes `blobs/`. Afterwards `prune_model_store` deletes blobs that no manifest references (left over from a model change).
8. Runtime scripts: writes `~/nlp-study-env.sh` (with `NLP_ENV_VERSION=2`) and `~/nlp-study.job` atomically (temp file plus rename, so a job sourcing the file never sees it half-written). Both are regenerated on every run; do not hand-edit.
9. Preflight at the highest tier the node allows, by sourcing the env script and, inside SGE (or with `NLP_ALLOW_INFERENCE=1`), starting Ollama for tier 1. Failure stops the run but leaves the build intact.
10. Summary with the next step, which differs by tier: after tier 1 the next step is `qsub`; after tier 0 it is `qlogin` and `./setup.sh` again.

Traps: Ctrl-C or TERM prints `Interrupted. Re-run to resume; partial downloads continue where they stopped.` and exits 130. Exit always kills a running curl, stops any Ollama the script started, and releases the lock.

Every run is transcribed to `~/nlp-study-logs/setup-<timestamp>.log`.

September version: the model step started a temporary Ollama server on the login node and ran `ollama pull`; pip was skipped when every requirement was already importable rather than on a hash stamp; the image was verified by executing `ollama --version` inside it; the preflight ran before the runtime scripts were written; there was no lock, no restore, no offline mode, no pruning.

### 5.4 Model name resolution

`setup.sh` does not hardcode the model. `detect_repo_model()` reads the first line matching `^LOCAL_MODEL *= *"..."` in `llm_analysis.py` (no leading whitespace allowed). `MODEL=name:tag ./setup.sh` overrides but warns if it disagrees with the repo, because the program will call whatever the repo says. If neither source yields a name the script dies. There is no `gemma4:e2b` fallback any more. Names parse as Ollama does: `gemma3:12b` is `registry.ollama.ai/library/gemma3:12b`, and `hf.co/<user>/<repo>:<quant>` is fetched from Hugging Face, which serves the same registry protocol.

Since 2026-10-13 the model is `hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M` (Gemma 3 12B instruct, Q4_K_M, about 7.8 GB, published March 2025 by the llama.cpp maintainers). Every `gemma3` and `gemma4` tag on `registry.ollama.ai` now declares `requires` 0.30.0 or newer in its config. The image is pinned to Ollama 0.24.0 for the driver-535 GPUs. Ollama 0.24.0 does not check `requires` itself, the registry checks it on `ollama pull`, and curl skips that check, so `setup.sh` reads the field and checks it itself. The Hugging Face build declares no requirement.

### 5.5 Installed package set (venv, 2026-09-14)

spacy 3.8.14, thinc 8.3.13, blis 1.3.3, numpy 2.2.6, ollama 0.6.2, httpx 0.28.1, pydantic 2.13.4, rich 15.0.0, click 8.4.2, typer 0.27.1, requests 2.34.2, tqdm 4.70.0, pip 26.2.1, setuptools 84.0.0, wheel 0.48.0, plus their transitive dependencies. `requirements.txt` pins exactly these versions (except pip, setuptools and wheel); the unrecorded transitive dependencies are left to pip (`click` is imported by nothing; BACKLOG B24). The local workstation venv is built from the same file on Python 3.12.14.

### 5.6 Upgrading Argon from the September script to the rewrite

`migrate.sh` in the repo does the one-time clean-up between the two layouts. It runs nothing but `git`, `find`, `mv` and `rm` (no Ollama, no `apptainer exec`), prints a plan and asks for `yes`; `--dry-run` only prints the plan. It never deletes anything that could be PHI: `notes.csv`, `data.db`, `output/` and every untracked file it does not recognise go to `preserved/migrate-<timestamp>/`, and it prints the `mv` that puts `notes.csv` back. Pass `--keep-data` to leave them in place.

1. Push the branch that carries the rewrite. As of 2026-10-13 that is `nlp-study-2.0`; `main` on GitHub (`957c6b8`) still has the September `setup.sh`. Either merge `nlp-study-2.0` into `main` and push, or deploy the branch directly with `--branch` in step 3.
2. On a login node, pull so the migration script is in the checkout (if `git pull` refuses because of local changes, `git stash` first, or let step 3 handle it with `--reset`):

   ```bash
   cd /nfsscratch/Users/apkane/nlp-study/repo && git pull --ff-only
   ```

3. Run the migration. It deletes build junk from the checkout, moves PHI aside, removes the `gemma4:e2b` manifest and the 6.5 GB of blobs nothing references, empties the Apptainer caches, removes the September `~/nlp-study.job`, `~/nlp-study-env.sh`, their `.bak` copies and the `nlp-preflight.py`, moves the `~/nlp-study-setup-*.log` transcripts into `~/nlp-study-logs/`, kills an orphaned login-node `ollama serve`, then fast-forwards the checkout (or switches it with `--branch`) and refuses to finish on a branch whose `setup.sh` is still the September one. The venv and the user-built Python are not touched; the September `ollama.sif` is left for `setup.sh` to replace (it has no `ollama.sif.image` stamp, so the next online run re-pulls the pinned image).

   ```bash
   ./migrate.sh --branch nlp-study-2.0      # or plain ./migrate.sh once main carries the rewrite
   ```

4. Run `./setup.sh`. It writes the venv's dependency stamp (one pip run against the pinned `requirements.txt`, no downloads when the pins are already installed), pulls the pinned image (the first login-node exercise of the `mksquashfs` wrapper, BACKLOG B14), removes stored models that need a newer Ollama than the image, re-verifies the model store against the manifest for `LOCAL_MODEL` and downloads whatever is missing, rewrites both `$HOME` scripts with `NLP_ENV_VERSION=2`, and runs tier 0.
5. Put `notes.csv` back from `preserved/migrate-<timestamp>/` (the migration prints the command), then `qlogin -q UI -pe smp 8` and `./setup.sh` again (or `./setup.sh --preflight`). The rewrite refuses `--preflight` if the env script lacks `NLP_ENV_VERSION=2`, so step 4 is mandatory first.

### 5.7 September version versus rewrite, at a glance

| Topic | September version (`3657d43`, in the logs) | Rewrite (`f8f76c0`) |
| --- | --- | --- |
| Ollama on login node | Started for `ollama pull` and tier 0 | Never; blobs via curl, tier 0 reads the manifest |
| Model health check | Directory non-empty | Manifest for the exact tag plus every blob at its size |
| Image health check | `apptainer exec ... ollama --version` | `apptainer sif list` (no execution), 100 MB minimum |
| Running inside a job | Warned; `setup` mode discouraged | First-class; builds if online, uses what exists if not |
| Concurrency | None | `.setup.lock` with stale takeover |
| PHI during repo rebuild | Moved to `preserved/` | Moved aside and restored into the new checkout |
| Dependency check | Importability of each requirement | Hash stamp plus import test; auto venv rebuild |
| Context length | Ollama default (4096 on CPU) | `OLLAMA_CONTEXT_LENGTH=8192` (`NLP_CONTEXT_LENGTH`) |
| Job preflight budget | 900 s | 600 s (preflight default) |
| GPU directive | `ngpus=1` and `gpu_p100` | `ngpus=1` only |
| Logs | `~/nlp-study-setup-*.log`, Ollama logs on scratch | Everything under `~/nlp-study-logs/`, 30-day rotation for setup logs |
| Generated scripts | Backed up as `.bak` when they differed | Overwritten atomically, no backup |
| Preflight row count | `wc -l` (wrong for multi-line notes) | `csv` module |
| Image tag | `latest`, hardcoded | `0.24.0` (last to use driver 535), overridable with `NLP_OLLAMA_IMAGE`; a different tag from the one recorded in `ollama.sif.image` triggers a re-pull |
| Repo URL | hardcoded | `NLP_REPO_URL` override |
| SquashFS conversion on pull | mksquashfs defaults | Committed: ineffective `APPTAINER_MKSQUASHFS_ARGS`; working tree: PATH wrapper with `-processors ${NSLOTS:-1} -mem 2G` |
| Stalled downloads | hang until killed | Working tree: curl stall timeouts, git credential prompt disabled |

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

- Hard (failure): `Endoscopy == 1`, `Colonoscopy == 0`. Note that the synthetic note says "No colonoscopy was performed", while the production prompt tells the model to set `Colonoscopy = 1` on the trigger word "colonoscopy"; a model that follows the prompt literally fails this gate (BACKLOG B11).
- Soft (warning): `DuodenalBiopsiesTaken == 1`, `NumberOfDuodenalBiopsies == 4`, `FellowPresent == 1`, `ScopeType` containing `H190`.
- Warns if the raw output is not bare JSON (the repair layer is doing work; `data_worker` retries up to 5x on JSON errors).
- Placement: PASS at 100% on GPU, PASS on a node with no `nvidia-smi`, WARN otherwise ("partly or fully on CPU, expect it to be slow").
- Prints seconds per note and projected hours for 100, 1000 and the real row count of `notes.csv` (counted with the `csv` module, so multi-line notes are counted correctly). Warns above 60 s per note.

September version: tier 0 also probed the endpoint (so it needed a running server, hence the login-node Ollama); the row count came from `wc -l`; placement was shown by printing `ollama ps`.

### 6.3 Defaults

| Context | Reps | Per-call budget |
| --- | --- | --- |
| `./setup.sh` inside `qlogin`, `./setup.sh --preflight`, `nlp_preflight` | 3 (`NLP_PREFLIGHT_REPS`) | 600 s (`NLP_PREFLIGHT_BUDGET`) |
| Inside `~/nlp-study.job` | 1 | 600 s unless `NLP_PREFLIGHT_BUDGET` is exported at `qsub` time (September version: 900 s) |

### 6.4 Direct invocation

```bash
python /nfsscratch/Users/apkane/nlp-study/nlp-preflight.py --repo /nfsscratch/Users/apkane/nlp-study/repo --model gemma3:12b --models-dir /nfsscratch/Users/apkane/nlp-study-assets/ollama-models --tier 1 --reps 3 --budget 600 --notes 5000
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

Exports are write-once. If `output/combined_results.csv` or either of the other two files exists, the export is skipped with a message, so after a resume the files on disk are from the first partial run (BACKLOG B17). Move or delete the old files first to regenerate them from the complete database.

### 7.5 Collecting results

Three files in `repo/output/` (details in Appendix D). All contain PHI. Copy them, and `data.db` if you want the raw LLM responses for audit, to durable storage. Scratch is purged on a schedule and `preserved/` is still scratch.

### 7.6 Compute nodes without internet

`./setup.sh` inside a job first probes HTTPS. Without it, the run prints `No outbound HTTPS from this node: existing resources are used as they are; nothing can be downloaded.` and continues with what is on scratch. If anything is absent or broken it stops with `[<name>] has to be built or updated, but this node has no outbound internet`; build it on a login node and come back.

---

## 8. Running on a workstation

The README describes a workstation deployment (Windows 10/11, with Mac and Linux variants). Everything in sections 2 and 15 applies there too; this section is what differs from Argon.

### 8.1 Prerequisites

| Software | Purpose |
| --- | --- |
| Windows 10/11, macOS or Linux | Supported OS |
| Python 3.12 | Core runtime (the installer accepts 3.10+; `.python-version` says `3.12`) |
| PowerShell (Windows) or a shell | Environment management |
| Ollama | Local LLM runtime |

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

   `spacy` (deterministic parsing), `ollama` (client for the local LLM), `rich` (progress bars and tables), `click` (listed, unused).

6. Install Ollama from https://ollama.com. On Linux or Mac the one-liner works on a workstation (it does not on Argon, see 3.6):

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

7. Pull the model the code asks for. `LOCAL_MODEL` in `llm_analysis.py` is the authority and currently says `hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M` (about 7.8 GB):

   ```bash
   ollama pull hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M
   ```

   Ollama needs internet only for this download. Afterwards it serves on `127.0.0.1:11434`, which is the `ollama` client's default, so `OLLAMA_HOST` need not be set.

### 8.3 Running

Same CLI as section 2.1, from the project directory:

```bash
python app.py --analyze --csv ./notes.csv
```

PowerShell paths use backslashes (`--csv path\to\endoscopy_notes.csv`). `data.db` and `output/` are created in the current directory.

### 8.4 Differences from Argon to keep in mind

- There is no preflight gate on a workstation unless you run the generated `nlp-preflight.py` yourself, and no job script. A wrong model name still produces a complete-looking run with `LLM_Failed = 1` on every row; check `--working_results` or the counts in 7.3 before trusting a run.
- A desktop Ollama with a GPU picks its context length from VRAM; a CPU-only machine gets 4096 tokens, and long notes are truncated server-side with no client-visible error (BACKLOG B10). Set `OLLAMA_CONTEXT_LENGTH=8192` in the Ollama service environment, or pass `num_ctx` in code.
- `requirements.txt` pins the Argon versions (spacy 3.8.14, ollama 0.6.2, see 5.5), so a workstation venv matches Argon's. Use Python 3.12.14 (`.python-version`) for an exact match.
- The same CSV header rules apply (14, "CSV must contain columns").
- With Apptainer installed, the Argon env script also works off-cluster: `NLP_ALLOW_INFERENCE=1` lets `nlp_start_ollama` run outside SGE.

### 8.5 HIPAA operational notes and disclaimer (from the README)

- All compute stays on the machine: no network calls, no telemetry, no prompt logging outside SQLite. Ollama listens on loopback.
- Enable disk encryption on the workstation, restrict OS-level user access, and treat CSV exports (and `data.db`) as PHI.
- `--print` truncates notes to 30 characters by default; raw LLM output is kept in the database for audit.
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

| Queue | Notes from the page |
| --- | --- |
| `UI` | Default queue |
| `UI-HM` | High-memory queue for large-memory jobs |
| `all.q` | No limit, but subordinate to the other queues. Jobs are evicted (TERM signal) when a higher-priority queue needs the node. Suggested for many single-processor jobs |
| `UI-GPU` | Not on this page. Referenced by `setup.sh`: `NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 ./setup.sh` bakes in `#$ -l ngpus=1`. The September version also pinned `#$ -l gpu_p100`; the rewrite leaves the card type to you (`qconf -sc` lists the complexes) |

### 9.3 Commonly used `qsub` options

| Option | Description |
| --- | --- |
| `-V` | Import the current environment into the job. On by default |
| `-N name` | Job name |
| `-l h_rt=hr:min:sec` | Maximum walltime |
| `-l h_vmem=bytes` (for example `2G`) | Virtual memory limit. An appropriate value is set automatically if an entire node is not requested |
| `-l mf=nG` or `-l mf=nM` | The `mem_free` request: free memory at scheduling time. Not a limit; only affects where the job starts |
| `-cwd` | Run from the current working directory. Otherwise the job starts in `$HOME` |
| `-S shell` | Shell for interpreting the job script |
| `-o path`, `-e path` | Standard output / error file or directory. A directory gets the default filenames inside it |
| `-j y` | Merge stderr into stdout |
| `-pe name n` | Parallel environment and slot count |
| `-M addr[,addr]` | Email recipients |
| `-m b\|e\|a\|s\|n` | Mail at begin, end, abort, suspend, never. The suspend option does not work. For multi-node jobs the mail's CPU and memory accounting covers only the primary host; use `qacct` for the rest |
| `-t n[-m[:s]]` | Array job with index range and step; tasks see `SGE_TASK_ID` |

### 9.4 Output files

Default names are `$JOB_NAME.o$JOB_ID` and `$JOB_NAME.e$JOB_ID`. Streams are appended, so reusing a file keeps previous contents. Shell redirection inside the script (`command > file`) overrides `-o`. For this project, with `-j y` and `-o /Users/apkane/nlp-study-logs/`, the single log is `/Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>`.

### 9.5 Exit status

| Code | Meaning |
| --- | --- |
| 0 to 127 | Exit status of the computation itself. `app.py` returns 0 even when every note's LLM step failed |
| 128 + signal | Job did not complete normally |
| 143 | 128 + TERM (15). Eviction from `all.q`, or the memory limit was hit. Outside `all.q` it almost always means memory. Confirm with `qacct -j <JOB_ID>` |
| 137 | 128 + KILL (9). Follow-up kill after TERM was not enough |

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
- If a central install were ever requested: Ollama is MIT-licensed (approved category). The Gemma model weights are under Google's Gemma Terms of Use, a non-standard licence, which would require review before central deployment.

---

## 11. Timeline of the 2026-09-14 session

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

## 12. Open items and known discrepancies

Bug-level items are tracked in `BACKLOG.md` (B1 to B26) with proposed fixes; this list is about the deployment state.

1. **`gemma3:12b` may not be on Argon yet.** The 20:04 status showed a 6.5 GB store, which matches `gemma4:e2b` alone. The code wants `gemma3:12b` (roughly 8 GB more). Under the September script, `--status` cannot tell and the job's tier 1 preflight is the first thing that would notice. Under the rewrite, `--status` reports `model absent` and `./setup.sh` on a login node downloads the missing blobs with resume and checksum, then prunes the old `gemma4:e2b` blobs. Either way: run `./setup.sh` on a login node before the next submission. Since 2026-10-13 the code wants `hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M` instead, and `setup.sh` deletes any stored `gemma3`/`gemma4` from `registry.ollama.ai` whose config requires Ollama newer than 0.24.0.

2. **Argon still runs the September script.** The rewrite is committed locally in `f8f76c0` but Argon's checkout only changes when `main` is pushed and pulled. See [5.6](#56-upgrading-argon-from-the-september-script-to-the-rewrite) for the order. After the upgrade, a full `./setup.sh` run is required before `--preflight` will accept the env script (`NLP_ENV_VERSION=2` check).

3. **The login-node image pull was never thread-limited in the committed script** (`APPTAINER_MKSQUASHFS_ARGS` is a no-op on Apptainer 1.3.2, 3.3). The working tree fixes this with a `mksquashfs` PATH wrapper, which still needs one confirming run on Argon: `./setup.sh --force sif` on a login node, then check the transcript shows the capped pull surviving (BACKLOG B14). BACKLOG B27 tracks runbook statements that drift from the script; this pass resolves the ones it lists.

4. **README is behind the code.** The README still says `gemma4:e2b` in the Ollama pull command and in the `llm_analysis.py` section; the code says `gemma3:12b`. It claims a "Boolean normalization" step that does not exist. Its "curl install.sh" instruction does not apply to Argon. (BACKLOG B24.)

5. **GPU nodes with driver 535 run on CPU.** The A10 node seen on 2026-09-14 could not be used by `ollama/ollama:latest`. `setup.sh` now pins `ollama/ollama:0.24.0`, which uses the A10 on driver 535; the next `./setup.sh` on a login node replaces the old `latest` image automatically (unconfirmed on Argon until a GPU job's placement line says GPU). Always read the placement line the preflight prints.

6. **Sample CSVs in the local `samples/` folder will not ingest as-is.** Verified on 2026-10-13:
   - `samples/notes.csv` header is `id,mrn,encounter,note_csn_id,note_date,note_type,note` (lower-case, underscores). Required names are case-sensitive. It also has a note-boundary error: note 9's CPT line sits at the head of note 10 (BACKLOG B8).
   - `samples/sample_5000_deidentified.csv` starts with a UTF-8 byte-order mark (`EF BB BF`) and names the second column `EncounterID`. With `encoding="utf-8"` the first header parses as `\ufeffMRN`, and `EncounterID` is not `Encounter`. Two failures (BACKLOG B7).
   Fixes are in [Troubleshooting](#csv-must-contain-columns). `samples/` and `environment-notes/` are in `.gitignore` and must stay out of git (names and MRNs).

7. **Runtime versus walltime.** 5000 notes at 24 h walltime allows about 17 s per note including retries. CPU inference on a 12 B model is unlikely to meet that. Plan on multiple resubmissions (resume is automatic) or set `NLP_JOB_HRT` higher, and read the tier 1 projection, which counts the real rows in `notes.csv`. Most of the 5000-note corpus is not procedure notes, so a relevance gate before the LLM (BACKLOG F3) would cut the work by roughly ten times.

8. **Container tag pinned to 0.24.0.** Before 2026-10-13 the default was `latest` (0.32.14 when pulled), which needs driver 550+. Do not move the default past 0.24.x until Argon's GPU nodes run driver 550 or newer (BACKLOG B25).

9. **Job preflight budget dropped from 900 s to 600 s** in the rewrite (it inherits the preflight script's default). On a CPU node a 12 B model can exceed 600 s for one call. Export `NLP_PREFLIGHT_BUDGET` at `qsub` time if the gate times out (BACKLOG B26).

10. **The code does not yet implement the study's definitions.** The researcher's comments (15.5) require "biopsies taken, count unknown" to be representable and colonoscopes to be ignored; the current pipeline coerces unknown counts to 0 and nulls a whole row on any disagreement. BACKLOG B1, B2, B4, B5 and B6 cover this.

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
Expected. Both generated scripts are rewritten on every setup run (the September version kept a `.bak`; the rewrite does not). Use `NLP_JOB_QUEUE`, `NLP_JOB_SLOTS`, `NLP_JOB_HRT`, `NLP_JOB_GPU`, or pass `qsub` options on the command line.

**Q: Where is the list of known bugs and planned work?**
`BACKLOG.md` in the repo: an evaluation of the pipeline against the researcher's comments and the 5000-note corpus, 26 bug fixes and 13 features, each with file and line references.

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
Any disagreement between NLP and LLM on the three numeric fields, any failure flag, or a missing scope type nulls the row. A note that never names the endoscope will be null regardless. Look at `working_results` for the individual answers. The researcher's comments argue for per-field reconciliation instead (15.5; BACKLOG B2).

**Q: Where are the researcher's requirements written down?**
As five review comments by Alexander P. Kane in `samples/Upper Gl endoscopy Scope details.docx` (also rendered in the margins of the PDF next to it). They are quoted in [15.5](#155-study-requirements-the-researchers-review-comments) and analysed in `BACKLOG.md` section 1. The README does not mention them.

### Running

**Q: How long will the run take?**
Run tier 1 with `notes.csv` staged and read the projection line for its row count. Multiply by up to 5 if the preflight warned that the raw output is not bare JSON (retries). Compare to `h_rt` (24 h by default, `NLP_JOB_HRT`).

**Q: The job ran out of walltime. Did I lose everything?**
No. Every processed note is already in `working_results`. Resubmit with `qsub ~/nlp-study.job --analyze` (no `--csv`). Delete or move the old `output/*.csv` so the exports regenerate.

**Q: How do I get more CPU for inference?**
`NLP_JOB_SLOTS=16 ./setup.sh` and resubmit, or `qsub -pe smp 16 ~/nlp-study.job --analyze ...`. Ollama uses the cores it sees for CPU inference; nothing currently limits it to the requested slots (BACKLOG B26). Returns diminish; measure with the preflight.

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
With no VRAM detected, Ollama picks a 4096-token default context. The rewrite passes `OLLAMA_CONTEXT_LENGTH=8192` (`NLP_CONTEXT_LENGTH`) into the container so long notes plus the roughly 700-token system prompt are less likely to be truncated. The 5000-note corpus has notes up to about 10 000 tokens (BACKLOG B10), so even 8192 is not always enough.

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
ValueError: CSV must contain columns: {'NoteType', 'MRN', 'Note', 'Encounter', 'NoteCsnID', 'NoteDate'}
```

Cause: the header does not contain the six names exactly. Three variants seen:

1. Lower-case snake_case header from a database export: `id,mrn,encounter,note_csn_id,note_date,note_type,note`.

   ```bash
   sed -i '1s/.*/id,MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note/' notes.csv
   ```

2. UTF-8 byte-order mark at the start of the file (Excel "CSV UTF-8" export). The first header becomes `\ufeffMRN`.

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

The code-side fix (open with `utf-8-sig`, normalise and alias headers) is BACKLOG B7.

#### `Error: CSV file not found`

The path is resolved relative to the current directory. In the job that is the repo dir after `cd`. Interactively, run from the repo dir or give an absolute path.

#### `Skipped N duplicate row(s) during import.`

Not an error. Identical rows already exist in `notes`.

#### `No notes to process.`

The `notes` table is empty. Ingest first, or pass `--csv`.

### setup.sh

#### `No outbound HTTPS from this node: existing resources are used as they are; nothing can be downloaded.`

A warning, not an error. You are probably on a compute node. The run continues offline and only fails if something must be built (next entry).

#### `[repo] has to be built or updated, but this node has no outbound internet. Run ... on a login node first.`

(Also `[sif]`, `[model]`, `[deps]`, `[spaCy pipeline ...]`.) Something is absent or broken and cannot be fetched here. Run `./setup.sh` on a login node, then come back.

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

Then pin an older version with a compatible wheel (the September script suggested `spacy==3.7.5` as the usual culprit), or allow a source build for that one package with `PIP_INSTALL_ARGS='' ./setup.sh --force deps` (slow, usually fails on GCC 4.8).

#### `[sif] unusable (not a valid SIF image); removing the existing copy and rebuilding`

Truncated or corrupt image. The script deletes it and the `.partial`, clears the Apptainer layer cache and re-pulls. With the committed script the conversion uses every core for a few minutes (3.3); the working-tree script caps it through the `mksquashfs` wrapper and logs `mksquashfs capped to N thread(s)`.

#### `[sif] mksquashfs not found; the pull will use Apptainer's default, uncapped`

The wrapper could not locate a real `mksquashfs` on `PATH` or in the usual `sbin`/`bin` directories. The pull proceeds without the cap; on a login node prefer pulling from a `qlogin` session that has internet.

#### `[sif] apptainer.conf pins mksquashfs; its CPU and memory use cannot be capped here`

The site configuration sets `mksquashfs path`, so Apptainer ignores `PATH` and the wrapper. Same advice as above.

#### `<host> has no '<model>' (or it is gated). Check LOCAL_MODEL in llm_analysis.py (or MODEL=...).`

The `name:tag` does not exist on the registry, or (on `hf.co`) the repository needs a Hugging Face login, which `setup.sh` does not support. Check spelling and tag, or pass `MODEL=`.

#### `<model> requires Ollama X, but the image has 0.24.0 ...`

The model's config declares a minimum Ollama version newer than the pinned image. Every `gemma3`/`gemma4` tag on `registry.ollama.ai` does this now. Nothing large was downloaded. Use a GGUF from `hf.co` in `LOCAL_MODEL` (the current one is), or move the image up with `NLP_OLLAMA_IMAGE` and accept CPU inference on driver-535 nodes.

#### `[model] removing <host>/<ns>/<name>/<tag>: requires Ollama X, the image has 0.24.0`

Informational. A stored model the pinned Ollama cannot run was deleted (for example the registry `gemma3:12b` or `gemma4:e2b`); its blobs are pruned on the next line. The check runs only when the image tag carries a version number.

#### `[model] download interrupted (attempt N/5); resuming in 10 s`

Network trouble, or (working tree) a stall: curl now gives up when the rate stays under 1 KB/s for 60 s instead of hanging. It resumes from the `.partial`. Only a problem if all five attempts fail.

#### `[model] checksum mismatch on ... (attempt N/5); downloading it again`

A corrupted blob was detected by sha256, deleted, and is re-downloaded within the same five attempts.

#### `[model] blob ... could not be downloaded intact after 5 attempts. Re-run to try again.`

Five attempts exhausted. Re-run `./setup.sh`; completed blobs are kept, the failing one resumes.

#### `[model] pruned N blob(s) that no manifest references`

Informational. Weights for a model the code no longer asks for were deleted (for example `gemma4:e2b` after the move to `gemma3:12b`, or `gemma3:12b` after the move to the Hugging Face build).

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

#### `llm_analysis.LOCAL_MODEL is 'gemma3:12b' but setup fetched 'gemma4:e2b'`

The repo changed models after setup, or `MODEL=` was given. Run `./setup.sh` on a login node; it follows the repo.

#### `model blobs missing or truncated: [...]` or `no usable manifest at ...`

Tier 0 found the on-disk model store incomplete for the model the code wants. Run `./setup.sh` on a login node; it resumes the download. This is the on-disk form of [open item 1](#12-open-items-and-known-discrepancies).

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

Then resubmit with `--analyze` and regenerate the exports. (A `--reprocess-failed` flag is BACKLOG B21.)

#### `File 'output/combined_results.csv' already exists. Export skipped.`

Write-once protection. Move the old files aside and re-run `--analyze` (reconcile and export are cheap when nothing new is pending).

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
3. **Compute nodes may have no internet.** Anything that downloads at runtime (`spacy.load("en_core_web_sm")`, Hugging Face, `ollama pull`) must happen in `setup.sh` on a login node. `setup.sh` scans for `spacy.load("<name>")` with a literal quoted name and pre-downloads; keep that call pattern literal if you add one. Today `nlp_analysis.py` uses `spacy.blank("en")` plus the `sentencizer`, so nothing is downloaded.
4. **Login nodes: 1 CPU, 16 GB, process killer.** Nothing that touches the model may run there. Tier 0 checks are the ceiling, and the rewrite enforces this in `nlp_start_ollama` by hostname and SGE variables. Keep the hostname check if you touch that function; `NLP_ALLOW_INFERENCE` deliberately does not override it.
5. **Apptainer 1.3 has no pull-time mksquashfs option.** `APPTAINER_MKSQUASHFS_ARGS` is the environment form of `apptainer build --mksquashfs-args` (new in 1.4.0) and `pull` never reads it; the committed script's export of it did nothing. The working tree caps the conversion with a generated `mksquashfs` wrapper placed first on `PATH` during the pull (BACKLOG B14). That only works while Apptainer resolves `mksquashfs` through `PATH`, so if Argon's `apptainer.conf` ever pins `mksquashfs path` the cap silently disappears (the script warns). Confirm on Argon with `./setup.sh --force sif` before relying on it.
6. **`setup.sh` edits must stay Bash 4.2-safe.** No `"${arr[@]}"` on a possibly-empty array under `set -u` (use `${arr[@]+"${arr[@]}"}` as the pip call does), no `git -C`, mind `${VAR-default}` versus `${VAR:-default}` (`PIP_INSTALL_ARGS=""` must mean "no flags"). `set -Eeuo pipefail` is on; `. activate` and `module` need `set +u` around them, as the script already does. Outside the venv, `python` on CentOS 7 is Python 2.7, so helper code must call `python3` or the venv interpreter (as `manifest_blobs` does). The script carries shellcheck directives; keep it shellcheck-clean.
7. **`NLP_SETUP_LIB_ONLY=1 source setup.sh`** loads the functions and returns before `main`; use it for shell tests instead of running the script.
8. **Bump `ENV_VERSION`** whenever the shape of the generated env or job script changes, because `--preflight` refuses an env script with a different version.
9. **Only one setup run per scratch tree.** The lock is a `mkdir` under the project root, with host, pid and epoch in `owner`; stale after six hours or when the owner pid is dead on the same host. Any new long-running step belongs inside the locked region, and anything that writes files a job may be sourcing must go through `atomic_write`.
10. **The dependency stamp assumes import name equals package name.** `deps_importable` imports each requirement by its distribution name. A package whose module differs (for example `PyYAML`/`yaml`) flips the stamp to `nocheck` and disables the "deps vanished, rebuild venv" safety net for everything. Prefer packages where the names match, or extend the check.
11. **Scratch is ephemeral.** Never treat `/nfsscratch` as the only copy of PHI results. Never write PHI under git-tracked paths; `.gitignore` covers `data.db`, `output/`, `notes.csv`, `samples`, `environment-notes`, `.venv`, `__pycache__`. `setup.sh` restores preserved PHI into a fresh checkout only if the checkout has no file of the same name, so never commit a `notes.csv` or `data.db`.

### 15.2 Model and Ollama

12. **`LOCAL_MODEL` in `llm_analysis.py` is parsed by `sed`.** Keep it a single line of the form `LOCAL_MODEL = "name:tag"` at column 0. Any model change requires `./setup.sh` on a login node to fetch the weights (and it prunes the old ones), and the README should be updated in the same commit (BACKLOG B24).
13. **Model size budget.** `gemma4:e2b` is 5.12 B parameters at Q4_K_M, about 7.2 GB, and is a reasoning model that emits a thinking trace by default. Gemma 3 12B (`hf.co/ggml-org/gemma-3-12b-it-GGUF:Q4_K_M`) is about 7.8 GB. Budget 8 to 10 GB of VRAM once the KV cache is allocated. The September job script pinned `gpu_p100` (16 GB) for that reason; the rewrite requests only `ngpus=1`, so pin a card type with `#$ -l <complex>` if a small card would silently fall back to CPU. CPU inference needs that much RAM plus headroom.
14. **The module-level `ollama` client is wired by `OLLAMA_HOST` at import time.** Do not create `ollama.Client()` with a hard-coded host or port, and do not import `ollama` before the host is exported. The port is random per job to avoid collisions with other users on the node. If you add a client with a timeout (BACKLOG B15), keep it reading `OLLAMA_HOST`.
15. **Ollama is not deterministic by default.** `ollama.chat()` is called without `options`, so the server default temperature (0.8) applies despite the prompt calling itself deterministic. For reproducibility add `options={"temperature": 0, "seed": <int>}` and use `format=` with the JSON schema, which also removes the markdown fences every saved response currently has (BACKLOG B9). Re-run the preflight after any change here.
16. **Context window.** The rewrite sets `OLLAMA_CONTEXT_LENGTH=8192` on the container (`NLP_CONTEXT_LENGTH`). The September script and a workstation install get Ollama's default, 4096 on CPU. The system prompt is roughly 700 tokens; the 5000-note corpus has notes up to about 10 000 tokens. Ollama truncates server-side and the client sees a normal response. Pass `num_ctx` explicitly and detect over-long notes (BACKLOG B10, F4).
17. **`OLLAMA_KEEP_ALIVE=-1` keeps the model loaded for the whole job.** Keep it; reloading an 8 GB model per note would dominate runtime. `OLLAMA_NUM_PARALLEL=1` matches the single-threaded `data_worker`. Nothing limits llama.cpp's threads to the requested slots (BACKLOG B26).
18. **The container tag is pinned.** Now pinned to `0.24.0`, the last release that uses a driver-535 GPU; a newer tag needs driver 550+ (BACKLOG B25). The Python packages, including `ollama` 0.6.2, are pinned in `requirements.txt` to the Argon versions.
19. **GPU use requires three things to line up:** a `*-GPU` queue, `#$ -l ngpus=1` (and a card-type complex if you need one), and a node driver the container's CUDA runtime accepts. `nlp_start_ollama` only passes `--nv` when `nvidia-smi -L` works. Verify with the preflight placement line every time.
20. **The on-disk model store is Ollama's own layout.** The rewrite writes `manifests/<host>/<ns>/<name>/<tag>` and `blobs/sha256-<hex>` with curl, removes models whose config `requires` a newer Ollama than the image, and prunes unreferenced blobs. If Ollama changes that layout, `create_model`, `verify_model`, `prune_model_store` and the preflight's tier 0 model check all need updating together.

### 15.3 Pipeline semantics

21. **LLM failures are swallowed by design.** Any change to the schema, prompt or model must be followed by a tier 1 preflight; nothing else will tell you it broke. If you add a field, update all of: the prompt schema text, `EXPECTED_FIELDS`, `BOOL_FIELDS` if boolean, `normalize_llm_result` if integer, `default_nlp_result` in `data_worker.py`, `WORKING_RESULTS_TABLE_SCHEMA`, `INSERT_WORKING_RESULT_SQL` and its `values` tuple, `SELECT_ALL_WORKING_RESULTS_SQL` and the positional unpack in `reconcile_working_results`, the export queries, `display.py` truncation rules, and `HARD`/`SOFT` plus the synthetic note in `setup.sh`.
22. **Keep `except Exception` in `llm_analysis()`, not `except BaseException`.** The preflight's timeout is a `BaseException` subclass precisely so that wrapper cannot swallow it.
23. **Validation rejects extra fields and non-0/1 booleans.** A model that adds commentary keys or returns `true`/`false` fails validation and is not retried (only JSON parse errors are). The prompt is the only defence; `format=` with a schema would be stronger (BACKLOG B9).
24. **Retries are only for JSON decode errors**, up to 5 attempts, each a full generation. Budget runtime accordingly and prefer fixing the prompt or decoding over raising the retry count.
25. **`CREATE TABLE IF NOT EXISTS` does not migrate.** Changing a schema does nothing to an existing `data.db`. Delete it (after copying out) or write a migration (BACKLOG B19).
26. **Resume logic keys on row existence, not freshness.** Changing the prompt, model or NLP rules does not reprocess notes that already have `working_results`. Delete those rows (and their `final_results`) to recompute.
27. **Relative paths everywhere.** `data.db` and `output/` are resolved from the current directory, and the export functions open `"data.db"` by name rather than reusing the connection. Run from the repo directory, or refactor to pass a path (BACKLOG B18).
28. **Exports are write-once and interlocked.** If any of the three CSVs exists, the corresponding export is skipped. The second export writes two files and skips both if either exists. After a resume the files on disk are stale (BACKLOG B17).
29. **Reconciliation nulls the whole row** when any numeric field disagrees, any side failed, or no scope is found, even if everything else agrees. The researcher's comments (15.5) and BACKLOG B2 argue for per-field reconciliation with a review flag; change `insert_null_row` deliberately and document it.
30. **NLP rule quirks to know before "fixing" disagreements:** `FellowPresent` only fires if the sentence starts after 70% of the document (signature-block heuristic). `ScopeType` needs the word `endoscope` in the sentence and a brand in `Olympus|Pentax|Fujifilm`; the LLM is asked for any brand, so other manufacturers produce one-sided scopes, which reconciliation accepts. Biopsy counts are tied to duodenum sentences within two sentences. Negation and term matching are substring-based (`"no" in "normal"`), and the bare `sentencizer` does not split on newlines or `HEADER:` tokens. BACKLOG B3 to B6 and B12, B13 quantify these on the 5000-note corpus.
31. **Ingest dedup is whole-row equality and quadratic.** A trailing space in one field creates a second note; identical notes under different CSN ids are analysed twice (BACKLOG B16).
32. **CSV contract is strict and case-sensitive**, opened with `encoding="utf-8"`. Switching to `encoding="utf-8-sig"` would make BOM-prefixed exports work. `EncounterID` would still need renaming unless the required set is widened (BACKLOG B7).

### 15.4 Operations and HIPAA

33. **Logs are not PHI-free by construction.** `data_worker` logs exceptions per note id, not note text, but `LLMAnalysisError` messages can echo model output, which copies note sentences, and those messages reach `~/nlp-study-logs/` in `$HOME` (BACKLOG F11). Do not add debug prints of note content, raw LLM output or CSV rows. Ollama at `OLLAMA_DEBUG=INFO` does not log prompts; the Ollama logs now also live in `~/nlp-study-logs/`.
34. **The job emails on end and abort** (`-m ea`). Do not put identifiers in the job name or in anything that reaches stdout at the end.
35. **Everything binds to `127.0.0.1`.** Keep it that way; never expose the Ollama port on the node's interfaces.
36. **Walltime versus volume.** 24 h at 8 slots is the default (`NLP_JOB_HRT`, `NLP_JOB_SLOTS`). Projected runtime comes from tier 1, which counts the real rows. Raise `h_rt` or plan resubmissions; the pipeline is resume-safe. Gating non-procedure notes before the LLM (BACKLOG F3) is the big lever.
37. **Windows line endings** in any script copied to Argon need `dos2unix`. Shebangs with `\r` fail with confusing "no such file" errors.

### 15.5 Study requirements (the researcher's review comments)

The ten example notes in `samples/` carry five review comments by Alexander P. Kane dated 2025-12-13 (in `word/comments.xml` of the `.docx`, and rendered in the PDF's margin), plus highlights marking the expected answers. Together they are the only written statement of what the fields must mean; the README and code do not record them. The comments were verified against both files on 2026-10-13.

| # | Attached to | Comment (verbatim) | Requirement |
| --- | --- | --- | --- |
| 1 | Note 2, "Random biopsies were taken to rule out celiac disease" | "Ideally they say the number of biopsies but lacking that just whether they were taken would be good to know" | Presence of duodenal biopsies is the minimum deliverable; the count is optional. "Count unknown" must be representable and must not become 0 |
| 2 | Note 4, "intubated with an Olympus Q 180 endoscope" (the same note also uses a PCF H180AL colonoscope) | "I only need the scopes used in upper endoscopy" | `ScopeType` is the EGD scope only; a colonoscope must never win |
| 3 | Note 5, "6 biopsies ... including 2 from bulb" | "I am looking for biopsies from the duodenum and the bulb is a part of that and another word used to describe it" | Bulb biopsies are duodenal biopsies; count them once (6 here, not 2 and not 8) |
| 4 | Note 6, PEG placement, "BIOPSY: None" | "This one no biopsies were taken" | A true zero, distinct from "unknown"; PEG notes are still in scope for the scope field |
| 5 | Note 9, "An Olympus PCF H180AL colonoscope was lubricated" | "Don't care about this one since it was for a colonoscopy" | Same as 2 |

The `.docx` also carries 36 yellow highlights marking the expected answer in each note (count and marker phrases verified on 2026-10-13; the per-note table below is `BACKLOG.md` section 1's reading of them; the `.md` conversion in `samples/` lost both comments and highlights and must not be used as the reference). Two things they settle:

- **Acceptable scope answers include brand-only and model-only forms**: "Olympus gastroscope" is the highlighted answer for note 2, "GIF-H 190" for notes 8 and 10.
- **`FellowPresent` is decided by the attending's attestation, not by the word "fellow"**: a "Teaching Statement" (the attending attests that a trainee performed under supervision) means 1; "Staff Only: I was present for and participated in the entire procedure" means 0. Notes 6 and 9 never contain the word "fellow", yet the gold is 1; both extractors return 0 there and agree, so agreement-based reconciliation cannot catch a shared blind spot.

| Note | ScopeType (highlighted) | Duodenal biopsies | FellowPresent (marker) |
| --- | --- | --- | --- |
| 1 | Olympus H190 | 4 | 1 (fellow signature + Teaching Statement) |
| 2 | Olympus gastroscope | taken, count not stated | 0 (RESIDENT/FELLOW None) |
| 3 | Olympus Q 180 | 4, one from bulb | 1 |
| 4 | Olympus Q 180 | 4 | 1 |
| 5 | Olympus H190 | 6, including 2 from bulb | 0 (Staff Only) |
| 6 | Olympus GIF XP190N | 0, "BIOPSY: None" | 1 (named fellow + Teaching Statement) |
| 7 | Olympus H190 | 6 | 0 (attending signs alone, no attestation) |
| 8 | GIF-H 190 | 6 | 0 (Staff Only) |
| 9 | Olympus H190 | 4 | 1 (named fellow + Teaching Statement) |
| 10 | GIF-H 190 | 4 | 0 (Staff Only) |

Where the current code falls short of these, with the BACKLOG item that fixes it:

- The prompt says integers are never null and "if no explicit number is stated, return 0"; `normalize_llm_result` and the NLP default also coerce to 0; the columns are plain `INTEGER`. Comments 1 and 4 need "unknown" and "zero" to differ (B1).
- All-or-nothing reconciliation throws away `DuodenalBiopsiesTaken = 1` when only the count disagrees, which is exactly note 2 (B2).
- The scope regex requires the word `endoscope`, so "gastroscope" and "GIF-H 190 scope" phrasings yield nothing, while the colonoscope exclusion is a substring list (B4).
- Biopsy counts attach to any number near a duodenum sentence, so CPT codes and other sites leak in; bulb handling is by proximity, not by site (B5, F6).
- `FellowPresent` is defined by the word "fellow" in both the NLP rule and prompt rule 9; the gold reads the attestation ("Teaching Statement" versus "Staff Only"). On the 5000-note corpus the NLP misses 127 of 193 Teaching-Statement procedure notes, per BACKLOG (B6, B11).
- Brand-only ("Olympus gastroscope") and model-only ("GIF-H 190") scopes are valid answers; the NLP regex requires a brand plus the word `endoscope`, and reconciliation has no canonical form to compare (B4, F5).
- The real input is mixed note types: per BACKLOG section 1, only about 609 of the 5000 deidentified rows are `Procedures` and about 330 are EGDs, so endoscopy and colonoscopy flags must distinguish a performed procedure from history, plans and instructions (B12, F3).

When changing extraction or reconciliation, check behaviour on all ten notes of `samples/notes.csv` against the gold table (after fixing its header and the note 9/10 boundary, B7 and B8). A gold-label fixture and `--evaluate` command are BACKLOG F1.

---

## Appendix A: environment variable reference

### Read by `setup.sh` (rewrite)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL` | read from `llm_analysis.py` | Override the model to fetch (warns on disagreement) |
| `NLP_REPO_URL` | `https://github.com/benpodium8/nlp-study.git` | Clone a fork or mirror instead |
| `NLP_PYTHON` | auto-detect | Path to a Python 3.10+ interpreter; tried first |
| `NLP_FAST_ROOT`, `NLP_BULK_ROOT` | `/scratch/Users/$USER` then `/nfsscratch/Users/$USER` | Force the roots for repo+venv and image+model |
| `NLP_OLLAMA_IMAGE` | `docker://ollama/ollama:0.24.0` | Container image; 0.24.0 is the last that uses a driver-535 GPU |
| `PIP_INSTALL_ARGS` | `--only-binary=:all:` | Extra pip flags; empty string permits source builds |
| `NLP_JOB_QUEUE` | `UI` | Queue baked into the job script |
| `NLP_JOB_SLOTS` | `8` | `-pe smp` slots baked into the job script |
| `NLP_JOB_HRT` | `24:00:00` | `-l h_rt` baked into the job script |
| `NLP_JOB_GPU` | unset | Bake `#$ -l ngpus=1` into the job script (no card type; add `#$ -l <complex>` by hand if needed) |
| `NLP_SETUP_LIB_ONLY` | unset | Source the script for its functions only |
| `USER` | set by the shell/SGE | HawkID for scratch paths; falls back to `id -un` |

Exported by the committed `setup.sh` but ineffective on Argon, and removed in the working tree: `APPTAINER_MKSQUASHFS_ARGS` (3.3). The working tree exports `GIT_TERMINAL_PROMPT=0` instead.

September-only variables, no longer read: `SKIP_SMOKE_TEST`, `FORCE_SMOKE_TEST`, `NLP_NOTE_ESTIMATE`, `FORCE_VENV`, `FORCE_SIF`.

### Read at run time by `~/nlp-study-env.sh`, the preflight and the job (inherited via `-V`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `NLP_CONTEXT_LENGTH` | `8192` | `OLLAMA_CONTEXT_LENGTH` passed into the container |
| `NLP_PREFLIGHT_REPS` | 3 (job forces 1 unless set) | Tier 1 repetitions |
| `NLP_PREFLIGHT_BUDGET` | 600 | Per-call timeout in seconds |
| `NLP_SKIP_PREFLIGHT` | unset | Bypass the gate in the job |
| `NLP_ALLOW_INFERENCE` | unset | Let `nlp_start_ollama` and `setup.sh` run tier 1 outside SGE (never on a login node) |
| `NLP_QUIET` | unset (job and setup set 1) | Suppress the env script's ready banner |
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

Written atomically (temp file plus rename), so a job that sources the env script mid-rewrite never sees a half-written file.

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

**final_results**: `id`, `ScopeType`, `NumberOfDuodenalBiopsies`, `DuodenalBiopsiesTaken`, `FellowPresent`. All null when reconciliation discards the note. It carries neither `note_csn_id` nor the endoscopy/colonoscopy flags, so selecting the EGD cohort needs a join (BACKLOG B20).

### Files in `output/`

| File | Content |
| --- | --- |
| `combined_results.csv` | `notes` left-joined with `final_results` |
| `all_tables_combined_results.csv` | `notes` left-joined with the numeric and scope columns of `working_results` (both sides) and `final_results` (prefixed `Final_`) |
| `full_combined_results.csv` | Every table dumped sequentially with a `Table: <name>` header line between them, including raw LLM responses |

All three are written once and never overwritten. All three contain PHI.

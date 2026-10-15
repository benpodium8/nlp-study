# Migrating the Argon install to nlp-study 2.0

One-time procedure for moving `/nfsscratch/Users/apkane/nlp-study/` from the September
2026 install (built by the `setup.sh` of commits `3657d43` to `1052cdd`; `main` on GitHub
is at `957c6b8`, 2026-10-13, and still carries that script) to the current version on the
`nlp-study-2.0` branch. Everything runs on a **login node** unless it says `qlogin`.
Nothing here starts Ollama.

## What changes between the two versions

Taken from `git diff main` on 2026-10-15: 24 files, about 8,900 lines added and 2,900
removed. What it means for the install:

| Area | September version (`main`) | nlp-study 2.0 |
| --- | --- | --- |
| Extraction | spaCy rules (`nlp_analysis.py`) beside one LLM, reconciled by `reconcile_working_results.py` into `final_results`; `display.py` with `--print` | The LLM is the only extractor. Every note goes to three models in turn (`medgemma:27b`, `gemma4:26b-a4b-it-q4_K_M`, `gpt-oss:20b`); every answer is checked against the note (`verify_against_note`) and retried up to five times; `consensus.py` consolidates the three answers into the study's answer with a review flag; `review.py` flags each model's own row |
| Files removed | `display.py`, `nlp_analysis.py`, `reconcile_working_results.py` | |
| Files added | | `consensus.py`, `export.py`, `reporter.py`, `review.py`, `migrate.sh`, `tests/test_robustness.py`, `ARGON_RUNBOOK.md`, `BACKLOG.md`, `model_candidates.md`, `results-csv-guide.md`, this guide |
| Rewritten | | `cli.py` (`--location {server,local}` replaces `--model`; `--clean`; a run header, numbered steps and a closing summary), `data_worker.py` (one model at a time, streamed answers, retries, a stop after three consecutive failed calls), `database.py` (one labelled block of result columns per model, the consensus block, the input contract below), `llm_analysis.py` (the study prompt, JSON-schema output, greedy decoding, the checks against the note, the token-count and timeout guards), `setup.sh` (`ENV_VERSION=2`: image pinned to `ollama/ollama:0.24.0`, model downloads with curl, a lock, a three-model preflight) |
| Dependencies | `rich`, `click`, `spacy`, `ollama`, unpinned; Python 3.12.13 | `rich==15.0.0`, `ollama==0.6.2` (with `httpx` and `pydantic` as recorded on Argon), wheels only; `.python-version` 3.12.14 |
| Models on scratch | `gemma4:e2b` (the code later asked for `gemma3:12b`) | The three above, about 49 GB, fetched by `setup.sh` with curl into Ollama's own store layout; the September weights are pruned |
| Input CSV | header exactly `MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note`, UTF-8 without a byte-order mark | header names matched in any case with spaces, underscores, dashes and a byte-order mark ignored; `EncounterID`, `note_csn_id` and the like accepted; bytes that are not UTF-8 replaced and counted; notes of any length; the file is read in batches |
| Database | `notes`, `working_results`, `final_results` | `notes` (with an index for the duplicate check) and `results`: one block of columns per model plus the consensus block. **The September `data.db` is not read**: its tables are ignored and every note is analysed again, which is why the migration moves it to `preserved/` |
| Outputs | three CSVs | `output/results.csv` (everything, plain-words titles, TRUE/FALSE values) and `output/results-review.csv` (identifiers, the consensus block and the note length, about 4% of the size), written together and write-once as a pair; `results-csv-guide.md` explains them to the researcher |
| Run time | one model pass | three passes per note; the runbook's open item 7 has the arithmetic (about 20 resubmissions of 24 h at 100,000 notes); resume is automatic and keeps every stored result |
| Environment variables | | New: `NLP_CONTEXT_LENGTH` (8192), `NLP_LLM_TIMEOUT` (1800), `NLP_HEARTBEAT_SECONDS` (60), `NLP_PLAIN`, `NLP_OLLAMA_IMAGE`, `NLP_REQUIRES_VERIFIED`, the `NLP_JOB_*` family; Appendix A of the runbook lists them |
| Tests | none | `python -m unittest discover -s tests` from the repo directory (27 cases; no model or server needed) |

## 1. On your workstation: push the branch

Commit the working tree yourself and push the branch:

```bash
git push origin nlp-study-2.0
```

(`main` on GitHub still has the September `setup.sh`. Either merge `nlp-study-2.0` into
`main` and push, or deploy the branch directly in step 3 with `--branch`.)

## 2. On Argon: get the migration script

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo && git fetch origin && git checkout nlp-study-2.0
```

If `main` already carries the new version, `git pull --ff-only` is enough.

## 3. Preview, then migrate

```bash
./migrate.sh --dry-run
```

Read the plan. Then:

```bash
./migrate.sh
```

Type `yes`. It deletes build junk and the September model weights (the `gemma4:e2b`
manifest and every blob no remaining manifest references), removes the September
`~/nlp-study.job`, `~/nlp-study-env.sh` and preflight script, moves the old setup
transcripts into `~/nlp-study-logs/`, and moves `notes.csv`, `data.db` and `output/` to
`preserved/migrate-<timestamp>/`. Nothing that could be PHI is deleted. The venv and the
user-built Python are not touched; the September container image is left for `setup.sh`
to replace.

The preserved `data.db` holds the September tables (`working_results`, `final_results`),
which the new code never reads; keep it with the other preserved data until the new run has
produced what you need, then copy it to durable storage or delete it.

## 4. Rebuild what was removed

```bash
./setup.sh
```

Writes the venv's dependency stamp (one pip run against the pinned `requirements.txt`),
pulls the pinned Ollama image, downloads the three models (about 49 GB, resumable,
checksummed), regenerates the two `$HOME` scripts, and runs the static checks. Allow time
for the downloads.

## 5. Put the input back

Use the `mv` line the migration printed, which looks like:

```bash
mv /nfsscratch/Users/apkane/nlp-study/preserved/migrate-<timestamp>/notes.csv /nfsscratch/Users/apkane/nlp-study/repo/notes.csv
```

The file no longer needs its header edited: a byte-order mark and `EncounterID` are
accepted as they come.

## 6. Prove the models work, then submit

```bash
qlogin -q UI -pe smp 8
```

```bash
/nfsscratch/Users/apkane/nlp-study/repo/setup.sh && exit
```

Wait for `PREFLIGHT PASSED` (tier 1 runs every model on a synthetic note and prints each
one's placement and seconds per note, then a projection for the real row count), then
from a login node:

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

The job ingests `notes.csv` afresh and analyses every note with each model. Expect several
resubmissions on a large corpus (`qsub ~/nlp-study.job --analyze`, without `--csv`); the
exports are written by the first run that completes, and must be moved aside to be
regenerated.

Optional, inside the venv on any node: `python -m unittest discover -s tests` runs the
test battery without a model.

## If something stops it

| Message | Do |
| --- | --- |
| `tracked files are modified in the Argon checkout` | `git stash`, or re-run with `./migrate.sh --reset` to discard them |
| `still carries the September setup.sh` | the branch you are on lacks the rewrite: `./migrate.sh --branch nlp-study-2.0` |
| `A setup run is active` | wait, or `rm -rf /nfsscratch/Users/apkane/nlp-study/.setup.lock` if you know it is dead |
| `An nlp-study job is queued or running` | wait for it, or `qdel` it |

Re-running `./migrate.sh` is always safe; once migrated it reports nothing to do.
`./migrate.sh --keep-data` leaves `notes.csv`, `data.db` and `output/` in place
instead of moving them aside.

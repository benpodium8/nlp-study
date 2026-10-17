# Migrating the Argon install from nlp-study 2.0 to 2.1

One-time procedure for moving `/nfsscratch/Users/apkane/nlp-study/` from 2.0 (`main` at
`4e50269`, the version that completed the 275-note sample run on 2026-10-15) to 2.1 (branch
`nlp-study-v2.1`). Everything runs on a **login node** unless it says `qlogin`. Nothing here
starts Ollama.

An install still on the September 2026 `setup.sh` (no `ENV_VERSION` line in it) must be
migrated to 2.0 first; `migrate.sh` refuses it and prints the command for the 2.0 script,
whose procedure is this file at commit `4e50269`.

## What changes between the two versions

From `git diff main nlp-study-v2.1`. What it means for the install:

| Area | 2.0 (`main`) | 2.1 |
| --- | --- | --- |
| Models | `medgemma:27b`, `gemma4:26b-a4b-it-q4_K_M`, `gpt-oss:20b` | `mistral-small3.2:24b-instruct-2506-q4_K_M` replaces `medgemma:27b` (on the sample run medgemma failed 28 notes and needed 157 rejected attempts, against 9 and 27 for gemma4, at five times the time per note; `model_candidates.md` section 8). The other two are unchanged |
| Long notes | a note too long for the 8192-token window (about 18,000 characters; 7 of the 275 sample notes) is recorded as failed for every model without a call | it is shortened (`note_shortening.py`: boilerplate removed, then the relevant sections, then the passages around the study's terms) and the shortened copy is sent; every quote is checked against the full note too. Only a note with no study term left is still failed without a call |
| Database | one 18-column block per model plus the consensus | two more columns per model, `InputReduced` and `InputReduction`, added by the first run; every other column is unchanged, and so are the prompt and the checks for a note that fits |
| Outputs | 16 columns per model in `results.csv` (80 columns with three models) | 18 per model (`Input shortened`, `How the input was shortened`; 86 columns). `results-review.csv` is unchanged |
| Job script | queue `UI`, no GPU unless `NLP_JOB_GPU` was set | queue `UI-GPU` with `#$ -l ngpus=1` by default (`NLP_JOB_GPU=0` for a CPU job; `NLP_JOB_GPU_TYPE` pins a card type) |
| Image, venv | `ollama/ollama:0.24.0`, pinned `requirements.txt` | the same |
| Files added | | `note_shortening.py`, `migrate_db.py` (the migration's database step) |

## What the migration does to the 2.0 data

The 2.0 run is not thrown away. `migrate.sh` keeps every gemma4 and gpt-oss answer, so the
first 2.1 job has much less to do than a fresh start:

- `data.db` is copied whole to `preserved/migrate-2.1-<timestamp>/data.db` and the copy is
  checked before anything changes. Then, under the run lock and in one transaction:
  - the `medgemma:27b@8192` block is removed (it stays in the copy);
  - the gemma4 and gpt-oss results 2.0 failed as too long without a call are cleared, so 2.1
    shortens and sends those notes (on the sample: notes 15, 17, 35, 54, 123, 153, 222);
  - the consensus block is cleared, for `--analyze` to rebuild from the three study models;
  - the `results` table is rebuilt in the layout a fresh 2.1 database has, the new model's
    (empty) block first, so `results.csv` has the column letters `results-csv-guide.md` uses.
    Without this the new model's block would come last.
- `output/` (the write-once 2.0 exports) is moved to the same `preserved/` directory, so the
  first 2.1 run writes new ones with the 2.1 columns.
- `notes.csv` stays where it is.
- The `medgemma:27b` manifest is removed with the blobs nothing else references (about
  17 GB); `setup.sh` then downloads `mistral-small3.2` (about 15 GB).
- A 2.0 `~/nlp-study.job` that requests no GPU is removed, so it cannot be submitted before
  `setup.sh` writes the 2.1 one.

The first 2.1 job then analyses every note with mistral-small3.2, only the requeued notes
with gemma4 and gpt-oss, rebuilds the consensus and writes both exports.

## 1. On your workstation: merge and push 2.1

```bash
git checkout main && git merge --ff-only nlp-study-v2.1 && git push origin main
```

(`main` has no commits that `nlp-study-v2.1` lacks, so this is a fast-forward. To deploy
the branch without merging, push it with `git push origin nlp-study-v2.1` and add
`--branch nlp-study-v2.1` to every `migrate.sh` command below.)

## 2. On Argon: make sure no job is running

```bash
qstat -u apkane
```

Wait for an `nlp-study` job to finish, or `qdel` it: the migration refuses while one is
queued or running, because it changes the database the job writes.

## 3. Get the migration script

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo && git pull --ff-only
```

This brings the checkout to 2.1, with the 2.1 `migrate.sh` and `migrate_db.py`. (The pull
cannot collide with `notes.csv`, `data.db` or `output/`: git ignores them.) Running the
2.0 `migrate.sh` instead would do the September clean-up and move `data.db` aside without
migrating it, so pull first.

## 4. Preview, then migrate

```bash
./migrate.sh --dry-run
```

Read the plan. The database section should list the three study models, the 7 requeued notes
for each of gemma4 and gpt-oss, `retire medgemma:27b@8192`, `clear` for the consensus and
`layout`. Then:

```bash
./migrate.sh
```

Type `yes`. The transcript is in `~/nlp-study-logs/migrate-<timestamp>.log`. The end of it
says where the 2.0 database and exports went; keep them until a 2.1 run has produced what
you need, then copy them to durable storage or delete them.

Options, if the defaults are not what you want:

| Option | Effect |
| --- | --- |
| `--keep-retired` | the medgemma block stays in `data.db`, and `results.csv` shows it as a fourth block after the three study models |
| `--keep-exports` | `output/` stays in place; 2.1 then writes no new `results.csv` until it is moved aside |
| `--keep-data` | neither `data.db` nor `output/` is touched: the too-long notes stay failed, and the new model's block lands after the others |
| `--keep-models` | the medgemma weights stay on scratch |

## 5. Fetch the new model and rewrite the job script

```bash
./setup.sh
```

Re-checks the venv, keeps the pinned image, downloads `mistral-small3.2` (about 15 GB,
resumable, checksummed), writes the 2.1 `~/nlp-study.job` (queue `UI-GPU`, one GPU) and
`~/nlp-study-env.sh`, and runs the static checks. It does not touch `data.db`.

## 6. Prove the models work, then submit

```bash
qlogin -q UI-GPU -pe smp 8 -l ngpus=1
```

```bash
/nfsscratch/Users/apkane/nlp-study/repo/setup.sh && exit
```

Wait for `PREFLIGHT PASSED`. This is the first load of `mistral-small3.2` on Ollama 0.24.0,
so read its placement line (it should be on the GPU, about 17.7 GB) and its seconds per note.
Then, from a login node:

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

The notes are already in the database, so the ingest only reports duplicates. The run header
should show about 275 pending for mistral-small3.2 and 7 for each of the other two models.

To check the result, `results.csv` should have 86 columns with the mistral-small3.2 block
first (columns Y to AP), and each model's `Input shortened` column should be TRUE on the 7
long notes. Answers carried over from 2.0 have it empty.

## If something stops it

| Message | Do |
| --- | --- |
| `this install predates 2.0` | migrate it to 2.0 first with the command the message prints, then run this procedure |
| `origin/main ... is still 2.0` | step 1 was not pushed: push it, or pass `--branch nlp-study-v2.1` |
| `tracked files are modified in the Argon checkout` | `git stash`, or re-run with `./migrate.sh --reset` to discard them |
| `An nlp-study job is queued or running` | wait for it, or `qdel` it |
| `a pipeline run holds data.db.lock` | an interactive run is using the database: let it finish |
| `A setup run is active` | wait, or `rm -rf /nfsscratch/Users/apkane/nlp-study/.setup.lock` if you know it is dead |
| `the database step needs the venv` | run `./setup.sh` (it does not touch `data.db`), then `./migrate.sh` again |

Re-running `./migrate.sh` is always safe: it plans again from what is there, and once the
install is migrated it reports nothing to do. The database is changed in one transaction
after its backup, so it is never half 2.0 and half 2.1. To go back, put the copy back:
`cp /nfsscratch/Users/apkane/nlp-study/preserved/migrate-2.1-<timestamp>/data.db
/nfsscratch/Users/apkane/nlp-study/repo/data.db` (with 2.0 checked out again).

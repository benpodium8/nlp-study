# Migrating the Argon install to the rewritten setup

One-time procedure for moving `/nfsscratch/Users/apkane/nlp-study/` from the
September 2026 install (commit `1052cdd`) to the current version. Everything
runs on a **login node** unless it says `qlogin`. Nothing here starts Ollama.

## 1. On your workstation: push the new version

```bash
git add migrate.sh migration-procedure.md && git commit -am "nlp-study 2.0" && git push origin nlp-study-2.0
```

(`main` on GitHub still has the old `setup.sh`. Either merge `nlp-study-2.0`
into `main` and push, or deploy the branch directly in step 3.)

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

Type `yes`. It deletes build junk and the old `gemma4:e2b` weights, removes
the September `~/nlp-study.job`, `~/nlp-study-env.sh` and preflight, and moves
`notes.csv`, `data.db` and `output/` to `preserved/migrate-<timestamp>/`.
Nothing that could be PHI is deleted. The venv and the user-built Python are
not touched.

## 4. Rebuild what was removed

```bash
./setup.sh
```

Downloads the pinned Ollama image and the model, regenerates the two `$HOME`
scripts, and runs the static checks. Allow time for a few GB of downloads.

## 5. Put the input back

Use the `mv` line the migration printed, which looks like:

```bash
mv /nfsscratch/Users/apkane/nlp-study/preserved/migrate-<timestamp>/notes.csv /nfsscratch/Users/apkane/nlp-study/repo/notes.csv
```

## 6. Prove the model works, then submit

```bash
qlogin -q UI -pe smp 8
```

```bash
/nfsscratch/Users/apkane/nlp-study/repo/setup.sh && exit
```

Wait for `PREFLIGHT PASSED`, then from a login node:

```bash
qsub ~/nlp-study.job --analyze --csv ./notes.csv
```

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

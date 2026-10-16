# Starting, stopping and resuming the full run on Argon

The full corpus (about 100,000 notes, sent to three models each: 300,000 model calls) takes
weeks of model time, far longer than one Argon batch job may run (24 hours by default). It
therefore runs as a **chain of jobs**: each job picks up where the previous one stopped,
works until shortly before its time limit, stops cleanly, and the next job in the chain
carries on. You can stop the chain at any time and resume it later, days afterwards if need
be, without losing any finished work.

This page is the day-to-day procedure. `ARGON_RUNBOOK.md` has the background (section 7.4
for the time estimates and the safeguards, section 14 for troubleshooting).

All commands assume the standard Argon layout: the repository at
`/nfsscratch/Users/apkane/nlp-study/repo` and job logs in `/Users/apkane/nlp-study-logs/`.
Run them on a **login node** unless a step says otherwise.

---

## 1. Before the first start (once)

1. **Stage the notes file** (PHI; scratch only, never git):

   ```bash
   cp /path/to/notes.csv /nfsscratch/Users/apkane/nlp-study/repo/notes.csv
   ```

2. **Build or update everything** on a login node, then run the live check inside an
   interactive allocation:

   ```bash
   /nfsscratch/Users/apkane/nlp-study/repo/setup.sh
   ```

   ```bash
   qlogin -q UI-GPU -pe smp 8 -l ngpus=1
   ```

   ```bash
   /nfsscratch/Users/apkane/nlp-study/repo/setup.sh
   ```

   Do not go on until it prints `PREFLIGHT PASSED`. Then leave the `qlogin` session (`exit`).

3. **Work out how many jobs you need.** The preflight prints the time per note for each
   model and a projection for the real number of rows in `notes.csv`. Divide the projected
   hours by 23 (the usable model time in a 24-hour job) and add a quarter for safety:

   | Time per note, all three models together | Jobs to submit |
   | --- | --- |
   | about 12 s | about 19 |
   | about 17 s | about 27 |
   | 60 s or more | too slow: the models are not on the GPU (the job requests one by default; read the placement line, and pin a 24 GB card with `NLP_JOB_GPU_TYPE` if a smaller one was given) or set a longer walltime first |

   Submitting too many jobs costs almost nothing: a job that finds no work left ends within
   seconds, before it starts Ollama.

---

## 2. Starting the run

Submit the whole chain at once from a login node. The first job loads `notes.csv` into the
database; every later job only analyses (`--analyze`, without `--csv`). Each job waits for
the one before it to finish (`-hold_jid`), whatever its exit status.

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
prev=$(qsub -terse ~/nlp-study.job --analyze --csv ./notes.csv)
for i in $(seq 2 27); do prev=$(qsub -terse -hold_jid "$prev" ~/nlp-study.job --analyze); done
qstat -u apkane
```

Replace `27` with your job count from step 1.3. `qstat` should show one job running (`r`)
or waiting for a node (`qw`), and the rest held (`hqw`).

Each job sends mail when it ends. To get mail only when a job aborts, add `-m a` after
`qsub` on both lines.

### You can log out now: no `screen` or `tmux` needed

Once `qsub` has accepted the jobs, the scheduler (SGE) runs them on compute nodes,
independently of your login session. You can log out and close your laptop; the jobs keep
running and hand over to each other. Log back in whenever you like to check on them
(section 3).

`screen` or `tmux` only helps in two other situations:

- **An interactive `qlogin` session.** It ends when your SSH connection drops, and whatever
  runs in it stops. The program stops cleanly then (finished notes are kept, and the next run
  resumes), but running inside `tmux` avoids the interruption. For multi-day work, use the
  `qsub` chain anyway: a `qlogin` session has its own time limit.
- **A long `setup.sh` download on a login node** (the model weights). A dropped connection
  kills it; rerunning it continues the download where it stopped, so `tmux` only saves you
  the restart.

If you do use `screen` or `tmux` on Argon, note which login node you are on (`hostname`,
for example `argon-login-5`): the login address can put you on a different node next time,
and a session can only be reattached on the node where it was started. Never run
`python app.py --analyze` on a login node, in `tmux` or not: the login node's 1 CPU / 16 GB
limit would kill it, and `nlp_start_ollama` refuses to start Ollama there.

---

## 3. Checking on it, day to day

Any of these is safe while a job is running:

```bash
qstat -u apkane
```

```bash
tail -f /Users/apkane/nlp-study-logs/nlp-study.o<JOB_ID>
```

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo && source ~/nlp-study-env.sh && python app.py --work-left
```

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo && source ~/nlp-study-env.sh && python app.py
```

- The job log has one line per note, a `progress` line every 10 notes with the time
  remaining, and a `...` line every 60 seconds while a model call is in progress.
- `python app.py --work-left` prints how many notes each model still has to do.
- `python app.py` with no options prints a status report: notes done per model, whether a run
  holds the database (`Run lock   HELD by a running pipeline: ...`), and when the last backup
  was taken.

**What a normal job end looks like.** About 45 minutes before its time limit a job stops
starting new notes, finishes the one in progress, and ends with:

```
STOPPED mistral-small3.2:24b-instruct-2506-q4_K_M@8192 after 4210 of 61,300 notes: the time limit (--stop-after 22:58:12) is reached
outcome   paused: the time limit (--stop-after 22:58:12) is reached
app.py stopped at ... with work left (time limit, or stopped early: see above); every stored result is kept.
```

That is expected; the exit status is 2 and the next job in the chain starts. The models run
one after another over the whole corpus, so the log shows `mistral-small3.2` for the first third
or so of the jobs, then `gemma4`, then `gpt-oss`.

---

## 4. Stopping

Every finished note is saved the moment it is done, so stopping never loses more than the
one note in progress, which is simply done again on resume.

### Pause after the current job (gentlest)

Let the running job finish its day, and hold the jobs still waiting so none starts after it:

```bash
qstat -u apkane
```

```bash
qhold <JOB_ID> <JOB_ID> ...
```

List every waiting job ID (`qw` or `hqw`), but not the running one (`r`). The running job
stops by itself before its time limit. To carry on later, release the held jobs (see 5).

### Stop now

```bash
qdel <JOB_ID>
```

for the running job, and the same for the waiting ones (or `qdel -u apkane` to delete all of
your jobs, including any unrelated ones). The program stops cleanly when it gets the signal
(`Stopped by SIGTERM.` in the log, exit 143). Even if it is killed outright, the database is
safe: an unfinished write is rolled back the next time it opens.

### Before you stop for a long break

Scratch is purged on a schedule. Copy the database to durable, PHI-approved storage. While a
job is running, copy the **backup**, which is a complete, checked copy taken at the start of
that job; `data.db` itself may be mid-write:

```bash
cp /nfsscratch/Users/apkane/nlp-study/repo/data.db.backup /path/to/durable/storage/data.db
```

When no job is running, copying `data.db` is fine and more recent. Do this every few days
during a long run too.

---

## 5. Resuming

**If you held the waiting jobs**, release them:

```bash
qrls <JOB_ID> <JOB_ID> ...
```

**If you deleted the jobs**, submit a new chain. It is the same as section 2 but without
`--csv` (the notes are already in the database), with the number of jobs still needed:

```bash
cd /nfsscratch/Users/apkane/nlp-study/repo
prev=$(qsub -terse ~/nlp-study.job --analyze)
for i in $(seq 2 20); do prev=$(qsub -terse -hold_jid "$prev" ~/nlp-study.job --analyze); done
```

Replace `20` with the jobs still needed: the hours left at the pace the job logs show, divided
by 23, plus a quarter. `python app.py --work-left` gives the notes left per model.

A resumed run skips every note that already has a result for a model and continues with the
rest. Nothing has to be cleaned up first:

- **Lock:** a lock file left by a killed job (`data.db.lock`) is taken over automatically
  once it is 3 minutes old. The log then says `Taking over the run lock ...`.
- **Database:** the database is checked at the start of every job and a fresh backup taken.
- **Export:** a half-written export (`output/*.partial`) is replaced by the next export.

If scratch was purged and you restored the database from durable storage, put it back as
`/nfsscratch/Users/apkane/nlp-study/repo/data.db` and run `setup.sh` on a login node first
(it rebuilds anything else the purge removed).

**Do not** run two chains at once, or an interactive `python app.py --analyze` while a job is
running. The second run waits for the first and then refuses (exit 3) rather than corrupting
anything, but it wastes the job.

---

## 6. When it is finished

The job that analyses the last note builds the final consensus and writes the exports. Its
log ends with:

```
outcome   complete: ... notes analysed in this run
app.py finished at ...: every note analysed, consensus built, exports written unless they existed
```

Then:

1. Delete the jobs still waiting (`qstat -u apkane`, then `qdel <JOB_ID> ...`). They would
   end on their own within seconds anyway.
2. Copy the results and the database to durable storage. All of them contain PHI:

   ```bash
   ls -l /nfsscratch/Users/apkane/nlp-study/repo/output/ /nfsscratch/Users/apkane/nlp-study/repo/data.db
   ```

   `output/results-review.csv` (about 30 MB) is the one to open in Google Sheets.
   `output/results.csv` (about 670 MB) holds everything, including the note text.
   `results-csv-guide.md` explains the columns.

The exports are written once and never overwritten. To regenerate them from the database,
move both files out of `output/` and submit one job: `qsub ~/nlp-study.job --analyze`.

---

## 7. If something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| Exit status 2, `outcome   paused` | Normal end of a job in the chain | Nothing; the next job continues |
| Exit status 143, `Stopped by SIGTERM.` | The job was deleted or evicted | Resume (section 5) |
| Exit status 143 without that line | Probably the memory limit | `qacct -j <JOB_ID>`, then more slots (`qsub -pe smp 16 ...`) |
| Exit status 137 | Killed at the time limit or by the system | Nothing; the next job continues. If it keeps happening, raise the margin: `NLP_STOP_MARGIN_MINUTES=90 qsub ...` |
| Exit status 3, `another run is using this database` | Two runs at once | Check `qstat -u apkane`; keep one. If no run is active, delete `data.db.lock` |
| `Preflight failed; not starting app.py.` | Ollama or a model is not working on that node | Read the lines above it; `ARGON_RUNBOOK.md` section 6 |
| `data.db failed its integrity check` | The database was damaged | Follow the restore command in the message, then resume |
| `STOPPED ... model calls in a row failed` | Ollama stopped responding mid-job | The next job retries the unstarted notes. The few notes recorded as failed calls are not retried automatically: see `ARGON_RUNBOOK.md` section 14 |
| Several jobs in a row end within minutes | Something is persistently broken | Hold the chain (section 4), read the job logs, fix, release |

**Changing the job length.** To use 48-hour jobs, add `-l h_rt=48:00:00` to the `qsub`
lines. The job reads its own time limit and stops 45 minutes before it
(`NLP_STOP_MARGIN_MINUTES` changes the margin). Halve the job count accordingly.

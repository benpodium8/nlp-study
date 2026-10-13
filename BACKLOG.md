# nlp-study: evaluation against the study goals, bugfix list, and feature list

Written 2026-10-13 against commit `957c6b8` plus the uncommitted `setup.sh` rewrite and `ARGON_RUNBOOK.md`.
Evidence comes from running `nlp_analysis.py` over `samples/notes.csv` (10 notes) and
`samples/sample_5000_deidentified.csv` (5,000 notes), from the saved LLM output in
`samples/working_results.csv`, and from the researcher's comments embedded in
`samples/Upper Gl endoscopy Scope details markdown pdf.pdf`.

---

## 1. What the study actually wants (from the PDF comments)

The `.docx`, `.md` and `.pdf` are the same ten example notes (the `.docx` text and `samples/notes.csv`
match word for word; the `.md` only adds a mailto link). The PDF is the one that matters: it carries
five review comments by the researcher (Alexander P. Kane, 2025-12-13) that define the targets.

| # | Attached to | Comment | Requirement it implies |
| --- | --- | --- | --- |
| 1 | Note 2, "Random biopsies were taken to rule out celiac disease" | "Ideally they say the number of biopsies but lacking that just whether they were taken would be good to know" | Count is optional; **presence** is the minimum. "Count unknown" must be distinguishable from "zero". |
| 2 | Note 4, the "Olympus Q 180 endoscope" sentence (note also has a PCF H180AL colonoscope) | "I only need the scopes used in upper endoscopy" | ScopeType = the EGD scope only. Colonoscope must never win. |
| 3 | Note 5, "6 biopsies ... including 2 from bulb" | "I am looking for biopsies from the duodenum and the bulb is a part of that and another word used to describe it" | Bulb biopsies are duodenal. Count them once (6 here, not 2 and not 8). |
| 4 | Note 6, PEG placement, "BIOPSY: None" | "This one no biopsies were taken" | A true zero. PEG notes are in scope for the scope field. |
| 5 | Note 9, "Olympus PCF H180AL colonoscope" | "Don't care about this one since it was for a colonoscopy" | Same as 2. |

Fields the code extracts: ScopeType, Endoscopy, Colonoscopy, NumberOfDuodenalBiopsies,
DuodenalBiopsiesTaken, FellowPresent, plus evidence sentences.

### How the current pipeline does on the ten annotated notes

Using the saved run in `samples/working_results.csv` (gemma3 era, older schema) and today's NLP output:

- **Note 2 (comment 1) fails exactly as the researcher feared.** No count is stated. The LLM
  invented `1` (the prompt forbids null integers), the NLP returned `2` by reading the list number in
  "...celiac disease 2. Normal colonoscopy". They disagree, so reconciliation writes an all-null row and
  **loses `DuodenalBiopsiesTaken = 1`, which both sides agreed on.**
- **Note 3: the LLM failed outright** (empty raw response, 1 of 10 = 10%), so the final row is null even
  though the NLP got every field right.
- **Notes 5, 8, 10: the NLP finds no scope** ("Olympus H190 scope", "GIF-H 190 scope" do not contain the
  word "endoscope"), so the final ScopeType is the unverified LLM string. For 8 and 10 that string is
  `GIF-H 190` with no brand.
- **Note 10's saved NLP result says Colonoscopy = 1.** That comes from a sample-data error: the CPT line
  that ends note 9 ("...LONG COLONOSCOPY with biopsy (45380)") was pasted at the head of note 10 in
  `samples/notes.csv`.
- Notes 1, 4, 6, 7, 9 reconcile correctly (note 6's final scope is `Olympus GIF XP190N video`, with the
  junk word "video" attached).
- **All nine successful raw LLM responses start with a ```` ```json ```` fence** despite the prompt
  forbidding markdown, so the repair layer runs on every note.

### How the NLP does on the 5,000-note deidentified corpus (10.6 s total)

This corpus is what real input will look like: only 609 of 5,000 rows are `Procedures`; the rest are
progress notes, H&Ps, discharge instructions, and so on. Notes run 15 to 40,940 characters (median 3,249,
p99 23,153).

| Measure | Result |
| --- | --- |
| Endoscopy = 1 | 440 total: 328 Procedures, 112 other note types (H&P "Procedure: EGD", progress notes "EGD today") |
| Procedures notes with EGD wording but Endoscopy = 0 | 38 (e.g. "endoscope was then placed in the oropharynx ... esophagus was easily intubated") |
| Colonoscopy = 1 | 353; 131 of the trigger sentences are plan/history/instruction text ("Colonoscopy tomorrow", "AFTER YOUR COLONOSCOPY") |
| FellowPresent = 1 | 1,073; **976 are non-procedure notes** (attending attestations: "discussed the case with the resident/fellow") |
| Procedures notes with "Teaching Statement" and the word "fellow" but FellowPresent = 0 | 6 ("Fellow: <NAME>" sits near the top, before the 70% mark) |
| ScopeType strings that over-capture ("Olympus Q180 endoscope was lubricated") | 52 of 257 |
| ScopeType = "Olympus" with the model lost ("Olympus endoscope (GIF-H180)") | 19 |
| EGD procedure notes with a model in the text but ScopeType = None | 81 of 328 (gastroscope / "GIF-H 190 scope" / "Olympus GIF-H 190 was lubricated") |
| Distinct raw ScopeType strings | 51 (37 after removing case, spaces, hyphens; probably ~8 real models) |
| Duodenal biopsy evidence sentence that names no duodenal site | 10 of 288 (stomach/antrum counts attributed to the duodenum) |
| NumberOfDuodenalBiopsies = 43239 | 1 (CPT code read as a count) |
| DuodenalBiopsiesTaken = 1 with count 0 | 22 (indistinguishable from "none" downstream) |
| Duodenum+biopsy sentences dropped because "no" matched inside "normal", "nodular", etc. | 68 |
| Sentences longer than 1,500 chars (first 1,500 notes) | 486: the sentencizer does not split on newlines or section headers, so "within two sentences" is often "within the whole note" |
| Exact duplicate note texts under different NoteCsnIDs | 69 |

Neither sample CSV ingests as shipped: `samples/notes.csv` has lower-case snake_case headers, and
`sample_5000_deidentified.csv` has a UTF-8 BOM and `EncounterID`.

---

## 2. Bugfixes (ordered; each is self-contained)

Severity: **H** = produces wrong study data or blocks a run, **M** = wrong in realistic cases, **L** = hygiene.

### B1 (H) "Count unknown" and "zero" are the same value
`llm_analysis.py:149-151,195-199` tells the model integers are "never null" and "if no explicit number is stated, return 0"; `normalize_llm_result` (`:81-97`) turns null into 0; `nlp_analysis.py:109` defaults to 0; `working_results.NumberOfDuodenalBiopsies_*` and `final_results` are plain INTEGER. Comment 1 asks for exactly this distinction. Fix: make the count nullable end to end (prompt: "null if not stated"; drop the null→0 coercion for this field; allow `None` in `EXPECTED_FIELDS`; compare `None == None` as agreement; keep `DuodenalBiopsiesTaken` as the hard field). Note 2 then reconciles to taken=1, count=null instead of an all-null row.

### B2 (H) Reconciliation discards agreed fields when any one field disagrees
`reconcile_working_results.py:95-118` nulls the whole row. Note 2 loses ScopeType and DuodenalBiopsiesTaken (both agreed) because the count differed. Fix: reconcile per field (write each field when both sides agree, null only that field otherwise) and record which fields were nulled (see F2 for the review flag).

### B3 (H) Substring negation drops valid biopsy sentences
`nlp_analysis.py:73-83` uses `n in text`, so "no" matches "normal", "nodular", "abnormalities", "diagnosis"; "not" matches "noted". 68 duodenum+biopsy sentences in the 5,000 sample are discarded with no negation present ("Duodenum: Normal. Biopsies taken"). Same substring pattern in `contains_any` (`:60-71`): "cf" in `COLON_EXCLUDE_TERMS` matches any word containing "cf", "bulb" matches "bulbar", "egd" and "colon" match inside longer words. Fix: tokenize (`\b...\b` regexes or spaCy tokens) and scope negation to a window before the biopsy term.

### B4 (H) Scope regex over-captures and misses most real phrasings
`nlp_analysis.py:37-40` captures up to 30 chars of letters/digits/spaces after the brand, so trailing verbs survive ("Olympus Q180 endoscope was lubricated", 52 of 257). `:134` requires the literal word "endoscope" in the sentence, so "gastroscope", "scope", "videoendoscope", "Olympus GIF-H 190 was lubricated" and "GIF-H 190 scope" all yield None (81 of 328 EGD notes; notes 5, 8, 10 in the sample). Models in parentheses after the noun ("Olympus endoscope (GIF-H180)") lose the model (19). Fix: match a model-token grammar instead of a span: optional brand, then `GIF`/`H`/`Q`/`XP`/`PCF`/`CF` prefix with digits and optional suffix letter, anywhere in a sentence that mentions an upper-GI insertion (esophagus, mouth, oropharynx, gastroscope) and does not mention a lower-GI site. Return brand + model only.

### B5 (H) CPT codes, list numbers and other sites become the biopsy count
`nlp_analysis.py:27-35,150-157,168-173`: the suffix regex `biops... (\d+)` picked up "43239" (CPT) and "2." (list numbering, note 2); the prefix regex has no upper bound, so any 5-digit number qualifies; candidates are accepted if within two sentences of any duodenum sentence, so antrum/stomach counts are attributed to the duodenum when they come first (10 of 288). Fix: require the number and a duodenal site term (duodenum, duodenal, bulb, D1/D2/D3, second/third portion) in the same clause; cap counts at a sane maximum (say 20); ignore numbers followed by `)` or preceded by `(`; prefer the sentence whose site is duodenal over proximity.

### B6 (H) Fellow detection is a position heuristic on a substring
`nlp_analysis.py:159-166`: "fellow" anywhere in the last 30% of the token stream, unless "none" is also in the sentence. Misses "Fellow: <NAME>" headers near the top (6 procedure notes), fires on attending attestations in 976 non-procedure notes, and ignores the two explicit signals in these notes: "Staff Only: I was present for and participated in the entire procedure" (no fellow) and "Teaching Statement: Dr. X was present and actively participated" (a fellow performed). Fix: pattern on those phrases and on `Fellow:`/`RESIDENT/FELLOW` header lines with a value, treat `none`/`Staff Only` as explicit 0, and drop the 70% rule.

### B7 (H) Ingest rejects both sample CSVs
`database.py:142,145-155`: `encoding="utf-8"` leaves the BOM on the first header (`﻿MRN`), matching is exact and case-sensitive, and `EncounterID` is not an alias of `Encounter`. The runbook's answer is to `sed` the file. Fix: open with `utf-8-sig`, normalize headers (strip, casefold, remove `_`/spaces) and map aliases (`encounterid`, `encounter`; `note_csn_id`, `notecsnid`; ...), and give a message naming the headers found versus required.

### B8 (H) `samples/notes.csv` has a note-boundary error and label text inside notes
Note 10 starts with the CPT line that ends note 9 ("EGD, beyond pylorus ... LONG COLONOSCOPY with biopsy (45380)"), which is why the saved NLP result says Colonoscopy = 1 for note 10. Notes 1 and 9 start with the document's section labels ("Upper Gl endoscopy Scope details: with biopsy"), which are not note text. Fix the CSV (move the line back to note 9, strip the labels) so it can serve as the gold fixture (F1). Keep `samples/` out of git.

### B9 (H) LLM output is non-deterministic and unconstrained
`llm_analysis.py:231` calls `ollama.chat(model, messages)` with no `options` and no `format`. The server default temperature (0.8) applies, so two runs of the same note can differ, which a study cannot tolerate; every response comes back fenced; `true`/`false` or `"4"` fail validation and are not retried (`data_worker.py:78` retries only JSON errors). Fix: `format=<JSON schema dict>` (the installed `ollama` 0.6.3 supports it), `options={"temperature": 0, "seed": 0, "num_ctx": ..., "num_predict": 1024}`, coerce bool/str to int before validation, and retry validation failures too with a corrective message (cap total attempts). Re-run the preflight after.

### B10 (H) Long notes are silently truncated
No `num_ctx` is passed (`llm_analysis.py:231`); the README's local workflow gets Ollama's default 4,096 tokens, and Argon gets 8,192 only because the launcher exports `OLLAMA_CONTEXT_LENGTH`. The 5,000 corpus has p99 = 23K chars (about 6K tokens) and max 41K chars (about 10K tokens), plus a ~700-token system prompt. Ollama truncates the prompt server-side and logs it only in the server log; the client sees a normal response. Fix: pass `num_ctx` explicitly, estimate tokens before the call, and either send only the procedure section (F4) or mark the note `LLM_Failed` with reason "too long" instead of returning a confident wrong answer.

### B11 (M) The prompt's "trigger word" rules cause false positives, and the preflight contradicts them
`llm_analysis.py:177-180` says Colonoscopy = 1 on the word "colonoscopy"; `:187-189` likewise for "EGD". A note saying "No colonoscopy was performed" or "prior EGD in 2019" is flagged. The synthetic preflight note in `setup.sh:415` contains "No colonoscopy was performed" and `setup.sh:420` treats Colonoscopy = 0 as a HARD failure, so a model that follows the prompt literally blocks job submission. Fix: rewrite both rules as "performed during this procedure, in the procedure/findings section", and make the preflight check consistent with the prompt.

### B12 (M) Endoscopy and colonoscopy flags fire on history, plans and instructions
`nlp_analysis.py:122-132,142-145`: any sentence with "upper endoscopy"/"egd" plus a verb, or any sentence with "colonoscopy", sets the flag (112 non-procedure notes flagged Endoscopy; 131 of 353 Colonoscopy hits are plan/history text). Procedure notes written as "The endoscope was then placed in the oropharynx ... esophagus was easily intubated" are missed (38) because "upper endoscopy"/"egd" is not in the same sentence. Fix: detect the procedure section (OPERATION/PROCEDURE, Procedure Details, FINDINGS, Upper endoscopy:) and look for insertion language there (esophagus/oropharynx intubated, scope advanced to duodenum); treat "history of", "prior", "tomorrow", "scheduled", "after your" as non-events.

### B13 (M) Sentence segmentation ignores newlines and headers
`nlp_analysis.py:4-5` uses the bare `sentencizer`; these notes separate sections with newlines and `HEADER:` tokens, not periods, so 486 "sentences" in the first 1,500 notes exceed 1,500 chars and the evidence fields become whole-note dumps (note 1's `EndoscopyInformation` is the entire header). Fix: pre-split on newlines and on `[A-Z][A-Z /]+:` headers before the sentencizer (or add a custom boundary component).

### B14 (M) `APPTAINER_MKSQUASHFS_ARGS` does nothing on Argon
`setup.sh:889` exports it and the comments say mksquashfs is pinned to one thread. Apptainer reads that variable only as the `--mksquashfs-args` flag of `apptainer build`, added in v1.4.0 (2025-03-18), and `apptainer pull` does not register the flag at all. Argon runs 1.3.2. So the image conversion on the login node uses every core and mksquashfs's default memory (25% of physical RAM), under a 1-CPU / 16 GB killer. Fix: put a wrapper named `mksquashfs` first on `PATH` during the pull that execs the real binary with `-processors 1 -mem 2G` (Apptainer finds mksquashfs on PATH unless `mksquashfs path` is set in `apptainer.conf`), or pull from a `qlogin` session if it has outbound HTTPS. Update the comment either way.

### B15 (M) No HTTP timeout on the LLM call
`ollama.Client` defaults to `timeout=None`. A wedged server stalls `data_worker` until SGE kills the job at walltime, with no row written for the note. Fix: build a module-level `Client(host=os.environ.get("OLLAMA_HOST"), timeout=NLP_LLM_TIMEOUT)` (keep the `OLLAMA_HOST`-at-import behaviour the runbook relies on) and treat the timeout as a retryable failure with a reason.

### B16 (M) Ingest dedup is whole-row equality and O(n²)
`database.py:108-112,173-181`: one full-table scan per incoming row comparing six columns including the note text; a trailing space creates a new note; the 5,000 corpus has 69 identical notes under different CsnIDs that will all be re-analysed. Fix: `UNIQUE(note_csn_id)` (CsnIDs were unique in the 5,000 sample) with `INSERT OR IGNORE`, plus a `note_sha256` column to reuse results across identical texts.

### B17 (M) Write-once exports silently serve stale results after a resume
`database.py:372-388,471-491`: if any of the three CSVs exists the export is skipped with a print. After a resumed run the `output/` files are from the first partial run. Fix: timestamped filenames (`combined_results_<YYYYMMDD-HHMMSS>.csv`) or overwrite with `--force-export`; never skip silently.

### B18 (M) Exports and the DB path are cwd-relative and duplicated
`cli.py:114` opens `data.db` relative to cwd; `database.py:346,398,435` open `"data.db"` by name again inside the export functions, and `with sqlite3.connect(...)` commits but never closes. Fix: a single `--db PATH` (default `data.db`) threaded through, exports take the open connection, connections closed in `finally`.

### B19 (M) `final_results` lacks the fields needed to filter the study cohort
`database.py:49-58`: only ScopeType, count, taken, fellow. The Endoscopy/Colonoscopy flags and the NoteCsnID live elsewhere, so the researcher cannot select "upper endoscopy notes" from the final export without joining tables. Fix: add `note_csn_id`, `Endoscopy`, `Colonoscopy`, and the evidence sentences to `final_results` and to `combined_results.csv`.

### B20 (M) Failure reasons are not stored
`data_worker.py:63,84,88` log exceptions to the console only; the DB keeps a 0/1 flag. The only run we have evidence of (`samples/working_results.csv`) lost 1 of 10 notes to an LLM failure with no recorded reason, and that file predates the `*_Failed` columns entirely. Fix: `LLM_Error TEXT`, `LLM_Attempts INTEGER`, `LLM_Seconds REAL`, `NLP_Error TEXT` in `working_results`; a `--reprocess-failed` CLI flag replacing the runbook's manual `sqlite3 delete`.

### B21 (M) No schema migration
`CREATE TABLE IF NOT EXISTS` (`database.py:126-129`) leaves an old `data.db` with old columns; B1, B19 and B20 all add columns. Fix: a `schema_version` table and `ALTER TABLE ... ADD COLUMN` steps on open.

### B22 (L) CLI mode precedence drops the CSV
`cli.py:117-127`: `--print --csv x` and `--working_results --csv x` never ingest the CSV because of the `elif` chain. Fix: ingest whenever `--csv` is given, then run the requested view.

### B23 (L) `--print` says "full notes" but shows 30 characters
`cli.py:34` and README versus `display.py:55`. Fix the help text, or add `--full` for operator-controlled visibility (keep truncation as the default, it is the PHI-minimizing choice).

### B24 (L) README and setup disagree with the code
README says `ollama pull gemma4:e2b` and "Model: gemma4:e2b"; code says `gemma3:12b` (`llm_analysis.py:6`). README claims "Boolean normalization" and "JSON repair layer"; only the latter exists. README's `curl install.sh` does not work on Argon (glibc 2.17), which the runbook explains. `requirements.txt` lists `click`, which nothing imports. Fix the README in the same commit as any model change (runbook constraint 9) and drop `click`.

### B25 (L) Unpinned dependencies and container tag
`requirements.txt` has no versions (local venv: spacy 3.8.16, ollama 0.6.3; Argon: 3.8.14, 0.6.2); `setup.sh:53` defaults to `ollama/ollama:latest`, which is what pulled an image whose CUDA runtime rejects Argon's driver 535. Fix: pin `spacy`, `ollama`, `rich` (check `manylinux2014` wheels per runbook constraint 1) and make a known-good Ollama tag the default.

### B26 (L) Job preflight budget and thread count do not match CPU inference
`setup.sh:647,792`: 600 s per call, 1 rep; a 12B model on CPU can exceed that and the gate aborts the job. `setup.sh:726-729` starts Ollama with no thread limit, so llama.cpp uses every core on the node, not the 8 slots requested. Fix: pass `options={"num_thread": int(os.environ.get("NSLOTS", 0)) or None}` from `llm_analysis.py`, and scale the preflight budget up when `nvidia-smi` is absent.

---

## 3. Feature improvements (ordered by value to robustness, reliability, accuracy)

### F1 Gold-label fixture and an `--evaluate` command
The ten annotated notes plus the five PDF comments are a ready-made gold set. Add `samples/gold.csv` (note id, expected ScopeType canonical, Endoscopy, Colonoscopy, count-or-null, taken, fellow) and `python app.py --evaluate gold.csv` that runs NLP, LLM and reconciliation and prints per-field precision/recall/agreement for each of the three. Every change in sections 2 and 3 is then measurable. Extend the gold set with 50 to 100 hand-labelled notes from the 5,000 corpus (stratified by NoteType) to measure the real false-positive rates listed above. Add pytest unit tests for the regexes using the sentences quoted in B3 to B6. This is the single highest-leverage item.

### F2 Replace "when in doubt, discard" with a review queue
Add `NeedsReview INTEGER` and `ReviewReason TEXT` to `final_results` (disagreement on which field, one-sided scope, LLM failure, note too long, count unknown) and export `output/review_queue.csv` with both answers and both evidence sentences side by side. A research study needs adjudication, not silent nulls; the all-null rows are currently indistinguishable from "nothing in the note".

### F3 Relevance gate before the LLM
Only 609 of 5,000 rows are procedure notes and only ~330 are EGDs. Run the NLP first and call the LLM only when the note passes a gate (configurable `--note-types Procedures` and/or NLP Endoscopy = 1 and/or an EGD-insertion regex). On the sample this cuts LLM calls by roughly 10x, which is the difference between fitting in the 24 h walltime and not (runbook open item 6), and it removes most of the FellowPresent and Colonoscopy false positives at the source. Record `Gate_Reason` so skipped notes are auditable.

### F4 Section-aware extraction
Split each note into sections (INDICATION, HISTORY, OPERATION/PROCEDURE or Procedure Details, FINDINGS, IMPRESSION, Teaching Statement / Staff Procedure Presence, signature). Give the LLM only the procedure, findings and attestation sections, and point each NLP rule at its section (scope and biopsies in procedure/findings; fellow in attestation/signature; "prior EGD" in history is ignored). This fixes most of B6, B10, B12 and B13 at once and reduces tokens per note several-fold.

### F5 Canonical scope names
Keep `ScopeType_raw` and add `ScopeType_canonical`: normalise case, spaces and hyphens, expand `GIF`-prefixed models to Olympus, map `H190`, `H 190`, `GIF-H 190`, `GIF H190`, `H-190` to `Olympus GIF-H190`, and so on for Q180, Q190, XP190N, XP160, H180. Reconcile on the canonical form (today "Olympus GIF XP190N video" versus "Olympus GIF XP190N" only matches by luck of the digit-token rule). Flag brand-only ("Olympus gastroscope") and unknown models for review instead of accepting them.

### F6 Per-site duodenal biopsy accounting
Parse each biopsy clause into (count, site) pairs; sum sites that are duodenal (bulb, D1, D2, D3, second/third portion, "duodenum"), but do not double count "6 of the duodenum including 2 from bulb" (comment 3). Recognise count-less forms ("random biopsies", "multiple", "x4", "biopsies (4)") as taken = 1 with count null or the parsed number. Add `DuodenalBiopsySites TEXT` as evidence.

### F7 Structured, reproducible LLM calls
Beyond B9: pass the schema as `format=`, log `model`, `options`, prompt hash and `ollama --version` into a `runs` table so every result is traceable to exact settings (needed for a methods section). Use `think=False` so a switch back to a reasoning model (gemma4) cannot put a thinking trace in `content`. Cache LLM results by `(note_sha256, prompt_hash, model)` so re-runs after a prompt change only redo what changed and the 69 duplicate notes are processed once.

### F8 Operational CLI flags
`--db PATH`, `--output DIR`, `--limit N` and `--ids 1,2,3` for smoke tests, `--reprocess-failed`, `--force-export`, `--gate` options from F3, `--dry-run` (NLP only, no LLM) for login-node use. Exit non-zero when the LLM failure rate exceeds a threshold (say 5%) so a dead server mid-run is loud instead of a full-walltime run of nulls.

### F9 Progress and health reporting inside the job
Write a heartbeat line every N notes (done / failed / seconds per note / ETA) to stdout so `tail -f` on the SGE log shows progress, and re-check `/api/tags` after K consecutive LLM failures to distinguish "server died" from "model confused", then stop early in the first case.

### F10 Stable keys and provenance in exports
Carry `note_csn_id` through `working_results` and `final_results` (B19) and include `run_id`, model, prompt hash and timestamp columns in `combined_results.csv`, so a reviewer can tie every value back to a run without the autoincrement `id`, which changes if the DB is rebuilt.

### F11 PHI-safe logging
Audit every `console.log`/`print` and exception message: `LLMAnalysisError` messages can echo model output (which copies note text) and end up in `~/nlp-study-logs/` in `$HOME`. Log codes and lengths, never content; keep raw output only in the DB.

### F12 Argon job script hardening
In `setup.sh`: explicit memory request (`NLP_JOB_MEM`), `num_thread` from `NSLOTS` (B26), a pinned image tag (B25), optional `#$ -l <gpu complex>` variable, and a post-run summary (counts of notes, failures, review-queue size) printed before exit so the end-of-job email is informative. Make tier 1 of the preflight run two notes: the synthetic one and a PHI-free paraphrase of note 2 (count-less biopsies) so the null-count behaviour from B1 is checked on every submission.

### F13 Deterministic NLP as the primary extractor for hard fields
After B3 to B6 and F4 to F6, measure (F1) whether the NLP alone matches the gold set on `DuodenalBiopsiesTaken`, `Endoscopy` and canonical `ScopeType`. If it does, treat the LLM as the cross-check and the NLP evidence sentence as the citation; disagreements go to the queue (F2). This keeps the study's "explainable, deterministic baseline" claim true in practice.

---

## 4. Sources checked for the Apptainer finding (B14)

- Apptainer CHANGELOG, v1.4.0 entry "Add new build option `--mksquashfs-args`": https://github.com/apptainer/apptainer/blob/main/CHANGELOG.md
- `cmd/internal/cli/build.go` registers the flag (env key `MKSQUASHFS_ARGS`) for `build` only; `cmd/internal/cli/pull.go` has no mksquashfs flag.
- `internal/pkg/util/fs/squashfs/squashfs.go`: `GetProcs()`/`GetMem()` read only `apptainer.conf`.
- Earlier proposal for env overrides (never the mechanism in Apptainer): https://github.com/apptainer/singularity/pull/6186

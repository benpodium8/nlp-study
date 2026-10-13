# nlp-study: evaluation against the study goals, bugfix list, and feature list

Written 2026-10-13 against commit `957c6b8` plus the uncommitted `setup.sh` rewrite (the 13:20 revision, 1,127 lines) and `ARGON_RUNBOOK.md`. Second pass done the same day: every cited line number re-checked against the files on disk, docx comments and highlights extracted, fellow-marker rule measured on the corpus.
Evidence comes from running `nlp_analysis.py` over `samples/notes.csv` (10 notes) and
`samples/sample_5000_deidentified.csv` (5,000 notes), from the saved LLM output in
`samples/working_results.csv`, and from the researcher's comments embedded in
`samples/Upper Gl endoscopy Scope details markdown pdf.pdf`.

> **Status, 2026-10-13 (later the same day).** The spaCy extractor (`nlp_analysis.py`), the reconciliation
> layer (`reconcile_working_results.py`), the `final_results` table, `display.py` and the `--print` and
> `--working_results` commands were removed: the NLP cross-check was built for a small early model that
> hallucinated, and with the current model it held the LLM back. The LLM is now the only extractor, its answer
> goes into a `results` table, and two CSVs are exported. The NLP measurements in section 1 stay as the record
> of why. Items that are now moot: B2, B3, B4, B5, B6 (its prompt half lives on in B11), B12, B13, B22, B23
> and F13. Done in the same change: `click` dropped from `requirements.txt` (B24); the exports reuse the open
> connection and there is one export, `output/results.csv`, instead of three (the rest of B17 and B18 is still
> open); `results` records `LLM_Attempts`, `LLM_Seconds` and `LLM_Error` per note (B21); missing `results`
> columns are added on startup (the column-add half of B19); the system prompt now implements the study definitions
> (B1 nullable count, B11 attestation-based fellow and performed-only procedures, comment 3 bulb counting; the
> validation coerces true/false and numeric strings, `temperature` 0 and a JSON schema `format=` are passed, with seeded retries for invalid JSON and runaway text (most of B9; validation failures are still not retried)); the run logs one line per note, a running
> summary with the projected time remaining, and the failed ids (the heartbeat half of F9); a bad CSV header
> now names the missing columns and the header found (the message half of B7; BOM and alias handling are
> still open); `python app.py` alone prints the requirements and a status check. F3 needs a gate
> that is not the NLP (note type and a procedure regex). Everything else still applies to the LLM path, and
> the line numbers cited below refer to the files as they were.

---

## 1. What the study actually wants (from the PDF comments)

The `.docx`, `.md` and `.pdf` are the same ten example notes (the `.docx` text and `samples/notes.csv`
match word for word; the `.md` only adds a mailto link and drops everything else). The `.docx` is the
source document and the `.pdf` is its print: both carry five review comments by the researcher
(Alexander P. Kane, 2025-12-13) that define the targets, and the `.docx` additionally holds 36 yellow
highlights (`word/document.xml`) that mark the expected answer in every note. The `.md` conversion
lost both the comments and the highlights, so it must not be used as the reference.

| # | Attached to | Comment | Requirement it implies |
| --- | --- | --- | --- |
| 1 | Note 2, "Random biopsies were taken to rule out celiac disease" | "Ideally they say the number of biopsies but lacking that just whether they were taken would be good to know" | Count is optional; **presence** is the minimum. "Count unknown" must be distinguishable from "zero". |
| 2 | Note 4, the "Olympus Q 180 endoscope" sentence (note also has a PCF H180AL colonoscope) | "I only need the scopes used in upper endoscopy" | ScopeType = the EGD scope only. Colonoscope must never win. |
| 3 | Note 5, "6 biopsies ... including 2 from bulb" | "I am looking for biopsies from the duodenum and the bulb is a part of that and another word used to describe it" | Bulb biopsies are duodenal. Count them once (6 here, not 2 and not 8). |
| 4 | Note 6, PEG placement, "BIOPSY: None" | "This one no biopsies were taken" | A true zero. PEG notes are in scope for the scope field. |
| 5 | Note 9, "Olympus PCF H180AL colonoscope" | "Don't care about this one since it was for a colonoscopy" | Same as 2. |

Fields the code extracts: ScopeType, Endoscopy, Colonoscopy, NumberOfDuodenalBiopsies,
DuodenalBiopsiesTaken, FellowPresent, plus evidence sentences.

### Gold labels from the docx highlights

The highlights fall into exactly three kinds per note: the scope string, the duodenal biopsy sentence,
and the fellow-presence marker. Two things they settle that the code and prompt currently get wrong:

- **Acceptable scope answers include brand-only and model-only forms.** "Olympus gastroscope" is
  highlighted as the answer for note 2, "GIF-H 190" for notes 8 and 10.
- **FellowPresent is decided by the attestation, not by the word "fellow".** "Teaching Statement"
  (the attending attests that a trainee performed under supervision) is highlighted as the fellow marker
  in notes 1, 3, 4, 6 and 9; "Staff Only" is highlighted in notes 5, 8 and 10 as the no-fellow marker.
  In notes 6 and 9 the word "fellow" never appears, yet the fellow's name ("Chinenye Dike",
  "Sussette Gonzalez, MD") and the Teaching Statement are highlighted.

| Note | ScopeType (highlighted) | Duodenal biopsies | FellowPresent (marker) |
| --- | --- | --- | --- |
| 1 | Olympus H190 | 4 | 1 (fellow signature + Teaching Statement) |
| 2 | Olympus gastroscope | taken, count not stated | 0 (RESIDENT/FELLOW None) |
| 3 | Olympus Q 180 | 4, one from bulb | 1 |
| 4 | Olympus Q 180 | 4 | 1 |
| 5 | Olympus H190 | 6, including 2 from bulb | 0 (Staff Only) |
| 6 | Olympus GIF XP190N | 0, "BIOPSY: None" | 1 (Chinenye Dike + Teaching Statement) |
| 7 | Olympus H190 | 6 | 0 (attending signs alone, no attestation) |
| 8 | GIF-H 190 | 6 | 0 (Staff Only) |
| 9 | Olympus H190 | 4 | 1 (Sussette Gonzalez + Teaching Statement) |
| 10 | GIF-H 190 | 4 | 0 (Staff Only) |

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
- **Notes 6 and 9: both extractors say FellowPresent = 0 and the gold says 1.** Neither note contains the
  word "fellow"; both carry a Teaching Statement. Because NLP and LLM agree, the row reconciles
  "successfully" with the wrong value. Agreement-based reconciliation cannot catch shared blind spots,
  and the NLP rule (`nlp_analysis.py:159-166`) and the prompt rule 9 (`llm_analysis.py:209-212`,
  "Set to 1 ONLY if a fellow is explicitly mentioned") share this one.
- Notes 1, 4, 7 reconcile correctly. Note 6's final scope is `Olympus GIF XP190N video`, with the junk
  word "video" attached.
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
| Procedures notes with "Teaching Statement" (the highlight's fellow marker) | 193; **NLP says FellowPresent = 0 in 127 of them**, and 115 of those never contain the word "fellow" |
| Procedures notes with "Staff Only" | 66; the two markers never co-occur, so they partition the attested notes cleanly |
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

### B6 (H) Fellow detection looks for the word "fellow"; the gold is decided by the attestation
`nlp_analysis.py:159-166`: "fellow" anywhere in the last 30% of the token stream, unless "none" is also in the sentence. The docx highlights show the researcher reads FellowPresent from the attending's attestation: "Teaching Statement ... was present and actively participated" means a fellow performed (1), "Staff Only: I was present for and participated in the entire procedure" means no fellow (0). In the 5,000 corpus the NLP misses 127 of 193 Teaching-Statement procedure notes (115 of them never say "fellow"), and in the ten-note sample it is wrong on notes 6 and 9 with the LLM agreeing. It also misses "Fellow: <NAME>" headers near the top (6 notes) and fires on attending attestations in 976 non-procedure notes. Fix: decide in this order: `Staff Only` → 0; `Teaching Statement` / "present and actively participated" → 1; `Fellow:`/`RESIDENT/FELLOW` header with a name → 1, with `none` → 0; a trainee signature line ("Peds GI fellow") → 1; otherwise 0. Drop the 70% rule. Update prompt rule 9 to the same definition (see B11).

### B7 (H) Ingest rejects both sample CSVs
`database.py:142,145-155`: `encoding="utf-8"` leaves the BOM on the first header (`﻿MRN`), matching is exact and case-sensitive, and `EncounterID` is not an alias of `Encounter`. The runbook's answer is to `sed` the file. Fix: open with `utf-8-sig`, normalize headers (strip, casefold, remove `_`/spaces) and map aliases (`encounterid`, `encounter`; `note_csn_id`, `notecsnid`; ...), and give a message naming the headers found versus required.

### B8 (H) `samples/notes.csv` has a note-boundary error and label text inside notes
Note 10 starts with the CPT line that ends note 9 ("EGD, beyond pylorus ... LONG COLONOSCOPY with biopsy (45380)"), which is why the saved NLP result says Colonoscopy = 1 for note 10. Notes 1 and 9 start with the document's section labels ("Upper Gl endoscopy Scope details: with biopsy"), which are not note text. Fix the CSV (move the line back to note 9, strip the labels) so it can serve as the gold fixture (F1). Keep `samples/` out of git.

### B9 (H) LLM output is non-deterministic and unconstrained
`llm_analysis.py:231` calls `ollama.chat(model, messages)` with no `options` and no `format`. The server default temperature (0.8) applies, so two runs of the same note can differ, which a study cannot tolerate; every response comes back fenced; `true`/`false` or `"4"` fail validation and are not retried (`data_worker.py:78` retries only JSON errors). Fix: `format=<JSON schema dict>` (the installed `ollama` 0.6.3 supports it), `options={"temperature": 0, "seed": 0, "num_ctx": ..., "num_predict": 1024}`, coerce bool/str to int before validation, and retry validation failures too with a corrective message (cap total attempts). Re-run the preflight after.

### B10 (H) Long notes are silently truncated
No `num_ctx` is passed (`llm_analysis.py:231`); the README's local workflow gets Ollama's default 4,096 tokens, and Argon gets 8,192 only because the launcher exports `OLLAMA_CONTEXT_LENGTH`. The 5,000 corpus has p99 = 23K chars (about 6K tokens) and max 41K chars (about 10K tokens), plus a ~700-token system prompt. Ollama truncates the prompt server-side and logs it only in the server log; the client sees a normal response. Fix: pass `num_ctx` explicitly, estimate tokens before the call, and either send only the procedure section (F4) or mark the note `LLM_Failed` with reason "too long" instead of returning a confident wrong answer.

### B11 (H) The prompt's "trigger word" rules cause false positives, encode the wrong fellow definition, and contradict the preflight
`llm_analysis.py:177-180` says Colonoscopy = 1 on the word "colonoscopy"; `:187-189` likewise for "EGD". A note saying "No colonoscopy was performed" or "prior EGD in 2019" is flagged. Rule 9 (`:209-212`) says FellowPresent = 1 "ONLY if a fellow is explicitly mentioned", which is why the LLM returned 0 for notes 6 and 9 where the gold is 1 (see B6). The synthetic preflight note in `setup.sh:443` contains "No colonoscopy was performed" and `setup.sh:448` treats Colonoscopy = 0 as a HARD failure, so a model that follows the prompt literally blocks job submission. Fix: rewrite the procedure rules as "performed during this procedure, in the procedure/findings section", rewrite rule 9 around Teaching Statement / Staff Only / Fellow: headers, and make the preflight's synthetic note and expectations consistent with the prompt (give it a Teaching Statement and expect FellowPresent = 1).

### B12 (M) Endoscopy and colonoscopy flags fire on history, plans and instructions
`nlp_analysis.py:122-132,142-145`: any sentence with "upper endoscopy"/"egd" plus a verb, or any sentence with "colonoscopy", sets the flag (112 non-procedure notes flagged Endoscopy; 131 of 353 Colonoscopy hits are plan/history text). Procedure notes written as "The endoscope was then placed in the oropharynx ... esophagus was easily intubated" are missed (38) because "upper endoscopy"/"egd" is not in the same sentence. Fix: detect the procedure section (OPERATION/PROCEDURE, Procedure Details, FINDINGS, Upper endoscopy:) and look for insertion language there (esophagus/oropharynx intubated, scope advanced to duodenum); treat "history of", "prior", "tomorrow", "scheduled", "after your" as non-events.

### B13 (M) Sentence segmentation ignores newlines and headers
`nlp_analysis.py:4-5` uses the bare `sentencizer`; these notes separate sections with newlines and `HEADER:` tokens, not periods, so 486 "sentences" in the first 1,500 notes exceed 1,500 chars and the evidence fields become whole-note dumps (note 1's `EndoscopyInformation` is the entire header). Fix: pre-split on newlines and on `[A-Z][A-Z /]+:` headers before the sentencizer (or add a custom boundary component).

### B14 (M, fixed in the working tree on 2026-10-13, needs an Argon run to confirm) `APPTAINER_MKSQUASHFS_ARGS` did nothing on Argon
The 11:41 revision of `setup.sh` exported that variable and claimed mksquashfs was pinned to one thread. Apptainer reads it only as the `--mksquashfs-args` flag of `apptainer build`, added in v1.4.0 (2025-03-18), and `apptainer pull` does not register the flag at all; Argon runs 1.3.2. So the login-node pull ran mksquashfs on every core with its default memory (25% of physical RAM) under the 1-CPU / 16 GB killer. The 13:20 revision replaces the export with `make_mksquashfs_wrapper` (`setup.sh:275-302`): a PATH-first wrapper that execs the real binary with `-processors ${NSLOTS:-1} -mem 2G`, skipped with a warning when `apptainer.conf` pins `mksquashfs path`. The code reads correctly. Remaining work: run `./setup.sh --force sif` on a login node and confirm in the transcript that the wrapper was used and the pull survived; then fix the runbook lines that still describe the old mechanism (B27).

### B15 (M) No HTTP timeout on the LLM call
`ollama.Client` defaults to `timeout=None`. A wedged server stalls `data_worker` until SGE kills the job at walltime, with no row written for the note. Fix: build a module-level `Client(host=os.environ.get("OLLAMA_HOST"), timeout=NLP_LLM_TIMEOUT)` (keep the `OLLAMA_HOST`-at-import behaviour the runbook relies on) and treat the timeout as a retryable failure with a reason.

### B16 (M) Ingest dedup is whole-row equality and O(n²)
`database.py:108-112,173-181`: one full-table scan per incoming row comparing six columns including the note text; a trailing space creates a new note; the 5,000 corpus has 69 identical notes under different CsnIDs that will all be re-analysed. Fix: `UNIQUE(note_csn_id)` (CsnIDs were unique in the 5,000 sample) with `INSERT OR IGNORE`, plus a `note_sha256` column to reuse results across identical texts.

### B17 (M) Write-once exports silently serve stale results after a resume
`database.py:372-388,471-491`: if any of the three CSVs exists the export is skipped with a print. After a resumed run the `output/` files are from the first partial run. Worse, `python app.py --analyze` on an empty database (the `No notes to process` path in `data_worker.py:19-21`) still falls through to the exports in `cli.py:120-121` and writes three header-only CSVs that then block every later export. Fix: timestamped filenames (`combined_results_<YYYYMMDD-HHMMSS>.csv`) or overwrite with `--force-export`; skip the export when there are no rows; never skip silently.

### B18 (M) Exports and the DB path are cwd-relative and duplicated
`cli.py:114` opens `data.db` relative to cwd; `database.py:346,398,435` open `"data.db"` by name again inside the export functions, and `with sqlite3.connect(...)` commits but never closes. Fix: a single `--db PATH` (default `data.db`) threaded through, exports take the open connection, connections closed in `finally`.

### B19 (M) No schema migration (do this before B20 and B21, which add columns)
`CREATE TABLE IF NOT EXISTS` (`database.py:126-129`) leaves an old `data.db` with old columns, so any column added by a later fix is silently absent on an existing database and the `INSERT` fails. Fix: a `schema_version` table and `ALTER TABLE ... ADD COLUMN` steps on open.

### B20 (M) `final_results` lacks the fields needed to filter the study cohort
`database.py:49-58`: only ScopeType, count, taken, fellow. The Endoscopy/Colonoscopy flags and the NoteCsnID live elsewhere, so the researcher cannot select "upper endoscopy notes" from the final export without joining tables. Fix: add `note_csn_id`, `Endoscopy`, `Colonoscopy`, and the evidence sentences to `final_results` and to `combined_results.csv`.

### B21 (M) Failure reasons are not stored
`data_worker.py:63,84,88` log exceptions to the console only; the DB keeps a 0/1 flag. The only run we have evidence of (`samples/working_results.csv`) lost 1 of 10 notes to an LLM failure with no recorded reason, and that file predates the `*_Failed` columns entirely. Fix: `LLM_Error TEXT`, `LLM_Attempts INTEGER`, `LLM_Seconds REAL`, `NLP_Error TEXT` in `working_results`; a `--reprocess-failed` CLI flag replacing the runbook's manual `sqlite3 delete`.

### B22 (L) CLI mode precedence drops the CSV
`cli.py:117-127`: `--print --csv x` and `--working_results --csv x` never ingest the CSV because of the `elif` chain. Fix: ingest whenever `--csv` is given, then run the requested view.

### B23 (L) `--print` says "full notes" but shows 30 characters
`cli.py:34` and README versus `display.py:55`. Fix the help text, or add `--full` for operator-controlled visibility (keep truncation as the default, it is the PHI-minimizing choice).

### B24 (L) README and setup disagree with the code
README said `ollama pull gemma4:e2b` and "Model: gemma4:e2b"; code said `gemma3:12b`. Model names fixed 2026-10-13: both now say `medgemma:27b` (`llm_analysis.py:9`). README claims "Boolean normalization" and "JSON repair layer"; only the latter exists. README's `curl install.sh` does not work on Argon (glibc 2.17), which the runbook explains. `requirements.txt` lists `click`, which nothing imports. Fix the README in the same commit as any model change (runbook constraint 9) and drop `click`.

### B25 (L) Unpinned dependencies and container tag
`requirements.txt` has no versions (local venv: spacy 3.8.16, ollama 0.6.3; Argon: 3.8.14, 0.6.2); `setup.sh:54` defaults to `ollama/ollama:latest`, which is what pulled an image whose CUDA runtime rejects Argon's driver 535. Python side done 2026-10-13: `requirements.txt` pins the Argon versions (all resolve to `manylinux2014` wheels) and `.python-version` pins 3.12.14. `setup.sh` defaults to `ollama/ollama:0.24.0` (last release that uses a driver-535 GPU) and re-pulls an image recorded under a different tag. Remaining: confirm GPU placement in an Argon job.

### B26 (L) Job preflight budget and thread count do not match CPU inference
`setup.sh:675,820`: 600 s per call, 1 rep; a 12B model on CPU can exceed that and the gate aborts the job. `setup.sh:754-757` starts Ollama with no thread limit, so llama.cpp uses every core on the node, not the 8 slots requested. Fix: pass `options={"num_thread": int(os.environ.get("NSLOTS", 0)) or None}` from `llm_analysis.py`, and scale the preflight budget up when `nvidia-smi` is absent.

### B27 (L) Runbook statements that no longer match the script
`ARGON_RUNBOOK.md:3` describes a 955-line `setup.sh` dated 11:19; the working-tree file is 1,127 lines, dated 13:20. `:219` and `:805` say mksquashfs is pinned through `APPTAINER_MKSQUASHFS_ARGS`; that variable is gone and the mechanism is now the PATH wrapper (B14). `:308` says the repo is healthy when it contains `app.py`, `cli.py`, `llm_analysis.py`, `requirements.txt`; `verify_repo` (`setup.sh:224-235`) only checks for a valid HEAD and no missing tracked files. `:319` says setup mode "requires outbound HTTPS"; the script warns, sets `HAVE_NET=0` and continues with what is built (`setup.sh:890-894`). Section 2.2 documents "Explicitly stated count, else 0", which is the B1 flaw stated as a feature. Fix the runbook when B1 and B14 land, and re-derive the line count and timestamps from the committed file.

---

## 3. Feature improvements (ordered by value to robustness, reliability, accuracy)

### F1 Gold-label fixture and an `--evaluate` command
The ten annotated notes are already labelled: the docx highlights give the scope, the biopsy sentence and the fellow marker for every note (table in section 1), and the comments give the semantics. Write them into `samples/gold.csv` (note id, expected ScopeType raw and canonical, Endoscopy, Colonoscopy, count-or-null, taken, fellow, evidence) and add `python app.py --evaluate gold.csv` that runs NLP, LLM and reconciliation and prints per-field precision/recall/agreement for each of the three, plus the "agree but wrong" count that notes 6 and 9 expose. Every change in sections 2 and 3 is then measurable. Extend the gold set with 50 to 100 hand-labelled notes from the 5,000 corpus (stratified by NoteType) to measure the real false-positive rates listed above. Add pytest unit tests for the regexes using the sentences quoted in B3 to B6. This is the single highest-leverage item.

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

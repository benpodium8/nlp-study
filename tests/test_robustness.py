"""
Checks that the pipeline survives input the sample never showed: CSV files in other
encodings and header spellings, long or odd fields, scope names and attestation forms from
a larger corpus, a prompt the server counts as too long, and cells a spreadsheet would read
as formulas. Standard library only; run from the project directory:

    python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import consensus as C  # noqa: E402
import database as D  # noqa: E402
import export as E  # noqa: E402
import llm_analysis as L  # noqa: E402
import review as R  # noqa: E402
from data_worker import analyze_note  # noqa: E402

HEADER = b"MRN,Encounter,NoteCsnID,NoteDate,NoteType,Note\n"


def ingest(content: bytes):
    """Ingests content as a notes CSV into a fresh database; returns (summary, stored rows, report lines)."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "notes.csv"
        path.write_bytes(content)
        conn = D.create_db(Path(folder) / "t.db", ["m@8192"])
        lines = []
        summary = D.ingest_csv(path, conn, report=lines.append)
        rows = conn.execute(
            "SELECT mrn, encounter, note_csn_id, note_date, note_type, note FROM notes ORDER BY id"
        ).fetchall()
        conn.close()
        return summary, rows, lines


class IngestTests(unittest.TestCase):
    def test_byte_order_mark_and_encounterid_header(self):
        content = b'\xef\xbb\xbfMRN,EncounterID,NoteCsnID,NoteDate,NoteType,Note\r\nP1,E1,N1,2020,Procedures,"hello\r\nworld"\r\n'
        summary, rows, lines = ingest(content)
        self.assertEqual(rows, [("P1", "E1", "N1", "2020", "Procedures", "hello\r\nworld")])
        self.assertEqual(summary.new, 1)
        self.assertIn("byte-order mark skipped", lines[0])
        self.assertIn("'EncounterID' read as Encounter", lines[0])

    def test_lowercase_underscore_header_with_extra_column(self):
        content = b"id,mrn,encounter,note_csn_id,note_date,note_type,note\n7,P1,E1,N1,2020,H&P,x\n"
        _, rows, _ = ingest(content)
        self.assertEqual(rows, [("P1", "E1", "N1", "2020", "H&P", "x")])

    def test_missing_column_is_named(self):
        content = b"MRN,Encounter,NoteCsnID,NoteType,Note\nP1,E1,N1,T,x\n"
        with self.assertRaises(ValueError) as caught:
            ingest(content)
        self.assertIn("NoteDate", str(caught.exception))
        self.assertIn("Header found: MRN, Encounter, NoteCsnID, NoteType, Note", str(caught.exception))

    def test_exact_name_beats_alias(self):
        mapping, missing = D.match_csv_columns(["Date", "NoteDate", "MRN", "Encounter", "NoteCsnID", "NoteType", "Note"])
        self.assertEqual(missing, [])
        self.assertEqual(mapping["NoteDate"], "NoteDate")

    def test_short_row_and_empty_note(self):
        content = HEADER + b"P1,E1,N1,2020\nP2,E2,N2,2020,T,\n"
        summary, rows, lines = ingest(content)
        self.assertEqual(rows[0], ("P1", "E1", "N1", "2020", "", ""))
        self.assertEqual(rows[1][5], "")
        self.assertTrue(any("fewer cells" in line for line in lines))
        self.assertTrue(any("2 new note(s) are empty" in line for line in lines))
        self.assertEqual(summary.new, 2)

    def test_invalid_utf8_and_nul_bytes(self):
        content = HEADER + b"P1,E1,N1,2020,T,caf\xe9 a\x00b\n"
        _, rows, lines = ingest(content)
        self.assertEqual(rows[0][5], "caf� ab")
        self.assertTrue(any("not valid UTF-8" in line for line in lines))
        self.assertTrue(any("NUL byte" in line for line in lines))

    def test_note_longer_than_csv_default_limit(self):
        long = b"x" * 300_000
        _, rows, _ = ingest(HEADER + b"P1,E1,N1,2020,T," + long + b"\n")
        self.assertEqual(len(rows[0][5]), 300_000)

    def test_duplicate_rows_skipped(self):
        row = b"P1,E1,N1,2020,T,same\n"
        summary, rows, _ = ingest(HEADER + row + row)
        self.assertEqual((summary.new, summary.duplicates, len(rows)), (1, 1, 1))


class StreamingTests(unittest.TestCase):
    """The ingest, the analysis loop and the consensus read the corpus piece by piece."""

    def corpus(self, n: int) -> bytes:
        rows = [f"P{i},E{i},N{i},2020,Procedures,note {i} text\n".encode() for i in range(n)]
        rows.insert(n // 2, rows[3])                  # a row the file repeats
        rows.insert(5, b"P5x,E5x,N5x,2020,T,a\x00b\n")  # a NUL byte in the middle of the file
        return HEADER + b"".join(rows)

    def test_ingest_counts_rows_first_and_stores_in_batches(self):
        n = 2 * D.INGEST_BATCH + 7
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notes.csv"
            path.write_bytes(self.corpus(n))
            conn = D.create_db(Path(folder) / "t.db", ["m@8192"])
            summary = D.ingest_csv(path, conn, report=lambda line: None, progress=lambda *a: calls.append(a))
            stored = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            pending = D.pending_notes(conn, "m@8192")
            text = D.note_text(conn, pending[0][0])
            conn.close()
        self.assertEqual((summary.rows, summary.new, summary.duplicates, stored), (n + 2, n + 1, 1, n + 1))
        self.assertEqual(calls[0], (0, n + 2, 0, 0))
        self.assertEqual(calls[-1], (n + 2, n + 2, n + 1, 1))
        self.assertEqual(len(pending), n + 1)
        self.assertEqual(pending[0], (1, len("note 0 text")))
        self.assertEqual(text, "note 0 text")

    def test_consensus_streams_and_writes_every_note(self):
        n = C.CONSENSUS_BATCH + 50
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notes.csv"
            path.write_bytes(self.corpus(n))
            models = [L.ModelSpec("a", 8192), L.ModelSpec("b", 8192)]
            conn = D.create_db(Path(folder) / "t.db", [m.label for m in models])
            D.ingest_csv(path, conn, report=lambda line: None)
            summary = C.consolidate(conn, models, report=lambda line: None)
            built, flagged = conn.execute(
                f"SELECT COUNT(*), SUM({D.quoted('ManualReviewRequired (consensus)')} = 'true') FROM results"
            ).fetchone()
            conn.close()
        self.assertEqual(summary.notes, n + 1)
        self.assertEqual((built, flagged), (n + 1, n + 1))   # no model has run: every row waits, flagged

    def test_estimate_accepts_a_length(self):
        self.assertEqual(L.estimated_prompt_tokens("x" * 300), L.estimated_prompt_tokens(300))
        self.assertEqual(L.note_fits_context(10 ** 6), False)


def answer(**over):
    """A schema-valid answer with every flag 0, overridden by over."""
    base = {
        "id": 1, "ScopeType": None, "Colonoscopy": 0, "ColonoscopyInformation": None, "Endoscopy": 0,
        "EndoscopyInformation": None, "NumberOfDuodenalBiopsies": 0, "DuodenalBiopsiesTaken": 0,
        "DuodenalBiopsiesInformation": None, "FellowPresent": 0, "FellowInformation": None,
    }
    base.update(over)
    return base


NOTE = (
    "Upper endoscopy: The patient's esophagus was easily intubated with an Olympus XP180 endoscope. "
    "4 biopsies were taken of the duodenum for histopathology. "
    "Staff Procedure Presence: RES/FEL Staff Procedure Presence Statement: Dr. Smith was present for the entire procedure. "
    "A Parsons laryngoscope was used by ENT. An EC-3890Li colonoscope was advanced to the cecum. "
    "4-6 random biopsies were taken of the bulb. Olympus endoscope (GIF-H180) noted elsewhere."
)
EGD = {"Endoscopy": 1, "EndoscopyInformation": "The patient's esophagus was easily intubated with an Olympus XP180 endoscope."}


class VerifierTests(unittest.TestCase):
    def test_scope_model_the_sample_never_showed_is_accepted(self):
        L.verify_against_note(answer(**EGD, ScopeType="Olympus XP180"), NOTE)
        L.verify_against_note(answer(**EGD, ScopeType="Olympus XP-180"), NOTE)  # a form of the note's name

    def test_res_fel_attestation_is_accepted_as_fellow_evidence(self):
        for quote in (
            "RES/FEL Staff Procedure Presence Statement: Dr. Smith was present for the entire procedure.",
            "Dr. Smith was present for the entire procedure.",
        ):
            L.verify_against_note(answer(**EGD, FellowPresent=1, FellowInformation=quote), NOTE)

    def test_laryngoscope_is_not_an_upper_endoscope(self):
        with self.assertRaises(L.RetryableLLMError) as caught:
            L.verify_against_note(answer(**EGD, ScopeType="Parsons laryngoscope"), NOTE)
        self.assertIn("not an upper endoscope", str(caught.exception))

    def test_pentax_style_colonoscope_prefix_is_rejected(self):
        with self.assertRaises(L.RetryableLLMError) as caught:
            L.verify_against_note(answer(**EGD, ScopeType="EC-3890Li"), NOTE)
        self.assertIn("colonoscope", str(caught.exception))

    def test_range_supports_either_end_and_is_not_a_sum(self):
        self.assertEqual(L.supported_counts("4-6 random biopsies were taken of the bulb"), {4, 6})
        self.assertFalse(L.summed_count_quote("4-6 random biopsies were taken of the bulb"))
        self.assertEqual(L.supported_counts("4 biopsies were taken of the duodenum and 2 from the bulb"), {6})

    def test_scope_as_stated_returns_the_notes_spelling_or_nothing(self):
        self.assertEqual(L.scope_as_stated("Olympus XP-180", NOTE), "Olympus XP180")
        self.assertIsNone(L.scope_as_stated("Olympus GIF-H180", NOTE))  # brand and model stated apart


class FakeClient:
    """Stands in for ollama.Client: streams a fixed answer with the given server token counts."""
    prompt_tokens = 0
    answer_tokens = 0
    content = '{"ScopeType": null, "Colonoscopy": 0, "ColonoscopyInformation": null, "Endoscopy": 0, "EndoscopyInformation": null, "NumberOfDuodenalBiopsies": 0, "DuodenalBiopsiesTaken": 0, "DuodenalBiopsiesInformation": null, "FellowPresent": 0, "FellowInformation": null}'

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def chat(self, **kwargs):
        chunk = lambda text, done: SimpleNamespace(  # noqa: E731
            message=SimpleNamespace(content=text), done=done,
            prompt_eval_count=self.prompt_tokens if done else None, eval_count=self.answer_tokens if done else None,
        )
        yield chunk(self.content[:40], False)
        yield chunk(self.content[40:], False)
        yield chunk("", True)


class PromptCutTests(unittest.TestCase):
    def setUp(self):
        self.real_client = L.ollama.Client
        L.ollama.Client = FakeClient

    def tearDown(self):
        L.ollama.Client = self.real_client

    def test_answer_from_a_cut_prompt_is_discarded_and_not_retried(self):
        FakeClient.prompt_tokens, FakeClient.answer_tokens = 8100, 150
        spec = L.ModelSpec("fake", 8192)
        with self.assertRaises(L.PromptCutError) as caught:
            L.llm_analysis(1, "short note", spec)
        self.assertNotIsInstance(caught.exception, L.RetryableLLMError)
        self.assertEqual(L.failure_kind(str(caught.exception)), L.NOT_SENT)
        analysis = analyze_note(1, "short note", spec)
        self.assertEqual((analysis.llm_failed, analysis.attempts), (1, 1))
        self.assertTrue(analysis.error.startswith(L.TOO_LONG_PREFIX))
        required, reason = R.manual_review("short note", None, 1, 1, analysis.error, None, 8192)
        self.assertTrue(required)
        self.assertIn("did not read it in full", reason)

    def test_answer_within_the_window_is_kept(self):
        FakeClient.prompt_tokens, FakeClient.answer_tokens = 3000, 150
        result, raw = L.llm_analysis(1, "short note", L.ModelSpec("fake", 8192))
        self.assertEqual(result["Endoscopy"], 0)
        self.assertEqual(raw, FakeClient.content)

    def test_missing_counts_never_fail_a_note(self):
        FakeClient.prompt_tokens, FakeClient.answer_tokens = None, None
        result, _ = L.llm_analysis(1, "short note", L.ModelSpec("fake", 8192))
        self.assertEqual(result["Endoscopy"], 0)


class ExportTests(unittest.TestCase):
    def test_formula_looking_text_gets_a_leading_space(self):
        self.assertEqual(E.cell(None, "- 4 biopsies were taken"), " - 4 biopsies were taken")
        self.assertEqual(E.cell("ScopeType", "=1+1"), " =1+1")
        self.assertEqual(E.cell(None, "plain"), "plain")
        self.assertEqual(E.cell("Endoscopy", 1), "TRUE")
        self.assertEqual(E.cell("ManualReviewRequired", "false"), "FALSE")
        self.assertEqual(E.cell(None, None), "")


class ExportFilesTests(unittest.TestCase):
    """results.csv and its review-sized copy agree on columns A to X and are write-once together."""

    def build(self, folder: Path):
        models = [L.ModelSpec("a", 8192), L.ModelSpec("b", 8192)]
        conn = D.create_db(folder / "t.db", [m.label for m in models])
        path = folder / "notes.csv"
        path.write_bytes(
            HEADER
            + b"P1,E1,N1,2020,Procedures,\"The esophagus was intubated with an Olympus Q180 endoscope. 4 biopsies were taken of the duodenum. Staff Only: I was present for and participated in the entire procedure.\"\n"
            + b"P2,E2,N2,2021,Progress Notes,- rounds note with nothing in it\n"
            + b"P3,E3,N3,2022,Consults,consult\n"
        )
        D.ingest_csv(path, conn, report=lambda line: None)
        egd = answer(
            Endoscopy=1, EndoscopyInformation="The esophagus was intubated with an Olympus Q180 endoscope.", ScopeType="Olympus Q180",
            DuodenalBiopsiesTaken=1, NumberOfDuodenalBiopsies=4, DuodenalBiopsiesInformation="4 biopsies were taken of the duodenum.",
        )
        for label in ("a@8192", "b@8192"):
            D.insert_result(conn, 1, label, egd, "{}", 0, 1, 1.0, None)
            D.insert_result(conn, 2, label, answer(), "{}", 0, 1, 1.0, None)
        D.insert_result(conn, 3, "a@8192", None, None, 1, 5, 9.0, "no answer the note supports")
        C.consolidate(conn, models, report=lambda line: None)
        return conn

    def read(self, path: Path):
        with path.open(newline="", encoding="utf-8") as f:
            return list(__import__("csv").reader(f))

    def test_review_copy_matches_columns_a_to_x(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            conn = self.build(folder)
            lines = []
            written = E.export_results(conn, str(folder / "output"), report=lines.append)
            conn.close()
            self.assertEqual(written, folder / "output" / E.EXPORT_NAME)
            full = self.read(folder / "output" / E.EXPORT_NAME)
            lean = self.read(folder / "output" / E.REVIEW_EXPORT_NAME)
        self.assertEqual(len(full), len(lean))
        self.assertEqual(len(full), 4)
        consensus_titles = [t for t in full[0] if t.endswith("(consensus)")]
        self.assertEqual(lean[0], full[0][:6] + consensus_titles + [E.NOTE_LENGTH_TITLE])
        shared = 6 + len(consensus_titles)
        length_index = full[0].index(E.NOTE_LENGTH_TITLE)
        for full_row, lean_row in zip(full[1:], lean[1:]):
            self.assertEqual(lean_row[:shared], full_row[:shared])
            self.assertEqual(lean_row[shared], full_row[length_index])
        flag = full[0].index(E.REVIEW_COLUMN)
        self.assertEqual([row[flag] for row in full[1:]], ["FALSE", "FALSE", "TRUE"])
        self.assertEqual(E.column_letter(flag), "G")
        self.assertTrue(any("review-sized copy" in line for line in lines))

    def test_an_existing_review_file_blocks_both(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            conn = self.build(folder)
            (folder / "output").mkdir()
            (folder / "output" / E.REVIEW_EXPORT_NAME).write_text("stale")
            lines = []
            written = E.export_results(conn, str(folder / "output"), report=lines.append)
            conn.close()
            self.assertIsNone(written)
            self.assertFalse((folder / "output" / E.EXPORT_NAME).exists())
            self.assertIn("already exist", lines[0])


def failed(label, error):
    return C.ModelAnswer(label, 1, error, {field: None for field, _ in D.RESULT_FIELDS})


class ConsensusTests(unittest.TestCase):
    def test_res_fel_attestation_flags_a_no_fellow_consensus(self):
        note = "... Staff Procedure Presence: RES/FEL Staff Procedure Presence Statement: Dr. X was present for the entire procedure"
        self.assertEqual(len(C.attestation_conflicts(note, 0)), 1)
        self.assertEqual(C.attestation_conflicts(note, 1), [])

    def test_fellow_signature_then_presence_line_flags_but_a_monitor_line_does_not(self):
        self.assertEqual(len(C.attestation_conflicts("J. Doe MD Pediatric Gastroenterology Fellow Dr. X was present for the entire procedure", 0)), 1)
        self.assertEqual(C.attestation_conflicts("RN Smith was present for the entire procedure as sedation monitor", 0), [])

    def test_operative_note_types_count_as_procedure_notes(self):
        self.assertEqual(C.procedure_marks("Operative Report", "nothing here", []), ["a procedure note type"])
        self.assertEqual(C.procedure_marks("Op Note", "nothing here", []), ["a procedure note type"])
        self.assertEqual(C.procedure_marks("Progress Notes", "nothing here", []), [])

    def test_too_long_wording_covers_sent_and_unsent(self):
        long = "x" * 30000
        answers = [failed("a@8192", L.too_long_error(long)), failed("b@8192", L.prompt_cut_error(8100, 150, long)), failed("c@8192", L.too_long_error(long))]
        row = C.consensus(answers, None, "Progress Notes", long)
        self.assertEqual(row["ManualReviewRequired"], "true")
        self.assertIn("so no model read it in full", row["ManualReviewReason"])
        self.assertEqual(row["Summary"], "No consensus: the note was too long for the context window")


class ResumeSafetyTests(unittest.TestCase):
    """A long corpus runs as a chain of jobs: one run per database, stops that lose nothing, exports never half written."""

    def test_second_run_is_refused_and_a_stale_lock_is_taken_over(self):
        import os
        import run_lock
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "data.db"
            first = run_lock.RunLock(db, report=lambda line: None)
            first.acquire()
            with self.assertRaises(run_lock.LockHeld):
                run_lock.RunLock(db, report=lambda line: None).acquire(wait_seconds=0)
            old = run_lock.lock_path(db).stat().st_mtime - run_lock.STALE_SECONDS - 10
            os.utime(run_lock.lock_path(db), (old, old))   # as if its owner had been killed
            lines = []
            second = run_lock.RunLock(db, report=lines.append)
            second.acquire(wait_seconds=0)
            self.assertIn("Taking over", lines[0])
            self.assertIn("taken over", first.check())   # the first run must stop writing
            self.assertIsNone(second.check())
            first.release()                              # leaves the second run's lock alone
            self.assertTrue(run_lock.lock_path(db).exists())
            second.release()
            self.assertFalse(run_lock.lock_path(db).exists())

    def test_stop_check_ends_the_pass_between_notes(self):
        import data_worker as W
        with tempfile.TemporaryDirectory() as folder:
            conn = D.create_db(Path(folder) / "t.db", ["fake@8192"])
            path = Path(folder) / "notes.csv"
            path.write_bytes(HEADER + b"".join(b"P,E%d,N%d,2020,T,note %d\n" % (i, i, i) for i in range(5)))
            D.ingest_csv(path, conn, report=lambda line: None)
            status = L.OllamaStatus("fake", True, True, "fake", ("fake",), "0", {})
            calls = []
            saved = W.ollama_status, W.load_model, W.llm_analysis
            W.ollama_status = lambda model=None, timeout=3.0: status
            W.load_model = lambda spec, timeout=None: L.LoadResult(0.0, None, None)
            W.llm_analysis = lambda id, note, model, attempt, rejected, observer=None: (calls.append(id), (answer(id=id), "{}"))[1]
            try:
                outcome = W.data_worker(conn, [L.ModelSpec("fake", 8192)], stop_check=lambda: "time is up" if len(calls) >= 2 else None)
            finally:
                W.ollama_status, W.load_model, W.llm_analysis = saved
            self.assertEqual(outcome.stopped, "time is up")
            self.assertEqual(calls, [1, 2])
            self.assertEqual([id for id, _ in D.pending_notes(conn, "fake@8192")], [3, 4, 5])
            conn.close()

    def test_an_export_cut_short_leaves_no_results_file(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            conn = ExportFilesTests().build(folder)
            real = E.cell
            E.cell = lambda field, value: (_ for _ in ()).throw(KeyboardInterrupt) if value == "P3" else real(field, value)
            try:
                with self.assertRaises(KeyboardInterrupt):
                    E.export_results(conn, str(folder / "output"), report=lambda line: None)
            finally:
                E.cell = real
            self.assertFalse((folder / "output" / E.EXPORT_NAME).exists())
            self.assertFalse((folder / "output" / E.REVIEW_EXPORT_NAME).exists())
            self.assertIsNotNone(E.export_results(conn, str(folder / "output"), report=lambda line: None))
            self.assertEqual(sorted(p.name for p in (folder / "output").iterdir()), [E.REVIEW_EXPORT_NAME, E.EXPORT_NAME])
            conn.close()

    def test_a_damaged_database_is_refused_and_a_sound_one_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "data.db"
            conn = D.create_db(db, ["m@8192"])
            path = Path(folder) / "notes.csv"
            path.write_bytes(HEADER + b"".join(b"P,E%d,N%d,2020,T,%s\n" % (i, i, b"x" * 3000) for i in range(300)))
            D.ingest_csv(path, conn, report=lambda line: None)
            conn.close()
            self.assertTrue(D.check_and_back_up(db, report=lambda line: None))
            copy = __import__("sqlite3").connect(D.backup_path(db))
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 300)
            copy.close()
            data = bytearray(db.read_bytes())
            data[4096 * 3 : 4096 * 3 + 200] = b"\xff" * 200   # scribble over a table page
            db.write_bytes(bytes(data))
            with self.assertRaises(D.DatabaseDamaged) as caught:
                D.check_and_back_up(db, report=lambda line: None)
            self.assertIn(str(D.backup_path(db)), str(caught.exception))

    def test_durations(self):
        from cli import parse_duration
        self.assertEqual(parse_duration("23.5"), 23.5 * 3600)
        self.assertEqual(parse_duration("23h30m"), 23.5 * 3600)
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("5400s"), 5400)
        self.assertEqual(parse_duration("1d"), 86400)
        with self.assertRaises(Exception):
            parse_duration("soon")


if __name__ == "__main__":
    unittest.main()

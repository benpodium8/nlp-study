"""
The database step of migrate.sh: brings a data.db written by nlp-study 2.0 to what 2.1
expects, so the next --analyze run only does the work 2.1 adds. Run by migrate.sh; it can
also be run by hand from the repo, with the venv's Python:

    python migrate_db.py data.db --plan                       what would change (read-only)
    python migrate_db.py data.db --apply --backup-to DIR      back up to DIR/data.db, then change it

What changes, and why:
  requeue   results of the study models that 2.0 recorded as failed without a model call
            because the note was too long for the window (LLM_Error starting with
            llm_analysis.TOO_LONG_PREFIX, LLM_Attempts 0) are cleared, so 2.1 shortens those
            notes (note_shortening) and sends them. Every other result is kept as it is:
            the prompt and the checks of a note that fits are unchanged in 2.1. Only rows 2.0
            wrote are cleared (InputReduced empty): a note 2.1 cannot shorten enough fails
            with the same prefix and stays failed.
  retire    the block of a model the study no longer uses (medgemma:27b@8192 since
            2026-10-16) is removed from the results table, so results.csv has the three
            study models' blocks only and the column letters in results-csv-guide.md hold.
            --keep-retired leaves such blocks in place (results.csv then shows them too).
  consensus the consensus block is cleared along with the changes above: it was built from the
            retired model's votes and without the shortened notes, and --analyze rebuilds it
            over every note. On its own (a database 2.1 has already run on) it is left alone.
  layout    the results table is rebuilt in the layout a fresh 2.1 database has: one block
            per study model in run order (llm_analysis.MODELS: the new mistral-small3.2
            block first, empty, then gemma4 and gpt-oss), each with the columns 2.1 adds
            (InputReduced, InputReduction, empty for results stored by 2.0), then the
            consensus block. The export lays the blocks out in table order, so without this
            the new model's block would come last and every letter in results-csv-guide.md
            after column X would be wrong. A kept retired block goes after the study blocks.

Nothing is changed before a full copy of the database has been written to --backup-to and
checked. The run lock (data.db.lock, run_lock.py) is held while the database is changed, so a
pipeline run cannot write at the same time. No note text is printed, only counts and note ids.

Exit status: 0 nothing to do (--plan) or done (--apply); 10 changes planned (--plan);
1 an error; 3 a run holds the lock.
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from database import (
    CONSENSUS_COLUMNS, CONSENSUS_LABEL, RESULTS_COLUMNS, backup_path, column_label, column_name, quoted,
)
from llm_analysis import MODELS, TOO_LONG_PREFIX
from run_lock import LockHeld, RunLock
from run_lock import status as lock_status

EXIT_CHANGES = 10
EXIT_LOCKED = 3


def item(verb: str, text: str):
    """One plan line, laid out like migrate.sh's own."""
    print(f"         {verb:<9} {text}")


def table_columns(conn, table: str) -> list[tuple[str, str]]:
    return [(row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({quoted(table)});")]


def model_name(label: str) -> str:
    """The model a label names ("gpt-oss:20b@8192" gives "gpt-oss:20b")."""
    name, at, _ = label.rpartition("@")
    return name if at else label


class Plan:
    """What --apply would do to the database, found without changing it."""

    def __init__(self, conn, keep_retired: bool):
        self.notes = conn.execute("SELECT COUNT(*) FROM notes;").fetchone()[0]
        self.results = conn.execute("SELECT COUNT(*) FROM results;").fetchone()[0]
        self.columns = table_columns(conn, "results")
        names = {name for name, _ in self.columns}
        found = []
        for name, _ in self.columns:
            label = column_label(name)
            if label is not None and label not in found:
                found.append(label)
        self.unlabelled = [field for field, _ in RESULTS_COLUMNS if field in names]
        self.wanted = [model.label for model in MODELS]
        self.present = [label for label in self.wanted if column_name("LLM_Failed", label) in names]
        wanted_names = {model.name for model in MODELS}
        self.retired, self.window_changed = [], []
        for label in found:
            if label == CONSENSUS_LABEL or label in self.wanted:
                continue
            # A study model under another window means NLP_CONTEXT_LENGTH differs from the run
            # that wrote it: that is not a retired model, and its answers are not dropped here.
            (self.window_changed if model_name(label) in wanted_names else self.retired).append(label)
        self.keep_retired = keep_retired
        self.drop = [] if keep_retired else list(self.retired)

        # The table 2.1 would have built: id, the study blocks in run order, whatever else is
        # kept (a retired block with --keep-retired, a block under another window, unlabelled
        # columns), then the consensus block. Columns are matched by name, so the data moves
        # with them; a column not there yet is created empty.
        types = dict(self.columns)
        study = [(column_name(field, label), sqltype) for label in self.wanted for field, sqltype in RESULTS_COLUMNS]
        consensus_block = [(column_name(field, CONSENSUS_LABEL), sqltype) for field, sqltype in CONSENSUS_COLUMNS]
        placed = {name for name, _ in study + consensus_block} | {"id"}
        dropped = {name for name, _ in self.columns if column_label(name) in self.drop}
        others = [(name, sqltype) for name, sqltype in self.columns if name not in placed and name not in dropped]
        self.layout = [(name, types.get(name, sqltype)) for name, sqltype in study + others + consensus_block]
        self.relayout = [name for name, _ in self.columns if name != "id"] != [name for name, _ in self.layout]

        self.analysed = {}
        for label in self.present + self.retired:
            self.analysed[label] = conn.execute(
                f"SELECT COUNT(*) FROM results WHERE {quoted(column_name('LLM_Failed', label))} IS NOT NULL;"
            ).fetchone()[0]

        # The notes 2.0 failed without a call because they did not fit the window. 2.1 writes
        # InputReduced on every result, so a row without it is 2.0's; a note 2.1 itself could not
        # shorten enough fails with the same prefix, but has InputReduced, and is left alone.
        self.requeue = {}
        for label in self.present:
            written_by_2_0 = (
                f"AND {quoted(column_name('InputReduced', label))} IS NULL "
                if column_name("InputReduced", label) in names else ""
            )
            ids = [row[0] for row in conn.execute(
                f"SELECT id FROM results WHERE {quoted(column_name('LLM_Error', label))} LIKE ? "
                f"AND COALESCE({quoted(column_name('LLM_Attempts', label))}, 0) = 0 {written_by_2_0}ORDER BY id;",
                (TOO_LONG_PREFIX + "%",),
            )]
            if ids:
                self.requeue[label] = ids

        consensus = column_name("ManualReviewRequired", CONSENSUS_LABEL)
        self.consensus = (
            conn.execute(f"SELECT COUNT(*) FROM results WHERE {quoted(consensus)} IS NOT NULL;").fetchone()[0]
            if consensus in names else 0
        )

    @property
    def changes(self) -> bool:
        # The consensus is cleared only along with a change to the models' data: on its own it is
        # current (a migrated database that 2.1 has since run on has a good one).
        return bool(self.requeue or self.drop or self.relayout)

    def show(self):
        item("found", f"{self.notes:,} note(s), {self.results:,} result row(s)")
        if self.unlabelled:
            item("note", "unlabelled result columns from before 2.0: the first 2.1 run labels them (database.create_db); "
                         "not touched here")
        for label in self.present:
            item("keep", f"{label}: {self.analysed[label]:,} analysed result(s) kept")
        for label in self.wanted:
            if label not in self.present:
                item("new", f"{label}: no block yet; the next --analyze run analyses every note with it")
        for label, ids in self.requeue.items():
            shown = ", ".join(str(i) for i in ids[:20]) + (", ..." if len(ids) > 20 else "")
            item("requeue", f"{label}: {len(ids):,} note(s) failed in 2.0 as too long for the window, without a call "
                            f"(ids {shown}); cleared so 2.1 shortens and sends them")
        for label in self.retired:
            if self.keep_retired:
                item("keep", f"{label}: retired model, {self.analysed[label]:,} result(s) left in place (--keep-retired); "
                             "results.csv will show its block too")
            else:
                width = sum(column_label(name) == label for name, _ in self.columns)
                item("retire", f"{label}: retired model, its block of {width} columns "
                               f"({self.analysed[label]:,} result(s)) removed; kept in the backup copy")
        for label in self.window_changed:
            item("keep", f"{label}: a study model under another window than this run's "
                         f"(NLP_CONTEXT_LENGTH?); left alone")
        if self.consensus and self.changes:
            item("clear", f"consensus block of {self.consensus:,} note(s); --analyze rebuilds it from the three study models")
        if self.relayout:
            added = len({name for name, _ in self.layout} - {name for name, _ in self.columns})
            item("layout", f"results table rebuilt as 2.1 lays it out: {', '.join(model_name(l) for l in self.wanted)} "
                           f"in run order, then the consensus ({added} empty column(s) added, InputReduced and "
                           f"InputReduction among them), so results.csv matches results-csv-guide.md")
        if not self.changes:
            item("keep", "the database is already in 2.1 shape")


def quick_check(conn) -> list[str]:
    return [row[0] for row in conn.execute("PRAGMA quick_check;").fetchmany(5)]


def back_up(conn, db_path: Path, backup_dir: Path) -> Path:
    """Copies the database to backup_dir/<name> (whole, via SQLite's backup API) and checks the copy."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / db_path.name
    partial = target.with_name(target.name + ".partial")
    if target.exists():
        raise SystemExit(f"error: {target} exists already; give another --backup-to")
    copy = sqlite3.connect(str(partial))
    try:
        conn.backup(copy)
        problems = quick_check(copy)
        counts = [copy.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0] for t in ("notes", "results")]
    finally:
        copy.close()
    original = [conn.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0] for t in ("notes", "results")]
    if problems != ["ok"] or counts != original:
        raise SystemExit(f"error: the backup copy {partial} did not check out ({problems}, rows {counts} vs {original}); "
                         "nothing was changed")
    partial.rename(target)
    return target


def apply(conn, plan: Plan):
    """Every change in one transaction: all of it lands, or none of it."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        for label, ids in plan.requeue.items():
            sets = ", ".join(f"{quoted(column_name(field, label))} = NULL" for field, _ in RESULTS_COLUMNS
                             if column_name(field, label) in {n for n, _ in plan.columns})
            conn.executemany(f"UPDATE results SET {sets} WHERE id = ?;", [(i,) for i in ids])
        if plan.changes:   # the whole block, whatever of it is filled; apply only runs with changes anyway
            names = {n for n, _ in plan.columns}
            sets = ", ".join(f"{quoted(column_name(field, CONSENSUS_LABEL))} = NULL" for field, _ in CONSENSUS_COLUMNS
                             if column_name(field, CONSENSUS_LABEL) in names)
            if sets:
                conn.execute(f"UPDATE results SET {sets};")
        if plan.relayout:
            # SQLite can neither reorder columns nor (before 3.35, which the Argon Python may not
            # have) drop one, so the table is rebuilt by copying, as database._label_legacy_columns does.
            existing = {name for name, _ in plan.columns}
            conn.execute("DROP TABLE IF EXISTS results_migrate;")
            conn.execute(
                "CREATE TABLE results_migrate (id INTEGER PRIMARY KEY"
                + "".join(f", {quoted(name)} {sqltype}" for name, sqltype in plan.layout)
                + ", FOREIGN KEY (id) REFERENCES notes(id));"
            )
            columns = ", ".join(["id"] + [quoted(name) for name, _ in plan.layout])
            select = ", ".join(["id"] + [quoted(name) if name in existing else "NULL" for name, _ in plan.layout])
            conn.execute(f"INSERT INTO results_migrate ({columns}) SELECT {select} FROM results;")
            conn.execute("DROP TABLE results;")
            conn.execute("ALTER TABLE results_migrate RENAME TO results;")
        conn.execute("COMMIT;")
    except BaseException:
        conn.execute("ROLLBACK;")
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path, help="the database, normally data.db in the repo")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="print what would change; change nothing")
    mode.add_argument("--apply", action="store_true", help="back up, then migrate")
    ap.add_argument("--backup-to", type=Path, help="directory the untouched copy is written to (required with --apply)")
    ap.add_argument("--keep-retired", action="store_true", help="leave the blocks of retired models in place")
    args = ap.parse_args()

    if not args.db.exists() or args.db.stat().st_size == 0:
        item("absent", f"no {args.db.name}: the first 2.1 run creates it")
        return 0
    if args.apply and args.backup_to is None:
        ap.error("--apply needs --backup-to")

    held = lock_status(args.db)
    if held and held.startswith("HELD"):
        print(f"error: {args.db}.lock is {held}; wait for that run to finish", file=sys.stderr)
        return EXIT_LOCKED

    mode_uri = f"file:{args.db}?mode=ro" if args.plan else str(args.db)
    conn = sqlite3.connect(mode_uri, uri=args.plan, isolation_level=None, timeout=300)
    try:
        tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")}
        if not {"notes", "results"} <= tables:
            print(f"error: {args.db} has no notes/results tables (tables: {', '.join(sorted(tables)) or 'none'}); "
                  "it is not a 2.0 database", file=sys.stderr)
            return 1
        plan = Plan(conn, args.keep_retired)
        if args.plan:
            plan.show()
            return EXIT_CHANGES if plan.changes else 0
        if not plan.changes:
            item("keep", "the database is already in 2.1 shape")
            return 0

        lock = RunLock(args.db)
        try:
            lock.acquire(wait_seconds=0)
        except LockHeld as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_LOCKED
        try:
            conn.execute("PRAGMA journal_mode=DELETE;")   # never WAL on NFS (database.create_db)
            problems = quick_check(conn)
            if problems != ["ok"]:
                print(f"error: {args.db} failed its integrity check: {'; '.join(problems)}; nothing was changed",
                      file=sys.stderr)
                return 1
            plan = Plan(conn, args.keep_retired)   # again, under the lock
            target = back_up(conn, args.db, args.backup_to)
            print(f"         backup    the 2.0 database, checked: {target}")
            apply(conn, plan)
            problems = quick_check(conn)
            after = Plan(conn, args.keep_retired)
            if problems != ["ok"] or after.changes or after.notes != plan.notes or after.results != plan.results:
                print(f"error: the migrated database does not check out ({problems}); restore it with:  cp {target} {args.db}",
                      file=sys.stderr)
                return 1
            stale = backup_path(args.db)
            if stale.exists():
                # A 2.0 copy a damaged-database message would point to; the next run writes a 2.1 one.
                shutil.move(str(stale), str(args.backup_to / stale.name))
            requeued = sum(len(ids) for ids in plan.requeue.values())
            print(f"         done      {requeued:,} result(s) requeued, {len(plan.drop)} retired block(s) removed, "
                  f"consensus cleared, "
                  f"table {'rebuilt in the 2.1 layout' if plan.relayout else 'already in the 2.1 layout'}; integrity ok")
        finally:
            lock.release()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

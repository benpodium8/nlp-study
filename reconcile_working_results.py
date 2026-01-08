import re
import unicodedata
from typing import Optional

from database import SELECT_ALL_WORKING_RESULTS_SQL


def normalize_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value)
    value = value.lower().strip()
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def reconcile_scope_type(scope_nlp: Optional[str], scope_llm: Optional[str]) -> Optional[str]:
    norm_llm = normalize_string(scope_llm)
    norm_nlp = normalize_string(scope_nlp)

    if norm_llm == norm_nlp:
        return scope_llm

    if norm_llm and not norm_nlp:
        return scope_llm

    return None


def reconcile_working_results(conn):
    print("Reconciling working results...")

    cursor = conn.execute(SELECT_ALL_WORKING_RESULTS_SQL)
    rows = cursor.fetchall()

    if not rows:
        print("No working results found to reconcile.")
        return

    for row in rows:
        (
            id,
            scope_nlp,
            scope_llm,
            biopsies_taken_nlp,
            biopsies_taken_llm,
            fellow_present_nlp,
            fellow_present_llm,
        ) = row

        final_scope_type = reconcile_scope_type(scope_nlp, scope_llm)

        reconciled = {
            "id": id,
            "ScopeType": final_scope_type,
            # numeric fields intentionally untouched
            "DuodenalBiopsiesTaken_NLP": biopsies_taken_nlp,
            "DuodenalBiopsiesTaken_LLM": biopsies_taken_llm,
            "FellowPresent_NLP": fellow_present_nlp,
            "FellowPresent_LLM": fellow_present_llm,
        }

        print(f"[RECONCILED] id={id} → {reconciled}")

    print("Reconciliation complete.")

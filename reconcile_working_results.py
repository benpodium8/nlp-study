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


def extract_model_tokens(value: Optional[str]) -> set[str]:
    if not value:
        return set()

    norm = normalize_string(value)
    return {
        token
        for token in norm.split()
        if any(char.isdigit() for char in token)
    }

def reconcile_scope_type(scope_nlp: Optional[str], scope_llm: Optional[str]) -> Optional[str]:
    norm_llm = normalize_string(scope_llm)
    norm_nlp = normalize_string(scope_nlp)

    # 1. Exact normalized match
    if norm_llm and norm_llm == norm_nlp:
        return scope_llm

    # 2. Model token agreement
    llm_models = extract_model_tokens(scope_llm)
    nlp_models = extract_model_tokens(scope_nlp)

    if llm_models and llm_models == nlp_models:
        # Prefer more descriptive source
        return scope_llm if scope_llm and len(scope_llm) >= len(scope_nlp or "") else scope_nlp

    # 3. One side present
    if norm_llm and not norm_nlp:
        return scope_llm

    if norm_nlp and not norm_llm:
        return scope_nlp

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
            ScopeType_NLP,
            ScopeType_LLM,
            NumberOfDuodenalBiopsies_NLP,
            NumberOfDuodenalBiopsies_LLM,
            DuodenalBiopsiesTaken_NLP,
            DuodenalBiopsiesTaken_LLM,
            FellowPresent_NLP,
            FellowPresent_LLM
        ) = row

        final_scope_type = reconcile_scope_type(ScopeType_NLP, ScopeType_LLM)

        reconciled = {
            "id": id,
            "ScopeType": final_scope_type,
            "ScopeType_NLP": ScopeType_NLP,
            "ScopeType_LLM": ScopeType_LLM,
            "NumberOfDuodenalBiopsies_NLP": NumberOfDuodenalBiopsies_NLP,
            "NumberOfDuodenalBiopsies_LLM": NumberOfDuodenalBiopsies_LLM,
            "DuodenalBiopsiesTaken_NLP": DuodenalBiopsiesTaken_NLP,
            "DuodenalBiopsiesTaken_LLM": DuodenalBiopsiesTaken_LLM,
            "FellowPresent_NLP": FellowPresent_NLP,
            "FellowPresent_LLM": FellowPresent_LLM,
        }

        print(f"[RECONCILED] id={id} → {reconciled}")

    print("Reconciliation complete.")

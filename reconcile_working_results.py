import re
import unicodedata
from typing import Optional

from database import (
    SELECT_ALL_WORKING_RESULTS_SQL,
    INSERT_FINAL_RESULT_SQL,
    check_final_result_exists
)


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
    if not norm:
        return set()

    return {
        token
        for token in norm.split()
        if any(char.isdigit() for char in token)
    }

def reconcile_scope_type(scope_nlp: Optional[str], scope_llm: Optional[str]) -> Optional[str]:
    norm_llm = normalize_string(scope_llm)
    norm_nlp = normalize_string(scope_nlp)

    if norm_llm and norm_llm == norm_nlp:
        return scope_llm
    
    if scope_llm and not scope_nlp:
        return scope_llm

    if scope_nlp and not scope_llm:
        return scope_nlp

    llm_models = extract_model_tokens(scope_llm)
    nlp_models = extract_model_tokens(scope_nlp)

    if llm_models and llm_models == nlp_models:
        return scope_llm if scope_llm and len(scope_llm) >= len(scope_nlp or "") else scope_nlp

    return None


def reconcile_working_results(conn):
    cursor = conn.execute(SELECT_ALL_WORKING_RESULTS_SQL)
    rows = cursor.fetchall()

    if not rows:
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
            FellowPresent_LLM,
            AllHardDataInAgreement,
            NLP_Failed,
            LLM_Failed,
        ) = row

        if check_final_result_exists(conn, id):
            continue

        final_scope_type = reconcile_scope_type(
            ScopeType_NLP,
            ScopeType_LLM,
        )

        numeric_mismatch = any([
            NumberOfDuodenalBiopsies_NLP != NumberOfDuodenalBiopsies_LLM,
            DuodenalBiopsiesTaken_NLP != DuodenalBiopsiesTaken_LLM,
            FellowPresent_NLP != FellowPresent_LLM,
        ])

        insert_null_row = (
            not AllHardDataInAgreement
            or NLP_Failed
            or LLM_Failed
            or numeric_mismatch
            or not final_scope_type
        )

        if insert_null_row:
            values = (
                id,
                None,
                None,
                None,
                None,
            )
        else:
            values = (
                id,
                final_scope_type,
                NumberOfDuodenalBiopsies_LLM,
                DuodenalBiopsiesTaken_LLM,
                FellowPresent_LLM,
            )

        conn.execute(INSERT_FINAL_RESULT_SQL, values)

    conn.commit()

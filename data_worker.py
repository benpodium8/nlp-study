from database import SELECT_ALL_SQL
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis, LLMAnalysisError
from display import print_nlp_llm_result

def data_worker(conn):
    print("Data worker started")

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_SQL)
        rows = cursor.fetchall()
        
        for row in rows:
            note = row[6]
            id = row[0]
            nlp_result = nlp_analysis(id, note)
            print_nlp_llm_result(nlp_result)

            # Run LLM with strict failure handling
            try:
                llm_result = llm_analysis(id, note)
                print_nlp_llm_result(llm_result)
            except LLMAnalysisError as e:
                print(f"[LLM ERROR] id={id} → {e}")
                # optionally write to a file/db for later review
                continue
            










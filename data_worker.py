from database import SELECT_ALL_NOTES_SQL, check_working_result_exists, insert_working_result
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis, LLMAnalysisError
from reconcile_working_results import reconcile_working_results

def data_worker(conn):
    """
    Processes all notes in the database: runs NLP and LLM analysis on each note,
    skipping notes that already have results, and stores the results in the database.
    
    Parameters:
        conn: Database connection object.
    """
    print("Data worker started")

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_NOTES_SQL)
        rows = cursor.fetchall()
        
        for row in rows:
            note = row[6]
            id = row[0]
            
            if check_working_result_exists(conn, id):
                print(f"Skipping id={id} (result already exists)")
                continue
            
            nlp_result = nlp_analysis(id, note)
            llm_result = None
            raw_llm_response = None
            max_retries = 5
            retry_count = 0
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    llm_result, raw_llm_response = llm_analysis(id, note)
                    success = True
                except LLMAnalysisError as e:
                    error_msg = str(e)
                    is_json_error = "Invalid JSON" in error_msg or "JSONDecodeError" in error_msg
                    
                    if is_json_error and retry_count < max_retries - 1:
                        retry_count += 1
                        print(f"[LLM JSON ERROR] id={id} → {e} (retry {retry_count}/{max_retries})")
                    else:
                        print(f"[LLM ERROR] id={id} → {e}")
                        if is_json_error:
                            print(f"[LLM ERROR] Failed after {max_retries} retry attempts")
                        break
            
            insert_working_result(conn, id, nlp_result, llm_result, raw_llm_response)
            print(f"Inserted working result for id={id}")

        print("Finished processing working results.")
        reconcile_working_results()
            










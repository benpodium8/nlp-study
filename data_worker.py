from database import SELECT_ALL_NOTES_SQL, check_working_result_exists, insert_working_result
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis, LLMAnalysisError

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
            
            # Default NLP result structure in case of failure
            default_nlp_result = {
                "id": id,
                "ScopeType": None,
                "Colonoscopy": 0,
                "ColonoscopyInformation": None,
                "Endoscopy": 0,
                "EndoscopyInformation": None,
                "NumberOfDuodenalBiopsies": 0,
                "DuodenalBiopsiesTaken": 0,
                "DuodenalBiopsiesInformation": None,
                "FellowPresent": 0,
                "FellowInformation": None,
            }
            
            # Try NLP analysis, use default if it fails
            nlp_failed = 0
            try:
                nlp_result = nlp_analysis(id, note)
            except Exception as e:
                print(f"[NLP ERROR] id={id} → {e}")
                nlp_result = default_nlp_result
                nlp_failed = 1
            
            # Try LLM analysis with retries
            llm_result = None
            raw_llm_response = None
            llm_failed = 0
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
                        llm_failed = 1
                        break
                except Exception as e:
                    # Catch any other unexpected exceptions from LLM analysis
                    print(f"[LLM ERROR] id={id} → Unexpected error: {e}")
                    llm_failed = 1
                    break
            
            # Always insert a row, even if both analyses failed
            insert_working_result(conn, id, nlp_result, llm_result, raw_llm_response, nlp_failed, llm_failed)
            print(f"Inserted working result for id={id}")

        print("Finished processing working results.")
            










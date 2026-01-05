from database import SELECT_ALL_SQL
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis
from display import print_nlp_results

def data_worker(conn):
    print("Data worker started")

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_SQL)
        rows = cursor.fetchall()
        
        for row in rows:
            note = row[6]
            id = row[0]
            nlp_result = nlp_analysis(id, note)
            print_nlp_results(nlp_result)
            
            llm_result = llm_analysis(id, note)
            










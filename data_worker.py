import spacy
import medspacy
from database import SELECT_ALL_SQL
from nlp_analysis import nlp_analysis
from llm_analysis import llm_analysis

def data_worker(conn):
    print("Data worker started")

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_SQL)
        rows = cursor.fetchall()
        
        for row in rows:
            note = row[6]
            id = row[0]
            nlp_result = nlp_analysis(id, note)
            llm_result = llm_analysis(id, note)
            










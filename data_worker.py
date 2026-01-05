import spacy
import medspacy
from database import SELECT_ALL_SQL

def nlp_analysis(id: int, note: str):
    print(id, note[0:10])
    return {
        "id": id,
        "note": note,
    }

def data_worker(conn=None):
    print("Data worker started")

    if conn is not None:
        cursor = conn.execute(SELECT_ALL_SQL)
        rows = cursor.fetchall()
        
        for row in rows:
            note = row[6]
            id = row[0]
            nlp_analysis(id, note)










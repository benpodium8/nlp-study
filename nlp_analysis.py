import spacy
import medspacy


def nlp_analysis(id: int, note: str):
    print({"id": id, "note": note})
    return {
        "id": id,
        "note": note,
    }
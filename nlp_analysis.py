def nlp_analysis(id: int, note: str):
    print(id, note[0:10])
    return {
        "id": id,
        "note": note,
    }
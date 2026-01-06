import ollama

MODEL = "gemma2:2b"


def llm_analysis(id: int, note: str):

    messages = [
        {
            'role': 'system',
            'content': 'You are a helpful assistant that explains technical concepts simply.'
        },
        {
            'role': 'user',
            'content': 'Explain how the internet works to a five-year-old.'
        }
    ]
    # Send the request to the local Ollama service
    response = ollama.chat(model='llama3', messages=messages)

    # Print the model's response
    print(response['message']['content'])


    return {
        "id": id,
        "note": note,
    }
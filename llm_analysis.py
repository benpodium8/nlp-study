import ollama

LOCAL_MODEL = "gemma2:2b"


def llm_analysis(id: int, note: str):

    messages = [
        {
            'role': 'system',
            'content': 
            f'''
            You are an API that parses through clinical notes and provides accurate and concise analysis. 
            Return a single JSON object that contains the required information from the parsed data.
            Replace all placeholder description values with the actual values found in the clinical note.
            Your response should start with {{ and end with }}. 

            This is the data that you need to provide:
            {{
                "ScopeType": "The type of endoscope used for each procedure. Here we are looking for the type of endoscope used for each procedure. This will be something like "The patient's esophagus was easily intubated with an Olympus H190 endoscope" with the "Olympus H190" being the part I want extracted. Some notes will mention a colonoscope or the type of scopes using in colonoscopy. We do not want this colonoscope here.",
                "Colonoscopy": "Was a colonoscopy performed yes or no. 1 for yes or 0 for no.",
                "ColonoscopyInformation": "Extra information about the colonoscopy if it was performed.",
                "Endoscopy": "Was an endoscopy performed yes or no. 1 for yes or 0 for no.",
                "EndoscopyInformation": "Extra information about the endoscopy if it was performed.",
                "NumberOfDuodenalBiopsies": "This is the most important field. Here we want the number of biopsies taken specifically from the part of the small intestine called the duodenum. Look for sentences like "6 biopsies were taken of the duodenum for histopathology." With the number being the only thing that needs to be reported. Watch for the term "bulb" or "bulb of the duodenum" this is also part of the duodenum and should be reported but may be included in the number reported earlier depending on the language used.",
                "DuodenalBiopsiesTaken": "Was at least one biopsy of the duodenum taken yes or no. 1 for yes or 0 for no.",
                "DuodenalBiopsiesInformation": "Extra information about duodenal biopsy or biopsies if taken",
                "FellowPresent": "Was someone at the stage of training known as fellowship was present. Typically this would be at the end of each note and their name along with the attending physicians name would be present yes or no. 1 for yes or 0 for no.",
                "FellowInformation": "Extra information about the fellow or fellows if present.",
            }}
            '''
        },
        {
            'role': 'user',
            'content': f'''
                    Parse this clinical note, following the line break and contained in double quotes: 
                    <br />
                    "{note}".
            '''
        }
    ]
    # Send the request to the local Ollama service
    response = ollama.chat(model=LOCAL_MODEL, messages=messages)

    # Print the model's response
    print(response['message']['content'])


    return {
        "id": id,
        "note": note,
    }
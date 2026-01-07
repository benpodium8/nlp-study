import sqlite3
from rich.console import Console
from rich.table import Table

from database import fetch_all_notes

def print_nlp_llm_result(result):
    """Prints formatted NLP/LLM analysis result to console. Returns None."""
    print(f"ID: {result['id']}")
    print(f"ScopeType: {result['ScopeType']}")
    print(f"Colonoscopy: {result['Colonoscopy']}")
    print(f"Endoscopy: {result['Endoscopy']}")
    print(f"Number of Duodenal Biopsies: {result['NumberOfDuodenalBiopsies']}")
    print(f"Duodenal Biopsies Taken: {result['DuodenalBiopsiesTaken']}")
    print(f"Fellow Present: {result['FellowPresent']}")
    print("-" * 50)

def print_db_contents(conn: sqlite3.Connection, include_note: bool = False):
    """Displays database contents as formatted table, optionally including notes. Returns None."""
    cursor = fetch_all_notes(conn)
    columns = [description[0] for description in cursor.description]
    
    note_index = columns.index("note")
    
    if include_note:
        display_columns = columns
    else:
        display_columns = [col for col in columns if col != "note"]

    console = Console()
    table = Table(title="Database contents", show_header=True, header_style="bold magenta")
    
    for col in display_columns:
        table.add_column(col)
    
    for row in cursor:
        if include_note:
            row_values = []
            for i, value in enumerate(row):
                if i == note_index:
                    note_value = str(value) if value is not None else ""
                    truncated_note = note_value[:30] + "..." if len(note_value) > 30 else note_value
                    row_values.append(truncated_note)
                else:
                    row_values.append(str(value) if value is not None else "")
            table.add_row(*row_values)
        else:
            filtered_row = [str(value) if value is not None else "" for i, value in enumerate(row) if i != note_index]
            table.add_row(*filtered_row)
    
    console.print("\n")
    console.print(table)

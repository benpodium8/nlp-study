import sqlite3
from rich.console import Console
from rich.table import Table

from database import fetch_all_notes

def print_nlp_results(nlp_result):
    print(f"ID: {nlp_result['id']}")
    print(f"ScopeType: {nlp_result['ScopeType']}")
    print(f"Colonoscopy: {nlp_result['Colonoscopy']}")
    print(f"Endoscopy: {nlp_result['Endoscopy']}")
    print(f"Number of Duodenal Biopsies: {nlp_result['NumberOfDuodenalBiopsies']}")
    print(f"Duodenal Biopsies Taken: {nlp_result['DuodenalBiopsiesTaken']}")
    print(f"Fellow Present: {nlp_result['FellowPresent']}")
    print("-" * 50)

def print_db_contents(conn: sqlite3.Connection, include_note: bool = False):
    cursor = fetch_all_notes(conn)
    columns = [description[0] for description in cursor.description]
    
    note_index = columns.index("note")
    
    if include_note:
        # Include all columns including note
        display_columns = columns
    else:
        # Exclude the 'note' column
        display_columns = [col for col in columns if col != "note"]

    console = Console()
    table = Table(title="Database contents", show_header=True, header_style="bold magenta")
    
    # Add columns to the table
    for col in display_columns:
        table.add_column(col)
    
    # Add rows to the table
    for row in cursor:
        if include_note:
            # Include all columns, truncate note to 30 chars
            row_values = []
            for i, value in enumerate(row):
                if i == note_index:
                    # Truncate note column to 30 characters
                    note_value = str(value) if value is not None else ""
                    truncated_note = note_value[:30] + "..." if len(note_value) > 30 else note_value
                    row_values.append(truncated_note)
                else:
                    row_values.append(str(value) if value is not None else "")
            table.add_row(*row_values)
        else:
            # Exclude the note column value
            filtered_row = [str(value) if value is not None else "" for i, value in enumerate(row) if i != note_index]
            table.add_row(*filtered_row)
    
    console.print("\n")
    console.print(table)

import sqlite3
from rich.console import Console
from rich.table import Table

from database import fetch_all_notes


def print_db_contents(conn: sqlite3.Connection):
    cursor = fetch_all_notes(conn)
    columns = [description[0] for description in cursor.description]
    
    # Exclude the 'note' column
    note_index = columns.index("note")
    filtered_columns = [col for col in columns if col != "note"]

    console = Console()
    table = Table(title="Database contents", show_header=True, header_style="bold magenta")
    
    # Add columns to the table
    for col in filtered_columns:
        table.add_column(col)
    
    # Add rows to the table
    for row in cursor:
        # Exclude the note column value
        filtered_row = [str(value) if value is not None else "" for i, value in enumerate(row) if i != note_index]
        table.add_row(*filtered_row)
    
    console.print("\n")
    console.print(table)

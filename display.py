import sqlite3
from rich.console import Console
from rich.table import Table

from database import fetch_all_notes

def print_nlp_llm_result(result):
    """
    Prints NLP or LLM analysis result fields to the console in a formatted manner.
    
    Parameters:
        result: Dictionary containing analysis result with fields: id, ScopeType, Colonoscopy,
                Endoscopy, NumberOfDuodenalBiopsies, DuodenalBiopsiesTaken, FellowPresent.
    """
    print(f"ID: {result['id']}")
    print(f"ScopeType: {result['ScopeType']}")
    print(f"Colonoscopy: {result['Colonoscopy']}")
    print(f"Endoscopy: {result['Endoscopy']}")
    print(f"Number of Duodenal Biopsies: {result['NumberOfDuodenalBiopsies']}")
    print(f"Duodenal Biopsies Taken: {result['DuodenalBiopsiesTaken']}")
    print(f"Fellow Present: {result['FellowPresent']}")
    print("-" * 50)

def print_db_contents(conn: sqlite3.Connection, include_note: bool = False):
    """
    Displays database contents in a formatted table using Rich library.
    Optionally includes or excludes the full note text (truncated if included).
    
    Parameters:
        conn: Database connection object.
        include_note: If True, includes note column (truncated to 30 chars); if False, excludes it.
    """
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


SELECT_WORKING_RESULTS_SQL = "SELECT * FROM working_results ORDER BY id;"


def print_working_results(conn: sqlite3.Connection):
    """
    Displays the working_results table in a formatted table using Rich library.
    Truncates long text fields (RawResponse_LLM and Information fields) for display.
    
    Parameters:
        conn: Database connection object.
    """
    cursor = conn.execute(SELECT_WORKING_RESULTS_SQL)
    columns = [description[0] for description in cursor.description]
    rows = cursor.fetchall()
    
    if not rows:
        console = Console()
        console.print("\n[bold yellow]No working results found in database.[/bold yellow]\n")
        return
    
    console = Console()
    table = Table(title="Working Results", show_header=True, header_style="bold cyan")
    
    # Add all columns
    for col in columns:
        table.add_column(col)
    
    for row in rows:
        row_values = []
        for i, value in enumerate(row):
            if value is None:
                row_values.append("")
            elif columns[i] == "RawResponse_LLM":
                str_value = str(value)
                if len(str_value) > 50:
                    row_values.append(str_value[:47] + "...")
                else:
                    row_values.append(str_value)
            elif "Information" in columns[i]:
                str_value = str(value)
                if len(str_value) > 40:
                    row_values.append(str_value[:37] + "...")
                else:
                    row_values.append(str_value)
            else:
                row_values.append(str(value))
        
        table.add_row(*row_values)
    
    console.print("\n")
    console.print(table)

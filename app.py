import argparse
from rich.console import Console

console = Console()
console.print("[bold cyan]Hello[/bold cyan] [underline red]World[/underline red]!")

parser = argparse.ArgumentParser(description="An NLP processor.")

parser.add_argument("filepath", type=str, help="The path of the file to process")

args = parser.parse_args()

print(f"Processing file: {args.filepath}")


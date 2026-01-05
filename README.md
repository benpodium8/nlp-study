# nlp-study

If at any point you get an error similar to
".venv\scripts\activate.ps1 cannot be loaded because running scripts is disabled on this system. for more information, see about_execution_policies at https:/go.microsoft.com/fwlink/?linkid=135170"

then you need to run

> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

Create virtual environment

> py -3 -m venv .venv

Activate virtual environment

> .venv\Scripts\activate

Install Dependencies:
> pip install -r requirements.txt
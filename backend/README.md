Use Change Policy for User to allow script execution
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

Create virtual environment
> py -3 -m venv .venv

Activate virtual environment
> .venv\Scripts\activate

Install flask
> pip install Flask

Run the app
> flask run
@echo off
setlocal
cd /d "%~dp0"

py -m pip install -r requirements.txt
echo.
echo HomeStock starts on:
echo http://127.0.0.1:8000
echo.
echo AI keys:
echo - Gemini recognition: gemini_api_key.txt
echo - Pollinations GPT Image reference edit: pollinations_api_key.txt
echo.
echo Open the same page on your phone using the local IP shown inside the app.
echo.
py -m uvicorn app:app --host 0.0.0.0 --port 8000

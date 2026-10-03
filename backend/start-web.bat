@echo off
rem Double-click to open contrib-buddy in your browser. Close this window to stop it.
cd /d "%~dp0"
start cmd /k "cd ../frontend && npm run dev"
uvicorn main:app --reload --port 8765
if errorlevel 1 pause

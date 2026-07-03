@echo off
cd /d %~dp0backend
echo Starting iMosque backend with auto-reload...
echo Watching: %cd%\app
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app --reload-include *.py --log-level info
pause

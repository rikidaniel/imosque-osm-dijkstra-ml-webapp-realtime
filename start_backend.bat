@echo off
cd /d %~dp0backend

powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { $listener=Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($listener) { exit 2 }; exit 1 }"
if errorlevel 2 goto unhealthy_port
if not errorlevel 1 goto already_running

echo Starting iMosque backend with auto-reload...
echo Watching: %cd%\app
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app --reload-include *.py --log-level info
pause
exit /b

:already_running
echo iMosque backend is already healthy on http://127.0.0.1:8000.
echo A second Uvicorn instance was not started.
exit /b 0

:unhealthy_port
echo Port 8000 is occupied, but the backend health check failed.
echo Close the old Uvicorn terminal/process before running this script again.
pause
exit /b 2

@echo off
rem Lance le moteur d analyse en direct (laisse cette fenetre ouverte pendant le match).
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python live_engine.py %*
) else (
  py live_engine.py %*
)
pause

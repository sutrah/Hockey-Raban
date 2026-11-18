@echo off
rem Glisse une video de match sur ce fichier pour lancer l analyse.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python hockey_analytics.py %*
) else (
  py hockey_analytics.py %*
)
pause

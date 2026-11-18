@echo off
rem Lance le moteur d analyse en direct (laisse cette fenetre ouverte pendant le match).
cd /d "%~dp0"
python live_engine.py %*
pause

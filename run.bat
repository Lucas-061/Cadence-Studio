@echo off
cd /d "%~dp0"
python cadence_studio.py
if errorlevel 1 pause

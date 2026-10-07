@echo off
rem Job Fit Scanner - run from this folder (used by Task Scheduler)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
"%~dp0venv\Scripts\python.exe" src\daily.py %* >> logs\daily.log 2>&1

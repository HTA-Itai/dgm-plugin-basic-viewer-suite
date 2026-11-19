@echo off
setlocal
"%~dp0runtime\python.exe" "%~dp0app\main.py"
if errorlevel 1 (
    echo.
    echo Viewer failed. Copy the error above when reporting this problem.
    pause
    exit /b 1
)

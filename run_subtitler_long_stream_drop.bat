@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv-win\Scripts\python.exe"
set "SCRIPT=%PROJECT_DIR%aviutl_subtitle.py"
set "CONFIG=%PROJECT_DIR%configs\local-long-stream.json"

if "%~1"=="" (
    echo Drag and drop a long stream video file onto this shortcut.
    echo.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "INPUT=%~1"
set "OUTPUT=%~dpn1-long-stream-local.exo"

echo Input:  %INPUT%
echo Output: %OUTPUT%
echo Workflow: local-long-stream
echo.

"%PYTHON_EXE%" "%SCRIPT%" "%INPUT%" --workflow local-long-stream --config "%CONFIG%" --output "%OUTPUT%"

echo.
if errorlevel 1 (
    echo Subtitler failed.
) else (
    echo Subtitler finished successfully.
)
echo.
pause

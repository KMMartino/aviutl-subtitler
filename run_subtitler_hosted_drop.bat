@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv-win\Scripts\python.exe"
set "SCRIPT=%PROJECT_DIR%aviutl_subtitle.py"
set "CONFIG=%PROJECT_DIR%configs\hosted.json"
set "ENV_FILE=%PROJECT_DIR%.env"

if "%~1"=="" (
    echo Drag and drop a video file onto this shortcut.
    echo.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "INPUT=%~1"
set "OUTPUT=%~dpn1-hosted-gemini35-gpt54mini.exo"

echo Input:  %INPUT%
echo Output: %OUTPUT%
echo Workflow: hosted
echo.

"%PYTHON_EXE%" "%SCRIPT%" "%INPUT%" --workflow hosted --config "%CONFIG%" --env-file "%ENV_FILE%" --output "%OUTPUT%"

echo.
if errorlevel 1 (
    echo Subtitler failed.
) else (
    echo Subtitler finished successfully.
)
echo.
pause

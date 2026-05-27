@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv-win\Scripts\python.exe"
set "SCRIPT=%PROJECT_DIR%aviutl_subtitle.py"
set "ENV_FILE=%PROJECT_DIR%.env"

set "TRANSCRIBER_BACKEND=gemini"
set "TRANSCRIPTION_MODEL=gemini-3.5-flash"
set "CLEANUP_BACKEND=openai"
set "CLEANUP_MODEL=gpt-5.4-mini"

if "%~1"=="" (
    echo Drag and drop a video file onto this shortcut.
    echo.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

if not exist "%ENV_FILE%" (
    echo Warning: .env was not found:
    echo %ENV_FILE%
    echo Hosted API keys may be missing.
    echo.
)

set "INPUT=%~1"
set "OUTPUT=%~dpn1-hosted-gemini35-gpt54mini.exo"
set "GLOSSARY=%~dp1glossary.txt"

echo Input:  %INPUT%
echo Output: %OUTPUT%
echo Transcription: %TRANSCRIBER_BACKEND% / %TRANSCRIPTION_MODEL%
echo Cleanup:       %CLEANUP_BACKEND% / %CLEANUP_MODEL%
if exist "%GLOSSARY%" echo Glossary: %GLOSSARY%
echo.

"%PYTHON_EXE%" "%SCRIPT%" "%INPUT%" ^
  --env-file "%ENV_FILE%" ^
  --transcriber-backend %TRANSCRIBER_BACKEND% ^
  --transcription-model %TRANSCRIPTION_MODEL% ^
  --cleanup-backend %CLEANUP_BACKEND% ^
  --cleanup-api-model %CLEANUP_MODEL% ^
  --tuning-profile hosted ^
  --transcription-workers 4 ^
  --chain-split-workers 6 ^
  --cleanup-workers 8 ^
  --cleanup-window-subtitles 8 ^
  --skip-final-review ^
  --offline-model-cache ^
  --transcription-max-split-depth 2 ^
  --alignment-max-split-depth 4 ^
  --profile ^
  --llm-split-diagnostics ^
  --llm-split-planning cleanup-model ^
  --regroup-gap-sec 0.5 ^
  --chain-lead-in-sec 0.08 ^
  --audio-track 1 ^
  --language ja ^
  -o "%OUTPUT%"

echo.
if errorlevel 1 (
    echo Subtitler failed.
) else (
    echo Subtitler finished successfully.
)
echo.
pause

@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv-win\Scripts\python.exe"
set "SCRIPT=%PROJECT_DIR%aviutl_subtitle.py"
set "MODEL=C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf"
set "MMPROJ=C:\coding\0_models\gemma\projectors\proj-for-q6.gguf"
set "CLEANUP_MODEL="

if "%~1"=="" (
    echo Drag and drop a video file onto this shortcut.
    echo.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo Gemma model not found:
    echo %MODEL%
    echo.
    pause
    exit /b 1
)

if not exist "%MMPROJ%" (
    echo Gemma projector not found:
    echo %MMPROJ%
    echo.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

set "INPUT=%~1"
set "OUTPUT=%~dpn1.exo"
set "GLOSSARY=%~dp1glossary.txt"
set "CLEANUP_MODEL=C:\coding\0_models\qwen\qwen3-14b-q6\Qwen3-14B-Q6_K.gguf"


echo Input:  %INPUT%
echo Output: %OUTPUT%
echo Model:  %MODEL%
echo MMProj: %MMPROJ%
if exist "%GLOSSARY%" echo Glossary: %GLOSSARY%
if not "%CLEANUP_MODEL%"=="" echo Cleanup model: %CLEANUP_MODEL%
echo.

set "CLEANUP_ARGS="
if not "%CLEANUP_MODEL%"=="" (
    if exist "%CLEANUP_MODEL%" (
        set "CLEANUP_ARGS=--cleanup-model "%CLEANUP_MODEL%" --cleanup-ctx-size 32768 --llm-split-planning cleanup-model"
    ) else (
        echo Cleanup model not found:
        echo %CLEANUP_MODEL%
        echo.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" "%SCRIPT%" "%INPUT%" ^
  --model "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --offline-model-cache ^
  --transcription-max-split-depth 2 ^
  --alignment-max-split-depth 4 ^
  --profile ^
  --llm-split-diagnostics ^
  --regroup-gap-sec 0.5 ^
  --chain-lead-in-sec 0.08 ^
  --audio-track 1 ^
  --language ja ^
  -o "%OUTPUT%" ^
  %CLEANUP_ARGS%

echo.
if errorlevel 1 (
    echo Subtitler failed.
) else (
    echo Subtitler finished successfully.
)
echo.
pause


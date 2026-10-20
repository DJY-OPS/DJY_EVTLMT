@echo off
set "DJY_UPLOAD_PY=%USERPROFILE%\telemetry-tools\venv\Scripts\python.exe"
if not exist "%DJY_UPLOAD_PY%" (
    echo Python environment not found: %DJY_UPLOAD_PY%
    pause
    exit /b 1
)
"%DJY_UPLOAD_PY%" "%~dp0scripts\esp_uploader.py"
if errorlevel 1 pause

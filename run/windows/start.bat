@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  AURA — Windows Launcher
::  Checks Python, creates/activates venv, installs deps,
::  trains missing models, then starts the server.
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "AURA_DIR=%SCRIPT_DIR%..\.."

:: Resolve to absolute path
pushd "%AURA_DIR%"
set "AURA_DIR=%CD%"
popd

set "VENV_DIR=%AURA_DIR%\.venv"
set "REQ_FILE=%AURA_DIR%\requirements.txt"
set "MODELS_DIR=%AURA_DIR%\models"

echo.
echo  ==========================================
echo   AURA  --  ECLSS Predictive Maintenance
echo  ==========================================
echo.

:: ── 1. Locate Python (3.10+) ────────────────────────────────
echo [1/6] Checking Python installation...

set "PYTHON_CMD="

:: Prefer python3 if available, then python
for %%P in (python3 python py) do (
    if not defined PYTHON_CMD (
        where %%P >nul 2>&1
        if !ERRORLEVEL! == 0 (
            for /f "tokens=*" %%V in ('%%P --version 2^>^&1') do set "PY_VER=%%V"
            echo     Found: !PY_VER! ^(%%P^)
            set "PYTHON_CMD=%%P"
        )
    )
)

if not defined PYTHON_CMD (
    echo.
    echo  [ERROR] Python not found in PATH.
    echo  Please install Python 3.10 or later from https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Verify version is >= 3.10
for /f "tokens=2 delims= " %%V in ('!PYTHON_CMD! --version 2^>^&1') do set "PY_FULL=%%V"
for /f "tokens=1,2 delims=." %%A in ("!PY_FULL!") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)

if !PY_MAJOR! LSS 3 (
    echo  [ERROR] Python 3.10+ required. Found !PY_FULL!
    pause & exit /b 1
)
if !PY_MAJOR! == 3 if !PY_MINOR! LSS 10 (
    echo  [ERROR] Python 3.10+ required. Found !PY_FULL!
    pause & exit /b 1
)

echo     OK ^(!PY_FULL!^)

:: ── 2. Create / activate virtualenv ────────────────────────
echo.
echo [2/6] Checking virtual environment...

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo     Creating .venv at %VENV_DIR%
    !PYTHON_CMD! -m venv "%VENV_DIR%"
    if !ERRORLEVEL! NEQ 0 (
        echo  [ERROR] Failed to create virtual environment.
        pause & exit /b 1
    )
    echo     Created.
) else (
    echo     Found existing .venv
)

call "%VENV_DIR%\Scripts\activate.bat"
echo     Activated.

:: Use the venv python from here on
set "PYTHON_CMD=python"

:: ── 3. Install / upgrade dependencies ──────────────────────
echo.
echo [3/6] Installing dependencies from requirements.txt...

if not exist "%REQ_FILE%" (
    echo  [ERROR] requirements.txt not found at %REQ_FILE%
    pause & exit /b 1
)

:: Use a hash file to skip reinstall when requirements haven't changed
set "HASH_FILE=%VENV_DIR%\.req_hash"
set "CURRENT_HASH="
for /f "tokens=*" %%H in ('certutil -hashfile "%REQ_FILE%" SHA256 2^>nul ^| findstr /v "hash"') do (
    if not defined CURRENT_HASH set "CURRENT_HASH=%%H"
)

set "STORED_HASH="
if exist "%HASH_FILE%" (
    for /f "tokens=*" %%H in ('type "%HASH_FILE%"') do set "STORED_HASH=%%H"
)

if "!CURRENT_HASH!" == "!STORED_HASH!" (
    echo     Requirements unchanged, skipping install.
) else (
    echo     Installing/updating packages...
    %PYTHON_CMD% -m pip install --upgrade pip --quiet
    %PYTHON_CMD% -m pip install -r "%REQ_FILE%"
    if !ERRORLEVEL! NEQ 0 (
        echo  [ERROR] pip install failed.
        pause & exit /b 1
    )
    echo !CURRENT_HASH!> "%HASH_FILE%"
    echo     Done.
)

:: ── 4. Train missing models ─────────────────────────────────
echo.
echo [4/6] Checking ML models...

set "ALL_MODELS=1"
for %%M in (isolationForestModel.joblib randomForestModel.joblib lstmModel.pt dqnModel.pt) do (
    if not exist "%MODELS_DIR%\%%M" (
        set "ALL_MODELS=0"
        echo     Missing: %%M
    )
)

if !ALL_MODELS! == 0 (
    echo     Training missing models ^(this may take a few minutes^)...
    cd /d "%AURA_DIR%"

    if not exist "%MODELS_DIR%\isolationForestModel.joblib" (
        echo     -- Training Isolation Forest...
        set PYTHONIOENCODING=utf-8
        %PYTHON_CMD% -m scripts.train_isolation_forest
        if !ERRORLEVEL! NEQ 0 ( echo  [ERROR] IF training failed. & pause & exit /b 1 )
    )

    if not exist "%MODELS_DIR%\randomForestModel.joblib" (
        echo     -- Training Random Forest...
        set PYTHONIOENCODING=utf-8
        %PYTHON_CMD% -m scripts.train_random_forest
        if !ERRORLEVEL! NEQ 0 ( echo  [ERROR] RF training failed. & pause & exit /b 1 )
    )

    if not exist "%MODELS_DIR%\lstmModel.pt" (
        echo     -- Training LSTM ^(patience required^)...
        set PYTHONIOENCODING=utf-8
        %PYTHON_CMD% -m scripts.train_lstm
        if !ERRORLEVEL! NEQ 0 ( echo  [ERROR] LSTM training failed. & pause & exit /b 1 )
    )

    if not exist "%MODELS_DIR%\dqnModel.pt" (
        echo     -- Training DQN ^(patience required^)...
        set PYTHONIOENCODING=utf-8
        %PYTHON_CMD% -m scripts.train_dqn
        if !ERRORLEVEL! NEQ 0 ( echo  [ERROR] DQN training failed. & pause & exit /b 1 )
    )

    echo     All models trained.
) else (
    echo     All models present.
)

:: ── 5. Run test suite ───────────────────────────────────────
echo.
echo [5/6] Running pre-flight tests...

cd /d "%AURA_DIR%"
set PYTHONIOENCODING=utf-8
%PYTHON_CMD% -m pytest tests/ -v --tb=short -q
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo     [WARN] Some tests failed. Server will still start.
    echo     Investigate failures before production use.
)

:: ── 6. Start AURA server ────────────────────────────────────
echo.
echo [6/6] Starting AURA server...
echo.
echo  Access the dashboard at:  http://localhost:8000
echo  Press Ctrl+C to stop.
echo.
echo  ------------------------------------------

cd /d "%AURA_DIR%"
set PYTHONIOENCODING=utf-8

%PYTHON_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

:: If server exits non-zero, pause so the user can read errors
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo  [ERROR] Server exited with code !ERRORLEVEL!
    pause
)

endlocal

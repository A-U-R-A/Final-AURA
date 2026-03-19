AURA — Plug-and-Play Launchers
==============================

Windows
-------
  Double-click:  Run\windows\start.bat
  Or from a terminal:
    cd path\to\AURA
    Run\windows\start.bat

Linux / macOS
-------------
  First run only — make the script executable:
    chmod +x Run/linux/start.sh

  Then launch:
    ./Run/linux/start.sh

  Or without chmod:
    bash Run/linux/start.sh

What the scripts do (in order)
-------------------------------
  1. Locate Python 3.10+  (errors out with install instructions if missing)
  2. Create .venv in the AURA root if it doesn't exist
  3. Activate the venv
  4. Install / update packages from requirements.txt
     (skipped automatically when requirements.txt hasn't changed)
  5. Train any missing ML models  (IF, RF, LSTM, DQN)
     (skipped if all four model files are already present in models/)
  6. Start the FastAPI server at http://localhost:8000

Dashboard URL
-------------
  http://localhost:8000

Notes
-----
  - The server runs with --reload (auto-restarts on code changes)
  - To use a different port, edit the last line of the script and
    change --port 8000 to the desired port
  - Ollama must be running separately for the AI Analyst tab
    (ollama serve  or the Ollama desktop app)

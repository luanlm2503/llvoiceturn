$ErrorActionPreference = "Stop"
$python = "C:\Users\manhl\voxcpm\.venv\Scripts\python.exe"
$env:HF_HOME = "C:\Users\manhl\voxcpm\hf_cache"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $python)) {
    throw "VoxCPM virtual environment not found: $python"
}

& $python -m uvicorn app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port 7861

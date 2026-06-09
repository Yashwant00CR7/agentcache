# Creates .venv in the project directory and installs all dependencies
$venvDir = ".venv"

if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venvDir
}

Write-Host "Installing dependencies..."
& "$venvDir\Scripts\pip.exe" install -r requirements.txt

Write-Host ""
Write-Host "Done. Activate with:"
Write-Host "  .\.venv\Scripts\Activate.ps1"

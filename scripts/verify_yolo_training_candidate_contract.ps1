$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "python" }

$testArgs = @(
  "-m", "pytest",
  "labelu/tests/e2e/test_training_ui_engineering_hardening_frontend.py",
  "labelu/tests/e2e/test_yolo_training_eligible_tasks_frontend.py",
  "labelu/tests/internal/adapter/routers/test_yolo_train_eligible_tasks.py",
  "-q"
)

Push-Location $repoRoot
try {
  & $pythonExe @testArgs
  exit $LASTEXITCODE
} finally {
  Pop-Location
}

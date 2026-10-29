$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcRoot = Join-Path $repoRoot "skills\\common"
$dstRoot = "C:\\Users\\deng5\\.codex\\skills"
$generator = "C:\\Users\\deng5\\.codex\\skills\\.system\\skill-creator\\scripts\\generate_openai_yaml.py"
$validator = "C:\\Users\\deng5\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py"

$env:PYTHONUTF8 = "1"

$skills = @(
    @{
        Name = "frontend-ui-pro"
        Display = "Frontend UI Pro"
        Desc = "Business logic to polished professional UI"
        Prompt = "Use `$frontend-ui-pro to turn this business logic into a professional frontend UI."
    },
    @{
        Name = "frontend-ui-pro-enterprise-admin"
        Display = "Enterprise Admin UI"
        Desc = "Professional enterprise admin pages and config centers"
        Prompt = "Use `$frontend-ui-pro-enterprise-admin to design this page as a professional enterprise admin interface."
    },
    @{
        Name = "frontend-ui-pro-data-console"
        Display = "Data Console UI"
        Desc = "Professional dashboards, consoles, and workflow workbenches"
        Prompt = "Use `$frontend-ui-pro-data-console to design this page as a professional data console or operational workbench."
    }
)

foreach ($skill in $skills) {
    $src = Join-Path $srcRoot $skill.Name
    $dst = Join-Path $dstRoot $skill.Name

    if (-not (Test-Path $src)) {
        throw "Missing source skill: $src"
    }

    if (Test-Path $dst) {
        Remove-Item -LiteralPath $dst -Recurse -Force
    }

    Copy-Item -LiteralPath $src -Destination $dst -Recurse

    python $generator $dst `
        --interface "display_name=$($skill.Display)" `
        --interface "short_description=$($skill.Desc)" `
        --interface "default_prompt=$($skill.Prompt)"

    python $validator $dst
}

Write-Output "Installed frontend UI skills to $dstRoot"

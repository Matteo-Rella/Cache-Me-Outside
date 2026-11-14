<#
.SYNOPSIS
Starts Codex in the existing Rewind checkout with its Python environment.
.EXAMPLE
.\tools\start_codex.ps1
.EXAMPLE
.\tools\start_codex.ps1 -Role T2 -PrintOnly
#>
[CmdletBinding()]
param(
    [ValidateSet('Coordinator', 'T1', 'T2', 'T3', 'T4')]
    [string]$Role = 'Coordinator',
    [string]$RepoPath = (Join-Path $env:USERPROFILE 'Documents\GitHub\Cache-Me-Outside'),
    [switch]$PrintOnly
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $RepoPath).Path
foreach ($required in @('AGENTS.md', 'docs\handover-codex.md', 'docs\round5-prompts.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $repo $required) -PathType Leaf)) {
        throw "Missing Rewind file: $required"
    }
}
$pythonScripts = Join-Path (Split-Path -Parent $repo) 'cmo-venv\Scripts'
if (-not (Test-Path -LiteralPath (Join-Path $pythonScripts 'python.exe') -PathType Leaf)) {
    throw "Python environment not found at $pythonScripts"
}
$codexCommand = Get-Command codex.exe -ErrorAction SilentlyContinue
if (-not $codexCommand) {
    throw 'Codex CLI was not found. Open this repository in the Codex app, or add Codex CLI to PATH.'
}

$tasks = @{
    Coordinator = 'Coordinate the takeover in this session. First check current changes and the handover findings, then implement the missing LLM/settings foundations for Round 5 before attempting the dependent chat and slides integration. Work sequentially unless the user requests parallel sessions. Keep the transcript contract compatible with the teammate transcriber.'
    T1 = 'Continue the T1 web-interface task from docs/round5-prompts.md. Own web/ and NOTES-web.md only. Preserve the existing styling rules and report missing backend contracts without editing backend files.'
    T2 = 'Continue the T2 task from docs/round5-prompts.md. Own rewind/slides.py, rewind/search.py, rewind/llm.py, rewind/settings.py, their listed tests and NOTES-search.md. Start with the missing LLM chat, purpose selection, estimates and settings fields, then slides. Append dependencies only.'
    T3 = 'Continue the T3 API/store/ingest task from docs/round5-prompts.md. Own api.py, rewind/store.py, rewind/ingest.py, their listed tests and NOTES-store.md. Check existing Round 5 work before changing it; record dependencies on T2 and T4.'
    T4 = 'Continue the T4 content/chat task from docs/round5-prompts.md. Own rewind/snippet_maker.py, rewind/context_package.py, rewind/chat.py, their tests/evaluations, tools/eval_chat.py, lecture content and NOTES-lectures.md. Check the existing verification and chat code first. Add the missing chat evaluation coverage without modifying T2-owned files.'
}

$prompt = @"
Take over Rewind from the existing Claude Code work. Read AGENTS.md, docs/handover-codex.md, the relevant NOTES files and docs/round5-prompts.md first.

$($tasks[$Role])

The checkout contains substantial uncommitted and untracked work. Preserve it. Do not reset, clean, discard, stage, commit, push, change branches, stop other sessions, or change stored AI settings as part of startup. The current user request and handover supersede historical launcher commands and historical automatic commit/push instructions. Never use bypass-approval flags. Do not print credentials. Mock AI in tests; do not make paid AI calls or regenerate lecture data without a current task explicitly authorizing them. This coding-assistant change does not change Rewind's configured runtime AI provider. Use the sibling cmo-venv Python. Report what changed and what was verified.
"@

if ($PrintOnly) {
    Write-Output "Repository: $repo"
    Write-Output "Python: $(Join-Path $pythonScripts 'python.exe')"
    Write-Output "Codex: $($codexCommand.Source)"
    Write-Output 'Options: --sandbox workspace-write --ask-for-approval on-request --no-alt-screen'
    Write-Output "Role: $Role"
    Write-Output $prompt
    return
}

$env:VIRTUAL_ENV = Split-Path -Parent $pythonScripts
$env:PATH = "$pythonScripts;$env:PATH"
Set-Location -LiteralPath $repo
& $codexCommand.Source --cd $repo --sandbox workspace-write --ask-for-approval on-request --no-alt-screen $prompt
exit $LASTEXITCODE

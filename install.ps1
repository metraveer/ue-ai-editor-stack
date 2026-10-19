<#
.SYNOPSIS
  Installs the UE AI Editor Stack (Unreal MCP + engine AI toolsets + VibeUE + Claude Code wiring). Safe to re-run.
.DESCRIPTION
  1) Builds VibeUE from GitHub for the chosen engine and installs it to Engine\Plugins\Marketplace\VibeUE
  2) Installs AIEditorStack - an engine-wide, code-free switch that enables Unreal MCP, the engine AI toolsets
     and VibeUE in every project (editor only; nothing is packaged into games)
  3) Turns on MCP server auto-start for all projects (per-user INI)
  4) Configures Claude Code: user-scope MCP server "unreal-mcp", Epic's plugin, managed block in ~/.claude/CLAUDE.md
.EXAMPLE
  .\install.ps1                                                   # newest UE found in the Epic Games Launcher
  .\install.ps1 -EngineDir "C:\Program Files\Epic Games\UE_5.9"
  .\install.ps1 -SkipVibeUE                                       # stack + config only
#>
[CmdletBinding()]
param(
  [string]$EngineDir,
  [switch]$SkipVibeUE,
  [switch]$SkipClaude,
  [string]$VibeUERepo = 'https://github.com/kevinpbuckley/VibeUE.git',
  [string]$VibeUEBranch,
  [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Utf8 = New-Object System.Text.UTF8Encoding($false)
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Native([scriptblock]$Cmd) {
  # git/claude write progress to stderr - only the exit code matters
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Cmd 2>&1 | ForEach-Object { "    $_" } | Out-Host; return $LASTEXITCODE } finally { $ErrorActionPreference = $old }
}

# ---------- 0. Engine ----------
if (-not $EngineDir) {
  $dat = 'C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat'
  if (-not (Test-Path $dat)) { throw "Epic Games Launcher not found. Pass -EngineDir <UE folder>" }
  $ue = (Get-Content $dat -Raw | ConvertFrom-Json).InstallationList | Where-Object { $_.AppName -match '^UE_\d+\.\d+$' } |
        Sort-Object { [version]($_.AppName -replace '^UE_', '') } | Select-Object -Last 1
  if (-not $ue) { throw "No Unreal Engine installed via the Epic Games Launcher. Pass -EngineDir" }
  $EngineDir = $ue.InstallLocation
}
$EngineDir = $EngineDir.TrimEnd('\')
if (-not (Test-Path "$EngineDir\Engine\Build\Build.version")) { throw "Not an Unreal Engine folder: $EngineDir" }
$bv = Get-Content "$EngineDir\Engine\Build\Build.version" -Raw | ConvertFrom-Json
$EngineVer = "$($bv.MajorVersion).$($bv.MinorVersion)"
if ([version]$EngineVer -lt [version]'5.8') { throw "Unreal Engine 5.8+ is required (native Unreal MCP). Found $EngineVer" }
$Market = "$EngineDir\Engine\Plugins\Marketplace"
Step "Unreal Engine $EngineVer.$($bv.PatchVersion): $EngineDir"
New-Item -ItemType Directory -Force $Market | Out-Null

# ---------- 1. VibeUE ----------
if (-not $SkipVibeUE) {
  Step "VibeUE: building for UE $EngineVer"
  $busy = Get-Process UnrealEditor -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($EngineDir, 'OrdinalIgnoreCase') }
  if ($busy) { throw "Close Unreal Editor $EngineVer first (its DLLs are locked), or run with -SkipVibeUE" }
  $work = "$env:LOCALAPPDATA\ue-ai-editor-stack"
  $src  = "$work\src\VibeUE"
  $pkg  = "$work\build\VibeUE-UE$EngineVer"
  $log  = "$work\build\VibeUE-UE$EngineVer.log"
  New-Item -ItemType Directory -Force "$work\src", "$work\build" | Out-Null
  if (Test-Path "$src\.git") {
    if ($VibeUEBranch) { $null = Native { git -C $src fetch --depth 1 origin $VibeUEBranch }; $null = Native { git -C $src checkout -B $VibeUEBranch FETCH_HEAD } }
    elseif ((Native { git -C $src pull --ff-only }) -ne 0) { throw "git pull of VibeUE failed" }
  } else {
    $cloneArgs = @('clone', '--depth', '1'); if ($VibeUEBranch) { $cloneArgs += @('--branch', $VibeUEBranch) }; $cloneArgs += @($VibeUERepo, $src)
    if ((Native { git @cloneArgs }) -ne 0) { throw "git clone of VibeUE failed" }
  }
  Ok ("VibeUE commit: " + (git -C $src log -1 --format='%h %cs %s'))
  Remove-Item $pkg -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "    RunUAT BuildPlugin... (1-5 min, log: $log)"
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  & "$EngineDir\Engine\Build\BatchFiles\RunUAT.bat" BuildPlugin "-Plugin=$src\VibeUE.uplugin" "-Package=$pkg" -TargetPlatforms=Win64 *> $log
  $code = $LASTEXITCODE; $ErrorActionPreference = $old
  if ($code -ne 0) {
    Get-Content $log -Tail 25 | Out-Host
    throw "VibeUE build failed (exit $code). Usual causes: no VS 2022 17.14+/VS 2026 C++ toolchain or no .NET Framework 4.8 SDK. Log: $log"
  }
  $dst = "$Market\VibeUE"
  if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
  robocopy $pkg $dst /E /NFL /NDL /NJH /NJS /NP /XD HostProject | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Could not copy VibeUE to $dst (robocopy $LASTEXITCODE)" }
  $u = "$dst\VibeUE.uplugin"
  [IO.File]::WriteAllText($u, ([IO.File]::ReadAllText($u, $Utf8) -replace '"Installed"\s*:\s*false', '"Installed": true'), $Utf8)
  Ok "VibeUE installed: $dst"
}

# ---------- 2. AIEditorStack ----------
Step "AIEditorStack (engine-wide switch + Python toolsets)"
$tpl = "$Root\AIEditorStack\AIEditorStack.uplugin"
$j = [IO.File]::ReadAllText($tpl, $Utf8) | ConvertFrom-Json
$have = @{}
Get-ChildItem "$EngineDir\Engine\Plugins" -Recurse -Filter *.uplugin -ErrorAction SilentlyContinue | ForEach-Object { $have[$_.BaseName] = $true }
$missing = @($j.Plugins | Where-Object { -not $have[$_.Name] } | ForEach-Object { $_.Name })
$dstDir = "$Market\AIEditorStack"
robocopy "$Root\AIEditorStack" $dstDir /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy AIEditorStack to $dstDir (robocopy $LASTEXITCODE)" }
if ($missing.Count -gt 0) {
  Warn ("Not in this engine, skipped (otherwise the editor won't start): " + ($missing -join ', '))
  $j.Plugins = @($j.Plugins | Where-Object { $have[$_.Name] })
  [IO.File]::WriteAllText("$dstDir\AIEditorStack.uplugin", (($j | ConvertTo-Json -Depth 10) -replace '\\u0027', "'"), $Utf8)
}
Ok "Enables $(@($j.Plugins).Count) plugins -> $dstDir"

# ---------- 3. MCP server auto-start ----------
Step "MCP server auto-start for all projects"
$iniDir = "$env:LOCALAPPDATA\Unreal Engine\Engine\Config"
$ini = "$iniDir\UserEditorPerProjectUserSettings.ini"
New-Item -ItemType Directory -Force $iniDir | Out-Null
$block = [IO.File]::ReadAllText("$Root\config\UserEditorPerProjectUserSettings.ini", $Utf8) -replace 'ServerPortNumber=\d+', "ServerPortNumber=$Port"
if (Test-Path $ini) {
  if ([IO.File]::ReadAllText($ini, $Utf8).Contains('[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]')) { Ok "already configured: $ini" }
  else { [IO.File]::AppendAllText($ini, "`r`n$block", $Utf8); Ok "appended to $ini" }
} else { [IO.File]::WriteAllText($ini, $block, $Utf8); Ok "created $ini" }

# ---------- 4. Claude Code ----------
if (-not $SkipClaude) {
  Step "Claude Code"
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Warn "'claude' is not on PATH - skipping MCP server and plugin registration" }
  else {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $null = claude mcp get unreal-mcp 2>&1; $has = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $old
    if ($has) { Ok "MCP server 'unreal-mcp' already registered" }
    else { $null = Native { claude mcp add --transport http --scope user unreal-mcp "http://127.0.0.1:$Port/mcp" } }
    if ((claude plugin list 2>&1 | Out-String) -match 'unreal-engine-skills-for-claude-code') { Ok "Epic plugin already installed" }
    else {
      $null = Native { claude plugin marketplace update claude-plugins-official }
      $null = Native { claude plugin install unreal-engine-skills-for-claude-code@claude-plugins-official --scope user }
    }
  }
  $md = "$env:USERPROFILE\.claude\CLAUDE.md"
  New-Item -ItemType Directory -Force (Split-Path $md) | Out-Null
  $body = [IO.File]::ReadAllText("$Root\claude\CLAUDE.unreal.md", $Utf8).Trim().Replace('{{REPO_DIR}}', $Root).Replace('{{PORT}}', "$Port")
  $new = "<!-- BEGIN ue-ai-editor-stack (managed by install.ps1 - edit claude/CLAUDE.unreal.md in the repo) -->`r`n$body`r`n<!-- END ue-ai-editor-stack -->"
  $cur = if (Test-Path $md) { [IO.File]::ReadAllText($md, $Utf8) } else { '' }
  $rx = '(?s)<!-- BEGIN ue-ai-editor-stack.*?<!-- END ue-ai-editor-stack -->'
  if ($cur -match $rx) { $cur = [regex]::Replace($cur, $rx, [Text.RegularExpressions.MatchEvaluator] { param($m) $new }) }
  else { $cur = ($cur.TrimEnd() + "`r`n`r`n" + $new + "`r`n").TrimStart() }
  [IO.File]::WriteAllText($md, $cur, $Utf8)
  Ok "Unreal rules updated in $md"
}

Step "Done"
Write-Host "    Open any project in UE $EngineVer -> Claude Code in the project folder -> /mcp -> unreal-mcp: connected"
Write-Host "    Server self-test without Claude (editor open): .\tools\mcp_check.ps1"

<#
.SYNOPSIS
  Installs the UE AI Editor Stack (Unreal MCP + engine AI toolsets + VibeUE + Claude Code wiring). Safe to re-run.
.DESCRIPTION
  1) Builds VibeUE from GitHub for the chosen engine and installs it to Engine\Plugins\Marketplace\VibeUE
  2) Installs AIEditorStack - an engine-wide, code-free switch that enables Unreal MCP, the engine AI toolsets
     and VibeUE in every project (editor only; nothing is packaged into games)
  3) Turns on MCP server auto-start for all projects (per-user INI; written from outside the package when this
     script runs inside an MSIX app such as the Claude desktop app, whose AppData writes Windows redirects)
  4) Configures Claude Code: user-scope MCP server "unreal-mcp" (a stdio bridge to the editor that stays connected
     while no editor runs), Epic's plugin, managed block in ~/.claude/CLAUDE.md
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

# Per-user state of the stack (VibeUE build cache, Sketchfab token) lives outside AppData, see Get-AppDataRedirect
$StateDir = Join-Path $env:USERPROFILE '.ue-ai-editor-stack'
$script:Redirect = $null

function Get-AppDataRedirect {
  # '' if this script writes to the real %LOCALAPPDATA%, else the folder its new files there really land in.
  # Run from the Claude desktop app (an MSIX package), Windows redirects every NEW file under %LOCALAPPDATA% /
  # %APPDATA% into %LOCALAPPDATA%\Packages\<app>\LocalCache, where an editor started from the Start menu never sees
  # it. The app's child processes report no package identity, so this is probed with a temporary file instead.
  if ($null -ne $script:Redirect) { return $script:Redirect }
  $name = ".aies-redirect-probe-$PID-" + [guid]::NewGuid().ToString('N').Substring(0, 8)
  $probe = Join-Path $env:LOCALAPPDATA $name
  $script:Redirect = ''
  try {
    [IO.File]::WriteAllText($probe, 'probe')
    $twin = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages\*\LocalCache\Local\$name") -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($twin) { $script:Redirect = $twin.DirectoryName }
  } catch { } finally { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
  $script:Redirect
}

function Get-RedirectedCopy([string]$Path) {
  $root = Get-AppDataRedirect
  if (-not $root) { return '' }
  Join-Path $root $Path.Substring($env:LOCALAPPDATA.Length).TrimStart('\')
}

function Write-UserAppDataFile([string]$Path, [string]$Text) {
  # Writes a file under %LOCALAPPDATA% so that every process sees it. When this script's AppData writes are
  # redirected, the file is written by a process started through WMI (Win32_Process.Create), which is not.
  if (-not (Get-AppDataRedirect)) {
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    [IO.File]::WriteAllText($Path, $Text, $Utf8)
    return
  }
  New-Item -ItemType Directory -Force $StateDir | Out-Null
  $tmp = Join-Path $StateDir ("install-" + [guid]::NewGuid().ToString('N') + '.tmp')
  [IO.File]::WriteAllText($tmp, $Text, $Utf8)
  try {
    $cmd = "New-Item -ItemType Directory -Force '$(Split-Path $Path)' | Out-Null; Copy-Item -LiteralPath '$tmp' -Destination '$Path' -Force"
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
      CommandLine = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $enc" }
    if ($r.ReturnValue -ne 0) { throw "Win32_Process.Create returned $($r.ReturnValue)" }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
  } finally { Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
  # drop the redirected copy, so this script and editors started from the same app read the real file
  $copy = Get-RedirectedCopy $Path
  if ($copy) { Remove-Item -LiteralPath $copy -ErrorAction SilentlyContinue }
}

function Read-UserAppDataFile([string]$Path) {
  # The real file's text ('' if none). A redirected copy would shadow it - remove that copy first.
  $copy = Get-RedirectedCopy $Path
  if ($copy -and (Test-Path -LiteralPath $copy)) {
    Warn "removing the redirected copy $copy (only this app's processes could see it)"
    Remove-Item -LiteralPath $copy -Force
  }
  if (Test-Path -LiteralPath $Path) { [IO.File]::ReadAllText($Path, $Utf8) } else { '' }
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
  $work = $StateDir
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
$ini = "$env:LOCALAPPDATA\Unreal Engine\Engine\Config\UserEditorPerProjectUserSettings.ini"
$redirect = Get-AppDataRedirect
if ($redirect) { Warn "this shell's AppData writes are redirected to $redirect - writing the INI from a process outside it, so every editor sees it" }
$block = [IO.File]::ReadAllText("$Root\config\UserEditorPerProjectUserSettings.ini", $Utf8) -replace 'ServerPortNumber=\d+', "ServerPortNumber=$Port"
$cur = Read-UserAppDataFile $ini
if ($cur.Contains('[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]')) { Ok "already configured: $ini" }
else {
  $new = if ($cur.Trim()) { $cur.TrimEnd() + "`r`n`r`n" + $block } else { $block }
  Write-UserAppDataFile $ini $new
  if ((Read-UserAppDataFile $ini).Contains('bAutoStartServer=True')) { Ok "written: $ini" }
  else { Warn "could not verify $ini - the AIEditorStack plugin starts the MCP server on its own anyway" }
}
Ok "AIEditorStack also starts the server itself if this setting is missing or the port was busy at startup"
# Sketchfab token from 1.3 and earlier: move it out of AppData (packaged apps' copies included)
$tokenNew = Join-Path $StateDir 'sketchfab_token.txt'
if (-not (Test-Path $tokenNew)) {
  $old = @(Join-Path $env:LOCALAPPDATA 'ue-ai-editor-stack\sketchfab_token.txt') +
         @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages\*\LocalCache\Local\ue-ai-editor-stack\sketchfab_token.txt') -ErrorAction SilentlyContinue | ForEach-Object FullName) |
         Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($old) { New-Item -ItemType Directory -Force $StateDir | Out-Null; Copy-Item $old $tokenNew; Ok "Sketchfab token moved to $tokenNew" }
}

# ---------- 4. Claude Code ----------
if (-not $SkipClaude) {
  Step "Claude Code"
  # "unreal-mcp" is the stdio bridge (bridge\unreal_mcp_bridge.py, run by the engine's Python), not the editor's HTTP
  # endpoint: with direct HTTP Claude Code gives up on a session started before the editor, its Reconnect then fails
  # in an OAuth flow, and calls silent for 300 s are aborted. The bridge stays connected and finds the editor itself.
  $py = "$EngineDir\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
  $bridge = Join-Path $StateDir 'unreal_mcp_bridge.py'
  New-Item -ItemType Directory -Force $StateDir | Out-Null
  Copy-Item "$Root\bridge\unreal_mcp_bridge.py" $bridge -Force
  Ok "MCP bridge: $bridge"
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Warn "'claude' is not on PATH - skipping MCP server and plugin registration" }
  else {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $cur = (claude mcp get unreal-mcp 2>&1 | Out-String); $has = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $old
    $want = (Test-Path $py) -and $cur.Contains($bridge) -and $cur.Contains($py) -and $cur.Contains("--port $Port")
    if ($want) { Ok "MCP server 'unreal-mcp' already registered (stdio bridge)" }
    else {
      if ($has) { $null = Native { claude mcp remove unreal-mcp -s user } }
      if (Test-Path $py) { $null = Native { claude mcp add --scope user unreal-mcp -- $py -I $bridge --port $Port } }
      else {
        Warn "no bundled Python at $py - registering the editor's HTTP endpoint directly (reconnect with /mcp after the editor starts)"
        $null = Native { claude mcp add --transport http --scope user unreal-mcp "http://127.0.0.1:$Port/mcp" }
      }
      $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      $cur = (claude mcp get unreal-mcp 2>&1 | Out-String); $ErrorActionPreference = $old
      if ($cur.Contains($bridge)) { Ok "MCP server 'unreal-mcp' registered: stdio bridge -> http://127.0.0.1:$Port/mcp" }
      elseif ($cur -match 'Type:\s*http') { Ok "MCP server 'unreal-mcp' registered: http://127.0.0.1:$Port/mcp" }
      else { Warn "could not verify the 'unreal-mcp' registration - check 'claude mcp get unreal-mcp'" }
    }
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
Write-Host "    Sessions that were already running keep the old connection: start a new one or reconnect unreal-mcp in /mcp once"
Write-Host "    Server self-test without Claude (editor open): .\tools\mcp_check.ps1"

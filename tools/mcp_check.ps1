# Self-test of the Unreal MCP server (editor must be open): handshake, tool list, toolsets, read-only Python via VibeUE.
param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$u = "http://127.0.0.1:$Port/mcp"
$script:sid = $null
function Invoke-Mcp($obj) {
  $hh = @{ Accept = 'application/json, text/event-stream' }
  if ($script:sid) { $hh['Mcp-Session-Id'] = $script:sid }
  $body = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Depth 10 -Compress))
  $r = Invoke-WebRequest -Uri $u -Method Post -Headers $hh -ContentType 'application/json' -Body $body -UseBasicParsing
  if (-not $script:sid -and $r.Headers['Mcp-Session-Id']) { $script:sid = $r.Headers['Mcp-Session-Id'] }
  $txt = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
  $json = ($txt -split "`n" | Where-Object { $_ -like 'data:*' } | ForEach-Object { $_.Substring(5).Trim() }) -join ''
  if (-not $json) { $json = $txt }
  if ($json) { $json | ConvertFrom-Json }
}
try { $init = Invoke-Mcp @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'mcp-check'; version = '1.0' } } } }
catch { Write-Host "FAIL: no MCP server on $u - is the project open in Unreal Editor?" -ForegroundColor Red; exit 1 }
"OK  server on $u (protocol $($init.result.protocolVersion))"
$null = Invoke-Mcp @{ jsonrpc = '2.0'; method = 'notifications/initialized' }
$tl = Invoke-Mcp @{ jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = @{} }
"OK  tools: " + (($tl.result.tools | ForEach-Object { $_.name }) -join ', ')
$ls = Invoke-Mcp @{ jsonrpc = '2.0'; id = 3; method = 'tools/call'; params = @{ name = 'list_toolsets'; arguments = @{} } }
$lsText = ($ls.result.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join ''
"OK  VibeUE toolsets: " + ([regex]::Matches($lsText, 'VibeUE\.[A-Za-z]+')).Value.Count
if ($lsText -match 'SketchfabTools') { "OK  SketchfabTools registered" } else { Write-Host "WARN: SketchfabTools not registered - restart the editor after install.ps1" -ForegroundColor Yellow }
if ($tl.result.tools.name -contains 'execute_python_code') {
  $code = "import unreal`nw = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()`na = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()`nprint('level=' + w.get_name() + ' actors=' + str(len(a)))"
  $pc = Invoke-Mcp @{ jsonrpc = '2.0'; id = 4; method = 'tools/call'; params = @{ name = 'execute_python_code'; arguments = @{ code = $code; auto_save = $false } } }
  $out = (($pc.result.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join ' ')
  if ($out -match '"output"\s*:\s*"([^"]*)"') { "OK  python: $($Matches[1])" } else { "??  python: $out" }
} else { Write-Host "WARN: execute_python_code not found - VibeUE is not loaded" -ForegroundColor Yellow }

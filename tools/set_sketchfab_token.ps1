# Saves your Sketchfab API token for the SketchfabTools toolset.
# Opens the same window as the editor menu Tools > AI Editor Stack > Sketchfab token...
# The token is checked against Sketchfab and stored only in your user profile
# (%USERPROFILE%\.ue-ai-editor-stack\sketchfab_token.txt), never in the repo or the project. No editor restart needed.
# The folder is outside AppData on purpose: Windows redirects AppData writes of packaged (MSIX) apps such as the
# Claude desktop app, so a token written there from Claude's shell would be invisible to the editor.
#   .\tools\set_sketchfab_token.ps1            # window
#   .\tools\set_sketchfab_token.ps1 -Console   # hidden prompt in this console instead of the window
#   .\tools\set_sketchfab_token.ps1 -Remove    # delete the saved token (also old copies from version 1.3)
param([switch]$Console, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir  = Join-Path $env:USERPROFILE '.ue-ai-editor-stack'
$file = Join-Path $dir 'sketchfab_token.txt'
$legacy = @(Join-Path $env:LOCALAPPDATA 'ue-ai-editor-stack\sketchfab_token.txt')
$legacy += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\*\LocalCache\Local\ue-ai-editor-stack\sketchfab_token.txt') -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })

if ($Remove) {
  foreach ($f in @($file) + $legacy) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
  Write-Host 'Sketchfab token removed.'
  return
}

if (-not $Console) {
  $dialog = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\AIEditorStack\Content\Python\ai_editor_stack\sketchfab_token_dialog.ps1'))
  & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -ExecutionPolicy Bypass -File $dialog -TokenFile $file
  exit $LASTEXITCODE
}

Write-Host 'Get the token at https://sketchfab.com/settings/password  (section "API Token", free account).'
$secure = Read-Host -AsSecureString 'Paste the Sketchfab API token (input is hidden)'
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $token) { throw 'Empty token.' }

try { $me = Invoke-RestMethod 'https://api.sketchfab.com/v3/me' -Headers @{ Authorization = "Token $token" } -UseBasicParsing }
catch { throw "Sketchfab rejected this token: $($_.Exception.Message)" }

New-Item -ItemType Directory -Force $dir | Out-Null
[IO.File]::WriteAllText($file, $token, (New-Object Text.UTF8Encoding($false)))
foreach ($f in $legacy) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
Write-Host "Saved. Sketchfab account: $($me.username)  ->  $file" -ForegroundColor Green

# Saves your Sketchfab API token for the SketchfabTools toolset.
# The token is checked against Sketchfab and stored only in your user profile
# (%LOCALAPPDATA%\ue-ai-editor-stack\sketchfab_token.txt), never in the repo or the project.
# No editor restart needed. Remove it with: .\tools\set_sketchfab_token.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir  = Join-Path $env:LOCALAPPDATA 'ue-ai-editor-stack'
$file = Join-Path $dir 'sketchfab_token.txt'
if ($Remove) { Remove-Item $file -ErrorAction SilentlyContinue; Write-Host 'Sketchfab token removed.'; return }

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
Write-Host "Saved. Sketchfab account: $($me.username)  ->  $file" -ForegroundColor Green

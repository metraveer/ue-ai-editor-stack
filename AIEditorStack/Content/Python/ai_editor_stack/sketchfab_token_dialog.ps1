<#
.SYNOPSIS
  Window for entering the Sketchfab API token of the SketchfabTools toolset (UE AI Editor Stack).
.DESCRIPTION
  Checks the token with GET https://api.sketchfab.com/v3/me and saves it (UTF-8, no BOM) to the file that
  ai_editor_stack.sketchfab_api.get_token() reads: %USERPROFILE%\.ue-ai-editor-stack\sketchfab_token.txt.
  That folder is outside AppData on purpose: Windows redirects AppData writes of packaged (MSIX) apps such as the
  Claude desktop app, so a token saved there from Claude's shell would be invisible to the editor. Older copies
  (%LOCALAPPDATA%\ue-ai-editor-stack and its MSIX-redirected twins) count as saved and are removed on Save/Remove.
  Opened by the editor menu Tools > AI Editor Stack > Sketchfab token..., by SketchfabTools.open_token_dialog
  and by tools\set_sketchfab_token.ps1.
  Only one window is open at a time. Russian UI when the Windows display language or regional format is Russian.
  Exit code: 0 = token saved, 2 = token removed, 1 = closed without changes.
  Keep this file UTF-8 with BOM: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
.EXAMPLE
  powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\sketchfab_token_dialog.ps1
#>
param([string]$TokenFile = (Join-Path $env:USERPROFILE '.ue-ai-editor-stack\sketchfab_token.txt'))
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Copies from version 1.3 and earlier, including the ones Windows redirected into an MSIX app's private AppData
$LegacyFiles = @(Join-Path $env:LOCALAPPDATA 'ue-ai-editor-stack\sketchfab_token.txt')
$LegacyFiles += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\*\LocalCache\Local\ue-ai-editor-stack\sketchfab_token.txt') -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
$LegacyFiles = @($LegacyFiles | Where-Object { $_ -and ($_ -ne $TokenFile) })

if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
  # WinForms needs a single-threaded apartment
  & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -ExecutionPolicy Bypass -File $PSCommandPath -TokenFile $TokenFile
  exit $LASTEXITCODE
}

$MeUrl     = 'https://api.sketchfab.com/v3/me'
$TokenPage = 'https://sketchfab.com/settings/password'
$UserAgent = 'ue-ai-editor-stack (+https://github.com/metraveer/ue-ai-editor-stack)'
$TokenEnv  = 'SKETCHFAB_API_TOKEN'

$cultures = @([Globalization.CultureInfo]::CurrentUICulture, [Globalization.CultureInfo]::CurrentCulture)
if ($cultures.TwoLetterISOLanguageName -contains 'ru') {
  $T = @{
    Title     = 'Токен Sketchfab — AI Editor Stack'
    Intro     = 'SketchfabTools нужен API-токен бесплатного аккаунта Sketchfab, чтобы скачивать модели. Токен хранится только на этом компьютере.'
    Token     = 'API-токен:'
    Show      = 'Показать'
    Link      = 'Где взять токен'
    Save      = 'Сохранить'
    Cancel    = 'Отмена'
    Close     = 'Закрыть'
    Remove    = 'Удалить токен'
    HasToken  = 'Токен уже сохранён. Вставь новый, чтобы заменить его.'
    NoToken   = 'Токен ещё не сохранён.'
    EnvSet    = "Задана переменная окружения ${TokenEnv}: пока она есть, используется её значение, а не сохранённый токен."
    Empty     = 'Вставь токен в поле.'
    Checking  = 'Проверяю токен на Sketchfab…'
    Saved     = 'Готово. Аккаунт Sketchfab: {0}'
    Rejected  = 'Sketchfab не принял токен (HTTP {0}). Скопируй его заново целиком со страницы API Token.'
    RateLimit = 'Sketchfab ограничил число запросов (HTTP 429). Попробуй через минуту.'
    HttpError = 'Ошибка Sketchfab: HTTP {0}. Попробуй позже.'
    Network   = 'Нет связи с Sketchfab: {0}'
    SaveError = 'Токен верный, но сохранить его не удалось: {0}'
    Confirm   = 'Удалить сохранённый токен Sketchfab с этого компьютера?'
    Removed   = 'Токен удалён.'
    NoChange  = 'Токен не изменён.'
  }
} else {
  $T = @{
    Title     = 'Sketchfab token — AI Editor Stack'
    Intro     = 'SketchfabTools needs the API token of a free Sketchfab account to download models. The token is stored only on this PC.'
    Token     = 'API token:'
    Show      = 'Show'
    Link      = 'Where to get the token'
    Save      = 'Save'
    Cancel    = 'Cancel'
    Close     = 'Close'
    Remove    = 'Remove token'
    HasToken  = 'A token is already saved. Paste a new one to replace it.'
    NoToken   = 'No token saved yet.'
    EnvSet    = "The $TokenEnv environment variable is set: while it exists, its value is used instead of the saved token."
    Empty     = 'Paste the token into the field.'
    Checking  = 'Checking the token with Sketchfab…'
    Saved     = 'Done. Sketchfab account: {0}'
    Rejected  = 'Sketchfab rejected the token (HTTP {0}). Copy it again in full from the API Token page.'
    RateLimit = 'Sketchfab rate limit reached (HTTP 429). Try again in a minute.'
    HttpError = 'Sketchfab error: HTTP {0}. Try again later.'
    Network   = 'Cannot reach Sketchfab: {0}'
    SaveError = 'The token is valid but could not be saved: {0}'
    Confirm   = 'Remove the saved Sketchfab token from this PC?'
    Removed   = 'Token removed.'
    NoChange  = 'Token not changed.'
  }
}

# One window at a time: a second launch brings the open one to the front
$created = $false
$mutex = [Threading.Mutex]::new($true,'Local\ue-ai-editor-stack-sketchfab-token', [ref]$created)
if (-not $created) {
  try { $null = (New-Object -ComObject WScript.Shell).AppActivate($T.Title) } catch { }
  exit 1
}

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
try {
  Add-Type -Namespace AIEditorStack -Name Native -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
  $null = [AIEditorStack.Native]::SetProcessDPIAware()
} catch { }
[Windows.Forms.Application]::EnableVisualStyles()

$protocols = [Net.ServicePointManager]::SecurityProtocol
if ($protocols -ne 0 -and -not ($protocols -band [Net.SecurityProtocolType]::Tls12)) {
  [Net.ServicePointManager]::SecurityProtocol = $protocols -bor [Net.SecurityProtocolType]::Tls12
}

$script:Result = 'none'
$script:Account = ''

function Test-SavedToken {
  foreach ($f in @($TokenFile) + $LegacyFiles) {
    if ((Test-Path -LiteralPath $f) -and ([IO.File]::ReadAllText($f).Trim() -ne '')) { return $true }
  }
  $false
}

function Remove-LegacyTokens {
  foreach ($f in $LegacyFiles) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
}

function Add-Control([string]$Type, [int]$X, [int]$Y, [int]$W = 0, [int]$H = 0) {
  $c = New-Object "Windows.Forms.$Type"
  $c.Location = New-Object Drawing.Point($X, $Y)
  if ($W) { $c.Size = New-Object Drawing.Size($W, $H) } else { $c.AutoSize = $true }
  $form.Controls.Add($c)
  $c
}

function Set-State([string]$Text, [string]$Kind = 'info') {
  $state.Text = $Text
  $state.ForeColor = switch ($Kind) {
    'ok'    { [Drawing.Color]::ForestGreen }
    'error' { [Drawing.Color]::Firebrick }
    default { [Drawing.SystemColors]::GrayText }
  }
}

function Get-ErrorText($ErrorRecord) {
  $code = 0
  $response = $ErrorRecord.Exception.Response
  if ($response) { try { $code = [int]$response.StatusCode } catch { } }
  if ($code -eq 401 -or $code -eq 403) { return $T.Rejected -f $code }
  if ($code -eq 429) { return $T.RateLimit }
  if ($code) { return $T.HttpError -f $code }
  $T.Network -f $ErrorRecord.Exception.Message
}

function Show-InitialState {
  $text = if (Test-SavedToken) { $T.HasToken } else { $T.NoToken }
  if ([Environment]::GetEnvironmentVariable($TokenEnv)) { $text += "`r`n" + $T.EnvSet }
  Set-State $text
}

# ---------- layout (96 DPI units, scaled to the screen DPI) ----------
$form = New-Object Windows.Forms.Form
$form.SuspendLayout()
$form.AutoScaleDimensions = New-Object Drawing.SizeF(96, 96)
$form.AutoScaleMode = 'Dpi'
$form.Font = [Drawing.SystemFonts]::MessageBoxFont
$form.ClientSize = New-Object Drawing.Size(460, 222)
$form.Text = $T.Title
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowIcon = $false
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true

$intro = Add-Control Label 12 12 436 36
$intro.Text = $T.Intro

$label = Add-Control Label 12 61
$label.Text = $T.Token

$box = Add-Control TextBox 92 58 266 23
$box.UseSystemPasswordChar = $true

$show = Add-Control CheckBox 368 59
$show.Text = $T.Show

$link = Add-Control LinkLabel 12 90
$link.Text = "$($T.Link) (sketchfab.com/settings/password → API Token)"
$link.LinkArea = New-Object Windows.Forms.LinkArea(0, $T.Link.Length)

$state = Add-Control Label 12 118 436 50

$remove = Add-Control Button 12 182 140 28
$remove.Text = $T.Remove

$save = Add-Control Button 248 182 100 28
$save.Text = $T.Save
$save.Enabled = $false

$cancel = Add-Control Button 354 182 94 28
$cancel.Text = $T.Cancel
$cancel.DialogResult = 'Cancel'

$form.AcceptButton = $save
$form.CancelButton = $cancel
$form.ResumeLayout($false)
$form.PerformLayout()

# ---------- behaviour ----------
$show.Add_CheckedChanged({ $box.UseSystemPasswordChar = -not $show.Checked })
$link.Add_LinkClicked({ Start-Process $TokenPage })
$box.Add_TextChanged({
  $save.Enabled = $box.Text.Trim() -ne ''
  if ($save.Enabled) { $form.AcceptButton = $save }
})

$save.Add_Click({
  $token = $box.Text -replace '\s', ''
  if (-not $token) { Set-State $T.Empty 'error'; return }
  Set-State $T.Checking
  foreach ($c in $box, $save, $remove) { $c.Enabled = $false }
  $form.UseWaitCursor = $true
  [Windows.Forms.Application]::DoEvents()
  try {
    $me = Invoke-RestMethod -Uri $MeUrl -Headers @{ Authorization = "Token $token" } -UserAgent $UserAgent -TimeoutSec 30 -UseBasicParsing
  } catch {
    Set-State (Get-ErrorText $_) 'error'
    return
  } finally {
    $form.UseWaitCursor = $false
    $box.Enabled = $true
    $save.Enabled = $box.Text.Trim() -ne ''
    $remove.Enabled = Test-SavedToken
  }
  try {
    New-Item -ItemType Directory -Force (Split-Path -Parent $TokenFile) | Out-Null
    [IO.File]::WriteAllText($TokenFile, $token, (New-Object Text.UTF8Encoding($false)))
    Remove-LegacyTokens
  } catch {
    Set-State ($T.SaveError -f $_.Exception.Message) 'error'
    return
  }
  $script:Result = 'saved'
  $script:Account = [string]$me.username
  $box.Clear()
  $remove.Enabled = $true
  $cancel.Text = $T.Close
  $form.AcceptButton = $cancel
  $null = $cancel.Focus()
  $text = $T.Saved -f $script:Account
  if ([Environment]::GetEnvironmentVariable($TokenEnv)) { $text += "`r`n" + $T.EnvSet }
  Set-State $text 'ok'
})

$remove.Add_Click({
  $answer = [Windows.Forms.MessageBox]::Show($form, $T.Confirm, $T.Title, 'YesNo', 'Question', 'Button2')
  if ($answer -ne 'Yes') { return }
  try {
    if (Test-Path -LiteralPath $TokenFile) { Remove-Item -LiteralPath $TokenFile -Force -ErrorAction Stop }
    Remove-LegacyTokens
  } catch { Set-State $_.Exception.Message 'error'; return }
  $script:Result = 'removed'
  $remove.Enabled = $false
  $cancel.Text = $T.Close
  Set-State $T.Removed 'ok'
})

$form.Add_Shown({
  $form.Activate()
  $null = $box.Focus()
})

$remove.Enabled = Test-SavedToken
Show-InitialState
$null = $form.ShowDialog()
$form.Dispose()
$mutex.ReleaseMutex()

switch ($script:Result) {
  'saved'   { Write-Output ($T.Saved -f $script:Account); exit 0 }
  'removed' { Write-Output $T.Removed; exit 2 }
  default   { Write-Output $T.NoChange; exit 1 }
}

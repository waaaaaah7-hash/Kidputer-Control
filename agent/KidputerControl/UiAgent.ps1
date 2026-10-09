#Requires -Version 5.1
<#
.SYNOPSIS
    Kidputer Control UI agent: countdown notice, lock screen with PIN entry, and custom messages.

.DESCRIPTION
    Launched by the SYSTEM watchdog into the child's session (CreateProcessAsUser) and relaunched within
    seconds if it is closed or killed. It runs with the child's own rights, so it holds no secrets and
    makes no decisions: it renders ipc\ui-state.json (written by the watchdog) and drops requests (PIN
    attempts, message acknowledgements) into ipc\inbox for the watchdog to verify.

    Locking is always LockWorkStation: reversible, no data loss.
#>
param([string]$DataRoot = (Join-Path $env:ProgramData 'KidputerControl'))

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Modules\KidputerControl.Common.psm1') -Force
$paths = Initialize-KcPaths -DataRoot $DataRoot

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Namespace KcUi -Name User32 -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true)] public static extern bool LockWorkStation();
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
[void][KcUi.User32]::SetProcessDPIAware()
[System.Windows.Forms.Application]::EnableVisualStyles()

# How long the lock screen stays reachable (for PIN entry) after the child signs back in to Windows,
# before the workstation is locked again.
$RelockAfterSeconds = 30

$script:ui = $null                 # last good ui-state.json
$script:lastLockCall = [DateTime]::MinValue
$script:lastInteraction = [DateTime]::MinValue
$script:pendingPin = $null
$script:shownPinResult = $null
$script:messageForm = $null
$script:messageId = $null
$script:messageCloseReason = 'user'
$script:lastErrorReport = [DateTime]::MinValue

$bg = [System.Drawing.Color]::FromArgb(18, 24, 38)
$fg = [System.Drawing.Color]::White
$muted = [System.Drawing.Color]::FromArgb(170, 180, 200)
$accent = [System.Drawing.Color]::FromArgb(88, 166, 255)

function Format-LocalTime($iso) {
    $d = ConvertTo-KcDate $iso
    if ($null -eq $d) { return '' }
    $d.ToLocalTime().ToString('t')
}

function Report-UiError([string]$Message) {
    if (([DateTime]::UtcNow - $script:lastErrorReport).TotalSeconds -lt 60) { return }
    $script:lastErrorReport = [DateTime]::UtcNow
    try { [void](New-KcInboxRequest -Type 'ui_error' -Fields @{ message = $Message }) } catch { }
}

function Invoke-Lock {
    [void][KcUi.User32]::LockWorkStation()
    $script:lastLockCall = [DateTime]::UtcNow
}

# --- Countdown notice ------------------------------------------------------------------------------------------

$countdownForm = New-Object System.Windows.Forms.Form
$countdownForm.FormBorderStyle = 'None'
$countdownForm.StartPosition = 'Manual'
$countdownForm.ShowInTaskbar = $false
$countdownForm.TopMost = $true
$countdownForm.BackColor = $bg
$countdownForm.Size = New-Object System.Drawing.Size(380, 110)
$countdownTitle = New-Object System.Windows.Forms.Label
$countdownTitle.Text = 'Screen time is ending'
$countdownTitle.ForeColor = $muted
$countdownTitle.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$countdownTitle.SetBounds(18, 12, 340, 22)
$countdownText = New-Object System.Windows.Forms.Label
$countdownText.ForeColor = $fg
$countdownText.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 16)
$countdownText.SetBounds(18, 38, 350, 34)
$countdownHint = New-Object System.Windows.Forms.Label
$countdownHint.Text = 'Save your work now.'
$countdownHint.ForeColor = $muted
$countdownHint.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$countdownHint.SetBounds(18, 76, 340, 20)
$countdownForm.Controls.AddRange(@($countdownTitle, $countdownText, $countdownHint))
$countdownForm.Add_FormClosing({ param($s, $e) if ($e.CloseReason -eq 'UserClosing') { $e.Cancel = $true } })

# --- Notices: an app about to be closed by a parent (and, later, blocked apps) -----------------------------------

function New-NoticeForm {
    <# A small always-on-top box in the bottom-right corner, like the bedtime countdown. #>
    $f = New-Object System.Windows.Forms.Form
    $f.FormBorderStyle = 'None'
    $f.StartPosition = 'Manual'
    $f.ShowInTaskbar = $false
    $f.TopMost = $true
    $f.BackColor = $bg
    $f.Size = New-Object System.Drawing.Size(380, 110)
    $title = New-Object System.Windows.Forms.Label
    $title.ForeColor = $muted
    $title.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $title.SetBounds(18, 12, 340, 22)
    $text = New-Object System.Windows.Forms.Label
    $text.ForeColor = $fg
    $text.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 14)
    $text.SetBounds(18, 38, 350, 34)
    $hint = New-Object System.Windows.Forms.Label
    $hint.ForeColor = $muted
    $hint.Font = New-Object System.Drawing.Font('Segoe UI', 9)
    $hint.SetBounds(18, 76, 340, 20)
    $f.Controls.AddRange(@($title, $text, $hint))
    $f.Add_FormClosing({ param($s, $e) if ($e.CloseReason -eq 'UserClosing') { $e.Cancel = $true } })
    @{ Form = $f; Title = $title; Text = $text; Hint = $hint }
}

function Show-Notice($notice, [int]$Slot) {
    # Slot 0 sits in the corner, slot 1 just above it (so it can share the screen with the countdown).
    if (-not $notice.Form.Visible) {
        $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        $y = $wa.Bottom - ($notice.Form.Height + 16) * ($Slot + 1)
        $notice.Form.Location = New-Object System.Drawing.Point(($wa.Right - $notice.Form.Width - 16), $y)
        $notice.Form.Show()
    }
}

$appNotice = New-NoticeForm
$appNotice.Title.Text = 'A parent is closing an app'
$appNotice.Hint.Text = 'Save your work now.'
$script:closeRequested = @{}   # process ids already asked to close

# --- Lock screen -------------------------------------------------------------------------------------------------

$lockForm = New-Object System.Windows.Forms.Form
$lockForm.FormBorderStyle = 'None'
$lockForm.StartPosition = 'Manual'
$lockForm.ShowInTaskbar = $false
$lockForm.TopMost = $true
$lockForm.BackColor = $bg
$lockForm.KeyPreview = $true
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$lockForm.Bounds = $vs

$panel = New-Object System.Windows.Forms.Panel
$panel.Size = New-Object System.Drawing.Size(560, 360)
$panel.BackColor = $bg
$primary = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$panel.Location = New-Object System.Drawing.Point(($primary.X - $vs.X + [int](($primary.Width - 560) / 2)), ($primary.Y - $vs.Y + [int](($primary.Height - 360) / 2)))

$lockTitle = New-Object System.Windows.Forms.Label
$lockTitle.Text = 'This PC is locked'
$lockTitle.ForeColor = $fg
$lockTitle.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 28)
$lockTitle.SetBounds(0, 0, 560, 60)
$lockTitle.TextAlign = 'MiddleCenter'

$lockSubtitle = New-Object System.Windows.Forms.Label
$lockSubtitle.ForeColor = $muted
$lockSubtitle.Font = New-Object System.Drawing.Font('Segoe UI', 13)
$lockSubtitle.SetBounds(0, 66, 560, 70)
$lockSubtitle.TextAlign = 'TopCenter'

$pinLabel = New-Object System.Windows.Forms.Label
$pinLabel.Text = 'Parent PIN'
$pinLabel.ForeColor = $muted
$pinLabel.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$pinLabel.SetBounds(130, 160, 300, 22)

$pinBox = New-Object System.Windows.Forms.TextBox
$pinBox.UseSystemPasswordChar = $true
$pinBox.MaxLength = 10
$pinBox.Font = New-Object System.Drawing.Font('Segoe UI', 16)
$pinBox.SetBounds(130, 184, 200, 36)

$pinButton = New-Object System.Windows.Forms.Button
$pinButton.Text = 'Unlock'
$pinButton.Font = New-Object System.Drawing.Font('Segoe UI', 11)
$pinButton.BackColor = $accent
$pinButton.ForeColor = $fg
$pinButton.FlatStyle = 'Flat'
$pinButton.SetBounds(340, 184, 90, 37)

$pinStatus = New-Object System.Windows.Forms.Label
$pinStatus.ForeColor = $muted
$pinStatus.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$pinStatus.SetBounds(0, 236, 560, 60)
$pinStatus.TextAlign = 'TopCenter'

$panel.Controls.AddRange(@($lockTitle, $lockSubtitle, $pinLabel, $pinBox, $pinButton, $pinStatus))
$lockForm.Controls.Add($panel)
$lockForm.AcceptButton = $pinButton
$lockForm.Add_FormClosing({ param($s, $e) if ($e.CloseReason -eq 'UserClosing') { $e.Cancel = $true } })   # blocks Alt+F4
$pinBox.Add_KeyPress({
        param($s, $e)
        $script:lastInteraction = [DateTime]::UtcNow
        if (-not [char]::IsDigit($e.KeyChar) -and -not [char]::IsControl($e.KeyChar)) { $e.Handled = $true }
    })

$submitPin = {
    $pin = $pinBox.Text
    $pinBox.Text = ''
    if ($pin.Length -lt 4) { $pinStatus.Text = 'Enter the 4-10 digit PIN.'; return }
    try {
        $script:pendingPin = New-KcInboxRequest -Type 'pin_attempt' -Fields @{ pin = $pin }
        $pinStatus.Text = 'Checking...'
        $pinButton.Enabled = $false
    } catch {
        $pinStatus.Text = 'Could not submit the PIN. Try again.'
        Report-UiError "PIN submit failed: $($_.Exception.Message)"
    }
}
$pinButton.Add_Click($submitPin)

# --- Custom message -----------------------------------------------------------------------------------------------

function Close-MessageForm([string]$Reason) {
    if ($null -eq $script:messageForm) { return }
    $script:messageCloseReason = $Reason
    $f = $script:messageForm
    $f.Tag = 'closing'
    $f.Close()
}

function Show-MessageForm($msg) {
    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'Message from your parent'
    $f.StartPosition = 'CenterScreen'
    $f.TopMost = $true
    $f.ShowInTaskbar = $true
    $f.FormBorderStyle = 'FixedDialog'
    $f.MinimizeBox = $false
    $f.MaximizeBox = $false
    $f.Size = New-Object System.Drawing.Size(520, 300)
    $f.Font = New-Object System.Drawing.Font('Segoe UI', 11)
    $closable = $msg.mode -eq 'closable'
    $f.ControlBox = $closable

    $body = New-Object System.Windows.Forms.TextBox
    $body.Multiline = $true
    $body.ReadOnly = $true
    $body.ScrollBars = 'Vertical'
    $body.BorderStyle = 'None'
    $body.BackColor = $f.BackColor
    $body.TabStop = $false
    $body.Text = ($msg.body -replace "`r?`n", "`r`n")
    $body.SetBounds(20, 20, 465, 170)
    $f.Controls.Add($body)

    if ($closable) {
        $ok = New-Object System.Windows.Forms.Button
        $ok.Text = 'OK'
        $ok.SetBounds(395, 205, 90, 34)
        $ok.Add_Click({ $script:messageForm.Close() })
        $f.Controls.Add($ok)
        $f.AcceptButton = $ok
    } else {
        $note = New-Object System.Windows.Forms.Label
        $note.Text = 'This message stays until a parent removes it.'
        $note.ForeColor = [System.Drawing.Color]::Gray
        $note.SetBounds(20, 210, 465, 24)
        $f.Controls.Add($note)
    }

    $f.Add_FormClosing({
            param($s, $e)
            # Persistent messages close only when the watchdog says the admin cleared them.
            if ($s.Tag -ne 'closing' -and -not $script:messageClosable) { $e.Cancel = $true }
        })
    # Single logging path for every way the window can go away.
    $f.Add_FormClosed({
            param($s, $e)
            try {
                [void](New-KcInboxRequest -Type 'message_closed' -Fields @{ message_id = $script:messageId; reason = $script:messageCloseReason })
            } catch { Report-UiError "message_closed ack failed: $($_.Exception.Message)" }
            $script:messageForm = $null
            $script:messageId = $null
        })
    $f.Add_Shown({
            try { [void](New-KcInboxRequest -Type 'message_displayed' -Fields @{ message_id = $script:messageId }) } catch { }
        })

    $script:messageClosable = $closable
    $script:messageCloseReason = 'user'
    $script:messageId = [string]$msg.id
    $script:messageForm = $f
    $f.Show()
}

# --- Render loop --------------------------------------------------------------------------------------------------

function Update-Ui {
    try {
        $fresh = Read-KcJsonFile -Path $paths.UiState
        if ($null -ne $fresh) { $script:ui = $fresh }
    } catch { }   # mid-replace or briefly locked: keep the last good state (fail-safe: stay locked)
    $u = $script:ui
    $mode = 'none'
    if ($null -ne $u) { $mode = [string]$u.mode }

    # Countdown
    if ($mode -eq 'countdown') {
        $ends = ConvertTo-KcDate $u.countdown_ends_at
        $left = [Math]::Max(0, [int][Math]::Ceiling(($ends - [DateTimeOffset]::UtcNow).TotalSeconds))
        $countdownText.Text = 'This PC locks in {0}:{1:00}' -f [int][Math]::Floor($left / 60), ($left % 60)
        if (-not $countdownForm.Visible) {
            $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
            $countdownForm.Location = New-Object System.Drawing.Point(($wa.Right - $countdownForm.Width - 16), ($wa.Bottom - $countdownForm.Height - 16))
            $countdownForm.Show()
        }
    } elseif ($countdownForm.Visible) {
        $countdownForm.Hide()
    }

    # App about to be closed by a parent
    $warnings = @()
    if ($null -ne $u -and $u.PSObject.Properties['app_warnings']) { $warnings = @($u.app_warnings) }
    if ($warnings.Count) {
        $w = $warnings[0]
        $ends = ConvertTo-KcDate $w.ends_at
        $left = 0
        if ($ends) { $left = [Math]::Max(0, [int][Math]::Ceiling(($ends - [DateTimeOffset]::UtcNow).TotalSeconds)) }
        $name = [string]$w.name
        if ($name.Length -gt 28) { $name = $name.Substring(0, 28) + '...' }
        $appNotice.Text.Text = '{0} closes in {1}:{2:00}' -f $name, [int][Math]::Floor($left / 60), ($left % 60)
        $slot = 0
        if ($countdownForm.Visible) { $slot = 1 }
        Show-Notice $appNotice $slot
    } elseif ($appNotice.Form.Visible) {
        $appNotice.Form.Hide()
    }

    # Warning over: ask those apps to close themselves (they can still save). The watchdog, which can't
    # reach windows in this session, force-closes anything still running a few seconds later.
    $closing = @()
    if ($null -ne $u -and $u.PSObject.Properties['app_closing']) { $closing = @($u.app_closing) }
    if ($closing.Count) {
        foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
            try {
                if ($script:closeRequested.ContainsKey($p.Id) -or -not $p.Path) { continue }
                foreach ($c in $closing) {
                    if ([string]::Equals([string]$c, $p.Path, [StringComparison]::OrdinalIgnoreCase)) {
                        $script:closeRequested[$p.Id] = $true
                        [void]$p.CloseMainWindow()
                    }
                }
            } catch { }
        }
    } elseif ($script:closeRequested.Count) {
        $script:closeRequested.Clear()
    }

    # Lock screen
    if ($mode -eq 'locked') {
        if ($u.reason -eq 'immediate') {
            $text = 'A parent has locked this PC.'
            if ($u.lock_reason_text) { $text += "`n" + [string]$u.lock_reason_text }
        } else {
            $text = 'Screen time is over for today.'
            if ($u.unlock_at) { $text += "`nThe PC unlocks at $(Format-LocalTime $u.unlock_at)." }
        }
        $lockSubtitle.Text = $text

        $pinLockedUntil = ConvertTo-KcDate $u.pin_locked_until
        $pinBlocked = $null -ne $pinLockedUntil -and [DateTimeOffset]::UtcNow -lt $pinLockedUntil
        $pinEnabled = [bool]$u.pin_enabled
        $pinLabel.Visible = $pinEnabled
        $pinBox.Visible = $pinEnabled
        $pinButton.Visible = $pinEnabled
        $pinBox.Enabled = -not $pinBlocked
        $result = $u.pin_result
        if ($script:pendingPin -and $result -and $result.request_id -eq $script:pendingPin) {
            $pinStatus.Text = [string]$result.message
            $script:pendingPin = $null
        }
        $pinButton.Enabled = (-not $pinBlocked) -and ($null -eq $script:pendingPin)
        if ($pinBlocked -and -not $script:pendingPin) { $pinStatus.Text = "Too many wrong attempts. Try again after $(Format-LocalTime $u.pin_locked_until)." }

        if (-not $lockForm.Visible) {
            $lockForm.Bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
            $lockForm.Show()
            $pinStatus.Text = ''
            Invoke-Lock
        }
        if (-not $lockForm.ContainsFocus) { $lockForm.Activate(); $pinBox.Focus() | Out-Null }
        $now = [DateTime]::UtcNow
        # Re-lock once the child has had time to reach the PIN box and isn't typing.
        if (($now - $script:lastLockCall).TotalSeconds -ge $RelockAfterSeconds -and ($now - $script:lastInteraction).TotalSeconds -ge $RelockAfterSeconds -and -not $script:pendingPin) {
            Invoke-Lock
        }
    } elseif ($lockForm.Visible) {
        $lockForm.Hide()
        $script:pendingPin = $null
        $pinStatus.Text = ''
    }

    # Message
    $msg = $null
    if ($null -ne $u) { $msg = $u.message }
    $wantId = $null
    if ($msg) { $wantId = [string]$msg.id }
    if ($script:messageId -and $script:messageId -ne $wantId) { Close-MessageForm 'admin_cleared' }
    if ($wantId -and $null -eq $script:messageForm) { Show-MessageForm $msg }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
        try { Update-Ui } catch { Report-UiError ("UI tick failed: {0}" -f $_.Exception.Message) }
    })
$timer.Start()
try { Update-Ui } catch { Report-UiError ("UI start failed: {0}" -f $_.Exception.Message) }
[System.Windows.Forms.Application]::Run((New-Object System.Windows.Forms.ApplicationContext))

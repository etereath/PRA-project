param()

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PraNativeKeyboard {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern void keybd_event(byte virtualKey, byte scanCode, uint flags, UIntPtr extraInfo);
}
"@

$SchemaVersion = 'shadowbot-host-helper-1.0'
$RunLabel = [string]([char]0x8FD0) + [string]([char]0x884C)
$RunApplicationLabel = $RunLabel + [string]([char]0x5E94) + [string]([char]0x7528)
$SaveLabel = [string]([char]0x4FDD) + [string]([char]0x5B58)
$ShadowBotWindowLabel = [string]([char]0x5F71) + [string]([char]0x5200)
$ProgramFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
if ([string]::IsNullOrWhiteSpace($ProgramFilesX86)) {
    $ProgramFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
}
$AllowedInstallRoot = [IO.Path]::GetFullPath((Join-Path $ProgramFilesX86 'ShadowBot'))

function Write-Response {
    param(
        [hashtable]$Request,
        [bool]$Ok,
        [string]$Detail,
        [hashtable]$Extra = @{}
    )
    $response = @{
        schema_version = $SchemaVersion
        action = [string]$Request.action
        app_name = [string]$Request.app_name
        ok = $Ok
        detail = $Detail
    }
    foreach ($key in $Extra.Keys) {
        $response[$key] = $Extra[$key]
    }
    [Console]::Out.Write(($response | ConvertTo-Json -Compress -Depth 8))
}

function Get-VerifiedShellProcesses {
    $verified = @()
    foreach ($process in @(Get-Process -Name 'ShadowBot.Shell' -ErrorAction SilentlyContinue)) {
        try {
            $path = [IO.Path]::GetFullPath([string]$process.Path)
        }
        catch {
            throw 'Cannot verify one ShadowBot.Shell process path.'
        }
        if (-not $path.StartsWith($AllowedInstallRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'A ShadowBot.Shell process is outside the approved installation root.'
        }
        $verified += [pscustomobject]@{
            process = $process
            path = $path
        }
    }
    return $verified
}

function Get-ShellExecutable {
    $configured = [Environment]::GetEnvironmentVariable('SHADOWBOT_SHELL_EXE')
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        $candidate = [IO.Path]::GetFullPath($configured)
        if (-not $candidate.StartsWith($AllowedInstallRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'SHADOWBOT_SHELL_EXE is outside the approved installation root.'
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw 'SHADOWBOT_SHELL_EXE does not exist.'
        }
        return $candidate
    }
    $candidate = Get-ChildItem -LiteralPath $AllowedInstallRoot -Directory -Filter 'shadowbot-*' |
        ForEach-Object {
            $versionText = $_.Name.Substring('shadowbot-'.Length)
            $version = $null
            if ([Version]::TryParse($versionText, [ref]$version)) {
                $exe = Join-Path $_.FullName 'ShadowBot.Shell.exe'
                if (Test-Path -LiteralPath $exe -PathType Leaf) {
                    [pscustomobject]@{ version = $version; path = $exe }
                }
            }
        } |
        Sort-Object version -Descending |
        Select-Object -First 1
    if ($null -eq $candidate) {
        throw 'No approved ShadowBot.Shell.exe installation was found.'
    }
    return [IO.Path]::GetFullPath([string]$candidate.path)
}

function Find-MainWindow {
    param([System.Diagnostics.Process[]]$Processes)
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $candidates = [System.Collections.Generic.List[
        System.Windows.Automation.AutomationElement
    ]]::new()
    foreach ($process in $Processes) {
        $condition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            $process.Id
        )
        foreach ($window in $root.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            $condition
        )) {
            $candidates.Add($window)
        }
    }
    $named = @(
        $candidates |
            Where-Object { [string]$_.Current.Name -eq $ShadowBotWindowLabel }
    )
    if ($named.Count -eq 1) {
        return $named[0]
    }
    if ($named.Count -eq 0 -and $candidates.Count -eq 1) {
        return $candidates[0]
    }
    return $null
}

function Find-NamedElement {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [string]$Name
    )
    if ($null -eq $Root) {
        return $null
    }
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        $Name
    )
    return $Root.FindFirst(
        [System.Windows.Automation.TreeScope]::Descendants,
        $condition
    )
}

function Find-UniqueNamedElement {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [string]$Name
    )
    if ($null -eq $Root) {
        return $null
    }
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        $Name
    )
    $matches = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        $condition
    )
    if ($matches.Count -ne 1) {
        return $null
    }
    return $matches.Item(0)
}

function Find-RunButtonNearApp {
    param(
        [System.Windows.Automation.AutomationElement]$AppElement
    )
    if ($null -eq $AppElement) {
        return $null
    }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $container = $AppElement
    for ($depth = 0; $depth -lt 6 -and $null -ne $container; $depth++) {
        $buttons = $container.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Button
            )
        )
        $resolved = $null
        $resolvedCount = 0
        foreach ($button in $buttons) {
            $name = [string]$button.Current.Name
            $automationId = [string]$button.Current.AutomationId
            if (
                $name.Trim() -notin @($RunApplicationLabel, $RunLabel, 'Run') -and
                $automationId -notmatch '(?i)(run|play|start)'
            ) {
                continue
            }
            try {
                $null = $button.GetCurrentPattern(
                    [System.Windows.Automation.InvokePattern]::Pattern
                )
            }
            catch {
                continue
            }
            $resolved = $button
            $resolvedCount++
        }
        if ($resolvedCount -eq 1) {
            return $resolved
        }
        if ($resolvedCount -gt 1) {
            return $null
        }
        $container = $walker.GetParent($container)
    }
    return $null
}

function Get-AppRowSelectionPattern {
    param([System.Windows.Automation.AutomationElement]$AppElement)
    if ($null -eq $AppElement) {
        return $null
    }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $row = $walker.GetParent($AppElement)
    if (
        $null -eq $row -or
        [string]$row.Current.ControlType.ProgrammaticName -ne 'ControlType.ListItem'
    ) {
        return $null
    }
    try {
        return $row.GetCurrentPattern(
            [System.Windows.Automation.SelectionItemPattern]::Pattern
        )
    }
    catch {
        return $null
    }
}

function Inspect-Host {
    param([string]$AppName)
    $verified = @(Get-VerifiedShellProcesses)
    $processes = @($verified | ForEach-Object { $_.process })
    $window = Find-MainWindow -Processes $processes
    $appElement = Find-UniqueNamedElement -Root $window -Name $AppName
    $runButton = Find-RunButtonNearApp -AppElement $appElement
    $selectionPattern = Get-AppRowSelectionPattern -AppElement $appElement
    $saveButton = Find-NamedElement -Root $window -Name $SaveLabel
    $editorOpen = $null -ne $saveButton
    return @{
        main_window_locatable = $null -ne $window
        app_list_locatable = (
            $null -ne $appElement -and
            ($null -ne $runButton -or $null -ne $selectionPattern)
        )
        run_button_locatable = $null -ne $runButton
        target_app_is_selected = (
            $null -ne $selectionPattern -and
            $selectionPattern.Current.IsSelected
        )
        editor_open = $editorOpen
        unsaved_changes = $editorOpen
        process_paths_verified = $true
        verified_process_paths = @($verified | ForEach-Object { $_.path })
        target_app_match_count = if ($null -ne $appElement) { 1 } else { 0 }
    }
}

$request = @{}
try {
    $raw = [Console]::In.ReadToEnd()
    $parsedRequest = $raw | ConvertFrom-Json
    foreach ($property in $parsedRequest.PSObject.Properties) {
        $request[$property.Name] = $property.Value
    }
    if ([string]$request.schema_version -ne $SchemaVersion) {
        throw 'Unsupported host helper schema_version.'
    }
    if ([string]$request.app_name -ne 'test2') {
        throw 'Only the test2 app is permitted.'
    }
    $action = [string]$request.action
    if ($action -notin @('inspect', 'start_test2', 'restart_shadowbot', 'send_quit_hotkey')) {
        throw 'Unsupported host helper action.'
    }

    if ($action -eq 'inspect') {
        $inspection = Inspect-Host -AppName 'test2'
        Write-Response -Request $request -Ok $true -Detail 'Host inspection completed.' -Extra $inspection
        exit 0
    }

    $before = Inspect-Host -AppName 'test2'
    if ($before.editor_open -or $before.unsaved_changes) {
        Write-Response -Request $request -Ok $false -Detail 'ShadowBot editor is open; refusing host action.' -Extra $before
        exit 0
    }

    if ($action -eq 'start_test2') {
        $verified = @(Get-VerifiedShellProcesses)
        $window = Find-MainWindow -Processes @($verified | ForEach-Object { $_.process })
        $appElement = Find-UniqueNamedElement -Root $window -Name 'test2'
        if ($null -ne $appElement) {
            $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
            $appRow = $walker.GetParent($appElement)
            if ($null -ne $appRow) {
                try {
                    $appRow.SetFocus()
                }
                catch {
                    # SelectionItemPattern below remains the authoritative binding.
                }
            }
        }
        $selectionPattern = Get-AppRowSelectionPattern -AppElement $appElement
        if ($null -ne $selectionPattern) {
            $selectionPattern.Select()
        }
        $runButton = $null
        for ($attempt = 0; $attempt -lt 12 -and $null -eq $runButton; $attempt++) {
            Start-Sleep -Milliseconds 250
            $window = Find-MainWindow -Processes @(
                $verified | ForEach-Object { $_.process }
            )
            $appElement = Find-UniqueNamedElement -Root $window -Name 'test2'
            $runButton = Find-RunButtonNearApp -AppElement $appElement
        }
        if ($null -eq $runButton) {
            $afterSelection = Inspect-Host -AppName 'test2'
            Write-Response -Request $request -Ok $false -Detail 'The test2 row or semantic run button is not safely locatable.' -Extra $afterSelection
            exit 0
        }
        $pattern = $runButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
        Write-Response -Request $request -Ok $true -Detail 'The test2 run control was invoked.' -Extra $before
        exit 0
    }

    if ($action -eq 'send_quit_hotkey') {
        [PraNativeKeyboard]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero)
        [PraNativeKeyboard]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
        [PraNativeKeyboard]::keybd_event(0x51, 0, 0, [UIntPtr]::Zero)
        [PraNativeKeyboard]::keybd_event(0x51, 0, 2, [UIntPtr]::Zero)
        [PraNativeKeyboard]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
        [PraNativeKeyboard]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
        Write-Response -Request $request -Ok $true -Detail 'Ctrl+Alt+Q was sent.' -Extra $before
        exit 0
    }

    $verified = @(Get-VerifiedShellProcesses)
    foreach ($item in $verified) {
        Stop-Process -Id $item.process.Id -Force -ErrorAction Stop
    }
    $shellExecutable = Get-ShellExecutable
    Start-Process -FilePath $shellExecutable -WindowStyle Normal
    Write-Response -Request $request -Ok $true -Detail 'ShadowBot shell restart was requested.' -Extra @{
        main_window_locatable = $false
        app_list_locatable = $false
        editor_open = $false
        unsaved_changes = $false
        process_paths_verified = $true
        verified_process_paths = @($verified | ForEach-Object { $_.path })
        started_executable = $shellExecutable
    }
    exit 0
}
catch {
    if ($request -isnot [hashtable]) {
        $request = @{}
    }
    Write-Response -Request $request -Ok $false -Detail ([string]$_.Exception.Message) -Extra @{
        main_window_locatable = $false
        app_list_locatable = $false
        editor_open = $false
        unsaved_changes = $false
        process_paths_verified = $false
    }
    exit 0
}

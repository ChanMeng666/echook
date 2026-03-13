#Requires -Version 5.1
<#
.SYNOPSIS
    Claude Code Audio Hooks - Windows PowerShell Installation Script

.DESCRIPTION
    This script installs Claude Code Audio Hooks on Windows without requiring Git Bash.
    It configures Python-based hooks for reliable audio playback.

.PARAMETER NonInteractive
    Skip all prompts and use default settings.

.PARAMETER Help
    Display this help message.

.EXAMPLE
    .\install-windows.ps1
    Interactive installation with prompts.

.EXAMPLE
    .\install-windows.ps1 -NonInteractive
    Automated installation without prompts.

.NOTES
    Version: 4.2.0
    Requires: Python 3.6+, Claude Code CLI
#>

param(
    [switch]$NonInteractive,
    [switch]$Help
)

# =============================================================================
# CONFIGURATION
# =============================================================================

$Version = "4.2.0"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$HooksDir = Join-Path $ClaudeDir "hooks"
$LogFile = Join-Path $env:TEMP "claude_hooks_install_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Colors for output
$Colors = @{
    Success = "Green"
    Error = "Red"
    Warning = "Yellow"
    Info = "Cyan"
    Header = "Magenta"
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp | $Level | $Message" | Out-File -FilePath $LogFile -Append -Encoding UTF8
}

function Write-Step {
    param([int]$StepNum, [int]$TotalSteps, [string]$Message)
    $prefix = "[$StepNum/$TotalSteps]"
    Write-Host "$prefix " -ForegroundColor $Colors.Header -NoNewline
    Write-Host $Message -ForegroundColor White
    Write-Log $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] " -ForegroundColor $Colors.Success -NoNewline
    Write-Host $Message
    Write-Log $Message "SUCCESS"
}

function Write-Error2 {
    param([string]$Message)
    Write-Host "  [X] " -ForegroundColor $Colors.Error -NoNewline
    Write-Host $Message
    Write-Log $Message "ERROR"
}

function Write-Warning2 {
    param([string]$Message)
    Write-Host "  [!] " -ForegroundColor $Colors.Warning -NoNewline
    Write-Host $Message
    Write-Log $Message "WARNING"
}

function Write-Info {
    param([string]$Message)
    Write-Host "  [i] " -ForegroundColor $Colors.Info -NoNewline
    Write-Host $Message
    Write-Log $Message "INFO"
}

function Test-PythonVersion {
    # Try 'py' launcher first (most reliable on Windows)
    $pythonCommands = @("py", "python3", "python")

    foreach ($cmd in $pythonCommands) {
        try {
            $version = & $cmd --version 2>&1
            if ($version -match "Python 3\.(\d+)") {
                $minor = [int]$Matches[1]
                if ($minor -ge 6) {
                    return @{
                        Command = $cmd
                        Version = $version
                        Valid = $true
                    }
                }
            }
        } catch {
            continue
        }
    }

    return @{
        Command = $null
        Version = $null
        Valid = $false
    }
}

function Test-ClaudeCode {
    try {
        $version = & claude --version 2>&1
        return @{
            Installed = $true
            Version = $version
        }
    } catch {
        return @{
            Installed = $false
            Version = $null
        }
    }
}

# =============================================================================
# INSTALLATION STEPS
# =============================================================================

function Show-Help {
    Write-Host ""
    Write-Host "Claude Code Audio Hooks - Windows Installation Script" -ForegroundColor $Colors.Header
    Write-Host "Version: $Version" -ForegroundColor $Colors.Info
    Write-Host ""
    Write-Host "USAGE:" -ForegroundColor White
    Write-Host "  .\install-windows.ps1 [OPTIONS]"
    Write-Host ""
    Write-Host "OPTIONS:" -ForegroundColor White
    Write-Host "  -NonInteractive    Skip all prompts, use defaults"
    Write-Host "  -Help              Show this help message"
    Write-Host ""
    Write-Host "EXAMPLES:" -ForegroundColor White
    Write-Host "  .\install-windows.ps1                  # Interactive installation"
    Write-Host "  .\install-windows.ps1 -NonInteractive  # Automated installation"
    Write-Host ""
    Write-Host "REQUIREMENTS:" -ForegroundColor White
    Write-Host "  - Python 3.6 or higher"
    Write-Host "  - Claude Code CLI"
    Write-Host ""
    exit 0
}

function Step-Welcome {
    Write-Host ""
    Write-Host "================================================" -ForegroundColor $Colors.Header
    Write-Host "  Claude Code Audio Hooks v$Version" -ForegroundColor $Colors.Header
    Write-Host "  Windows PowerShell Installation" -ForegroundColor $Colors.Header
    Write-Host "================================================" -ForegroundColor $Colors.Header
    Write-Host ""
    Write-Info "Installation log: $LogFile"
    Write-Host ""
}

function Step-CheckPrerequisites {
    Write-Step 1 7 "Checking Prerequisites"

    $errors = 0

    # Check Python
    $python = Test-PythonVersion
    if ($python.Valid) {
        Write-Success "Python: $($python.Version) (using '$($python.Command)')"
        $script:PythonCmd = $python.Command
    } else {
        Write-Error2 "Python 3.6+ not found"
        Write-Info "  Download from: https://www.python.org/downloads/"
        Write-Info "  Make sure to check 'Add Python to PATH' during installation"
        $errors++
    }

    # Check Claude Code
    $claude = Test-ClaudeCode
    if ($claude.Installed) {
        Write-Success "Claude Code: $($claude.Version)"
    } else {
        Write-Error2 "Claude Code not found"
        Write-Info "  Install from: https://docs.anthropic.com/claude/docs/claude-code"
        $errors++
    }

    if ($errors -gt 0) {
        Write-Host ""
        Write-Error2 "Missing $errors required dependencies"
        Write-Info "Please install missing dependencies and try again"
        exit 1
    }
}

function Step-ValidateProject {
    Write-Step 2 7 "Validating Project Structure"

    $valid = 0

    # Check required directories and files
    $checks = @(
        @{ Path = (Join-Path $ProjectDir "hooks"); Type = "Directory"; Name = "hooks/ directory" },
        @{ Path = (Join-Path $ProjectDir "audio\default"); Type = "Directory"; Name = "audio/default/ directory" },
        @{ Path = (Join-Path $ProjectDir "config"); Type = "Directory"; Name = "config/ directory" },
        @{ Path = (Join-Path $ProjectDir "hooks\hook_runner.py"); Type = "File"; Name = "hook_runner.py" }
    )

    foreach ($check in $checks) {
        $exists = if ($check.Type -eq "Directory") { Test-Path -Path $check.Path -PathType Container } else { Test-Path -Path $check.Path -PathType Leaf }
        if ($exists) {
            Write-Success "$($check.Name) found"
            $valid++
        } else {
            Write-Error2 "$($check.Name) not found"
        }
    }

    # Check audio files
    $audioFiles = Get-ChildItem -Path (Join-Path $ProjectDir "audio\default") -Filter "*.mp3" -ErrorAction SilentlyContinue
    if ($audioFiles.Count -ge 14) {
        Write-Success "Audio files: $($audioFiles.Count) MP3 files found"
        $valid++
    } else {
        Write-Warning2 "Audio files: Only $($audioFiles.Count) MP3 files found"
    }

    if ($valid -lt 4) {
        Write-Host ""
        Write-Error2 "Project structure incomplete"
        Write-Info "Make sure you're running from the project root directory"
        exit 1
    }
}

function Step-InstallHooks {
    Write-Step 3 7 "Installing Hook Scripts"

    # Create hooks directory
    if (-not (Test-Path $HooksDir)) {
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
    }
    Write-Success "Created hooks directory: $HooksDir"

    # Copy hook_runner.py
    $sourceRunner = Join-Path $ProjectDir "hooks\hook_runner.py"
    $destRunner = Join-Path $HooksDir "hook_runner.py"
    Copy-Item -Path $sourceRunner -Destination $destRunner -Force
    Write-Success "Installed hook_runner.py"

    # Save project path (Windows format, UTF-8 without BOM)
    $projectPathFile = Join-Path $HooksDir ".project_path"
    $projectPathContent = $ProjectDir.Replace('\', '/')
    # Use .NET method to write UTF-8 without BOM (PowerShell 5.x's -Encoding UTF8 adds BOM)
    [System.IO.File]::WriteAllText($projectPathFile, $projectPathContent, [System.Text.UTF8Encoding]::new($false))
    Write-Success "Recorded project path: $ProjectDir"

    # Install /snooze slash command
    $sourceSnooze = Join-Path $ProjectDir "commands\snooze.md"
    if (Test-Path $sourceSnooze) {
        $commandsDir = Join-Path $ClaudeDir "commands"
        if (-not (Test-Path $commandsDir)) {
            New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
        }
        Copy-Item -Path $sourceSnooze -Destination (Join-Path $commandsDir "snooze.md") -Force
        Write-Success "Installed /snooze slash command"
    }
}

function Step-ConfigureSettings {
    Write-Step 4 7 "Configuring Claude Settings"

    $settingsFile = Join-Path $ClaudeDir "settings.json"

    # Backup existing settings
    if (Test-Path $settingsFile) {
        $backupFile = "$settingsFile.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -Path $settingsFile -Destination $backupFile
        Write-Info "Backed up existing settings"
    }

    # Read or create settings
    if (Test-Path $settingsFile) {
        $settings = Get-Content $settingsFile -Raw | ConvertFrom-Json
    } else {
        $settings = [PSCustomObject]@{}
    }

    # Ensure hooks property exists
    if (-not $settings.PSObject.Properties['hooks']) {
        $settings | Add-Member -NotePropertyName 'hooks' -NotePropertyValue ([PSCustomObject]@{})
    }

    # Configure hooks using Python runner
    $hookRunnerPath = ($HooksDir.Replace('\', '/')) + "/hook_runner.py"

    # Load preferences to determine which hooks are enabled
    $defaultPrefs = Join-Path $ProjectDir "config\default_preferences.json"
    $userPrefs = Join-Path $ProjectDir "config\user_preferences.json"
    $prefsFile = if (Test-Path $userPrefs) { $userPrefs } elseif (Test-Path $defaultPrefs) { $defaultPrefs } else { $null }

    $enabledHooks = @{}
    if ($prefsFile) {
        try {
            $prefs = Get-Content $prefsFile -Raw | ConvertFrom-Json
            $prefs.enabled_hooks.PSObject.Properties | ForEach-Object {
                if (-not $_.Name.StartsWith('_') -and $_.Value -eq $true) {
                    $enabledHooks[$_.Name] = $true
                }
            }
        } catch {
            $enabledHooks = @{ 'notification' = $true; 'stop' = $true; 'subagent_stop' = $true; 'permission_request' = $true }
        }
    } else {
        $enabledHooks = @{ 'notification' = $true; 'stop' = $true; 'subagent_stop' = $true; 'permission_request' = $true }
    }

    $hookTypes = @{
        'Notification' = 'notification'
        'Stop' = 'stop'
        'PreToolUse' = 'pretooluse'
        'PostToolUse' = 'posttooluse'
        'UserPromptSubmit' = 'userpromptsubmit'
        'SubagentStop' = 'subagent_stop'
        'PreCompact' = 'precompact'
        'SessionStart' = 'session_start'
        'SessionEnd' = 'session_end'
        'PermissionRequest' = 'permission_request'
        'PostToolUseFailure' = 'posttoolusefailure'
        'SubagentStart' = 'subagent_start'
        'TeammateIdle' = 'teammate_idle'
        'TaskCompleted' = 'task_completed'
    }

    $hooksWithMatcher = @('PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest', 'SubagentStart')
    $registered = 0

    foreach ($hookName in $hookTypes.Keys) {
        $hookType = $hookTypes[$hookName]
        if (-not $enabledHooks.ContainsKey($hookType)) { continue }

        $command = "py `"$hookRunnerPath`" $hookType || true"

        if ($hooksWithMatcher -contains $hookName) {
            $hookConfig = @(
                @{
                    matcher = ''
                    hooks = @(
                        @{ type = 'command'; command = $command; timeout = 10 }
                    )
                }
            )
        } else {
            $hookConfig = @(
                @{
                    hooks = @(
                        @{ type = 'command'; command = $command; timeout = 10 }
                    )
                }
            )
        }

        # Remove existing property if it exists and add new one
        if ($settings.hooks.PSObject.Properties[$hookName]) {
            $settings.hooks.PSObject.Properties.Remove($hookName)
        }
        $settings.hooks | Add-Member -NotePropertyName $hookName -NotePropertyValue $hookConfig
        $registered++
    }

    # Save settings (UTF-8 without BOM)
    $jsonContent = $settings | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($settingsFile, $jsonContent, [System.Text.UTF8Encoding]::new($false))
    Write-Success "Configured $registered hook(s) in settings.json"
}

function Step-InitializeConfig {
    Write-Step 5 7 "Initializing Configuration"

    $userPrefs = Join-Path $ProjectDir "config\user_preferences.json"
    $defaultPrefs = Join-Path $ProjectDir "config\default_preferences.json"

    if (-not (Test-Path $userPrefs)) {
        if (Test-Path $defaultPrefs) {
            Copy-Item -Path $defaultPrefs -Destination $userPrefs
            Write-Success "Created user_preferences.json from defaults"
        } else {
            Write-Warning2 "default_preferences.json not found"
        }
    } else {
        Write-Success "user_preferences.json already exists"
    }
}

function Step-RunTests {
    Write-Step 6 7 "Running Installation Tests"

    $passed = 0
    $failed = 0

    # Test 1: Hook runner exists
    $hookRunner = Join-Path $HooksDir "hook_runner.py"
    if (Test-Path $hookRunner) {
        Write-Success "Hook runner installed"
        $passed++
    } else {
        Write-Error2 "Hook runner not found"
        $failed++
    }

    # Test 2: Settings configured
    $settingsFile = Join-Path $ClaudeDir "settings.json"
    if (Test-Path $settingsFile) {
        $content = Get-Content $settingsFile -Raw
        if ($content -match "hook_runner.py") {
            Write-Success "Settings configured correctly"
            $passed++
        } else {
            Write-Warning2 "Settings may not be configured correctly"
            $failed++
        }
    } else {
        Write-Error2 "Settings file not found"
        $failed++
    }

    # Test 3: Project path recorded
    $projectPathFile = Join-Path $HooksDir ".project_path"
    if (Test-Path $projectPathFile) {
        Write-Success "Project path recorded"
        $passed++
    } else {
        Write-Error2 "Project path not recorded"
        $failed++
    }

    # Test 4: Audio files accessible
    $audioDir = Join-Path $ProjectDir "audio\default"
    if (Test-Path $audioDir) {
        $audioCount = (Get-ChildItem -Path $audioDir -Filter "*.mp3").Count
        Write-Success "Audio files accessible ($audioCount files)"
        $passed++
    } else {
        Write-Error2 "Audio directory not accessible"
        $failed++
    }

    Write-Host ""
    Write-Info "Tests: $passed passed, $failed failed"
}

function Step-Complete {
    Write-Step 7 7 "Installation Complete!"

    Write-Host ""
    Write-Host "================================================" -ForegroundColor $Colors.Header
    Write-Host "  Installation Summary" -ForegroundColor $Colors.Header
    Write-Host "================================================" -ForegroundColor $Colors.Header
    Write-Host ""

    Write-Success "Installation completed successfully!"
    Write-Host ""
    Write-Host "Next Steps:" -ForegroundColor White
    Write-Host ""
    Write-Host "  1. " -NoNewline; Write-Host "Restart Claude Code" -ForegroundColor $Colors.Info
    Write-Host "     (Settings require restart to take effect)"
    Write-Host ""
    Write-Host "  2. " -NoNewline; Write-Host "Test with Claude:" -ForegroundColor $Colors.Info
    Write-Host "     claude `"What is 2+2?`""
    Write-Host "     (You should hear audio when Claude responds)"
    Write-Host ""
    Write-Host "  3. " -NoNewline; Write-Host "Enable debug logging (if issues):" -ForegroundColor $Colors.Info
    Write-Host "     `$env:CLAUDE_HOOKS_DEBUG = '1'"
    Write-Host ""
    Write-Host "  4. " -NoNewline; Write-Host "View logs:" -ForegroundColor $Colors.Info
    Write-Host "     Get-Content `$env:TEMP\claude_audio_hooks_queue\logs\hook_triggers.log"
    Write-Host ""

    Write-Host "Installation log saved to:" -ForegroundColor White
    Write-Host "  $LogFile" -ForegroundColor $Colors.Info
    Write-Host ""
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================

if ($Help) {
    Show-Help
}

# Initialize log
"Claude Code Audio Hooks - Installation Log" | Out-File -FilePath $LogFile -Encoding UTF8
"Started: $(Get-Date)" | Out-File -FilePath $LogFile -Append -Encoding UTF8
"" | Out-File -FilePath $LogFile -Append -Encoding UTF8

try {
    Step-Welcome
    Step-CheckPrerequisites
    Step-ValidateProject
    Step-InstallHooks
    Step-ConfigureSettings
    Step-InitializeConfig
    Step-RunTests
    Step-Complete
    exit 0
} catch {
    Write-Host ""
    Write-Error2 "Installation failed: $_"
    Write-Host ""
    Write-Info "Check the log file for details: $LogFile"
    Write-Log "FATAL: $_" "ERROR"
    exit 1
}

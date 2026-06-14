# agentmemory PowerShell terminal hook
# Source this file from your $PROFILE to automatically log terminal commands.
#
# Usage:
#   . C:\path\to\powershell-hook.ps1
#
# Configuration (set as environment variables or override at top of profile):
#   $env:AGENTMEMORY_URL    = "http://127.0.0.1:3111"  (default)
#   $env:AGENTMEMORY_SECRET = ""                         (optional)
#   $env:AGENTMEMORY_AGENT_ID = "powershell"             (default)

param(
    [string]$ServerUrl   = $env:AGENTMEMORY_URL   ?? "http://127.0.0.1:3111",
    [string]$AgentId     = $env:AGENTMEMORY_AGENT_ID ?? "powershell",
    [string]$Secret      = $env:AGENTMEMORY_SECRET ?? ""
)

function Send-AgentMemoryObservation {
    param(
        [string]$Text,
        [string]$FolderPath = (Get-Location).Path
    )

    $payload = @{
        folderPath = $FolderPath
        agentId    = $AgentId
        text       = $Text.Substring(0, [Math]::Min($Text.Length, 4000))
        timestamp  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json -Compress

    $headers = @{ "Content-Type" = "application/json" }
    if ($Secret) {
        $headers["Authorization"] = "Bearer $Secret"
    }

    try {
        Invoke-RestMethod `
            -Uri "$ServerUrl/agentmemory/agent/observe" `
            -Method Post `
            -Headers $headers `
            -Body $payload `
            -TimeoutSec 5 `
            -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Non-fatal — agentmemory is a best-effort sidecar
    }
}

# Hook into PSReadLine's CommandValidationHandler to log every command
if (Get-Module -Name PSReadLine -ErrorAction SilentlyContinue) {
    $existingHandler = (Get-PSReadLineOption).CommandValidationHandler

    Set-PSReadLineOption -CommandValidationHandler {
        param([System.Management.Automation.Language.CommandAst]$CommandAst)

        # Forward to existing handler if any
        if ($existingHandler) { & $existingHandler $CommandAst }

        # Log the command to agentmemory (best-effort, non-blocking)
        $cmd = $CommandAst.ToString()
        if ($cmd -and $cmd.Trim()) {
            $job = Start-Job -ScriptBlock {
                param($u, $a, $s, $t, $f)
                $payload = "{`"folderPath`":`"$f`",`"agentId`":`"$a`",`"text`":`"$t`",`"timestamp`":`"$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')`"}"
                $h = @{"Content-Type"="application/json"}
                if ($s) { $h["Authorization"] = "Bearer $s" }
                try { Invoke-RestMethod -Uri "$u/agentmemory/agent/observe" -Method Post -Headers $h -Body $payload -TimeoutSec 5 -EA SilentlyContinue | Out-Null } catch {}
            } -ArgumentList $ServerUrl, $AgentId, $Secret, $cmd, (Get-Location).Path
            $null = $job  # fire-and-forget
        }

        return $true
    }
    Write-Host "[agentmemory] PowerShell hook active (agent: $AgentId, server: $ServerUrl)"
} else {
    Write-Warning "[agentmemory] PSReadLine not available — hook not installed. Call Send-AgentMemoryObservation manually."
}

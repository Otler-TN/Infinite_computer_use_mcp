# The token arrives over stdin, never in command-line arguments or transcripts.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
try {
    $taskToken = [Console]::In.ReadToEnd().Trim()
    if ($taskToken -notmatch '^[A-Za-z0-9_-]{16,256}$') { throw 'Paste only your ngrok authtoken.' }
    $taskFolder = Join-Path (Split-Path -Parent $PSScriptRoot) '.run\private'
    New-Item -ItemType Directory -Force -Path $taskFolder | Out-Null
    $taskAcl = New-Object Security.AccessControl.DirectorySecurity
    $taskAcl.SetAccessRuleProtection($true, $false)
    $taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    foreach ($taskPrincipal in @($taskSid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
        $taskRule = [Security.AccessControl.FileSystemAccessRule]::new($taskPrincipal, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $taskAcl.AddAccessRule($taskRule)
    }
    Set-Acl -LiteralPath $taskFolder -AclObject $taskAcl
    $taskPath = Join-Path $taskFolder 'ngrok.yml'
    $taskTemporary = Join-Path $taskFolder 'ngrok.pending'
    $taskYaml = "version: '3'`nagent:`n  authtoken: '$taskToken'`n"
    [IO.File]::WriteAllText($taskTemporary, $taskYaml, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $taskTemporary -Destination $taskPath -Force
    Write-Output 'Saved. Start MCP to verify your ngrok account.'
} catch {
    Write-Output 'Could not save the token. Paste only the authtoken and check that the project folder is writable.'
    exit 1
}

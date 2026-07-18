#Requires -Version 7.0
<#
.SYNOPSIS
    Claude-Codex continuity helper for Good Badminton.

.DESCRIPTION
    Single continuity CLI described in
    docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md and
    docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md.

    Task 2 implements only the read-only `status` operation plus the internal
    boundaries later tasks extend: Invoke-Git, Get-RepositoryContext,
    Normalize-Id, Normalize-ScopePath, Read-ContinuityState,
    New-OperationResult, Write-Result, and Invoke-Status. `start`, `update`,
    `handoff`, `accept`, and `takeover` are declared in the parameter surface
    per the approved helper contract but are not yet implemented; invoking
    them returns a validation error (exit 2) until later tasks add them.

    `status` never acquires the common continuity lock and never creates
    `<git-common-dir>/ai-continuity`. Missing continuity directories mean
    empty state, not an error.

.NOTES
    Default output is concise human-readable text. `-Json` serializes exactly
    one JSON object to stdout; all diagnostics are kept off stdout so JSON
    output stays parseable.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('status', 'start', 'update', 'handoff', 'accept', 'takeover')]
    [string] $Operation,

    [string] $Workstream,
    [string] $Agent,
    [string] $SessionId,
    [string[]] $Scope,
    [ValidateRange(1, 24)]
    [int] $LeaseHours,
    [string] $ClaimId,
    [string] $Summary,
    [string[]] $ChangedPath,
    [string] $State,
    [string] $NextAction,
    [string] $VerificationResult,
    [string] $VerificationCommand,
    [string] $VerificationCommit,
    [string[]] $VerificationDirtyPath,
    [string] $NotRunReason,
    [string] $PreviousClaimId,
    [string] $Reason,

    [switch] $Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# UTF-8 without BOM for all stdout, matching the project-wide encoding rule.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# ---------------------------------------------------------------------------
# Stable exception types used by the single top-level try/catch/finally below
# to map failures to the design's stable exit codes:
#   2 = validation error / protected-branch use / claim conflict
#   3 = malformed continuity state / lock timeout / atomic-write failure
#   4 = not a Git worktree or Git common directory unavailable
# ---------------------------------------------------------------------------
class ContinuityValidationException : System.Exception {
    ContinuityValidationException([string] $Message) : base($Message) {}
}

class ContinuityStateException : System.Exception {
    ContinuityStateException([string] $Message) : base($Message) {}
}

class ContinuityGitContextException : System.Exception {
    ContinuityGitContextException([string] $Message) : base($Message) {}
}

# Configured protected integration branch for this pilot (design spec,
# "Branch validation"). Read-only inspection is allowed there; mutating
# operations reject it.
$Script:ProtectedBranchName = 'main'

# Directory name for local, uncommitted continuity state under the absolute
# Git common directory (design spec, "Local-only state").
$Script:ContinuityDirName = 'ai-continuity'

# Case-insensitive reserved Windows device names rejected as workstream IDs
# (design spec, "Identifier and path validation").
$Script:ReservedDeviceNames = @(
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
)

function ConvertTo-ForwardSlashPath {
    <#
    .SYNOPSIS
        Normalizes an absolute filesystem path to forward slashes for
        serialization, matching Git's own convention on Windows.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    return $Path.Replace('\', '/')
}

function Invoke-Git {
    <#
    .SYNOPSIS
        Runs Git through System.Diagnostics.Process, preserving NUL bytes and
        exact argument values (no shell interpolation, no PowerShell pipeline
        text mangling). Later tasks parse `git ... -z` output from this same
        boundary.
    .OUTPUTS
        [PSCustomObject] with ExitCode, StdOut, and StdErr.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments,

        [Parameter(Mandatory = $true)]
        [string] $WorkingDirectory
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = 'git'
    foreach ($argument in $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.UseShellExecute = $false
    $startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)

    try {
        $process = [System.Diagnostics.Process]::Start($startInfo)
    }
    catch {
        throw [ContinuityGitContextException]::new("Unable to start git: $($_.Exception.Message)")
    }

    $stdOutTask = $process.StandardOutput.ReadToEndAsync()
    $stdErrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()

    return [PSCustomObject]@{
        ExitCode = $process.ExitCode
        StdOut   = $stdOutTask.GetAwaiter().GetResult()
        StdErr   = $stdErrTask.GetAwaiter().GetResult()
    }
}

function Get-RepositoryContext {
    <#
    .SYNOPSIS
        Read-only Git discovery: repo root, canonical absolute Git common
        directory, current worktree path, branch, full HEAD SHA, upstream,
        and the configured protected branch. Detached HEAD is reported, not
        rejected; only mutating operations (added in later tasks) reject it.
    #>
    param(
        [string] $StartingDirectory = (Get-Location).Path
    )

    $topLevel = Invoke-Git -Arguments @('rev-parse', '--show-toplevel') -WorkingDirectory $StartingDirectory
    if ($topLevel.ExitCode -ne 0) {
        throw [ContinuityGitContextException]::new('Not inside a Git working tree.')
    }
    $repoRoot = [System.IO.Path]::GetFullPath($topLevel.StdOut.Trim())

    $commonDirResult = Invoke-Git -Arguments @('rev-parse', '--git-common-dir') -WorkingDirectory $repoRoot
    if ($commonDirResult.ExitCode -ne 0) {
        throw [ContinuityGitContextException]::new('Unable to resolve the Git common directory.')
    }
    $rawCommonDir = $commonDirResult.StdOut.Trim()
    $gitCommonDir = if ([System.IO.Path]::IsPathRooted($rawCommonDir)) {
        [System.IO.Path]::GetFullPath($rawCommonDir)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $repoRoot $rawCommonDir))
    }

    $branchResult = Invoke-Git -Arguments @('symbolic-ref', '-q', '--short', 'HEAD') -WorkingDirectory $repoRoot
    $detached = $branchResult.ExitCode -ne 0
    $branch = if ($detached) { $null } else { $branchResult.StdOut.Trim() }

    $headResult = Invoke-Git -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $repoRoot
    $head = if ($headResult.ExitCode -eq 0) { $headResult.StdOut.Trim() } else { $null }

    $upstreamResult = Invoke-Git -Arguments @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') -WorkingDirectory $repoRoot
    $upstream = if ($upstreamResult.ExitCode -eq 0) { $upstreamResult.StdOut.Trim() } else { $null }

    return [PSCustomObject]@{
        RepoRoot        = $repoRoot
        GitCommonDir    = $gitCommonDir
        WorktreePath    = $repoRoot
        Branch          = $branch
        Detached        = $detached
        Head            = $head
        Upstream        = $upstream
        ProtectedBranch = $Script:ProtectedBranchName
    }
}

function Normalize-Id {
    <#
    .SYNOPSIS
        Validates and returns a workstream or session ID exactly as the
        approved design's "Identifier and path validation" section requires.
        Invalid input throws ContinuityValidationException (exit 2) before
        any filesystem mutation.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Value,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Workstream', 'Session')]
        [string] $Kind
    )

    if ([string]::IsNullOrEmpty($Value)) {
        throw [ContinuityValidationException]::new("$Kind id must not be empty.")
    }

    switch ($Kind) {
        'Workstream' {
            if ($Value -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
                throw [ContinuityValidationException]::new(
                    "Workstream id '$Value' is invalid; it must match [a-z0-9][a-z0-9-]{0,63}."
                )
            }
            if ($Script:ReservedDeviceNames -contains $Value.ToUpperInvariant()) {
                throw [ContinuityValidationException]::new(
                    "Workstream id '$Value' is a reserved Windows device name."
                )
            }
        }
        'Session' {
            if ($Value -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
                throw [ContinuityValidationException]::new(
                    "Session id '$Value' is invalid; it must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}."
                )
            }
        }
    }

    return $Value
}

function Normalize-ScopePath {
    <#
    .SYNOPSIS
        Normalizes one repository-relative scope path prefix to forward
        slashes and rejects the unsafe forms the design always forbids:
        empty/root scope, `.`/`..`, traversal segments, wildcards, absolute
        or drive-qualified paths, UNC paths, control characters, and `.git`
        or any `.git/` descendant.
    .NOTES
        Task 3 extends this boundary with repository-root containment
        (resolving the nearest existing ancestor to a canonical absolute
        path) and rejection of scopes whose existing path components pass
        through a symbolic link, junction, or other reparse point. `status`
        does not call this function; it exists now so Task 3 extends one
        boundary instead of adding another.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw [ContinuityValidationException]::new('Scope path must not be empty.')
    }

    if ($Path -match '[\x00-\x1f]') {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not contain control characters.")
    }

    $normalized = $Path.Trim().Replace('\', '/')

    if ($normalized -eq '.' -or $normalized -eq '..' -or $normalized -eq '/') {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not be empty, '.', '..', or the repository root.")
    }
    if ($normalized -match '(^|/)\.\.($|/)') {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not contain traversal segments.")
    }
    if ($normalized -match '[\*\?]') {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not contain wildcard characters.")
    }
    if ($normalized -match '^[A-Za-z]:' -or $normalized.StartsWith('//') -or [System.IO.Path]::IsPathRooted($normalized)) {
        throw [ContinuityValidationException]::new("Scope path '$Path' must be repository-relative, not absolute, drive-qualified, or UNC.")
    }
    if ($normalized -eq '.git' -or $normalized -match '(^|/)\.git($|/)') {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not reference '.git'.")
    }

    $normalized = $normalized.TrimEnd('/')
    if ([string]::IsNullOrEmpty($normalized)) {
        throw [ContinuityValidationException]::new("Scope path '$Path' must not resolve to the repository root.")
    }

    return $normalized
}

function Read-ContinuityState {
    <#
    .SYNOPSIS
        Reads local claim state under `<git-common-dir>/ai-continuity/claims`
        without mutation. A missing continuity directory or missing claims
        directory means empty state, not an error. Malformed claim JSON or a
        claim missing a required field throws ContinuityStateException
        (exit 3). This function never creates a directory or lock file.
    .NOTES
        Task 3+ extend this boundary to also read history and pending
        transaction journals when present.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $GitCommonDir
    )

    # A plain array (not System.Collections.Generic.List[object]) is used
    # deliberately: wrapping a generic List in the `@()` array subexpression
    # used later by New-OperationResult triggers a binder error ("Argument
    # types do not match") in this PowerShell runtime, so claims/warnings/
    # errors collections stay plain PowerShell arrays throughout this script.
    $claims = @()

    $claimsDir = Join-Path (Join-Path $GitCommonDir $Script:ContinuityDirName) 'claims'
    if (Test-Path -LiteralPath $claimsDir -PathType Container) {
        $requiredFields = @(
            'schema_version', 'claim_id', 'workstream_id', 'agent', 'session_id',
            'worktree_path', 'branch', 'base_commit', 'scope_paths',
            'started_utc', 'heartbeat_utc', 'lease_until_utc', 'state'
        )

        $claimFiles = @(Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File | Sort-Object Name)
        foreach ($file in $claimFiles) {
            $rawText = [System.IO.File]::ReadAllText($file.FullName, [System.Text.UTF8Encoding]::new($false))

            $parsed = $null
            try {
                $parsed = $rawText | ConvertFrom-Json -ErrorAction Stop
            }
            catch {
                throw [ContinuityStateException]::new("Malformed claim JSON in '$($file.Name)': $($_.Exception.Message)")
            }

            if ($null -eq $parsed -or $parsed -is [array]) {
                throw [ContinuityStateException]::new("Claim file '$($file.Name)' must contain one JSON object.")
            }

            $presentFields = @($parsed.PSObject.Properties.Name)
            foreach ($field in $requiredFields) {
                if ($presentFields -notcontains $field) {
                    throw [ContinuityStateException]::new(
                        "Claim file '$($file.Name)' is missing required field '$field'."
                    )
                }
            }

            $claims += $parsed
        }
    }

    return [PSCustomObject]@{
        Claims = $claims
    }
}

function New-OperationResult {
    <#
    .SYNOPSIS
        Builds the one stable result object every operation returns:
        schema_version, ok, operation, repo_root, git_common_dir,
        workstream_id, claim_id, git, claims, warnings, errors, and
        recovery (only when an operation ends in a recoverable
        successor-owned state). `ok` is true only when Errors is empty.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Operation,
        # RepoRoot/GitCommonDir/WorkstreamId/ClaimId are intentionally
        # untyped (not [string]) so an absent value stays JSON `null`
        # instead of coercing to an empty string.
        $RepoRoot,
        $GitCommonDir,
        $WorkstreamId,
        $ClaimId,
        $Git,
        $Claims,
        $Warnings,
        $Errors,
        $Recovery
    )

    # Assigning directly inside each branch (rather than `$x = if (...) {A}
    # else {B}`) avoids a PowerShell pipeline-unwrapping quirk that collapses
    # a one-element array to its bare element, and an empty array to $null,
    # when an if/else block is used as a value-returning expression.
    $claimsList = @()
    if ($null -ne $Claims) { $claimsList = @($Claims) }
    $warningsList = @()
    if ($null -ne $Warnings) { $warningsList = @($Warnings) }
    $errorsList = @()
    if ($null -ne $Errors) { $errorsList = @($Errors) }

    $ordered = [ordered]@{
        schema_version = 1
        ok             = ($errorsList.Count -eq 0)
        operation      = $Operation
        repo_root      = $RepoRoot
        git_common_dir = $GitCommonDir
        workstream_id  = $WorkstreamId
        claim_id       = $ClaimId
        git            = $Git
        claims         = $claimsList
        warnings       = $warningsList
        errors         = $errorsList
    }

    if ($null -ne $Recovery) {
        $ordered['recovery'] = $Recovery
    }

    return [PSCustomObject] $ordered
}

function Write-HumanReadableResult {
    <#
    .SYNOPSIS
        Renders the concise human-readable report for non-JSON output. Uses
        the same warning/error meaning as the JSON form without leaking JSON
        internals.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Result
    )

    $lines = @()
    $lines += "operation: $($Result.operation)"
    $lines += "ok: $($Result.ok)"
    if ($Result.repo_root) { $lines += "repo_root: $($Result.repo_root)" }
    if ($Result.git_common_dir) { $lines += "git_common_dir: $($Result.git_common_dir)" }
    if ($Result.workstream_id) { $lines += "workstream_id: $($Result.workstream_id)" }
    if ($Result.claim_id) { $lines += "claim_id: $($Result.claim_id)" }

    if ($Result.git) {
        $lines += "branch: $($Result.git.branch)"
        $lines += "detached: $($Result.git.detached)"
        $lines += "head: $($Result.git.head)"
        $lines += "upstream: $($Result.git.upstream)"
        $lines += "protected_branch: $($Result.git.protected_branch)"
    }

    foreach ($warning in $Result.warnings) {
        $lines += "warning: $($warning.message)"
    }
    foreach ($item in $Result.errors) {
        $lines += "error: $($item.message)"
    }

    return ($lines -join [Environment]::NewLine)
}

function Write-Result {
    <#
    .SYNOPSIS
        The single stdout boundary. Default output is concise human-readable
        text; `-Json` serializes exactly one JSON object at sufficient depth.
        No other function writes to stdout, so diagnostic PowerShell streams
        never corrupt JSON output.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Result,
        [switch] $Json
    )

    $text = if ($Json) {
        $Result | ConvertTo-Json -Depth 12
    }
    else {
        Write-HumanReadableResult -Result $Result
    }

    [Console]::Out.Write($text)
    [Console]::Out.Write([Environment]::NewLine)
    [Console]::Out.Flush()
}

function Invoke-Status {
    <#
    .SYNOPSIS
        Read-only report of Git state, claims, malformed claims, and expired
        leases. Never acquires the common lock and never creates
        `.git/ai-continuity`. Detached HEAD and the protected branch are
        reported as warnings, not errors; `status` remains available on
        `main`.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Context,
        [string] $Workstream
    )

    $context = $Context

    $normalizedWorkstream = $null
    if (-not [string]::IsNullOrWhiteSpace($Workstream)) {
        $normalizedWorkstream = Normalize-Id -Value $Workstream -Kind 'Workstream'
    }

    $state = Read-ContinuityState -GitCommonDir $context.GitCommonDir
    $claims = @($state.Claims)

    $warnings = @()

    if ($context.Detached) {
        $warnings += @{ code = 'detached-head'; message = 'HEAD is detached.' }
    }
    if ($context.Branch -eq $context.ProtectedBranch) {
        $warnings += @{
            code    = 'protected-branch'
            message = "Current branch '$($context.ProtectedBranch)' is the protected integration branch; mutating operations are rejected here."
        }
    }

    $claimId = $null
    if ($normalizedWorkstream) {
        $matching = @($claims | Where-Object { $_.workstream_id -eq $normalizedWorkstream })
        if ($matching.Count -gt 0) {
            $claimId = $matching[0].claim_id
        }
    }

    $gitInfo = [ordered]@{
        worktree_path    = ConvertTo-ForwardSlashPath -Path $context.WorktreePath
        branch           = $context.Branch
        detached         = $context.Detached
        head             = $context.Head
        upstream         = $context.Upstream
        protected_branch = $context.ProtectedBranch
    }

    return New-OperationResult -Operation 'status' `
        -RepoRoot (ConvertTo-ForwardSlashPath -Path $context.RepoRoot) `
        -GitCommonDir (ConvertTo-ForwardSlashPath -Path $context.GitCommonDir) `
        -WorkstreamId $normalizedWorkstream `
        -ClaimId $claimId `
        -Git ([PSCustomObject] $gitInfo) `
        -Claims $claims `
        -Warnings $warnings `
        -Errors @()
}

# ---------------------------------------------------------------------------
# Single top-level try/catch/finally: maps validation, malformed-state, and
# Git-context exceptions to the stable exit codes 2, 3, and 4. Every other
# unexpected failure is treated conservatively as malformed state (exit 3)
# rather than leaking a raw PowerShell error onto stdout. Repository context
# is resolved once up front so even error responses report the already-known
# canonical repo_root/git_common_dir instead of leaving them null.
# ---------------------------------------------------------------------------
$exitCode = 0
$result = $null
$repositoryContext = $null

function Get-KnownRepoRootForResult {
    if ($null -eq $repositoryContext) { return $null }
    return ConvertTo-ForwardSlashPath -Path $repositoryContext.RepoRoot
}

function Get-KnownGitCommonDirForResult {
    if ($null -eq $repositoryContext) { return $null }
    return ConvertTo-ForwardSlashPath -Path $repositoryContext.GitCommonDir
}

try {
    $repositoryContext = Get-RepositoryContext

    $result = switch ($Operation) {
        'status' { Invoke-Status -Context $repositoryContext -Workstream $Workstream }
        default {
            throw [ContinuityValidationException]::new(
                "Operation '$Operation' is not implemented yet; only 'status' is available."
            )
        }
    }

    if (-not $result.ok) {
        $exitCode = 2
    }
}
catch [ContinuityGitContextException] {
    $exitCode = 4
    $result = New-OperationResult -Operation $Operation -Errors @(
        @{ code = 'git-context'; message = $_.Exception.Message }
    )
}
catch [ContinuityStateException] {
    $exitCode = 3
    $result = New-OperationResult -Operation $Operation `
        -RepoRoot (Get-KnownRepoRootForResult) `
        -GitCommonDir (Get-KnownGitCommonDirForResult) `
        -Errors @(@{ code = 'malformed-state'; message = $_.Exception.Message })
}
catch [ContinuityValidationException] {
    $exitCode = 2
    $result = New-OperationResult -Operation $Operation `
        -RepoRoot (Get-KnownRepoRootForResult) `
        -GitCommonDir (Get-KnownGitCommonDirForResult) `
        -Errors @(@{ code = 'validation'; message = $_.Exception.Message })
}
catch {
    $exitCode = 3
    $result = New-OperationResult -Operation $Operation `
        -RepoRoot (Get-KnownRepoRootForResult) `
        -GitCommonDir (Get-KnownGitCommonDirForResult) `
        -Errors @(@{ code = 'unexpected-error'; message = $_.Exception.Message })
}
finally {
    Write-Result -Result $result -Json:$Json
}

exit $exitCode

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
    # Deliberately undecorated with [Parameter(Mandatory)], [ValidateSet], or
    # [ValidateRange]: those declarative attributes throw at parameter-binding
    # time, before the top-level try/catch/finally below ever runs, which
    # would print a raw PowerShell error to stderr with nothing on stdout and
    # exit `1` instead of the stable exit `2` + one JSON object the design
    # contract requires. Assert-ValidOperation and Assert-LeaseHoursInRange
    # (defined below) perform the equivalent checks INSIDE the top-level try
    # so every validation failure -- bad/missing operation, out-of-range
    # -LeaseHours, and future validated parameters -- funnels through the same
    # catch to exit 2 with exactly one JSON object on stdout.
    [Parameter(Position = 0)]
    [string] $Operation,

    [string] $Workstream,
    [string] $Agent,
    [string] $SessionId,
    [string[]] $Scope,
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
    # ClaimId/Recovery are optional structured extras a thrower can attach so
    # the top-level catch below can report them without the generic
    # "malformed state" path having to know about every specific caller.
    # Both default to $null, which New-OperationResult already treats as
    # "omit"/"null", so ordinary malformed-state failures (for example a
    # broken claim JSON file read by Read-ContinuityState) are unaffected.
    [string] $ClaimId
    [object] $Recovery

    ContinuityStateException([string] $Message) : base($Message) {}
}

class ContinuityGitContextException : System.Exception {
    ContinuityGitContextException([string] $Message) : base($Message) {}
}

class ContinuityTestFaultException : System.Exception {
    # Phase distinguishes the fixed 'before'/'after' boundary a fault name
    # brackets (design spec test hook), so a caller like Invoke-Start can
    # report different authoritative-state facts for a fault injected before
    # a claim replace versus one injected after it, without ever printing
    # the fault name or the environment variable's value.
    [string] $Phase

    ContinuityTestFaultException([string] $Message, [string] $Phase) : base($Message) {
        $this.Phase = $Phase
    }
}

# The complete set of operations the approved helper contract declares.
# `status` is implemented; the rest are recognized but not yet implemented
# (later tasks add them) and route to a validation error until then.
$Script:ValidOperations = @('status', 'start', 'update', 'handoff', 'accept', 'takeover')

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

# Valid `-Agent` values (design spec, helper contract signature).
$Script:ValidAgentNames = @('claude', 'codex')

# Default lease when `-LeaseHours` is not supplied (design spec, Helper
# Contract: "The default lease is eight hours"). `update` also uses this
# fixed default to renew a claim's lease; it has no `-LeaseHours` parameter
# of its own (design spec, Helper Contract: "update" signature).
$Script:DefaultLeaseHours = 8

# Valid `-VerificationResult` values and valid `update`-only `-State` values
# (design spec, Helper Contract: "update" signature; `update` may only set
# `active`, `blocked`, or `handoff` -- `planned` and `merged` are set by
# other operations).
$Script:ValidVerificationResults = @('passed', 'failed', 'not-run')
$Script:ValidUpdateStates = @('active', 'blocked', 'handoff')

# The exact managed milestone marker pair every workstream template contains
# (design spec, Helper Contract: "Workstream templates contain a
# helper-managed section bounded by..."). `update` may edit only the content
# between these two exact lines, plus the four explicit fields named below.
$Script:MilestoneMarkerStart = '<!-- ai-continuity:milestones:start -->'
$Script:MilestoneMarkerEnd = '<!-- ai-continuity:milestones:end -->'

# The one documented backfill exception to the `codex/<workstream-id>` /
# `claude/<workstream-id>` branch-naming rule (design spec, "Branch
# validation"). No workstream other than the exact one below may use it.
$Script:BootstrapExceptionBranch = 'codex/good-badminton-development'
$Script:BootstrapExceptionWorkstream = 'continuity-pilot'

# Single persistent lock file path segment under
# `<git-common-dir>/ai-continuity/` (design spec, "Local-only state").
$Script:LockFileName = 'lock'
$Script:LockTimeoutMilliseconds = 5000
$Script:LockRetryIntervalMilliseconds = 50

# The complete set of fixed fault names honored by the single test hook
# `AI_CONTINUITY_TEST_FAULT` (design spec / plan Global Constraints: "the
# fixed boundary names listed in Tasks 3 and 5-7"). Task 3's two names exist
# plus Task 4's one name (`update-after-workstream-write`, exercised by its
# claim-rewrite-failure test: a real OS file lock cannot simulate this
# boundary because it would also block the earlier read that locates the
# claim, so this deterministic hook is used instead); later tasks extend
# this list as they add their own fixed boundaries. The variable is inactive
# unless a test explicitly sets it, is never serialized or printed, and any
# value outside this fixed set fails closed before any mutation.
$Script:KnownTestFaultNames = @(
    'start-before-claim-replace',
    'start-after-claim-replace',
    'update-after-workstream-write'
)

# Case sensitivity for scope/path comparisons is derived from the
# repository filesystem (design spec, Local Claim Model: "Case comparison
# follows the filesystem behavior while serialized paths use forward
# slashes"). This pilot's repository always lives on a Windows volume, and
# Windows filesystems (NTFS/ReFS) are case-insensitive by default -- the
# one deployment-specific fact PowerShell 7's `$IsWindows` automatic
# variable exposes without touching disk (a live per-call probe would
# require writing a file even for the read-only `status` path, which the
# design forbids). Non-Windows hosts are treated as case-sensitive.
$Script:IsCaseInsensitiveFileSystem = $IsWindows

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

function Format-CodeSpan {
    <#
    .SYNOPSIS
        Wraps text in a Markdown inline-code span using a literal backtick
        character built from its char code, so double-quoted string
        interpolation elsewhere never has to fight PowerShell's own backtick
        escape-character syntax.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Text
    )

    $backtick = [char] 0x60
    return "$backtick$Text$backtick"
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

function Test-ReparsePointPath {
    <#
    .SYNOPSIS
        True when an existing filesystem path is a symbolic link, junction,
        or other reparse point, determined from its attributes without ever
        opening or traversing the link target (design spec, "Identifier and
        path validation": "Reject any scope whose existing path components
        contain a symbolic link, junction, or other reparse point").
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Resolve-ScopeContainment {
    <#
    .SYNOPSIS
        Resolves a syntactically-normalized scope path (from
        Normalize-ScopePath) to a canonical absolute path under the
        repository root, walking component-by-component from the root so
        every reparse-point rejection and containment check happens at a
        path boundary rather than a substring match.
    .DESCRIPTION
        For each path component, from the repository root down: if that
        prefix currently exists on disk, reject it when it is a reparse
        point (design spec: "The pilot does not attempt to prove containment
        through links") and otherwise continue from its canonical
        (filesystem-cased) full name. The first component that does not
        exist stops the existence walk -- the design only requires
        resolving "the nearest existing ancestor" -- but the full candidate
        path is still checked for repository-root containment using plain
        (non-link-following) path arithmetic, since a path that does not
        exist yet cannot itself be a link.
    .OUTPUTS
        The canonical absolute path string. Throws ContinuityValidationException
        (exit 2) before any filesystem mutation when a component is a
        reparse point or the resolved path escapes the repository root.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,
        [Parameter(Mandatory = $true)]
        [string] $NormalizedScope
    )

    $repoRootFull = [System.IO.Path]::GetFullPath($RepoRoot)
    $current = $repoRootFull

    foreach ($component in $NormalizedScope.Split('/')) {
        $candidate = Join-Path $current $component
        if (Test-ReparsePointPath -Path $candidate) {
            throw [ContinuityValidationException]::new(
                "Scope path '$NormalizedScope' passes through a symbolic link, junction, or reparse point at '$component'; scopes must not cross one."
            )
        }
        if (Test-Path -LiteralPath $candidate) {
            # Adopt the filesystem's own canonical casing/representation for
            # the next iteration so containment comparison below is exact.
            $current = (Get-Item -LiteralPath $candidate -Force).FullName
        }
        else {
            $current = $candidate
        }
    }

    $candidateFull = [System.IO.Path]::GetFullPath($current)
    $comparison = if ($Script:IsCaseInsensitiveFileSystem) {
        [System.StringComparison]::OrdinalIgnoreCase
    }
    else {
        [System.StringComparison]::Ordinal
    }
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $isContained = $candidateFull.Equals($repoRootFull, $comparison) -or
        $candidateFull.StartsWith("$repoRootFull$separator", $comparison)
    if (-not $isContained) {
        throw [ContinuityValidationException]::new(
            "Scope path '$NormalizedScope' must resolve under the repository root."
        )
    }

    return $candidateFull
}

function Assert-ValidOperation {
    <#
    .SYNOPSIS
        Validates the required `-Operation` argument INSIDE the top-level
        try, instead of via a declarative [Parameter(Mandatory)]/[ValidateSet]
        attribute, so a missing or unrecognized operation funnels through the
        same ContinuityValidationException catch as every other validation
        failure (stable exit 2, exactly one JSON object on stdout) instead of
        failing at parameter-binding time with a raw PowerShell error and
        exit 1.
    #>
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string] $Operation
    )

    if ([string]::IsNullOrWhiteSpace($Operation)) {
        throw [ContinuityValidationException]::new(
            "An operation is required; expected one of: $($Script:ValidOperations -join ', ')."
        )
    }
    if ($Script:ValidOperations -notcontains $Operation) {
        throw [ContinuityValidationException]::new(
            "Operation '$Operation' is not recognized; expected one of: $($Script:ValidOperations -join ', ')."
        )
    }
}

function Assert-LeaseHoursInRange {
    <#
    .SYNOPSIS
        Validates an explicitly supplied `-LeaseHours` INSIDE the top-level
        try, instead of via a declarative [ValidateRange(1, 24)] attribute, so
        an out-of-range value funnels through the same
        ContinuityValidationException catch as every other validation failure
        (stable exit 2, exactly one JSON object on stdout) instead of failing
        at parameter-binding time with a raw PowerShell error and exit 1. A
        `-LeaseHours` never supplied by the caller is not validated here; the
        default only matters to operations that use it (added in later
        tasks).
    #>
    param(
        [int] $LeaseHours,
        [Parameter(Mandatory = $true)]
        [bool] $WasSupplied
    )

    if (-not $WasSupplied) {
        return
    }
    if ($LeaseHours -lt 1 -or $LeaseHours -gt 24) {
        throw [ContinuityValidationException]::new(
            "LeaseHours '$LeaseHours' is out of range; expected an integer between 1 and 24."
        )
    }
}

function Assert-RequiredParameter {
    <#
    .SYNOPSIS
        Fails a missing/blank required string parameter through the same
        stable ContinuityValidationException path (exit 2) as every other
        validation failure, rather than a raw parameter-binding error.
    #>
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string] $Value,
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw [ContinuityValidationException]::new("-$Name is required.")
    }
}

function Assert-ValidAgent {
    <#
    .SYNOPSIS
        Validates `-Agent` is exactly 'claude' or 'codex' (design spec,
        helper contract signature: `-Agent <claude|codex>`).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyString()]
        [string] $Value
    )

    if ($Script:ValidAgentNames -notcontains $Value) {
        throw [ContinuityValidationException]::new(
            "Agent must be one of: $($Script:ValidAgentNames -join ', ')."
        )
    }
    return $Value
}

function Assert-WorkstreamBranchAllowed {
    <#
    .SYNOPSIS
        Enforces the design spec's branch rules for every mutating
        operation: reject detached HEAD and the protected integration
        branch, then require either the documented
        `codex/good-badminton-development` + `continuity-pilot` backfill
        exception or a `codex/<workstream-id>`/`claude/<workstream-id>`
        branch matching the exact normalized workstream ID. The prefix
        identifies the branch's creator, not its current owner, so either
        agent may operate on either prefix (design spec: "Claude may accept
        a codex/ branch and Codex may accept a claude/ branch").
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Context,
        [Parameter(Mandatory = $true)]
        [string] $WorkstreamId
    )

    if ($Context.Detached) {
        throw [ContinuityValidationException]::new(
            'HEAD is detached; check out a workstream branch before this operation.'
        )
    }
    if ($Context.Branch -eq $Context.ProtectedBranch) {
        throw [ContinuityValidationException]::new(
            "Branch '$($Context.ProtectedBranch)' is the protected integration branch; this operation is not allowed there."
        )
    }
    if ($Context.Branch -eq $Script:BootstrapExceptionBranch) {
        if ($WorkstreamId -ne $Script:BootstrapExceptionWorkstream) {
            throw [ContinuityValidationException]::new(
                "Branch '$($Script:BootstrapExceptionBranch)' is reserved for workstream '$($Script:BootstrapExceptionWorkstream)'."
            )
        }
        return
    }

    $allowedBranches = @('codex', 'claude') | ForEach-Object { "$_/$WorkstreamId" }
    if ($allowedBranches -notcontains $Context.Branch) {
        throw [ContinuityValidationException]::new(
            "Branch '$($Context.Branch)' does not match required 'codex/$WorkstreamId' or 'claude/$WorkstreamId'."
        )
    }
}

function Test-ScopePrefixesOverlap {
    <#
    .SYNOPSIS
        True when two normalized repository-relative scope path prefixes
        overlap: identical, or one is a path-boundary-respecting ancestor of
        the other (design spec, Local Claim Model: "overlap when either
        normalized prefix contains the other at a path boundary"). Comparison
        case-sensitivity is derived from the repository filesystem.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Left,
        [Parameter(Mandatory = $true)]
        [string] $Right
    )

    $comparison = if ($Script:IsCaseInsensitiveFileSystem) {
        [System.StringComparison]::OrdinalIgnoreCase
    }
    else {
        [System.StringComparison]::Ordinal
    }

    if ($Left.Equals($Right, $comparison)) {
        return $true
    }
    if ($Right.StartsWith("$Left/", $comparison)) {
        return $true
    }
    if ($Left.StartsWith("$Right/", $comparison)) {
        return $true
    }
    return $false
}

function Test-ScopeOverlap {
    <#
    .SYNOPSIS
        True when any normalized scope path prefix in one list overlaps any
        normalized scope path prefix in another list (Task 3 addition: the
        pairwise primitive `start` uses to check a proposed scope against
        every other live claim's recorded scope, distinct from
        Test-ClaimsScopeOverlap which compares two whole claim objects).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $Left,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $Right
    )

    foreach ($leftPath in $Left) {
        foreach ($rightPath in $Right) {
            if (Test-ScopePrefixesOverlap -Left $leftPath -Right $rightPath) {
                return $true
            }
        }
    }
    return $false
}

function Test-ClaimsScopeOverlap {
    <#
    .SYNOPSIS
        True when any scope path prefix recorded on one claim overlaps any
        scope path prefix recorded on another claim.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Left,
        [Parameter(Mandatory = $true)]
        $Right
    )

    return Test-ScopeOverlap -Left @($Left.scope_paths) -Right @($Right.scope_paths)
}

function Test-PathWithinScope {
    <#
    .SYNOPSIS
        True when a concrete repository-relative path (as reported by Git,
        never itself a prefix) is claimed by one of the given normalized
        scope prefixes: an exact match, or nested under a prefix at a path
        boundary. Unlike Test-ScopePrefixesOverlap this check is
        deliberately asymmetric -- a claimed scope contains everything under
        it, but a concrete file path can never be an ancestor of anything.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $ScopePrefixes
    )

    $comparison = if ($Script:IsCaseInsensitiveFileSystem) {
        [System.StringComparison]::OrdinalIgnoreCase
    }
    else {
        [System.StringComparison]::Ordinal
    }

    foreach ($prefix in $ScopePrefixes) {
        if ($Path.Equals($prefix, $comparison)) {
            return $true
        }
        if ($Path.StartsWith("$prefix/", $comparison)) {
            return $true
        }
    }
    return $false
}

function Test-ScopeSetEqual {
    <#
    .SYNOPSIS
        True when two normalized scope path lists contain the same entries,
        order-independent (used to detect an identical-scope `start` renewal
        versus a scope-change, design spec: "the identical normalized
        scope").
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $Left,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $Right
    )

    $comparer = if ($Script:IsCaseInsensitiveFileSystem) {
        [System.StringComparer]::OrdinalIgnoreCase
    }
    else {
        [System.StringComparer]::Ordinal
    }

    # `Sort-Object -Culture` binds to a [string] culture NAME, and
    # InvariantCulture's Name is the empty string -- which Sort-Object treats
    # as "unspecified" and silently falls back to the current culture. A
    # genuine [System.StringComparer]::InvariantCulture comparer instance has
    # no such pitfall (Task 3 review fix 6; matches the fix applied to the
    # preexisting_dirty sort in Invoke-Start).
    $leftSorted = @(
        [System.Linq.Enumerable]::OrderBy(
            [string[]] $Left,
            [Func[string, string]] { param($item) $item },
            [System.StringComparer]::InvariantCulture
        )
    )
    $rightSorted = @(
        [System.Linq.Enumerable]::OrderBy(
            [string[]] $Right,
            [Func[string, string]] { param($item) $item },
            [System.StringComparer]::InvariantCulture
        )
    )
    if ($leftSorted.Count -ne $rightSorted.Count) {
        return $false
    }
    for ($i = 0; $i -lt $leftSorted.Count; $i++) {
        if (-not $comparer.Equals($leftSorted[$i], $rightSorted[$i])) {
            return $false
        }
    }
    return $true
}

function Test-ClaimIdentity {
    <#
    .SYNOPSIS
        True when a claim's recorded agent, session ID, canonical worktree
        path, and branch all match the identity of the current caller
        (design spec, "Claim ownership identity": "A claim ID is not an
        authentication token... the helper derives the current canonical
        worktree path, branch, and Git common directory. All values must
        match the active claim").
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Claim,
        [Parameter(Mandatory = $true)]
        [string] $Agent,
        [Parameter(Mandatory = $true)]
        [string] $SessionId,
        [Parameter(Mandatory = $true)]
        [string] $WorktreePath,
        [Parameter(Mandatory = $true)]
        [string] $Branch
    )

    return (
        $Claim.agent -eq $Agent -and
        $Claim.session_id -eq $SessionId -and
        $Claim.worktree_path -eq $WorktreePath -and
        $Claim.branch -eq $Branch
    )
}

function ConvertFrom-ContinuityTimestamp {
    <#
    .SYNOPSIS
        Parses a claim's RFC3339 UTC timestamp field into a [DateTimeOffset].
        An unparseable value is malformed durable state (exit 3), so this
        raises ContinuityStateException rather than letting a raw .NET
        FormatException leak out as an unexpected error.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Value,
        [Parameter(Mandatory = $true)]
        [string] $FieldName,
        [Parameter(Mandatory = $true)]
        [string] $ClaimId
    )

    try {
        return [DateTimeOffset]::Parse($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw [ContinuityStateException]::new(
            "Claim '$ClaimId' has an unparseable '$FieldName' timestamp: '$Value'."
        )
    }
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

function Get-Sha256HexOfBytes {
    <#
    .SYNOPSIS
        Lowercase hex SHA-256 of raw bytes.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [byte[]] $Bytes
    )

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha256.ComputeHash($Bytes)
        $builder = [System.Text.StringBuilder]::new($hashBytes.Length * 2)
        foreach ($b in $hashBytes) {
            [void] $builder.Append($b.ToString('x2'))
        }
        return $builder.ToString()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-Sha256HexOfString {
    <#
    .SYNOPSIS
        Lowercase hex SHA-256 of a string's UTF-8 (no BOM) bytes. Used to
        fingerprint symlink/reparse-point link-target metadata without ever
        reading through the link (design spec: "links or reparse points are
        fingerprinted from their link metadata without traversal").
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Value
    )

    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Value)
    return Get-Sha256HexOfBytes -Bytes $bytes
}

function Get-GitStatusEntries {
    <#
    .SYNOPSIS
        Parses `git status --porcelain=v1 -z -uall` into structured entries
        without losing rename/copy sources (design spec, "Local Claim
        Model": "Each entry records the two-character Git status,
        destination path, rename/copy source path when present").
    .DESCRIPTION
        Records are NUL-separated. Each record is normally `XY PATH`; when
        either status character is `R` (renamed) or `C` (copied), Git
        appends exactly one more NUL-terminated record holding the original
        (source) path, which must be consumed before parsing the next
        record.
    .OUTPUTS
        Array of PSCustomObject { Status; Path; OriginalPath } with
        forward-slash paths repository-relative to $RepoRoot. OriginalPath
        is $null when the entry has no rename/copy source.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot
    )

    $result = Invoke-Git -Arguments @('status', '--porcelain=v1', '-z', '-uall') -WorkingDirectory $RepoRoot
    if ($result.ExitCode -ne 0) {
        throw [ContinuityGitContextException]::new('Unable to read Git status.')
    }

    $entries = @()
    if ([string]::IsNullOrEmpty($result.StdOut)) {
        return $entries
    }

    $tokens = $result.StdOut -split "`0"
    $i = 0
    while ($i -lt $tokens.Count) {
        $record = $tokens[$i]
        if ([string]::IsNullOrEmpty($record)) {
            $i++
            continue
        }

        $status = $record.Substring(0, 2)
        $path = $record.Substring(3).Replace('\', '/')
        $originalPath = $null

        if ($status[0] -eq 'R' -or $status[0] -eq 'C' -or $status[1] -eq 'R' -or $status[1] -eq 'C') {
            $i++
            if ($i -lt $tokens.Count) {
                $originalPath = $tokens[$i].Replace('\', '/')
            }
        }

        $entries += [PSCustomObject]@{
            Status = $status
            Path = $path
            OriginalPath = $originalPath
        }
        $i++
    }

    return $entries
}

function Get-IndexEntries {
    <#
    .SYNOPSIS
        Queries `git ls-files --stage -z -- <path>` for the complete index
        tuples of one repository-relative path: stage, mode, and object ID,
        including stages 1-3 for unmerged entries (design spec, "Local Claim
        Model": "the complete git ls-files --stage -z tuples for that path").
        Untracked or otherwise absent-from-index paths return an empty array.
    .OUTPUTS
        Array of PSCustomObject { stage; mode; object_id }, sorted by stage.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $result = Invoke-Git -Arguments @('ls-files', '--stage', '-z', '--', $Path) -WorkingDirectory $RepoRoot
    if ($result.ExitCode -ne 0) {
        throw [ContinuityGitContextException]::new("Unable to read the Git index for '$Path'.")
    }

    $entries = @()
    if ([string]::IsNullOrEmpty($result.StdOut)) {
        return $entries
    }

    foreach ($record in ($result.StdOut -split "`0")) {
        if ([string]::IsNullOrEmpty($record)) {
            continue
        }
        $tabIndex = $record.IndexOf("`t")
        if ($tabIndex -lt 0) {
            continue
        }
        $left = $record.Substring(0, $tabIndex)
        $parts = $left -split ' '
        $entries += [PSCustomObject]@{
            stage = [int] $parts[2]
            mode = $parts[0]
            object_id = $parts[1]
        }
    }

    return @($entries | Sort-Object stage)
}

function Get-DirtyFingerprint {
    <#
    .SYNOPSIS
        Fingerprints one repository-relative path's current working-tree
        state without ever reading through a symlink/reparse-point target:
        its filesystem `kind`, and a content hash appropriate to that kind.
    .DESCRIPTION
        - Missing path (deleted tracked file): kind `absent`, hash $null
          (design spec: "Missing tracked paths use kind absent and a null
          worktree hash").
        - Symbolic link or junction/other reparse point: kind `symlink` /
          `junction` / `reparse-point`, hash of the immediate link target
          text only -- `Get-Item -Force` exposes this via `.LinkTarget`
          without the runtime ever opening the target file (design spec:
          "links or reparse points are fingerprinted from their link
          metadata without traversal").
        - Regular file: kind `regular-file`, SHA-256 of its raw bytes.
    .OUTPUTS
        PSCustomObject { Kind; WorktreeSha256 }.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,
        [Parameter(Mandatory = $true)]
        [string] $RepoRelativePath
    )

    $absolutePath = Join-Path $RepoRoot ($RepoRelativePath -replace '/', [System.IO.Path]::DirectorySeparatorChar)

    if (-not (Test-Path -LiteralPath $absolutePath)) {
        return [PSCustomObject]@{
            Kind = 'absent'
            WorktreeSha256 = $null
        }
    }

    $item = Get-Item -LiteralPath $absolutePath -Force
    $isReparse = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0

    if ($isReparse) {
        $kind = switch ($item.LinkType) {
            'SymbolicLink' { 'symlink' }
            'Junction' { 'junction' }
            default { 'reparse-point' }
        }
        $hash = Get-Sha256HexOfString -Value ("$($item.LinkType)`0$($item.LinkTarget)")
        return [PSCustomObject]@{
            Kind = $kind
            WorktreeSha256 = $hash
        }
    }

    if ($item.PSIsContainer) {
        return [PSCustomObject]@{
            Kind = 'directory'
            WorktreeSha256 = $null
        }
    }

    $bytes = [System.IO.File]::ReadAllBytes($absolutePath)
    return [PSCustomObject]@{
        Kind = 'regular-file'
        WorktreeSha256 = (Get-Sha256HexOfBytes -Bytes $bytes)
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
        # Mandatory but explicitly AllowNull/AllowEmptyString: an operation
        # validation failure (missing or unrecognized -Operation) reports
        # through this same result builder with whatever raw, possibly
        # null/empty, value the caller supplied. A bare [string] Mandatory
        # parameter would otherwise reject null AND empty string at bind
        # time, turning that reporting path itself into an uncaught crash.
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyString()]
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
        Read-only report of Git state and claims: expired leases, ordinary
        Git drift (a live claim's recorded base commit no longer matches
        current HEAD), and overlapping live claim scopes. Never acquires the
        common lock and never creates `.git/ai-continuity`. Detached HEAD,
        the protected branch, expiry, and drift are reported as warnings
        (exit 0); overlapping live claims are reported as a conflict error
        (exit 2). `status` remains available on `main`.
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
    $errors = @()

    if ($context.Detached) {
        $warnings += @{ code = 'detached-head'; message = 'HEAD is detached.' }
    }
    if ($context.Branch -eq $context.ProtectedBranch) {
        $warnings += @{
            code    = 'protected-branch'
            message = "Current branch '$($context.ProtectedBranch)' is the protected integration branch; mutating operations are rejected here."
        }
    }

    # "Live" claims (design spec, Local Claim Model: "Active claim files use
    # state: active or state: handoff-ready") are the ones whose lease can
    # expire, whose recorded base commit can drift from current HEAD, and
    # whose scope can conflict with another live claim.
    $liveClaims = @($claims | Where-Object { $_.state -eq 'active' -or $_.state -eq 'handoff-ready' })
    $nowUtc = [DateTimeOffset]::UtcNow

    foreach ($claim in $liveClaims) {
        $leaseUntil = ConvertFrom-ContinuityTimestamp -Value $claim.lease_until_utc -FieldName 'lease_until_utc' -ClaimId $claim.claim_id
        if ($leaseUntil -le $nowUtc) {
            $warnings += @{
                code    = 'expired-lease'
                message = "Claim '$($claim.claim_id)' for workstream '$($claim.workstream_id)' has an expired lease (lease_until_utc: $($claim.lease_until_utc))."
            }
        }

        if ($context.Head -and $claim.base_commit -and ($claim.base_commit -ne $context.Head)) {
            $warnings += @{
                code    = 'git-drift'
                message = "Claim '$($claim.claim_id)' for workstream '$($claim.workstream_id)' recorded base commit '$($claim.base_commit)', but current HEAD is '$($context.Head)'."
            }
        }
    }

    for ($i = 0; $i -lt $liveClaims.Count; $i++) {
        for ($j = $i + 1; $j -lt $liveClaims.Count; $j++) {
            if (Test-ClaimsScopeOverlap -Left $liveClaims[$i] -Right $liveClaims[$j]) {
                $errors += @{
                    code    = 'overlapping-claims'
                    message = "Claim '$($liveClaims[$i].claim_id)' (workstream '$($liveClaims[$i].workstream_id)') and claim '$($liveClaims[$j].claim_id)' (workstream '$($liveClaims[$j].workstream_id)') have overlapping scope."
                }
            }
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
        -Errors $errors
}

function Assert-KnownTestFault {
    <#
    .SYNOPSIS
        The single test hook `AI_CONTINUITY_TEST_FAULT` is inactive unless a
        test explicitly sets it. When set, it must be exactly one of the
        fixed names this script (across all tasks) recognizes; any other
        value fails closed BEFORE any mutation, and -- like every other test
        fault path -- the exception message never echoes the environment
        variable's actual value (design spec / plan Global Constraints:
        "never serialized or printed... accepts only the fixed boundary
        names... unknown values fail before mutation").
    #>
    $value = $env:AI_CONTINUITY_TEST_FAULT
    if ([string]::IsNullOrEmpty($value)) {
        return
    }
    if ($Script:KnownTestFaultNames -notcontains $value) {
        throw [ContinuityValidationException]::new(
            'AI_CONTINUITY_TEST_FAULT is set to an unrecognized value.'
        )
    }
}

function Invoke-TestFault {
    <#
    .SYNOPSIS
        Throws ContinuityTestFaultException when `$Name` is non-empty and
        exactly matches the live `AI_CONTINUITY_TEST_FAULT` environment
        value; otherwise a no-op. Never includes the fault name or the
        environment value in any message, JSON, or log.
    #>
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [string] $Phase
    )

    if ([string]::IsNullOrEmpty($Name)) {
        return
    }
    $active = $env:AI_CONTINUITY_TEST_FAULT
    if ([string]::IsNullOrEmpty($active)) {
        return
    }
    if ($active -eq $Name) {
        throw [ContinuityTestFaultException]::new(
            'A test-injected fault interrupted the operation.', $Phase
        )
    }
}

function Use-ContinuityLock {
    <#
    .SYNOPSIS
        Runs a script block while holding the single persistent continuity
        lock file exclusively open. Ownership is the open OS file handle,
        never the file's mere existence: acquired via an exclusive
        `FileStream` open (FileShare.None) with bounded retry, so a process
        that dies while holding the lock releases the handle immediately and
        a waiting caller reacquires it without ever deleting a stale marker
        (design spec, "Failure and conflict handling": "Claim writes use an
        atomic common-directory lock").
    .OUTPUTS
        Whatever `$ScriptBlock` returns. Throws ContinuityStateException
        (exit 3) on timeout.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $LockPath,
        [Parameter(Mandatory = $true)]
        [scriptblock] $ScriptBlock
    )

    $lockDir = Split-Path -Parent $LockPath
    New-Item -ItemType Directory -Path $lockDir -Force | Out-Null

    $stream = $null
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try {
            $stream = [System.IO.File]::Open(
                $LockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            break
        }
        catch [System.IO.IOException] {
            if ($stopwatch.ElapsedMilliseconds -ge $Script:LockTimeoutMilliseconds) {
                throw [ContinuityStateException]::new(
                    'Timed out waiting for the continuity lock; another process may be holding it.'
                )
            }
            Start-Sleep -Milliseconds $Script:LockRetryIntervalMilliseconds
        }
    }

    try {
        return & $ScriptBlock
    }
    finally {
        $stream.Dispose()
    }
}

function Write-JsonAtomic {
    <#
    .SYNOPSIS
        Serializes `$Object` to JSON and atomically replaces `$Path`: write
        to a unique same-directory temporary file, flush it fully to disk,
        then atomically replace the target (`File.Move` with overwrite,
        which performs a single atomic rename on the same volume). A failure
        at any point leaves either the complete prior file (if replace never
        ran) or the complete new file (if it did) -- never a partial file
        (design spec / plan: "Failed writes retain either the readable old
        file or the complete new file, never a partial file").
    .PARAMETER FaultBeforeReplace
        Fixed test-fault name checked immediately before the atomic replace.
    .PARAMETER FaultAfterReplace
        Fixed test-fault name checked immediately after the atomic replace.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        $Object,
        [string] $FaultBeforeReplace,
        [string] $FaultAfterReplace
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null

    $tempPath = Join-Path $directory ([Guid]::NewGuid().ToString('N') + '.tmp')
    $json = $Object | ConvertTo-Json -Depth 20
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)

    $stream = [System.IO.File]::Open(
        $tempPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None
    )
    try {
        $bytes = $utf8NoBom.GetBytes($json)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }

    try {
        Invoke-TestFault -Name $FaultBeforeReplace -Phase 'before'
        [System.IO.File]::Move($tempPath, $Path, $true)
        Invoke-TestFault -Name $FaultAfterReplace -Phase 'after'
    }
    catch {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

function Invoke-Start {
    <#
    .SYNOPSIS
        Creates or idempotently renews exactly one active claim at
        `<git-common-dir>/ai-continuity/claims/<workstream-id>.json` (design
        spec, Helper Contract: "start"). Validates all identity/scope input
        and the current branch BEFORE any filesystem mutation, then -- under
        the single common lock -- re-reads every claim, fails on live scope
        overlap with any OTHER workstream, allows only an identical
        idempotent same-identity/same-scope renewal of THIS workstream's own
        live claim, captures every pre-existing out-of-scope dirty path, and
        rejects any dirty path inside the requested scope, before atomically
        writing the schema-1 claim with the default eight-hour lease.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Context,
        [string] $Agent,
        [string] $SessionId,
        [string] $Workstream,
        [string[]] $Scope,
        [int] $LeaseHours,
        [Parameter(Mandatory = $true)]
        [bool] $LeaseHoursSupplied
    )

    # --- Validation before any filesystem mutation -------------------------
    Assert-RequiredParameter -Value $Agent -Name 'Agent'
    Assert-RequiredParameter -Value $SessionId -Name 'SessionId'
    Assert-RequiredParameter -Value $Workstream -Name 'Workstream'
    if (-not $Scope -or @($Scope).Count -eq 0) {
        throw [ContinuityValidationException]::new('-Scope must include at least one path.')
    }

    $agent = Assert-ValidAgent -Value $Agent
    $sessionId = Normalize-Id -Value $SessionId -Kind 'Session'
    $workstreamId = Normalize-Id -Value $Workstream -Kind 'Workstream'

    $normalizedScope = @()
    foreach ($rawScopePath in $Scope) {
        $normalizedPath = Normalize-ScopePath -Path $rawScopePath
        Resolve-ScopeContainment -RepoRoot $Context.RepoRoot -NormalizedScope $normalizedPath | Out-Null
        $normalizedScope += $normalizedPath
    }
    $normalizedScope = @($normalizedScope | Select-Object -Unique)

    $leaseHours = if ($LeaseHoursSupplied) { $LeaseHours } else { $Script:DefaultLeaseHours }

    Assert-WorkstreamBranchAllowed -Context $Context -WorkstreamId $workstreamId

    # --- Under the common lock ---------------------------------------------
    $continuityDir = Join-Path $Context.GitCommonDir $Script:ContinuityDirName
    $lockPath = Join-Path $continuityDir $Script:LockFileName
    $claimsDir = Join-Path $continuityDir 'claims'

    return Use-ContinuityLock -LockPath $lockPath -ScriptBlock {
        $state = Read-ContinuityState -GitCommonDir $Context.GitCommonDir
        $claims = @($state.Claims)
        $liveClaims = @($claims | Where-Object { $_.state -eq 'active' -or $_.state -eq 'handoff-ready' })

        $existingForWorkstream = @($liveClaims | Where-Object { $_.workstream_id -eq $workstreamId })
        $othersLive = @($liveClaims | Where-Object { $_.workstream_id -ne $workstreamId })

        foreach ($other in $othersLive) {
            if (Test-ScopeOverlap -Left $normalizedScope -Right @($other.scope_paths)) {
                throw [ContinuityValidationException]::new(
                    "Requested scope overlaps live claim '$($other.claim_id)' for workstream '$($other.workstream_id)'."
                )
            }
        }

        $isRenewal = $false
        $existingClaim = $null
        if ($existingForWorkstream.Count -gt 0) {
            $existingClaim = $existingForWorkstream[0]
            if ($existingClaim.state -ne 'active') {
                throw [ContinuityValidationException]::new(
                    "Workstream '$workstreamId' has a '$($existingClaim.state)' claim '$($existingClaim.claim_id)' that must be accepted or replaced through takeover before start."
                )
            }

            $sameIdentity = Test-ClaimIdentity -Claim $existingClaim -Agent $agent -SessionId $sessionId `
                -WorktreePath (ConvertTo-ForwardSlashPath -Path $Context.WorktreePath) -Branch $Context.Branch
            $sameScope = Test-ScopeSetEqual -Left @($existingClaim.scope_paths) -Right $normalizedScope

            if ($sameIdentity -and $sameScope) {
                $isRenewal = $true
            }
            elseif ($sameIdentity) {
                throw [ContinuityValidationException]::new(
                    "Workstream '$workstreamId' already has an active claim with a different scope; complete a handoff/accept or takeover before changing scope."
                )
            }
            else {
                throw [ContinuityValidationException]::new(
                    "Workstream '$workstreamId' already has an active claim owned by a different agent, session, or worktree."
                )
            }
        }

        # --- Dirty-state check: reject in-scope, capture out-of-scope -------
        $statusEntries = Get-GitStatusEntries -RepoRoot $Context.RepoRoot
        $inScopeMessages = @()
        $preexistingDirty = @()

        foreach ($entry in $statusEntries) {
            $touchedPaths = @($entry.Path)
            if ($entry.OriginalPath) { $touchedPaths += $entry.OriginalPath }

            $entryInScope = $false
            foreach ($touchedPath in $touchedPaths) {
                if (Test-PathWithinScope -Path $touchedPath -ScopePrefixes $normalizedScope) {
                    $entryInScope = $true
                    break
                }
            }

            if ($entryInScope) {
                $inScopeMessages += "Path '$($entry.Path)' (status '$($entry.Status)') is dirty inside the requested scope."
                continue
            }

            $fingerprint = Get-DirtyFingerprint -RepoRoot $Context.RepoRoot -RepoRelativePath $entry.Path
            # @() forces array-ness even when Get-IndexEntries' own single-
            # element array return value has already been unwrapped to a
            # bare PSCustomObject by PowerShell's function-output pipeline
            # enumeration (the same quirk documented on New-OperationResult
            # above); without it, a path with exactly one index entry would
            # serialize `index_entries` as a bare JSON object instead of a
            # one-element JSON array.
            $indexEntries = @(Get-IndexEntries -RepoRoot $Context.RepoRoot -Path $entry.Path)

            $preexistingDirty += [PSCustomObject][ordered]@{
                path             = $entry.Path
                original_path    = $entry.OriginalPath
                status           = $entry.Status
                kind             = $fingerprint.Kind
                worktree_sha256  = $fingerprint.WorktreeSha256
                index_entries    = $indexEntries
            }
        }

        if ($inScopeMessages.Count -gt 0) {
            throw [ContinuityValidationException]::new(($inScopeMessages -join ' '))
        }

        # Culture-invariant, stable ascending sort by path -- matching the
        # InvariantCulture discipline Test-ScopeSetEqual already applies, so
        # this JSON array's order never depends on the current process's
        # culture. `Sort-Object -Culture` takes a culture NAME string, and
        # the invariant culture's Name is the empty string, which Sort-Object
        # silently treats as "unspecified" and falls back to the current
        # culture; [System.StringComparer]::InvariantCulture is a genuine
        # comparer instance and has no such pitfall.
        $preexistingDirtySorted = @(
            [System.Linq.Enumerable]::OrderBy(
                [object[]] $preexistingDirty,
                [Func[object, string]] { param($item) $item.path },
                [System.StringComparer]::InvariantCulture
            )
        )

        # --- Build (new or renewed) claim ------------------------------------
        # InvariantCulture is mandatory here: "yyyy-MM-ddTHH:mm:ssZ" treats ':'
        # as the current culture's time-separator PLACEHOLDER, not a literal
        # character, so under a non-English culture (for example da-DK) this
        # would otherwise render as "01.14.34" and corrupt the required
        # RFC3339 UTC format.
        $nowUtc = [DateTimeOffset]::UtcNow
        $nowText = $nowUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)
        $leaseUntilText = $nowUtc.AddHours($leaseHours).ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)

        if ($isRenewal) {
            $claimId = $existingClaim.claim_id
            $startedUtc = $existingClaim.started_utc
            $baseCommit = $existingClaim.base_commit
            $predecessorClaimId = $existingClaim.predecessor_claim_id
            $replacesClaimId = $existingClaim.replaces_claim_id
            $replacementReason = $existingClaim.replacement_reason
            $durableStatusPath = $existingClaim.durable_status_path
            $durableStatusSha256 = $existingClaim.durable_status_sha256
            $durableStatusBlobOid = $existingClaim.durable_status_blob_oid
            $handoffCommit = $existingClaim.handoff_commit
        }
        else {
            $claimId = [Guid]::NewGuid().ToString()
            $startedUtc = $nowText
            $baseCommit = $Context.Head
            $predecessorClaimId = $null
            $replacesClaimId = $null
            $replacementReason = $null
            $durableStatusPath = $null
            $durableStatusSha256 = $null
            $durableStatusBlobOid = $null
            $handoffCommit = $null
        }

        $claimObject = [PSCustomObject][ordered]@{
            schema_version          = 1
            claim_id                 = $claimId
            workstream_id            = $workstreamId
            agent                    = $agent
            session_id               = $sessionId
            worktree_path            = (ConvertTo-ForwardSlashPath -Path $Context.WorktreePath)
            branch                   = $Context.Branch
            base_commit              = $baseCommit
            scope_paths              = $normalizedScope
            started_utc              = $startedUtc
            heartbeat_utc            = $nowText
            lease_until_utc          = $leaseUntilText
            state                    = 'active'
            preexisting_dirty        = $preexistingDirtySorted
            predecessor_claim_id     = $predecessorClaimId
            replaces_claim_id        = $replacesClaimId
            replacement_reason       = $replacementReason
            durable_status_path      = $durableStatusPath
            durable_status_sha256    = $durableStatusSha256
            durable_status_blob_oid  = $durableStatusBlobOid
            handoff_commit           = $handoffCommit
        }

        $claimPath = Join-Path $claimsDir "$workstreamId.json"
        try {
            Write-JsonAtomic -Path $claimPath -Object $claimObject `
                -FaultBeforeReplace 'start-before-claim-replace' `
                -FaultAfterReplace 'start-after-claim-replace'
        }
        catch [ContinuityTestFaultException] {
            if ($_.Exception.Phase -eq 'after') {
                $stateException = [ContinuityStateException]::new(
                    'Atomic claim write did not complete after replacing the target file; the new claim is authoritative.'
                )
                $stateException.ClaimId = $claimId
                # Normalize-ScopePath does not forbid a literal single quote in a
                # scope segment (for example "o'brien/notes"), so each segment
                # must have its embedded quotes escaped as '' before it is
                # spliced into this single-quoted PowerShell array literal --
                # otherwise the emitted retry_command is not valid, copy-pasteable
                # PowerShell source.
                $escapedScope = @($normalizedScope | ForEach-Object { $_.Replace("'", "''") })
                $stateException.Recovery = [PSCustomObject][ordered]@{
                    claim_id             = $claimId
                    authoritative_owner  = 'new-claim'
                    retry_command        = "start -Agent $agent -SessionId $sessionId -Workstream $workstreamId -Scope @('$($escapedScope -join "', '")')"
                }
                throw $stateException
            }
            else {
                throw [ContinuityStateException]::new(
                    'Atomic claim write did not complete before replacing the target file; no claim was changed.'
                )
            }
        }

        $warnings = @()
        foreach ($dirty in $preexistingDirtySorted) {
            $warnings += @{
                code    = 'preexisting-dirty'
                message = "Path '$($dirty.path)' (status '$($dirty.status)') is outside the claimed scope and was left untouched."
            }
        }

        $resultClaims = @($othersLive) + @($claimObject)

        $gitInfo = [ordered]@{
            worktree_path    = ConvertTo-ForwardSlashPath -Path $Context.WorktreePath
            branch           = $Context.Branch
            detached         = $Context.Detached
            head             = $Context.Head
            upstream         = $Context.Upstream
            protected_branch = $Context.ProtectedBranch
        }

        return New-OperationResult -Operation 'start' `
            -RepoRoot (ConvertTo-ForwardSlashPath -Path $Context.RepoRoot) `
            -GitCommonDir (ConvertTo-ForwardSlashPath -Path $Context.GitCommonDir) `
            -WorkstreamId $workstreamId `
            -ClaimId $claimId `
            -Git ([PSCustomObject] $gitInfo) `
            -Claims $resultClaims `
            -Warnings $warnings `
            -Errors @()
    }
}

function ConvertTo-DocumentLines {
    <#
    .SYNOPSIS
        Splits raw text into an ordered list of `{ Content; Eol }` line
        records, each line's own original newline terminator (`` `r`n ``,
        `` `n ``, or none for a final unterminated line) preserved exactly,
        so Write-WorkstreamDocument can change only specific lines while
        reconstructing every other byte -- including the document's own
        newline style -- exactly.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Text
    )

    $lines = [System.Collections.Generic.List[object]]::new()
    $pos = 0
    while ($true) {
        $newlineIndex = $Text.IndexOf("`n", $pos)
        if ($newlineIndex -lt 0) {
            $lines.Add([PSCustomObject]@{ Content = $Text.Substring($pos); Eol = '' })
            break
        }
        $eolStart = $newlineIndex
        if ($newlineIndex -gt $pos -and $Text[$newlineIndex - 1] -eq "`r") {
            $eolStart = $newlineIndex - 1
        }
        $content = $Text.Substring($pos, $eolStart - $pos)
        $eol = $Text.Substring($eolStart, ($newlineIndex - $eolStart) + 1)
        $lines.Add([PSCustomObject]@{ Content = $content; Eol = $eol })
        $pos = $newlineIndex + 1
    }
    return $lines
}

function Write-TextAtomic {
    <#
    .SYNOPSIS
        Same same-directory-temp-file-then-`File.Move` atomic pattern as
        Write-JsonAtomic, but for plain UTF-8-no-BOM text instead of a
        serialized JSON object; used to rewrite the owned workstream
        Markdown file. Task 4 has no named test-fault boundary of its own
        (the plan's global constraints reserve new fault names for Tasks 3
        and 5-7), so this function accepts none -- the analogous production
        failure (another process holding the destination file open when the
        atomic replace runs) is exercised in tests directly, not injected.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [string] $Text
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null

    $tempPath = Join-Path $directory ([Guid]::NewGuid().ToString('N') + '.tmp')
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)

    $stream = [System.IO.File]::Open(
        $tempPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None
    )
    try {
        $bytes = $utf8NoBom.GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }

    try {
        [System.IO.File]::Move($tempPath, $Path, $true)
    }
    catch {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

function Read-WorkstreamDocument {
    <#
    .SYNOPSIS
        Parses one owned `.ai/workstreams/<workstream-id>.md` file into the
        line-preserving structure `update` needs to change only the managed
        milestone section plus the explicit `Last milestone`/`Head
        commit`/`State`/`Next action` fields, leaving every other byte --
        including the document's own newline style -- untouched (design
        spec, Helper Contract: "update may edit only that bounded section
        plus the template's explicit... fields"). Throws
        ContinuityStateException (exit 3, no mutation) for a missing file, a
        missing or duplicated managed marker, an out-of-order marker pair, a
        missing/duplicated/malformed required field, or a `State`/`Head
        commit`/`Last milestone` field that appears at or after the `Next
        action` heading (Write-WorkstreamDocument assumes those fields
        precede it).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [string] $WorkstreamId
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' does not exist.")
    }

    $rawText = [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
    $lines = ConvertTo-DocumentLines -Text $rawText

    $markerStartIndices = @()
    $markerEndIndices = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Content -ceq $Script:MilestoneMarkerStart) { $markerStartIndices += $i }
        if ($lines[$i].Content -ceq $Script:MilestoneMarkerEnd) { $markerEndIndices += $i }
    }

    if ($markerStartIndices.Count -eq 0 -or $markerEndIndices.Count -eq 0) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' is missing the managed milestone marker pair."
        )
    }
    if ($markerStartIndices.Count -gt 1 -or $markerEndIndices.Count -gt 1) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' has a duplicate managed milestone marker."
        )
    }
    $markerStartIndex = $markerStartIndices[0]
    $markerEndIndex = $markerEndIndices[0]
    if ($markerStartIndex -ge $markerEndIndex) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' has the milestone end marker before the start marker."
        )
    }

    $workstreamIdLineIndex = $null
    $parsedWorkstreamId = $null
    $stateLineIndex = $null
    $currentState = $null
    $headCommitLineIndex = $null
    $lastMilestoneLineIndex = $null
    $nextActionHeadingIndex = $null

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $content = $lines[$i].Content

        if ($content -cmatch '^- \*\*workstream_id:\*\* `([^`]*)`$') {
            if ($null -ne $workstreamIdLineIndex) {
                throw [ContinuityStateException]::new("Owned workstream file '$Path' has a duplicate 'workstream_id' field.")
            }
            $workstreamIdLineIndex = $i
            $parsedWorkstreamId = $Matches[1]
        }
        elseif ($content -cmatch '^- \*\*workstream_id:\*\* ') {
            throw [ContinuityStateException]::new("Owned workstream file '$Path' has a malformed 'workstream_id' field.")
        }

        if ($content -cmatch '^- \*\*State:\*\* (.+)$') {
            if ($null -ne $stateLineIndex) {
                throw [ContinuityStateException]::new("Owned workstream file '$Path' has a duplicate 'State' field.")
            }
            $stateLineIndex = $i
            $currentState = $Matches[1]
        }
        elseif ($content -cmatch '^- \*\*State:\*\* ') {
            throw [ContinuityStateException]::new("Owned workstream file '$Path' has a malformed 'State' field.")
        }

        if ($content -cmatch '^- \*\*Head commit:\*\* `([0-9a-fA-F]{40})`$') {
            if ($null -ne $headCommitLineIndex) {
                throw [ContinuityStateException]::new("Owned workstream file '$Path' has a duplicate 'Head commit' field.")
            }
            $headCommitLineIndex = $i
        }
        elseif ($content -cmatch '^- \*\*Head commit:\*\* ') {
            throw [ContinuityStateException]::new(
                "Owned workstream file '$Path' has a malformed 'Head commit' field; it must be a full 40-character commit SHA in backticks."
            )
        }

        if ($content -cmatch '^- \*\*Last milestone:\*\* (.+)$') {
            if ($null -ne $lastMilestoneLineIndex) {
                throw [ContinuityStateException]::new("Owned workstream file '$Path' has a duplicate 'Last milestone' field.")
            }
            $lastMilestoneLineIndex = $i
        }
        elseif ($content -cmatch '^- \*\*Last milestone:\*\* ') {
            throw [ContinuityStateException]::new("Owned workstream file '$Path' has a malformed 'Last milestone' field.")
        }

        if ($content -ceq '## Next action') {
            if ($null -ne $nextActionHeadingIndex) {
                throw [ContinuityStateException]::new("Owned workstream file '$Path' has a duplicate 'Next action' section.")
            }
            $nextActionHeadingIndex = $i
        }
    }

    if ($null -eq $workstreamIdLineIndex) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' is missing the required 'workstream_id' field.")
    }
    if ($parsedWorkstreamId -cne $WorkstreamId) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' has workstream_id '$parsedWorkstreamId', expected '$WorkstreamId'."
        )
    }
    if ($null -eq $stateLineIndex) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' is missing the required 'State' field.")
    }
    if ($null -eq $headCommitLineIndex) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' is missing the required 'Head commit' field.")
    }
    if ($null -eq $lastMilestoneLineIndex) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' is missing the required 'Last milestone' field.")
    }
    if ($null -eq $nextActionHeadingIndex) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' is missing the required 'Next action' section.")
    }
    if ($nextActionHeadingIndex -ge $markerStartIndex) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' must have the 'Next action' section before the milestone marker."
        )
    }
    # Write-WorkstreamDocument's "Next action" body rewrite removes every
    # line between $nextActionHeadingIndex and $markerStartIndex, then
    # single-line-replaces the State/Head commit/Last milestone fields
    # in-place at their originally recorded indices. That is only safe when
    # those three fields all precede the "Next action" heading -- otherwise
    # a relocated field would fall inside the removed span (silently
    # deleted) while its recorded index still pointed at stale content.
    # Reject that ordering explicitly here, before any mutation, instead of
    # relying on it silently.
    if ($stateLineIndex -ge $nextActionHeadingIndex -or
        $headCommitLineIndex -ge $nextActionHeadingIndex -or
        $lastMilestoneLineIndex -ge $nextActionHeadingIndex) {
        throw [ContinuityStateException]::new(
            "Owned workstream file '$Path' must have the 'State', 'Head commit', and 'Last milestone' fields before the 'Next action' section."
        )
    }

    $bodyLines = @()
    if ($markerStartIndex - 1 -ge $nextActionHeadingIndex + 1) {
        $bodyLines = @($lines[($nextActionHeadingIndex + 1)..($markerStartIndex - 1)])
    }
    $nextActionBody = (($bodyLines | ForEach-Object { $_.Content }) -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($nextActionBody)) {
        throw [ContinuityStateException]::new("Owned workstream file '$Path' has an empty required 'Next action' section.")
    }

    return [PSCustomObject]@{
        Path                   = $Path
        Lines                  = $lines
        MarkerStartIndex       = $markerStartIndex
        MarkerEndIndex         = $markerEndIndex
        StateLineIndex         = $stateLineIndex
        CurrentState           = $currentState
        HeadCommitLineIndex    = $headCommitLineIndex
        LastMilestoneLineIndex = $lastMilestoneLineIndex
        NextActionHeadingIndex = $nextActionHeadingIndex
        NextActionBody         = $nextActionBody
    }
}

function Write-WorkstreamDocument {
    <#
    .SYNOPSIS
        Atomically rewrites the owned workstream file, changing only: the
        `State` field (when `-NewState` is supplied), the `Head commit`
        field (always, to the current HEAD), the `Last milestone` field
        (always, to the new milestone's one-line summary), the `Next
        action` section body (only when `-NewNextAction` is supplied), and
        the managed milestone marker region (always, appending
        `-MilestoneEntryLines` after any prior entries). Every other byte of
        the document, including its newline style, is copied through
        unchanged because only these specific lines are ever replaced.
        Edits are applied highest-line-index-first so an earlier edit never
        shifts an index Read-WorkstreamDocument already recorded.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Document,
        [AllowNull()]
        [AllowEmptyString()]
        [string] $NewState,
        [Parameter(Mandatory = $true)]
        [string] $NewHeadCommit,
        [Parameter(Mandatory = $true)]
        [string] $LastMilestoneText,
        [Parameter(Mandatory = $true)]
        [string[]] $MilestoneEntryLines,
        [AllowNull()]
        [AllowEmptyString()]
        [string] $NewNextAction
    )

    $lines = [System.Collections.Generic.List[object]]::new($Document.Lines)
    $eol = $lines[$Document.MarkerStartIndex].Eol
    if ([string]::IsNullOrEmpty($eol)) { $eol = [Environment]::NewLine }

    # 1) Highest index: insert the new milestone entry immediately before the
    #    end marker, after any prior entries. This never shifts any index
    #    below $Document.MarkerEndIndex.
    $milestoneLineObjects = @($MilestoneEntryLines | ForEach-Object { [PSCustomObject]@{ Content = $_; Eol = $eol } })
    $lines.InsertRange($Document.MarkerEndIndex, [object[]] $milestoneLineObjects)

    # 2) Middle: replace the "Next action" body span (still at its original
    #    indices; step 1 only touched indices at or above MarkerEndIndex).
    if (-not [string]::IsNullOrEmpty($NewNextAction)) {
        $removeStart = $Document.NextActionHeadingIndex + 1
        $removeCount = $Document.MarkerStartIndex - $removeStart
        if ($removeCount -gt 0) {
            $lines.RemoveRange($removeStart, $removeCount)
        }

        $newBodyLines = [System.Collections.Generic.List[object]]::new()
        $newBodyLines.Add([PSCustomObject]@{ Content = ''; Eol = $eol })
        foreach ($textLine in ($NewNextAction -split "`r`n|`n|`r")) {
            $newBodyLines.Add([PSCustomObject]@{ Content = $textLine; Eol = $eol })
        }
        $newBodyLines.Add([PSCustomObject]@{ Content = ''; Eol = $eol })
        $lines.InsertRange($removeStart, [object[]] $newBodyLines)
    }

    # 3) Lowest: single-line, in-place field replacements; these indices are
    #    always above the document's top and below the "Next action"
    #    heading, so neither step 1 nor step 2 ever shifted them.
    $lines[$Document.LastMilestoneLineIndex] = [PSCustomObject]@{
        Content = '- **Last milestone:** ' + $LastMilestoneText
        Eol     = $lines[$Document.LastMilestoneLineIndex].Eol
    }
    $lines[$Document.HeadCommitLineIndex] = [PSCustomObject]@{
        Content = '- **Head commit:** ' + (Format-CodeSpan -Text $NewHeadCommit)
        Eol     = $lines[$Document.HeadCommitLineIndex].Eol
    }
    if (-not [string]::IsNullOrEmpty($NewState)) {
        $lines[$Document.StateLineIndex] = [PSCustomObject]@{
            Content = '- **State:** ' + $NewState
            Eol     = $lines[$Document.StateLineIndex].Eol
        }
    }

    $rawText = ($lines | ForEach-Object { $_.Content + $_.Eol }) -join ''
    Write-TextAtomic -Path $Document.Path -Text $rawText
}

function Test-VerificationArguments {
    <#
    .SYNOPSIS
        Validates `-VerificationResult` and its dependent parameters against
        the design's verification matrix (Helper Contract, "update"):
        `not-run` requires only `-NotRunReason` and forbids
        command/commit/dirty-path evidence; `passed`/`failed` require
        `-VerificationCommand` and the full current `HEAD` commit SHA (a
        stale, non-HEAD commit is rejected) and forbid `-NotRunReason`. Also
        rejects the literal `dirty` placeholder up front, since that check
        needs no Git call. Currently-dirty/scope/changed-path membership
        checks for dirty paths are Test-DirtyVerificationPaths' job.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyString()]
        [string] $VerificationResult,
        [AllowNull()]
        [AllowEmptyString()]
        [string] $VerificationCommand,
        [AllowNull()]
        [AllowEmptyString()]
        [string] $VerificationCommit,
        [string[]] $VerificationDirtyPath,
        [AllowNull()]
        [AllowEmptyString()]
        [string] $NotRunReason,
        [Parameter(Mandatory = $true)]
        [string] $CurrentHead
    )

    if ($Script:ValidVerificationResults -notcontains $VerificationResult) {
        throw [ContinuityValidationException]::new(
            "-VerificationResult must be one of: $($Script:ValidVerificationResults -join ', ')."
        )
    }

    $dirtyPaths = @()
    if ($null -ne $VerificationDirtyPath) { $dirtyPaths = @($VerificationDirtyPath) }

    if ($VerificationResult -eq 'not-run') {
        if ([string]::IsNullOrWhiteSpace($NotRunReason)) {
            throw [ContinuityValidationException]::new("-NotRunReason is required when -VerificationResult is 'not-run'.")
        }
        if (-not [string]::IsNullOrWhiteSpace($VerificationCommand)) {
            throw [ContinuityValidationException]::new("-VerificationCommand must not be supplied when -VerificationResult is 'not-run'.")
        }
        if (-not [string]::IsNullOrWhiteSpace($VerificationCommit)) {
            throw [ContinuityValidationException]::new("-VerificationCommit must not be supplied when -VerificationResult is 'not-run'.")
        }
        if ($dirtyPaths.Count -gt 0) {
            throw [ContinuityValidationException]::new("-VerificationDirtyPath must not be supplied when -VerificationResult is 'not-run'.")
        }
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($NotRunReason)) {
        throw [ContinuityValidationException]::new("-NotRunReason must not be supplied when -VerificationResult is '$VerificationResult'.")
    }
    if ([string]::IsNullOrWhiteSpace($VerificationCommand)) {
        throw [ContinuityValidationException]::new("-VerificationCommand is required when -VerificationResult is '$VerificationResult'.")
    }
    if ([string]::IsNullOrWhiteSpace($VerificationCommit)) {
        throw [ContinuityValidationException]::new("-VerificationCommit is required when -VerificationResult is '$VerificationResult'.")
    }
    if ($VerificationCommit -notmatch '^[0-9a-fA-F]{40}$') {
        throw [ContinuityValidationException]::new('-VerificationCommit must be a full 40-character commit SHA.')
    }
    if (-not $VerificationCommit.Equals($CurrentHead, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw [ContinuityValidationException]::new(
            "-VerificationCommit '$VerificationCommit' does not match the current HEAD '$CurrentHead'; stale commits are rejected."
        )
    }
    foreach ($dirtyPath in $dirtyPaths) {
        if ($dirtyPath -ceq 'dirty') {
            throw [ContinuityValidationException]::new("-VerificationDirtyPath must not use the literal placeholder 'dirty'.")
        }
    }
}

function Test-DirtyVerificationPaths {
    <#
    .SYNOPSIS
        Validates that every supplied `-VerificationDirtyPath` entry is
        currently dirty, inside the claim's own scope, and also present in
        this same `update` call's `-ChangedPath` evidence -- so it is
        genuinely "listed in the owned workstream file" once the milestone
        this call records is written (design spec, Helper Contract:
        "supplied dirty paths are currently dirty, within the claim scope,
        and listed in the owned workstream file"). Only called when at
        least one dirty path was supplied.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Context,
        [Parameter(Mandatory = $true)]
        [string[]] $DirtyPath,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $ScopePath,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]] $ChangedPath
    )

    $statusEntries = Get-GitStatusEntries -RepoRoot $Context.RepoRoot
    $currentlyDirtyPaths = @($statusEntries | ForEach-Object { $_.Path })

    # The loop variable is deliberately NOT named `$dirtyPath`: PowerShell
    # variable names are case-insensitive, so a loop variable matching the
    # iterated collection's own name (`$DirtyPath`) reuses that same
    # strongly-typed `[string[]]` variable slot. Each iteration's assignment
    # then gets coerced back to `string[]`, silently re-wrapping the scalar
    # element in a new single-element array instead of unwrapping it -- which
    # breaks the -Path string parameter binding below.
    foreach ($candidatePath in $DirtyPath) {
        if ($currentlyDirtyPaths -notcontains $candidatePath) {
            throw [ContinuityValidationException]::new("-VerificationDirtyPath '$candidatePath' is not currently dirty.")
        }
        if (-not (Test-PathWithinScope -Path $candidatePath -ScopePrefixes $ScopePath)) {
            throw [ContinuityValidationException]::new("-VerificationDirtyPath '$candidatePath' is outside the claim's scope.")
        }
        if ($ChangedPath -notcontains $candidatePath) {
            throw [ContinuityValidationException]::new(
                "-VerificationDirtyPath '$candidatePath' must also be listed in -ChangedPath so it is recorded in the owned workstream file."
            )
        }
    }
}

function Invoke-Update {
    <#
    .SYNOPSIS
        Renews the lease on an existing active claim and appends one
        durable milestone to its owned workstream file (design spec, Helper
        Contract: "update"). Validates identity, branch, and the full
        verification matrix BEFORE any filesystem mutation, then -- under
        the single common lock -- locates the claim by `-ClaimId`, confirms
        it is `active`, confirms the current branch is allowed and the
        caller's identity matches, validates any dirty verification
        evidence, atomically rewrites only the owned workstream file's
        managed milestone section plus its explicit fields, and only THEN
        atomically renews the claim's lease. A workstream-file write
        failure leaves the claim completely unchanged. A claim-renewal
        failure AFTER a successful workstream write leaves the old claim
        authoritative and reports `durable_update_applied: true` plus an
        identical-scope `start` retry command -- recovery is always
        `start`, never `update`, so the milestone can never be duplicated.
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Context,
        [string] $ClaimId,
        [string] $Agent,
        [string] $SessionId,
        [string] $Summary,
        [string[]] $ChangedPath,
        [string] $State,
        [string] $NextAction,
        [string] $VerificationResult,
        [string] $VerificationCommand,
        [string] $VerificationCommit,
        [string[]] $VerificationDirtyPath,
        [string] $NotRunReason
    )

    # --- Validation before any filesystem mutation -------------------------
    Assert-RequiredParameter -Value $ClaimId -Name 'ClaimId'
    Assert-RequiredParameter -Value $Agent -Name 'Agent'
    Assert-RequiredParameter -Value $SessionId -Name 'SessionId'
    Assert-RequiredParameter -Value $Summary -Name 'Summary'

    $agent = Assert-ValidAgent -Value $Agent
    $sessionId = Normalize-Id -Value $SessionId -Kind 'Session'

    if (-not [string]::IsNullOrEmpty($State) -and $Script:ValidUpdateStates -notcontains $State) {
        throw [ContinuityValidationException]::new("-State must be one of: $($Script:ValidUpdateStates -join ', ').")
    }
    if ($State -eq 'handoff' -and [string]::IsNullOrWhiteSpace($NextAction)) {
        throw [ContinuityValidationException]::new("-NextAction is required and must be non-empty when -State is 'handoff'.")
    }

    Test-VerificationArguments -VerificationResult $VerificationResult `
        -VerificationCommand $VerificationCommand -VerificationCommit $VerificationCommit `
        -VerificationDirtyPath $VerificationDirtyPath -NotRunReason $NotRunReason `
        -CurrentHead $Context.Head

    $rawChangedPaths = @()
    if ($null -ne $ChangedPath) { $rawChangedPaths = @($ChangedPath) }
    $normalizedChangedPaths = @()
    foreach ($rawPath in $rawChangedPaths) {
        $normalizedChangedPaths += Normalize-ScopePath -Path $rawPath
    }
    $normalizedChangedPaths = @($normalizedChangedPaths | Select-Object -Unique)

    $dirtyPaths = @()
    if ($null -ne $VerificationDirtyPath) { $dirtyPaths = @($VerificationDirtyPath) }

    # --- Under the common lock ---------------------------------------------
    $continuityDir = Join-Path $Context.GitCommonDir $Script:ContinuityDirName
    $lockPath = Join-Path $continuityDir $Script:LockFileName

    return Use-ContinuityLock -LockPath $lockPath -ScriptBlock {
        # Deliberately NOT named `$state`: PowerShell variable names are
        # case-insensitive, so that name would reuse the same strongly-typed
        # `[string] $State` parameter slot (Invoke-Update's own `-State`
        # parameter), silently clobbering the caller-supplied state value
        # with this continuity-state object for the rest of the closure.
        $continuityState = Read-ContinuityState -GitCommonDir $Context.GitCommonDir
        $claims = @($continuityState.Claims)

        $matching = @($claims | Where-Object { $_.claim_id -ceq $ClaimId })
        if ($matching.Count -eq 0) {
            throw [ContinuityValidationException]::new("No claim found matching claim id '$ClaimId'.")
        }
        $claim = $matching[0]

        if ($claim.state -ne 'active') {
            throw [ContinuityValidationException]::new(
                "Claim '$ClaimId' is not active (state '$($claim.state)'); update requires an active claim."
            )
        }

        $workstreamId = $claim.workstream_id
        Assert-WorkstreamBranchAllowed -Context $Context -WorkstreamId $workstreamId

        $identityMatches = Test-ClaimIdentity -Claim $claim -Agent $agent -SessionId $sessionId `
            -WorktreePath (ConvertTo-ForwardSlashPath -Path $Context.WorktreePath) -Branch $Context.Branch
        if (-not $identityMatches) {
            throw [ContinuityValidationException]::new(
                "Claim '$ClaimId' does not match the current agent, session, worktree, or branch."
            )
        }

        $claimScopePaths = @($claim.scope_paths)

        if ($dirtyPaths.Count -gt 0) {
            Test-DirtyVerificationPaths -Context $Context -DirtyPath $dirtyPaths `
                -ScopePath $claimScopePaths -ChangedPath $normalizedChangedPaths
        }

        $workstreamPath = Join-Path $Context.RepoRoot (Join-Path '.ai' (Join-Path 'workstreams' "$workstreamId.md"))
        $document = Read-WorkstreamDocument -Path $workstreamPath -WorkstreamId $workstreamId

        $nowUtc = [DateTimeOffset]::UtcNow
        $nowText = $nowUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)
        $effectiveState = if (-not [string]::IsNullOrEmpty($State)) { $State } else { $document.CurrentState }

        $milestoneLines = [System.Collections.Generic.List[string]]::new()
        $milestoneLines.Add("- $nowText - state: $effectiveState - $Summary")

        if ($normalizedChangedPaths.Count -gt 0) {
            $quotedChanged = @($normalizedChangedPaths | ForEach-Object { Format-CodeSpan -Text $_ })
            $milestoneLines.Add("  - Changed paths: $($quotedChanged -join ', ')")
        }

        if ($VerificationResult -eq 'not-run') {
            $milestoneLines.Add("  - Verification: not-run - reason: $NotRunReason")
        }
        else {
            $verificationLine = "  - Verification: $VerificationResult - command $(Format-CodeSpan -Text $VerificationCommand) - commit $(Format-CodeSpan -Text $VerificationCommit)"
            if ($dirtyPaths.Count -gt 0) {
                $quotedDirty = @($dirtyPaths | ForEach-Object { Format-CodeSpan -Text $_ })
                $verificationLine += " - dirty paths: $($quotedDirty -join ', ')"
            }
            $milestoneLines.Add($verificationLine)
        }

        if ($effectiveState -eq 'blocked') {
            $milestoneLines.Add("  - Blockers: $Summary")
        }

        if (-not [string]::IsNullOrWhiteSpace($NextAction)) {
            $milestoneLines.Add("  - Next action: $NextAction")
        }

        Write-WorkstreamDocument -Document $document -NewState $State -NewHeadCommit $Context.Head `
            -LastMilestoneText "$nowText - $Summary" -MilestoneEntryLines @($milestoneLines) `
            -NewNextAction $NextAction

        $renewedLeaseUntilText = $nowUtc.AddHours($Script:DefaultLeaseHours).ToString(
            "yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture
        )
        $renewedClaim = [PSCustomObject][ordered]@{
            schema_version          = $claim.schema_version
            claim_id                = $claim.claim_id
            workstream_id           = $claim.workstream_id
            agent                   = $claim.agent
            session_id              = $claim.session_id
            worktree_path           = $claim.worktree_path
            branch                  = $claim.branch
            base_commit             = $claim.base_commit
            scope_paths             = $claimScopePaths
            started_utc             = $claim.started_utc
            heartbeat_utc           = $nowText
            lease_until_utc         = $renewedLeaseUntilText
            state                   = $claim.state
            preexisting_dirty       = @($claim.preexisting_dirty)
            predecessor_claim_id    = $claim.predecessor_claim_id
            replaces_claim_id       = $claim.replaces_claim_id
            replacement_reason      = $claim.replacement_reason
            durable_status_path     = $claim.durable_status_path
            durable_status_sha256   = $claim.durable_status_sha256
            durable_status_blob_oid = $claim.durable_status_blob_oid
            handoff_commit          = $claim.handoff_commit
        }

        $claimsDir = Join-Path $continuityDir 'claims'
        $claimPath = Join-Path $claimsDir "$workstreamId.json"
        try {
            Write-JsonAtomic -Path $claimPath -Object $renewedClaim `
                -FaultBeforeReplace 'update-after-workstream-write'
        }
        catch {
            $stateException = [ContinuityStateException]::new(
                'The workstream milestone was written durably, but the claim lease could not be renewed afterward; the existing claim remains authoritative.'
            )
            $stateException.ClaimId = $claim.claim_id
            $escapedScope = @($claimScopePaths | ForEach-Object { $_.Replace("'", "''") })
            $stateException.Recovery = [PSCustomObject][ordered]@{
                claim_id               = $claim.claim_id
                authoritative_owner    = 'existing-claim'
                durable_update_applied = $true
                retry_command          = "start -Agent $($claim.agent) -SessionId $($claim.session_id) -Workstream $workstreamId -Scope @('$($escapedScope -join "', '")')"
            }
            throw $stateException
        }

        $gitInfo = [ordered]@{
            worktree_path    = ConvertTo-ForwardSlashPath -Path $Context.WorktreePath
            branch           = $Context.Branch
            detached         = $Context.Detached
            head             = $Context.Head
            upstream         = $Context.Upstream
            protected_branch = $Context.ProtectedBranch
        }

        return New-OperationResult -Operation 'update' `
            -RepoRoot (ConvertTo-ForwardSlashPath -Path $Context.RepoRoot) `
            -GitCommonDir (ConvertTo-ForwardSlashPath -Path $Context.GitCommonDir) `
            -WorkstreamId $workstreamId `
            -ClaimId $renewedClaim.claim_id `
            -Git ([PSCustomObject] $gitInfo) `
            -Claims @($renewedClaim) `
            -Warnings @() `
            -Errors @()
    }
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
    Assert-ValidOperation -Operation $Operation
    Assert-LeaseHoursInRange -LeaseHours $LeaseHours -WasSupplied $PSBoundParameters.ContainsKey('LeaseHours')
    Assert-KnownTestFault

    $repositoryContext = Get-RepositoryContext

    $result = switch ($Operation) {
        'status' { Invoke-Status -Context $repositoryContext -Workstream $Workstream }
        'start' {
            Invoke-Start -Context $repositoryContext -Agent $Agent -SessionId $SessionId `
                -Workstream $Workstream -Scope $Scope -LeaseHours $LeaseHours `
                -LeaseHoursSupplied $PSBoundParameters.ContainsKey('LeaseHours')
        }
        'update' {
            Invoke-Update -Context $repositoryContext -ClaimId $ClaimId -Agent $Agent -SessionId $SessionId `
                -Summary $Summary -ChangedPath $ChangedPath -State $State -NextAction $NextAction `
                -VerificationResult $VerificationResult -VerificationCommand $VerificationCommand `
                -VerificationCommit $VerificationCommit -VerificationDirtyPath $VerificationDirtyPath `
                -NotRunReason $NotRunReason
        }
        default {
            throw [ContinuityValidationException]::new(
                "Operation '$Operation' is not implemented yet; only 'status', 'start', and 'update' are available."
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
        -ClaimId $_.Exception.ClaimId `
        -Recovery $_.Exception.Recovery `
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

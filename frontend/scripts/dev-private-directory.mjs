import { execFileSync } from "node:child_process";
import { chmodSync } from "node:fs";
import { join } from "node:path";

// Apply only to a newly created, empty staging directory. Paths travel as data,
// never as PowerShell source; inherited grants are removed before writing secrets.
export function protectDevStagingDirectory(directory) {
  if (process.platform !== "win32") {
    chmodSync(directory, 0o700);
    return;
  }
  const script = `
$ErrorActionPreference = 'Stop'
$target = $env:ECHO_PRIVATE_STAGING_DIRECTORY
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($sid)
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
[System.IO.Directory]::SetAccessControl($target, $acl)
$actual = [System.IO.Directory]::GetAccessControl($target)
$rules = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
if (-not $actual.AreAccessRulesProtected -or $rules.Count -ne 1 -or $rules[0].IdentityReference -ne $sid -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl') {
  throw 'Private staging ACL verification failed'
}
`;
  try {
    execFileSync(
      join(
        process.env.SystemRoot || "C:\\Windows",
        "System32/WindowsPowerShell/v1.0/powershell.exe",
      ),
      ["-NoProfile", "-NonInteractive", "-Command", script],
      {
        env: { ...process.env, ECHO_PRIVATE_STAGING_DIRECTORY: directory },
        windowsHide: true,
        timeout: 15_000,
        stdio: "pipe",
      },
    );
  } catch {
    throw new Error(
      "Cannot protect development model staging directory; no model configuration was copied",
    );
  }
}

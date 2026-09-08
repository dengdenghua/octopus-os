"""Verify disk ACLs independently of appliance's ctypes implementation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def read_windows_acl(path: Path) -> dict:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "Windows ACL verification requires PowerShell"
    script = r"""
$acl = Get-Acl -LiteralPath $env:ECHO_TEST_ACL_PATH
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
    ForEach-Object { [PSCustomObject]@{sid=$_.IdentityReference.Value; rights=[int]$_.FileSystemRights;
        type=[string]$_.AccessControlType; inherited=$_.IsInherited} })
[PSCustomObject]@{owner=$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value;
    current=$sid; protected=$acl.AreAccessRulesProtected; rules=$rules} | ConvertTo-Json -Depth 4 -Compress
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={
            **{key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
            "ECHO_TEST_ACL_PATH": str(path),
        },
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_private_windows_acl(path: Path) -> None:
    acl = read_windows_acl(path)
    assert acl["owner"] == acl["current"]
    assert acl["protected"] is True
    assert acl["rules"] == [
        {"sid": acl["current"], "rights": 0x1F01FF, "type": "Allow", "inherited": False}
    ]

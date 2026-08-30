"""Shell state snapshot manager.

Handles capturing, wrapping, and restoring shell environment state
across subprocess boundaries.

Key design:
- Before execution: wrap user command with state restore logic
- After execution: parse new state from special output markers
- Graceful degradation: snapshot failure doesn't break command execution
"""

from __future__ import annotations

import logging
import os

from .shell_state import ShellEnvState

_logger = logging.getLogger(__name__)

STATE_MARKER_START = "🐙_SHELL_STATE_START"
STATE_MARKER_END = "🐙_SHELL_STATE_END"


class ShellStateManager:
    """Manages shell environment state across subprocess executions.

    This class provides:
    1. State capture from current process
    2. Command wrapping to restore state before execution
    3. New state extraction from command output
    """

    def __init__(
        self,
        enabled: bool = True,
        shell_type: str = "bash",
    ) -> None:
        self._enabled = enabled
        self._shell_type = shell_type
        self._current_state: ShellEnvState | None = None

    @property
    def current_state(self) -> ShellEnvState | None:
        return self._current_state

    @current_state.setter
    def current_state(self, state: ShellEnvState | None) -> None:
        self._current_state = state

    def capture_current_env(self) -> ShellEnvState:
        """Capture current process environment as a snapshot."""
        return ShellEnvState(
            cwd=os.getcwd(),
            env_vars=dict(os.environ),
        )

    def wrap_command(
        self,
        user_command: str,
        state: ShellEnvState | None = None,
    ) -> str:
        """Wrap a user command with state restore and capture logic.

        Args:
            user_command: The original command to execute.
            state: Optional previous state to restore. If None, uses
                self._current_state.

        Returns:
            Wrapped command string that restores state before execution
            and outputs new state after execution.
        """
        if not self._enabled:
            return user_command

        target_state = state or self._current_state

        if self._shell_type in ("bash", "zsh"):
            return self._wrap_bash_zsh(user_command, target_state)
        if self._shell_type in ("pwsh", "powershell"):
            return self._wrap_powershell(user_command, target_state)
        if self._shell_type == "cmd":
            return self._wrap_cmd(user_command, target_state)
        _logger.warning(
            "unsupported shell type: %s, skipping state wrap",
            self._shell_type,
        )
        return user_command

    def _wrap_bash_zsh(
        self,
        user_command: str,
        state: ShellEnvState | None,
    ) -> str:
        """Wrap command for bash/zsh with state restore and capture."""
        restore_block = ""
        if state:
            env_exports = "\n".join(
                f"export {self._escape_bash_var(k)}={self._escape_bash_var(v)}"
                for k, v in state.env_vars.items()
                if k not in ("SHLVL", "_", "PWD")
            )
            cd_line = f"cd {self._escape_bash_var(state.cwd)} 2>/dev/null || true"
            restore_block = f"{cd_line}\n{env_exports}" if env_exports else cd_line

        wrapped = f"""
{restore_block}

(
{user_command}
)
__EXIT_CODE__=$?

echo "{STATE_MARKER_START}"
__SWD__=$(pwd 2>/dev/null || echo "$PWD")
__STATE_JSON__=$(python3 -c "
import json, base64, os
state = {{'cwd': os.getcwd(), 'env_vars': dict(os.environ)}}
print(base64.b64encode(json.dumps(state).encode()).decode())
" 2>/dev/null || echo "")
if [ -z "$__STATE_JSON__" ]; then
    __STATE_JSON__=$(echo "$__SWD__" | base64)
fi
echo "$__STATE_JSON__"
echo "{STATE_MARKER_END}"

exit $__EXIT_CODE__
"""
        return wrapped.strip()

    def _wrap_powershell(
        self,
        user_command: str,
        state: ShellEnvState | None,
    ) -> str:
        """Wrap command for PowerShell with state restore and capture."""
        restore_block = ""
        if state:
            env_sets = "\n".join(
                f'$env:{self._escape_ps_key(k)}="{self._escape_ps_var(v)}"'
                for k, v in state.env_vars.items()
            )
            cd_line = (
                f'Set-Location "{self._escape_ps_var(state.cwd)}" -ErrorAction SilentlyContinue'
            )
            restore_block = f"{cd_line}\n{env_sets}" if env_sets else cd_line

        wrapped = f"""
{restore_block}

& {{
    {user_command}
    $global:__EXIT_CODE__ = $LASTEXITCODE
}}

Write-Output "{STATE_MARKER_START}"
$stateObj = @{{
    cwd = (Get-Location).Path
    env_vars = @{{}}
}}
Get-ChildItem Env: | ForEach-Object {{
    $stateObj.env_vars[$_.Name] = $_.Value
}}
$stateJson = $stateObj | ConvertTo-Json -Depth 3
$stateBytes = [System.Text.Encoding]::UTF8.GetBytes($stateJson)
[Convert]::ToBase64String($stateBytes)
Write-Output "{STATE_MARKER_END}"

exit $global:__EXIT_CODE__
"""
        return wrapped.strip()

    def _wrap_cmd(
        self,
        user_command: str,
        state: ShellEnvState | None,
    ) -> str:
        """Wrap command for cmd.exe with state restore."""
        if not state:
            return user_command

        env_sets = " & ".join(f"set {k}={v}" for k, v in state.env_vars.items())
        return f"cd /d {state.cwd} 2>nul & {env_sets} & {user_command}"

    def parse_new_state(self, output: str) -> ShellEnvState | None:
        """Extract new shell state from command output.

        Looks for the special markers in the output and parses the
        enclosed JSON state.

        Args:
            output: Full command output string.

        Returns:
            Parsed ShellEnvState if found, None otherwise.
        """
        start = output.find(STATE_MARKER_START)
        end = output.find(STATE_MARKER_END)

        if start == -1 or end == -1 or start >= end:
            return None

        state_b64 = output[start + len(STATE_MARKER_START) : end].strip()

        try:
            state = ShellEnvState.from_base64(state_b64)
            _logger.debug("parsed shell state: cwd=%s", state.cwd)
            return state
        except (OSError, TypeError, ValueError):
            _logger.warning("failed to parse shell state from output")
            return None

    def extract_clean_output(self, output: str) -> str:
        """Remove state markers from output, returning clean command output."""
        start = output.find(STATE_MARKER_START)
        end = output.find(STATE_MARKER_END)

        if start == -1 or end == -1 or start >= end:
            return output

        before = output[:start]
        after = output[end + len(STATE_MARKER_END) :]
        return before + after

    @staticmethod
    def _escape_bash_var(s: str) -> str:
        """Escape a string for safe use in bash double quotes."""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
            .replace("!", "\\!")
        )

    @staticmethod
    def _escape_ps_var(s: str) -> str:
        """Escape a string for safe use in PowerShell double quotes."""
        return s.replace("`", "``").replace('"', '`"').replace("$", "`$")

    @staticmethod
    def _escape_ps_key(s: str) -> str:
        """Escape a PowerShell environment variable name."""
        return s.replace("-", "`-").replace(" ", "` ")

"""
CLI-based software engineering agents, such as Claude Code and Codex CLI, are
carefully designed to be suitable for noninteractive work. Unfortunately, these
agents do not have a uniform command-line interface, so its easy to write code
that only works with one agent, even though the code could have been made to
support any agent. This little library is a uniform interface to any agent.

It assumes that the agent already works in your environment. For example, it is
up to you to ensure that you've logged in to your agent provider's account.
"""
from abc import ABC, abstractmethod
import subprocess
from pathlib import Path
from typing import Optional
import json
import sys
from contextlib import suppress

# cspell:ignore argsv


class Agent(ABC):
    def __init__(self):
        self._cwd = Path.cwd()

    @abstractmethod
    def prompt(self, prompt: str) -> None:
        """
        Set the prompt for the agent.
        """
        pass

    @abstractmethod
    def get_argsv(self) -> list[str]:
        """
        Get the command-line arguments for the agent. There is no need to
        call this method directly. Instead, use the run method.
        """
        pass

    def cwd(self, path: Path | str) -> None:
        """
        Set the current working directory for the agent. The agent will be able
        to edit files in this directory.
        """
        self._cwd = Path(path)

    @abstractmethod
    def add_dir(self, path: Path | str) -> None:
        """
        Give an additional working directory to the agent.
        """
        pass

    @abstractmethod
    def allow_bash_patterns(self, *patterns: str) -> None:
        """
        Grant the agent access to run the given commands. The patterns are
        either a full command, or a string such as "podman:*", to grant access
        to all podman subcommands.
        """
        pass

    @abstractmethod
    def allow_file(self, path: Path | str) -> None:
        """
        Grant the agent access to edit a single file. The exact behavior
        depends on the agent implementation.
        """
        pass

    @abstractmethod
    def allow_web_search(self) -> None:
        """
        Grant the agent access to perform web searches. The exact behavior
        depends on the agent implementation.
        """
        pass

    @abstractmethod
    def may_get_assistant_message(self, line: dict) -> Optional[str]:
        """
        While the agent is running, returns assistant messages from its output
        so that we can print them to the console.

        This is used by the run method and it should not be necessary to call
        it directly.
        """
        pass

    def run(self, log_file: Optional[Path] = None, silent: bool = False) -> int:
        
        if not log_file:
            log_file = Path("/dev/null")

        with log_file.open("w") as log:
            process = subprocess.Popen(
                self.get_argsv(),
                cwd=self._cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
            )

            for line in process.stdout:
                log.write(line)
                log.flush()

                if not silent:
                    try:
                        message = self.may_get_assistant_message(json.loads(line))
                    except json.JSONDecodeError:
                        message = line
                    if message:
                        print(message)

            process.wait()
            return process.returncode


class Codex(Agent):
    """
    The Codex CLI documentaion is on these web pages:

    - https://developers.openai.com/codex/cli/reference/
    """
    def __init__(self):
        super().__init__()
        self._ask_for_approval = "never"
        self._sandbox = "workspace-write"
        self._skip_git_repo_check = True
        self._json = True
        self._search = False
        self._dirs = []

    def prompt(self, prompt: str) -> None:
        self._prompt = prompt

    def allow_bash_patterns(self, *patterns: str) -> None:
        self._sandbox = "danger-full-access"

    def allow_file(self, path: Path | str) -> None:
        """
        Codex doesn't support file-level permissions, so we add the containing
        directory instead and print a warning.
        """
        file_path = Path(path).absolute()
        if not file_path.exists():
            raise ValueError(f"File {file_path} does not exist")
        if not file_path.is_file():
            raise ValueError(f"Path {file_path} is not a file")
        
        containing_dir = file_path.parent
        print(
            f"Warning: Codex doesn't support file-level permissions. "
            f"Adding containing directory {containing_dir} instead of file {file_path}",
            file=sys.stderr
        )
        self.add_dir(containing_dir)

    def allow_web_search(self) -> None:
        """
        Enable web search by setting the --search flag for Codex CLI.
        """
        self._search = True

    def add_dir(self, p: Path | str) -> None:
        p = p.absolute()
        if not p.is_dir():
            raise ValueError(f"Directory {p} does not exist")
        self._dirs.append(str(p))

    def may_get_assistant_message(self, message: dict) -> Optional[str]:
        with suppress(KeyError):
            if message["type"] != "item.completed":
                return
            # In Codex, the intermediate textual output is "reasoning".
            if message["item"]["type"] not in [ "agent_message", "reasoning" ]:
                return
            return message["item"]["text"]

    def get_argsv(self) -> list[str]:
        argsv = [
            "codex",
            "--ask-for-approval",
            self._ask_for_approval,
        ]

        if self._search:
            argsv.append("--search")

        argsv.append("exec")

        if self._skip_git_repo_check:
            argsv.append("--skip-git-repo-check")

        argsv.extend(["--sandbox", self._sandbox])

        if self._json:
            argsv.append("--json")

        for dir in self._dirs:
            argsv.extend(["--add-dir", dir])

        argsv.append(self._prompt)
        return argsv


class ClaudeCode(Agent):
    """
    The Claude Code CLI documentation spans several web pages:
    
    - https://code.claude.com/docs/en/cli-reference
    - https://code.claude.com/docs/en/iam
    """
    def __init__(self):
        super().__init__()
        self._output_format = "stream-json"
        self._verbose = True
        self._print = True
        self._tools = ["Bash", "Edit", "Read", "Write"]
        # Permission mode
        self._permission_mode = "acceptEdits"
        self._allowed_tools = []
        self._dirs = []

    def prompt(self, prompt: str) -> None:
        self._prompt = prompt

    def allow_bash_patterns(self, *patterns: str) -> None:
        for pattern in patterns:
            self._allowed_tools.append(f"Bash({pattern})")

    def allow_file(self, path: Path | str) -> None:
        """
        Grant the agent access to edit a single file using --allowedTools Edit(/path/to/file).
        """
        file_path = Path(path).absolute()
        if not file_path.exists():
            raise ValueError(f"File {file_path} does not exist")
        if not file_path.is_file():
            raise ValueError(f"Path {file_path} is not a file")
        
        self._allowed_tools.append(f"Edit({file_path})")

    def allow_web_search(self) -> None:
        """
        Grant the agent access to perform web searches by adding WebSearch to
        the tools list and allowing WebSearch(*) in allowedTools.
        """
        if "WebSearch" not in self._tools:
            self._tools.append("WebSearch")
        if "WebSearch(*)" not in self._allowed_tools:
            self._allowed_tools.append("WebSearch(*)")

    def add_dir(self, p: Path | str) -> None:
        p = p.absolute()
        if not p.is_dir():
            raise ValueError(f"Directory {p} does not exist")
        self._dirs.append(str(p))

    def may_get_assistant_message(self, message: dict) -> Optional[str]:
        if message["type"] != "assistant":
            return

        with suppress(KeyError):
            if message["message"]["content"][0]["type"] != "text":
                return
            return message["message"]["content"][0]["text"]

    def get_argsv(self) -> list[str]:
        argsv = [
            "claude",
            "--output-format",
            self._output_format,
            "--permission-mode",
            self._permission_mode,
        ]
        if self._verbose:
            argsv.append("--verbose")

        if self._print:
            argsv.append("--print")

        argsv.extend(["--tools", ",".join(self._tools)])

        for tool in self._allowed_tools:
            argsv.extend(["--allowedTools", tool])

        for dir in self._dirs:
            argsv.extend(["--add-dir", dir])

        argsv.append(self._prompt)

        return argsv


class Cursor(Agent):
    """
    Integration with Cursor Agent CLI.
    
    Documentation references:
    - Output format: https://cursor.com/docs/cli/reference/output-format
    - Permissions: https://cursor.com/docs/cli/reference/permissions
    - Parameters: https://cursor.com/docs/cli/reference/parameters
    
    The Cursor CLI uses stream-json format (NDJSON) for structured output.
    Permissions are configured via ~/.cursor/cli-config.json or .cursor/cli.json.
    This class creates a temporary .cursor/cli.json file in the workspace directory
    with specific permissions for each run.
    """
    def __init__(self):
        super().__init__()
        # Use stream-json format for structured output (NDJSON)
        # See: https://cursor.com/docs/cli/reference/output-format
        self._output_format = "stream-json"
        self._print = True
        self._workspace = None
        self._dirs = []
        # Track permissions for cli.json generation
        self._bash_patterns = []
        self._allowed_files = []
        self._has_web_search = False
        self._cli_config_path = None  # Path to temporary cli.json file

    def prompt(self, prompt: str) -> None:
        self._prompt = prompt

    def cwd(self, path: Path | str) -> None:
        """
        Set the current working directory for the agent. The subprocess will run
        in this directory. Also sets the workspace if not already set.
        """
        super().cwd(path)
        # Set workspace from cwd if not already set
        if self._workspace is None:
            self._workspace = str(self._cwd)

    def allow_bash_patterns(self, *patterns: str) -> None:
        """
        Enable bash command patterns. Patterns are stored and will be added to
        .cursor/cli.json as Shell(commandBase) permissions.
        
        See: https://cursor.com/docs/cli/reference/permissions
        """
        for pattern in patterns:
            if pattern not in self._bash_patterns:
                self._bash_patterns.append(pattern)

    def allow_file(self, path: Path | str) -> None:
        """
        Grant access to edit a single file. The file path will be added to
        .cursor/cli.json as Write(pathOrGlob) permission.
        
        The file doesn't need to exist - Cursor can create new files.
        See: https://cursor.com/docs/cli/reference/permissions
        """
        file_path = Path(path)
        # Use relative path if within workspace, otherwise absolute
        if self._workspace:
            try:
                rel_path = file_path.relative_to(Path(self._workspace))
                file_path = rel_path
            except ValueError:
                # Path is outside workspace, use absolute
                file_path = file_path.absolute()
        
        file_str = str(file_path)
        if file_str not in self._allowed_files:
            self._allowed_files.append(file_str)

    def allow_web_search(self) -> None:
        """
        Enable web search capabilities. Web search may be available through
        MCP servers. This is tracked but web search permissions aren't
        directly configurable via cli.json.
        """
        self._has_web_search = True

    def add_dir(self, p: Path | str) -> None:
        """
        Add a directory to the workspace. Cursor CLI supports --workspace flag
        for a single directory. We use the first directory added, or fall back to cwd.
        """
        p = Path(p).absolute()
        if not p.is_dir():
            raise ValueError(f"Directory {p} does not exist")
        # Use the first directory added as workspace
        if self._workspace is None:
            self._workspace = str(p)
        self._dirs.append(str(p))

    def may_get_assistant_message(self, message: dict) -> Optional[str]:
        """
        Parse cursor-agent stream-json output (NDJSON format).
        
        Assistant messages have the format:
        {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."}]}}
        
        See: https://cursor.com/docs/cli/reference/output-format
        """
        if message.get("type") == "assistant":
            with suppress(KeyError):
                # Format: {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
                content_list = message["message"]["content"]
                if isinstance(content_list, list) and len(content_list) > 0:
                    first_content = content_list[0]
                    if isinstance(first_content, dict) and first_content.get("type") == "text":
                        text = first_content.get("text", "")
                        # Return text if it's not empty (cursor-agent sometimes sends empty strings)
                        return text if text else None
        
        return None

    def _create_cli_config(self) -> Optional[Path]:
        """
        Create a temporary .cursor/cli.json file with the configured permissions.
        Returns the path to the created config file, or None if no permissions needed.
        
        See: https://cursor.com/docs/cli/reference/permissions
        """
        # Build permissions list
        allow = []
        
        # Add bash patterns as Shell() permissions
        for pattern in self._bash_patterns:
            allow.append(f"Shell({pattern})")
        
        # Add file permissions as Write() permissions
        for file_path in self._allowed_files:
            allow.append(f"Write({file_path})")
        
        # Only create config if we have permissions to set
        if not allow:
            return None
        
        workspace = Path(self._workspace) if self._workspace else self._cwd
        cursor_dir = workspace / ".cursor"
        cursor_dir.mkdir(exist_ok=True)
        
        config_path = cursor_dir / "cli.json"
        
        config = {
            "permissions": {
                "allow": allow,
                "deny": []
            }
        }
        
        with config_path.open("w") as f:
            json.dump(config, f, indent=2)
        
        self._cli_config_path = config_path
        return config_path
    
    def _cleanup_cli_config(self) -> None:
        """Remove the temporary .cursor/cli.json file if it was created."""
        if self._cli_config_path and self._cli_config_path.exists():
            self._cli_config_path.unlink()
            # Remove .cursor directory if empty
            cursor_dir = self._cli_config_path.parent
            try:
                if cursor_dir.exists() and not any(cursor_dir.iterdir()):
                    cursor_dir.rmdir()
            except OSError:
                pass  # Directory not empty or other error, ignore
            self._cli_config_path = None

    def get_argsv(self) -> list[str]:
        """
        Build command-line arguments for cursor-agent.
        
        Uses --print for non-interactive mode and --output-format stream-json
        for structured NDJSON output.
        
        See: https://cursor.com/docs/cli/reference/parameters
        """
        argsv = [
            "cursor-agent",
            "--print",
            "--output-format",
            self._output_format,
        ]

        # Use --workspace if set, otherwise subprocess will run in self._cwd
        # Note: --workspace may not be in official docs but appears in CLI help
        if self._workspace:
            argsv.extend(["--workspace", self._workspace])

        # Add the prompt as arguments
        argsv.append(self._prompt)
        
        return argsv
    
    def run(self, log_file: Optional[Path] = None, silent: bool = False) -> int:
        """
        Run the cursor-agent with temporary .cursor/cli.json permissions.
        Creates the config file before running and cleans it up afterwards.
        Only creates cli.json if specific permissions have been configured.
        """
        # Create temporary cli.json if we have permissions to configure
        config_created = False
        if self._bash_patterns or self._allowed_files:
            try:
                config_path = self._create_cli_config()
                config_created = config_path is not None
            except Exception as e:
                # If config creation fails, log but continue
                print(f"Warning: Failed to create cli.json: {e}", file=sys.stderr)
        
        try:
            # Call parent run method
            return super().run(log_file, silent)
        finally:
            # Clean up the config file if we created it
            if config_created:
                self._cleanup_cli_config()

def agent(name: str) -> Agent:
    """
    Create an agent based on the name.
    """
    if name == "codex":
        return Codex()
    elif name == "claude":
        return ClaudeCode()
    elif name == "cursor":
        return Cursor()
    else:
        raise ValueError(f"Unknown agent: {name}")
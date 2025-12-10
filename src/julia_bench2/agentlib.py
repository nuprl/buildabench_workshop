#!/usr/bin/env python3
"""
Shared library functions for agent scripts.
"""

import subprocess
import sys
from pathlib import Path
import json
from contextlib import suppress


def env_subst(template_str, **kwargs):
    """
    Replace $VAR in a string with the value of the VAR environment variable.
    """
    for key, value in kwargs.items():
        template_str = template_str.replace(f"${key}", str(value))
    return template_str


def container_exists(container: str) -> bool:
    try:
        subprocess.check_output(["podman", "image", "exists", container])
        return True
    except subprocess.CalledProcessError:
        return False


def standard_container_name(repo_path: Path) -> str:
    """
    Generate a standardized container name from a repository path.
    For files (tarballs), uses the stem. For directories, uses the name.
    Normalizes by converting to lowercase and replacing '#' with '__'.
    """
    name = repo_path.stem if repo_path.is_file() else repo_path.name
    return "env_agent__" + name.lower().replace("#", "__")


def print_if_assistant_message(message_str: str):
    try:
        message = json.loads(message_str)
    except json.JSONDecodeError:
        print(f"Count not parse message as JSON: {message_str}")
        return

    if message["type"] != "assistant":
        return

    with suppress(KeyError):
        if message["message"]["content"][0]["type"] != "text":
            return
        print(message["message"]["content"][0]["text"])


def run_claude_command(claude_cmd, log_file: Path, silent: bool = False):
    """
    Run a claude command and tee output to both stdout and log file.
    Returns the process return code.
    
    Args:
        claude_cmd: Command to run
        log_file: Path to log file
        silent: If True, don't print to stdout (only log to file)
    """
    with open(log_file, "w") as log_f:
        process = subprocess.Popen(
            claude_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
        )

        for line in process.stdout:
            if not silent:
                print_if_assistant_message(line)
            log_f.write(line)
            log_f.flush()

        process.wait()
        return process.returncode


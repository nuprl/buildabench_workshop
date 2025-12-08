#!/usr/bin/env python3
"""
This script requires Claude Code and Podman.  It can probably be easily adapted
to use a different CLI agent, such as Codex or Cursor. It can definitely be
adapted to support Docker instead of Podman. It has no Python dependencies and
should work with fairly old versions of Python 3.

At this point, you should read the REPO_INSTALL_PROMPT below to understand what
this script does.

The only additional features of this script that are not described in the prompt
are that it saves the interaction log to env_agent_log.jsonl in the repository
directory and that it carefully scopes the allowed tools. Nevertheless,I run
this script in an unprivileged user account that does not have my personal
files, and has limited access to GitHub.
"""

import argparse
import subprocess
import sys
from pathlib import Path
import json
from contextlib import suppress

REPO_INSTALL_PROMPT = """
I have checked out a GitHub repository to $REPO. I want you to try to create a
container that builds the code in the repository and runs the test suite when I
run it as follows:

    podman run --rm --network none -v $REPO:/repo:rw $CONTAINER

Create a Dockerfile in the repository directory. You must not copy the
repository into the container, because I may modify the code on the host later
and re-run. However, you should install all dependencies so that the test suite
starts running immediately. If the repository uses language-specific packages
(e.g., Node or PIP), you should try to install them during the container build
process:

    podman build -t $CONTAINER . # install prerequisites

To facilitate this, you may copy package manifests and and other minimal build
configuration into the container. However, keep in mind that running the
container must run the code on the volume mounted to /repo. So, do not copy the
full source code into the container.

The project may have test-only dependencies that are not installed by default.
so, ensure you install them during the build stage, since network access is
disabled during podman run.

You should build and run the container to verify that it is behaving as
expected. To run. use exactly the podman run command shown above, but apply a 5
minute timeout.

You should give up if running fails after five attempts, though keep in mind
that a run may be successful even if some tests fail. You should also give up if
ten successive builds fail.

The file $TIPS_PATH has tips from previous runs on other repositories that may
be helpful. When you are done building and running, revisit this tips file and,
if needed, update it with new tips or modify existing tips.

In your final response, you should say, "the container is ready to use" if you
were able to build and run the container.
""".strip()


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


def main_with_args(repo: Path, container, tips_path: Path):
    repo = repo.absolute()
    tips_path = tips_path.absolute()

    if not container:
        container = repo.name

    if not repo.exists():
        print(f"Error: Repository directory {repo} does not exist", file=sys.stderr)
        return 1

    if not tips_path.exists():
        print(
            f"Error: Tips file {tips_path} does not exist. You should at least create a file with 'no tips yet'.",
            file=sys.stderr,
        )
        return 1

    if container_exists(container):
        print(f"Error: Container {container} already exists", file=sys.stderr)
        return 1

    prompt = env_subst(
        REPO_INSTALL_PROMPT, REPO=repo, CONTAINER=container, TIPS_PATH=tips_path
    )

    # Build claude command
    log_file = repo / "env_agent_log.jsonl"
    claude_cmd = [
        "claude",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "Bash,Edit,Read,Write,WebSearch",
        "--add-dir",
        repo,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        f"Bash(podman run --rm --network none -v {repo}:/repo:rw {container})",
        # Claude Code sometimes interprets the timeout to use the Bash timeout command, and at other times uses its
        # internal timeout ability.
        "--allowedTools",
        f"Bash(timeout 300 podman run --rm --network none -v {repo}:/repo:rw {container})",
        "--allowedTools",
        f"Bash(podman build -t {container}:*)",
        "--allowedTools",
        "Bash(jobs:*)",
        "--allowedTools",
        "Bash(podman images:*)",
        "--allowedTools",
        f"Edit({tips_path})",
        "--allowedTools",
        "WebSearch(*)",
        "--print",
        prompt,
    ]

    # Run claude command and tee output to both stdout and log file
    with open(log_file, "w") as log_f:
        process = subprocess.Popen(
            claude_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
        )

        for line in process.stdout:
            print_if_assistant_message(line)
            log_f.write(line)
            log_f.flush()

        process.wait()
        return process.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--container", type=str)
    parser.add_argument("--tips-path", type=Path, required=True)
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

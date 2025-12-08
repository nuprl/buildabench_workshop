#!/usr/bin/env python3
"""
This script requires Claude Code and Podman.  It can probably be easily adapted
to use a different CLI agent, such as Codex or Cursor. It can definitely be
adapted to support Docker instead of Podman. It has no Python dependencies and
should work with fairly old versions of Python 3.

At this point, you should read the AGENT_PROMPT below to understand what
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

AGENT_PROMPT = """
I have checked out a GitHub repository to $REPO. I want you to remove the
following feature from the codebase and verify that you have done so correctly:

$FEATURE

I suggest proceeding in the following steps:

1. I have prepared a container that you can use to run the tests called $CONTAINER.
   Confirm that you can find it with "podman images".

2. Read the code to understand how the feature is implemented, and then read the
   test suite and determine if there are any tests that already test for the
   feature. If no such tests exist, write new tests for the feature.

3. Run the test suite, which you must do in the container with exactly this command:

    podman run --rm --network none -v "$REPO:/repo:rw" $CONTAINER

4. Some existing tests may fail, but the tests for the target feature should
   pass.

5. Remove the feature from the codebase. Make no changes to the tests, and
   verify that the feature tests now fail.

If a step goes wrong, you should try to fix the problem and re-execute. But, do
not run the test suite more than six times total.

If you succeed, write "the feature has been removed" in your final response.

The file $TIPS_PATH has tips from previous runs on other repositories that may
be helpful. When you are done, revisit this tips file and, if needed, update it
with new tips or modify existing tips.
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


def standard_container_name(repo: Path) -> str:
    return repo.name.lower().replace("#", "__")

def main_with_args(repo: Path, container, tips_path: Path, feature: str):
    repo = repo.absolute()
    tips_path = tips_path.absolute()

    if not container:
        container = standard_container_name(repo)

    if not repo.exists():
        print(f"Error: Repository directory {repo} does not exist", file=sys.stderr)
        return 1

    if not tips_path.exists():
        print(
            f"Error: Tips file {tips_path} does not exist. You should at least create a file with 'no tips yet'.",
            file=sys.stderr,
        )
        return 1

    if not container_exists(container):
        print(f"Error: Container {container} does not exist", file=sys.stderr)
        return 1

    prompt = env_subst(
        AGENT_PROMPT, REPO=repo, CONTAINER=container, TIPS_PATH=tips_path, FEATURE=feature
    )

    log_file = repo / "feature_removal_agent_log.jsonl"
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
        f"Bash(podman run --rm --network none -v \"{repo}:/repo:rw\" {container})",
        # Claude Code sometimes interprets the timeout to use the Bash timeout command, and at other times uses its
        # internal timeout ability.
        "--allowedTools",
        f"Bash(timeout 300 podman run --rm --network none -v \"{repo}:/repo:rw\" {container})",
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
    parser.add_argument("--feature", type=str, required=True)
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

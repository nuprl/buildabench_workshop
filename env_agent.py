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
import sys
import json
from pathlib import Path

from agentlib import env_subst, container_exists, run_claude_command, standard_container_name
from repolib import tarball_or_repo

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


def collect_output_artifacts(repo_dir: Path, log_file: Path, tips_path: Path, container: str) -> dict:
    """Collect tips file, Dockerfile, and log file."""
    dockerfile_path = repo_dir / "Dockerfile"
    return {
        "docker_image_name": container,
        "tips": tips_path.read_text(encoding="utf-8", errors="replace"),
        "dockerfile": dockerfile_path.read_text(encoding="utf-8", errors="replace"),
        "log": log_file.read_text(encoding="utf-8", errors="replace"),
    }


def main_with_args(repo: Path, container, tips_path: Path, output_json: bool = False):
    repo_path = repo.absolute()
    tips_path = tips_path.absolute()

    if not container:
        # Use the original repo path name for container, even if it's a tarball
        if repo_path.is_file():
            # For tarballs, combine parent directory name and stem
            name = f"{repo_path.parent.name}#{repo_path.stem}"
        else:
            name = repo_path.name
        # Normalize the container name using the helper function
        container = standard_container_name(Path(name))

    if not repo_path.exists():
        print(f"Error: Repository path {repo_path} does not exist", file=sys.stderr)
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

    # Use tarball_or_repo to handle both tarballs and directories
    with tarball_or_repo(repo_path) as repo_dir:
        repo_dir = repo_dir.absolute()
        
        prompt = env_subst(
            REPO_INSTALL_PROMPT, REPO=repo_dir, CONTAINER=container, TIPS_PATH=tips_path
        )

        # Build claude command
        log_file = repo_dir / "env_agent_log.jsonl"
        claude_cmd = [
            "claude",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            "Bash,Edit,Read,Write,WebSearch",
            "--add-dir",
            str(repo_dir),
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            f"Bash(podman run --rm --network none -v {repo_dir}:/repo:rw {container})",
            # Claude Code sometimes interprets the timeout to use the Bash timeout command, and at other times uses its
            # internal timeout ability.
            "--allowedTools",
            f"Bash(timeout 300 podman run --rm --network none -v {repo_dir}:/repo:rw {container})",
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
        return_code = run_claude_command(claude_cmd, log_file, silent=output_json)
        
        # If output_json mode, collect and print artifacts
        if output_json:
            artifacts = collect_output_artifacts(repo_dir, log_file, tips_path, container)
            print(json.dumps(artifacts))
        
        return return_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--container", type=str)
    parser.add_argument("--tips-path", type=Path, required=True)
    parser.add_argument("--output-json", action="store_true", help="Output JSON with all created files")
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

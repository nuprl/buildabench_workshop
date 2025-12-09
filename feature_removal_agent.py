#!/usr/bin/env python3
"""
This script requires Claude Code and Podman.  It can probably be easily adapted
to use a different CLI agent, such as Codex or Cursor. It can definitely be
adapted to support Docker instead of Podman. It has no Python dependencies and
should work with fairly old versions of Python 3.

At this point, you should read the AGENT_PROMPT below to understand what this
script does.

The only additional features of this script that are not described in the prompt
are that it saves the interaction log in the repository directory and that it
carefully scopes the allowed tools. Nevertheless,I run this script in an
unprivileged user account that does not have my personal files, and has limited
access to GitHub.
"""

import argparse
import sys
import json
import subprocess
from pathlib import Path

from agentlib import env_subst, container_exists, run_claude_command, standard_container_name
from repolib import tarball_or_repo

AGENT_PROMPT = """
I have checked out a GitHub repository to $REPO. I want you to remove the
following feature from the codebase and verify that you have done so correctly:

$FEATURE

I think the feature is implemented in the following locations, but you should
explore the codebase yourself:

$LOCATIONS

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
not run the test suite more than six times total. If you determine that the
feature cannot be removed without breaking other dependent feature that are
key, you should abort the task and explain why.

If you succeed at the task, conclude as follows:

1. Write "the feature has been removed" in your final response.

2. Commit the code changes to the repository.

3. Use "git diff" to create two diff files in the repository root called
   "src.diff" and "test.diff". These should contain the changes to the source
   code and test code, respectively.

Finally, the file $TIPS_PATH has tips from previous runs on other repositories
that  may be helpful. When you are done, revisit this tips file and, if needed,
update it with new tips or modify existing tips.



""".strip()


def collect_output_artifacts(repo_dir: Path, log_file: Path, tips_path: Path) -> dict:
    """Collect tips file, log file, src.diff, test.diff, and commit message."""
    commit_message = ""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )
        commit_message = result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    return {
        "tips": tips_path.read_text(encoding="utf-8", errors="replace"),
        "log": log_file.read_text(encoding="utf-8", errors="replace"),
        "src.diff": (repo_dir / "src.diff").read_text(encoding="utf-8", errors="replace"),
        "test.diff": (repo_dir / "test.diff").read_text(encoding="utf-8", errors="replace"),
        "commit_message": commit_message,
    }

def main_with_args(repo: Path, container, tips_path: Path, feature: str, locations: str, output_json: bool = False):
    repo_path = repo.absolute()
    tips_path = tips_path.absolute()

    if not container:
        container = standard_container_name(repo_path)

    if not repo_path.exists():
        print(f"Error: Repository path {repo_path} does not exist", file=sys.stderr)
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

    # Use tarball_or_repo to handle both tarballs and directories
    with tarball_or_repo(repo_path) as repo_dir:
        repo_dir = repo_dir.absolute()
        
        prompt = env_subst(
            AGENT_PROMPT, REPO=repo_dir, CONTAINER=container, TIPS_PATH=tips_path, FEATURE=feature, LOCATIONS=locations
        )

        log_file = repo_dir / "feature_removal_agent_log.jsonl"
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
            f"Bash(podman run --rm --network none -v \"{repo_dir}:/repo:rw\" {container})",
            # Claude Code sometimes interprets the timeout to use the Bash timeout command, and at other times uses its
            # internal timeout ability.
            "--allowedTools",
            f"Bash(timeout 300 podman run --rm --network none -v \"{repo_dir}:/repo:rw\" {container})",
            "--allowedTools",
            f"Bash(podman build -t {container}:*)",
            "--allowedTools",
            "Bash(jobs:*)",
            "--allowedTools",
            "Bash(podman images:*)",
            "--allowedTools",
            "Bash(git diff:*)",
            "--allowedTools",
            "Bash(git commit:*)",
            "--allowedTools",
            "Bash(git add:*)",
            "--allowedTools",
            "Bash(git status)",
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
            artifacts = collect_output_artifacts(repo_dir, log_file, tips_path)
            print(json.dumps(artifacts))
        
        return return_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--container", type=str)
    parser.add_argument("--tips-path", type=Path, required=True)
    parser.add_argument("--feature", type=str, required=True)
    parser.add_argument("--locations", type=str, required=True)
    parser.add_argument("--output-json", action="store_true", help="Output JSON with all created files")
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

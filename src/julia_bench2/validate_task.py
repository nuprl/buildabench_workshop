"""
You should read the AGENT_PROMPT below to understand what this script does.

The only additional features of this script that are not described in the prompt
are that it can save the output artifacts (tests, Dockerfile, etc.).
"""

import argparse
import sys
import json
import subprocess
from pathlib import Path

from .agentlib import env_subst, container_exists, standard_container_name
from .repolib import tarball_or_repo
from .anyagent import agent

AGENT_PROMPT = """
I need to interview a candidate software engineer. During the interview,
I want to test their programming ability by asking them to re-implement a
feature that already exists in the project.

These are instructions that I will give the candidate:

<INSTRUCTIONS>
$TASK_DESCRIPTION
</INSTRUCTIONS>

Here is how I think the feature should be removed:

<CHANGES>
$PATCHES
</CHANGES>

I may not have comprehensively described everything you need to change, so
explore the codebase yourself.

I have step-by-step instructions for you to follow. If you make a mistake, you
may backtrack. However, you must not re-run the test suite more than six times
total.

Here are the steps you should follow:

1. I have prepared a container that you can use to run the tests called
   $CONTAINER. Confirm that you can find it with "podman images" and give up
   immediately if you cannot find it.

2. Read the code to understand how the feature is implemented. My instructions
   on how to remove the feature should help you do this, but I may have missed
   some details.

3. Read the test suite and determine if there are any tests that already test for
   the feature. If no such tests exist, write new tests for the feature.

4. Run the test suite, which you must do in the container with exactly this
   command:

   podman run --rm --network none -v "$REPO:/repo:rw" $CONTAINER

   Some existing tests may fail, but the tests for the target feature should
   pass.  In addition, remove any other tests that fail so that all tests pass
   at this point. You must not proceed unless all tests pass at this point. If
   you cannot do this, give up and explain what went wrong.

5. If you changed the tests in the previous step, commit them to the repository
   now.

6. Remove the feature from the codebase without making any changes to the tests.
   I have provided the changes that I think you need to make, but you should
   adapt them as needed. Ensure that you do not leave any trace of the
   feature you remove, and do not add comments saying that you removed code.

7. Verify that the tests (old or new) that target the feature now fail. At
   least one test case that targets the feature should fail. If you cannot do
   this, give up and explain what went wrong. If you succeed, commit the
   code changes to the repository.

8. Remove all tests cases that target the feature and fail in the previous step.
   Run the test suite to verify that all tests pass. If you cannot do this,
   give up and explain what went wrong. If you succeed, commit the code changes
   to the repository.

9. Create two untracked diff files in the repository root: The file src.diff
   should be a diff between the original repository state, and the current state
   of the repository (i.e., from step 8). The file tests.diff should be a diff
   that only adds the tests that target the feature to the current state of the
   repository. That is, after the candidate implements the feature in their own
   way, I will apply tests.diff to introduce the tests that target the feature.

The file $TIPS_PATH has tips from previous runs on other repositories that  may
be helpful. When you are done, revisit this tips file and, if needed, update it
with new tips or modify existing tips, but ensure the tips are generic.

When you use diff, you may notice a file called feature_removal_agent_log.jsonl.
Ignore it and leave it untracked.
""".strip()


def may_read(file_path: Path) -> str | None:
    """Read a file if it exists, returning None on any error."""
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def collect_output_artifacts(repo_dir: Path, log_file: Path, tips_path: Path) -> dict:
    """Collect tips file, log file, src.diff, tests.diff, and commit message."""
    commit_message = None
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
        "tips": may_read(tips_path),
        "log": may_read(log_file),
        "src.diff": may_read(repo_dir / "src.diff"),
        "tests.diff": may_read(repo_dir / "tests.diff"),
        "commit_message": commit_message,
    }

def main_with_args(repo: Path, container, tips_path: Path, task_description: str, patches: str, agent_name: str, task_id: str, output_json: bool = False):
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

    with tarball_or_repo(repo_path) as repo_dir:
        repo_dir = repo_dir.absolute()
        print(f"Working directory is {repo_dir}", file=sys.stderr, flush=True)

        
        prompt = env_subst(
            AGENT_PROMPT, REPO=repo_dir, CONTAINER=container, TIPS_PATH=tips_path, TASK_DESCRIPTION=task_description, PATCHES=patches
        )

        log_file = repo_dir / "feature_removal_agent_log.jsonl"
        
        agent_instance = agent(agent_name)
        agent_instance.prompt(prompt)
        agent_instance.cwd(repo_dir)
        agent_instance.allow_web_search()
        agent_instance.allow_bash_patterns(
            f"podman run --rm --network none -v \"{repo_dir}:/repo:rw\" {container}",
            # Claude Code sometimes interprets the timeout to use the Bash timeout command, and at other times uses its
            # internal timeout ability.
            f"timeout 300 podman run --rm --network none -v \"{repo_dir}:/repo:rw\" {container}",
            f"podman build -t {container}:*",
            "jobs:*",
            "podman images:*",
            "git diff:*",
            "git commit:*",
            "git add:*",
            "git status",
        )
        agent_instance.allow_file(tips_path)
        agent_instance.cwd(repo_dir)
        
        return_code = agent_instance.run(log_file=log_file, silent=output_json)
        
        if output_json:
            artifacts = collect_output_artifacts(repo_dir, log_file, tips_path)
            artifacts["task_id"] = task_id
            print(json.dumps(artifacts))
        
        return return_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str)
    parser.add_argument("--container", type=str)
    parser.add_argument("--tips-path", type=Path, required=True)
    parser.add_argument("--task-description", type=Path, dest="task_description_file", help="File containing task description (required unless --input-json)")
    parser.add_argument("--patches", type=Path, dest="patches_file", help="File containing patches (required unless --input-json)")
    parser.add_argument("--input-json", action="store_true", help="Read task-description and patches from JSONL on stdin")
    parser.add_argument("--agent", type=str, required=True, help="Agent name (e.g., 'claude' or 'codex')", dest="agent_name")
    parser.add_argument("--output-json", action="store_true", help="Output JSON with all created files")
    parser.add_argument("--task-id", type=str, help="Task ID to include in output JSON (required unless --input-json provides it)")
    args = parser.parse_args()
    

    task_id = args.task_id
    repo = args.repo
    
    if args.input_json:
        # Read JSONL line from stdin
        line = sys.stdin.readline()
        if not line:
            print("Error: No input provided on stdin", file=sys.stderr)
            return 1
        try:
            data = json.loads(line.strip())
            task_description = data.get("task_description", "")
            patches = data.get("patches", "")
            repo = data.get("repo", "")
            if not task_description or not patches or not repo:
                print("Error: JSON must contain 'task_description', 'patches', and 'repo' fields", file=sys.stderr)
                print(f"Keys were {data.keys()}", file=sys.stderr)
                return 1
            # Extract task_id from JSON (command-line flag takes precedence)
            if task_id is None:
                task_id = data.get("task_id")
            if not task_id:
                print("Error: task_id must be provided via --task-id flag or 'task_id' field in input JSON", file=sys.stderr)
                return 1
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse JSON from stdin: {e}", file=sys.stderr)
            return 1
    else:
        # Read task description and patches from files
        if not args.task_description_file or not args.patches_file:
            print("Error: --task-description and --patches are required unless --input-json is used", file=sys.stderr)
            return 1
        if not task_id:
            print("Error: --task-id is required when not using --input-json", file=sys.stderr)
            return 1
        task_description = args.task_description_file.read_text(encoding="utf-8")
        patches = args.patches_file.read_text(encoding="utf-8")
    
    # Call main_with_args with the file contents
    return main_with_args(
        repo=Path(repo),
        container=args.container,
        tips_path=args.tips_path,
        task_description=task_description,
        patches=patches,
        agent_name=args.agent_name,
        output_json=args.output_json,
        task_id=task_id
    )


if __name__ == "__main__":
    sys.exit(main())

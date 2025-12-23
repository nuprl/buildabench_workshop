"""
Evaluate an agent (using anyagent) on a task. For now, we do not allow the
agent to run code, and only allow it to edit files.

Usage:

python3 -m julia_bench2.eval_agent \
    --tasks TASK_FILE \
    --validated-tasks VALIDATED_TASK_FILE \
    --task-id TASK_ID \
    --agent-name AGENT_NAME

See synth_task.py for the format of TASK_FILE and validate_task.py for the
format of VALIDATED_TASK_FILE. The TASK_ID is the unique key that selects the
right row from both files. The container name is read from the validated_tasks_file.

Approach:

1. Load the container name from the validated task data.
2. Assert that the Podman image exists.
3. Extract the repository (named in the task row) to a temporary directory
   using repolib.
4. Apply the validated task patch (src.diff from the validated task row) to the
   repository with `git apply`.
5. Run the agent (using anyagent) on the temporary directory. It can edit
   any file, but cannot run any code. The prompt for the agent is
   task_description from the task row.
6. Apply the validated test path (tests.diff from the validated task row) to the
   repository with `git apply`.
7. Run the container with the temporary directory mounted to /repo.
   (See env_agent.py to see how this is expected to work.)
8. Print to stdout a JSON object with the agent log, the container run log,
   exit codes from the various steps, and a git diff on the working copy of
   the repository.
"""

import argparse
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional

from bounded_subprocess import run as bounded_run
from .agentlib import container_exists
from .repolib import tarball_or_repo
from .anyagent import agent


class EvalAgentError(Exception):
    """Base exception for eval_agent errors."""
    pass


def may_read(file_path: Path) -> str | None:
    """Read a file if it exists, returning None on any error."""
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def load_jsonl_task(jsonl_file: Path, task_id: str) -> dict | None:
    """
    Load a task from a JSONL file by task_id.
    
    Returns the matching task dictionary, or None if not found.
    Raises an exception if there's an error reading the file.
    """
    with jsonl_file.open() as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("task_id") == task_id:
                return data
    return None


def apply_git_diff(repo_dir: Path, diff_content: str) -> tuple[int, str]:
    """
    Apply a git diff to a repository using `git apply`.
    
    Returns a tuple of (exit_code, stderr_output).
    """
    if not diff_content:
        return 0, ""
    
    result = subprocess.run(
        ["git", "apply"],
        cwd=repo_dir,
        input=diff_content,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def run_container(repo_dir: Path, container: str, timeout_seconds: int) -> tuple[int, str, bool]:
    """
    Run the container with the repository directory mounted.
    
    Returns a tuple of (exit_code, stdout+stderr_output, timed_out).
    """
    result = bounded_run(
        ["podman", "run", "--rm", "--network", "none", "-v", f"{repo_dir}:/repo:rw", container],
        max_output_size=1024 * 1024,
        timeout_seconds=timeout_seconds,
    )
    output = result.stdout + result.stderr
    return result.exit_code, output, result.timeout


def get_git_diff(repo_dir: Path) -> str | None:
    """Get the git diff of the working copy."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main_with_args(
    tasks_file: Path,
    validated_tasks_file: Path,
    task_id: str,
    agent_name: str,
    timeout: int = 300,
    working_path: Optional[Path] = None,
):
    """
    Main evaluation logic.
    
    Returns a dictionary with results from all steps.s
    """
    # Initialize result dictionary with mostly None values
    result = {
        "task_id": task_id,
        "agent_log": None,
        "container_log": None,
        "git_diff": None,
        "src_diff_apply_exit_code": None,
        "src_diff_apply_stderr": None,
        "tests_diff_apply_exit_code": None,
        "tests_diff_apply_stderr": None,
        "agent_exit_code": None,
        "container_exit_code": None,
        "container_timed_out": None,
        "error": None,
    }
    
    # Step 1: Load task and validated task data
    try:
        task_data = load_jsonl_task(tasks_file, task_id)
    except Exception as e:
        raise EvalAgentError(f"Error reading tasks file: {e}") from e
    
    if not task_data:
        raise EvalAgentError(f"Task ID {task_id} not found in tasks file")
    
    try:
        validated_task_data = load_jsonl_task(validated_tasks_file, task_id)
    except Exception as e:
        raise EvalAgentError(f"Error reading validated tasks file: {e}") from e
    
    if not validated_task_data:
        raise EvalAgentError(f"Task ID {task_id} not found in validated tasks file")
    
    # Extract container name from validated task data
    container = validated_task_data.get("container")
    if not container:
        raise EvalAgentError("Validated task data missing 'container' field")
    
    # Step 2: Assert that the Podman image exists
    if not container_exists(container):
        raise EvalAgentError(f"Container {container} does not exist")
    
    # Extract repository path from task data (prefer validated task data, fall back to task data)
    repo_path_str = validated_task_data.get("repo") or task_data.get("repo")
    if not repo_path_str:
        raise EvalAgentError("Task data missing 'repo' field")
    
    repo_path = Path(repo_path_str)
    if not repo_path.exists():
        raise EvalAgentError(f"Repository path {repo_path} does not exist")
    
    # Extract task description and diffs
    task_description = task_data["task_description"]
    src_diff = validated_task_data["src.diff"]
    tests_diff = validated_task_data["tests.diff"]
    
    # Step 3: Extract repository and apply src.diff
    with tarball_or_repo(repo_path, working_dir=working_path) as repo_dir:
        repo_dir = repo_dir.absolute()
        print(f"Working directory is {repo_dir}", file=sys.stderr, flush=True)
        
        # Apply src.diff
        exit_code, stderr = apply_git_diff(repo_dir, src_diff)
        result["src_diff_apply_exit_code"] = exit_code
        result["src_diff_apply_stderr"] = stderr
        if exit_code != 0:
            raise EvalAgentError(f"Failed to apply src.diff: {stderr}")
        
        # Commit the applied patch
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "applied feature removal patch"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        
        # Step 4: Run the agent
        agent_instance = agent(agent_name)
        agent_instance.prompt(task_description)
        agent_instance.cwd(repo_dir)

        
        log_file = repo_dir / "eval_agent_log.jsonl"
        agent_exit_code = agent_instance.run(log_file=log_file, silent=True)
        result["agent_exit_code"] = agent_exit_code
        result["agent_log"] = may_read(log_file)
        
        # Step 5: Apply tests.diff
        exit_code, stderr = apply_git_diff(repo_dir, tests_diff)
        result["tests_diff_apply_exit_code"] = exit_code
        result["tests_diff_apply_stderr"] = stderr
        if exit_code != 0:
            result["error"] = f"Failed to apply tests.diff: {stderr}"
            # Continue anyway to get git diff
        
        # Step 6: Run the container
        container_exit_code, container_output, container_timed_out = run_container(repo_dir, container, timeout)
        result["container_exit_code"] = container_exit_code
        result["container_log"] = container_output
        result["container_timed_out"] = container_timed_out
        
        # Step 7: Get git diff
        result["git_diff"] = get_git_diff(repo_dir)
    
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True, dest="tasks_file", help="Path to tasks JSONL file")
    parser.add_argument("--validated-tasks", type=Path, required=True, dest="validated_tasks_file", help="Path to validated tasks JSONL file")
    parser.add_argument("--task-id", type=str, required=True, help="Task ID to evaluate")
    parser.add_argument("--agent-name", type=str, required=True, help="Agent name (e.g., 'claude' or 'codex')")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for container execution (default: 300)")
    parser.add_argument("--working-path", type=Path, default=None, dest="working_path", help="Persistent directory to extract repository to (default: use temporary directory)")
    args = parser.parse_args()
    
    try:
        result = main_with_args(
            tasks_file=args.tasks_file,
            validated_tasks_file=args.validated_tasks_file,
            task_id=args.task_id,
            agent_name=args.agent_name,
            timeout=args.timeout,
            working_path=args.working_path,
        )
    except EvalAgentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Print JSON result to stdout
    print(json.dumps(result))
    
    # Exit with non-zero code if agent or container failed (including timeout)
    if result.get("agent_exit_code", 0) != 0 or result.get("container_exit_code", 0) != 0 or result.get("container_timed_out", False):
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())

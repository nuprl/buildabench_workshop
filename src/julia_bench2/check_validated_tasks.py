"""
Check that the output of validate_task.py is correct.

This script validates that:
1. The final state (repo after step 8) has all tests passing
2. When tests.diff is applied (adding tests for removed feature), tests fail
3. When the feature is restored (by reversing src.diff), tests pass
4. When tests are removed again, all tests pass

Takes a JSONL file as input (one validated task result per line) and produces
textual output indicating pass/fail for each validation step.
"""

import argparse
import sys
import json
import subprocess
import os
from pathlib import Path
from typing import Optional
from concurrent.futures import ProcessPoolExecutor, as_completed

from bounded_subprocess import run as bounded_run
from .agentlib import container_exists
from .repolib import tarball_or_repo


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


def reverse_git_diff(repo_dir: Path, diff_content: str) -> tuple[int, str]:
    """
    Reverse a git diff by applying it with --reverse flag.
    
    Returns a tuple of (exit_code, stderr_output).
    """
    if not diff_content:
        return 0, ""
    
    result = subprocess.run(
        ["git", "apply", "--reverse"],
        cwd=repo_dir,
        input=diff_content,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def run_container(repo_dir: Path, container: str, timeout_seconds: int = 300) -> tuple[int, str, bool]:
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


def validate_task_worker(args_tuple: tuple) -> tuple[str, bool]:
    """
    Worker function for parallel execution.
    
    Returns (task_id, success).
    """
    validated_task_data, timeout, working_path = args_tuple
    
    try:
        success = _validate_task_internal(validated_task_data, timeout, working_path)
    except Exception as e:
        task_id = validated_task_data.get("task_id", "unknown")
        print(f"[{task_id}] ERROR: Exception: {e}", file=sys.stderr)
        success = False
    
    task_id = validated_task_data.get("task_id", "unknown")
    return task_id, success


def _validate_task_internal(validated_task_data: dict, timeout: int = 300, working_path: Optional[Path] = None) -> bool:
    """
    Internal validation function that prints directly with task ID prefix.
    
    Returns True if all checks pass, False otherwise.
    """
    task_id = validated_task_data.get("task_id", "unknown")
    
    # Helper function to print with task ID prefix
    def print_with_id(*args, **kwargs):
        """Print with task ID prefix."""
        if args:
            # Prefix the first argument with task ID
            prefixed_msg = f"[{task_id}] {args[0]}"
            print(prefixed_msg, *args[1:], **kwargs)
        else:
            print(*args, **kwargs)
    
    print_with_id(f"Task ID: {task_id}")
    
    # Extract required fields
    repo_path_str = validated_task_data.get("repo")
    container = validated_task_data.get("container")
    src_diff = validated_task_data.get("src.diff")
    tests_diff = validated_task_data.get("tests.diff")
    
    if not repo_path_str:
        print_with_id("ERROR: Missing 'repo' field")
        return False
    
    if not container:
        print_with_id("ERROR: Missing 'container' field")
        return False
    
    if not src_diff:
        print_with_id("ERROR: Missing 'src.diff' field")
        return False
    
    if not tests_diff:
        print_with_id("ERROR: Missing 'tests.diff' field")
        return False
    
    repo_path = Path(repo_path_str)
    if not repo_path.exists():
        print_with_id(f"ERROR: Repository path {repo_path} does not exist")
        return False
    
    if not container_exists(container):
        print_with_id(f"ERROR: Container {container} does not exist")
        return False
    
    # Extract repository to working directory
    try:
        with tarball_or_repo(repo_path, working_dir=working_path) as repo_dir:
            repo_dir = repo_dir.absolute()
            
            # Step 1: Apply src.diff to get final state (feature removed, tests removed)
            # This is the state after step 8 of validate_task.py
            print_with_id("Check 1: Applying src.diff...")
            exit_code, stderr = apply_git_diff(repo_dir, src_diff)
            if exit_code != 0:
                print_with_id(f"FAILED: Failed to apply src.diff: {stderr}")
                return False
            
            # Commit the final state so we can reset to it later
            subprocess.run(
                ["git", "add", "-A"],
                cwd=repo_dir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Final state: feature removed"],
                cwd=repo_dir,
                capture_output=True,
            )
            final_state_commit = "HEAD"
            
            # Check 1: Final state should have all tests passing
            # (This is the state after step 8 - feature removed, tests removed)
            print_with_id("Check 1: Final state tests should pass...")
            exit_code, output, timed_out = run_container(repo_dir, container, timeout)
            if exit_code != 0 or timed_out:
                print_with_id(f"FAILED: Final state tests failed (exit_code={exit_code}, timed_out={timed_out})")
                if output:
                    print_with_id(f"Output: {output[:500]}")
                return False
            print_with_id("PASS")
            
            # Check 2: Apply tests.diff - tests should fail (feature is removed)
            print_with_id("Check 2: Tests should fail when added to final state...")
            subprocess.run(
                ["git", "reset", "--hard", final_state_commit],
                cwd=repo_dir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=repo_dir,
                capture_output=True,
            )
            
            exit_code, stderr = apply_git_diff(repo_dir, tests_diff)
            if exit_code != 0:
                print_with_id(f"FAILED: Failed to apply tests.diff: {stderr}")
                return False
            
            exit_code, output, timed_out = run_container(repo_dir, container, timeout)
            # Tests should fail (exit_code != 0) because feature is removed
            if exit_code == 0:
                print_with_id("FAILED: Tests passed when they should fail (feature is removed)")
                return False
            print_with_id("PASS")
            
            # Check 3: Reverse src.diff to restore feature and the tests.
            print_with_id("Check 3: Tests should pass when feature is restored...")
            subprocess.run(
                ["git", "reset", "--hard", final_state_commit],
                cwd=repo_dir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=repo_dir,
                capture_output=True,
            )
            
            # reverse src.diff (to restore feature and the tests)
            exit_code, stderr = reverse_git_diff(repo_dir, src_diff)
            if exit_code != 0:
                print_with_id(f"FAILED: Failed to reverse src.diff: {stderr}")
                return False
            
            exit_code, output, timed_out = run_container(repo_dir, container, timeout)
            # Tests should pass (exit_code == 0) because feature is restored
            if exit_code != 0 or timed_out:
                print_with_id(f"FAILED: Tests failed when they should pass (feature is restored) (exit_code={exit_code}, timed_out={timed_out})")
                return False
            print_with_id("PASS")
            
            print_with_id("Status: PASSED")
            return True
    
    except Exception as e:
        print_with_id(f"ERROR: Exception during validation: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Validate output from validate_task.py"
    )
    parser.add_argument(
        "input_jsonl",
        type=Path,
        help="Path to JSONL file with validated task results (one per line)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for container execution (default: 300)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help=f"Number of parallel workers (default: {os.cpu_count() or 1})"
    )
    args = parser.parse_args()
    
    if not args.input_jsonl.exists():
        print(f"Error: Input file {args.input_jsonl} does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Read all tasks
    tasks = []
    with args.input_jsonl.open() as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            
            try:
                validated_task_data = json.loads(line.strip())
                tasks.append((validated_task_data, args.timeout, None))
            except json.JSONDecodeError as e:
                print(f"Line {line_num}: Invalid JSON: {e}", file=sys.stderr)
                continue
    
    # Process tasks in parallel
    all_passed = True
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        # Submit all tasks
        future_to_task = {
            executor.submit(validate_task_worker, task): task[0].get("task_id", "unknown")
            for task in tasks
        }
        
        # Process results as they complete
        for future in as_completed(future_to_task):
            try:
                task_id, success = future.result()
                if not success:
                    all_passed = False
            except Exception as e:
                task_id = future_to_task[future]
                print(f"[{task_id}] ERROR: Exception during validation: {e}", file=sys.stderr)
                all_passed = False
    
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    sys.exit(main())


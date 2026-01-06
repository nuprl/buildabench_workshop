# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
Check if patches from tasks.jsonl apply cleanly. This script iterates through all
tasks in the file and verifies that each patch can be applied without errors.

Usage:

python3 -m buildabench_workshop.eval_patch_only \
    --tasks TASK_FILE

See synth_task.py for the format of TASK_FILE. The script processes all tasks in
the file and outputs JSON results for each task, one per line.

Approach:

1. Read all tasks from the tasks.jsonl file.
2. For each task:
   a. Extract the repository to a temporary directory using repolib.
   b. Apply the task patch (patches from the task row) to the repository using
      SEARCH/REPLACE format.
   c. Record whether the patch applied successfully and any errors.
3. Print to stdout JSON objects (one per line) with patch application results
   for each task.
"""

import argparse
import sys
import json
from pathlib import Path
from typing import Optional

from .repolib import tarball_or_repo
from .apply_patch import apply_patch


class EvalPatchError(Exception):
    """Base exception for eval_patch_only errors."""
    pass


def check_patch_for_task(task_data: dict, working_path: Optional[Path] = None) -> dict:
    """
    Check if a patch applies cleanly for a single task.
    
    Returns a dictionary with patch application results.
    """
    task_id = task_data.get("task_id", "unknown")
    
    # Initialize result dictionary
    result = {
        "task_id": task_id,
        "src_patch_apply_errors": None,
        "src_patch_apply_success": None,
        "error": None,
    }
    
    # Extract repository path from task data
    repo_path_str = task_data.get("repo")
    if not repo_path_str:
        result["error"] = "Task data missing 'repo' field"
        result["src_patch_apply_success"] = False
        return result
    
    repo_path = Path(repo_path_str)
    if not repo_path.exists():
        result["error"] = f"Repository path {repo_path} does not exist"
        result["src_patch_apply_success"] = False
        return result
    
    # Extract patches
    patches = task_data.get("patches", "")
    
    # Extract repository and apply patches
    try:
        with tarball_or_repo(repo_path, working_dir=working_path) as repo_dir:
            repo_dir = repo_dir.absolute()
            
            # Apply patches using SEARCH/REPLACE format
            errors: list[str] = []
            patch_success = apply_patch(repo_dir, patches, errors, dry_run=False)
            result["src_patch_apply_success"] = patch_success
            result["src_patch_apply_errors"] = "\n".join(errors) if errors else None
            
            if not patch_success:
                result["error"] = f"Failed to apply patches: {result['src_patch_apply_errors']}"
    except Exception as e:
        result["error"] = f"Error processing task: {e}"
        result["src_patch_apply_success"] = False
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Check if patches from tasks.jsonl apply cleanly"
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        required=True,
        dest="tasks_file",
        help="Path to tasks JSONL file"
    )
    parser.add_argument(
        "--working-path",
        type=Path,
        default=None,
        dest="working_path",
        help="Persistent directory to extract repositories to (default: use temporary directories)"
    )
    args = parser.parse_args()
    
    if not args.tasks_file.exists():
        print(f"Error: Tasks file {args.tasks_file} does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Read all tasks
    tasks = []
    with args.tasks_file.open() as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            
            try:
                task_data = json.loads(line.strip())
                tasks.append(task_data)
            except json.JSONDecodeError as e:
                print(f"Line {line_num}: Invalid JSON: {e}", file=sys.stderr)
                continue
    
    if not tasks:
        print("Error: No valid tasks found in file", file=sys.stderr)
        sys.exit(1)
    
    # Process each task
    all_passed = True
    for task_data in tasks:
        result = check_patch_for_task(task_data, working_path=args.working_path)
        
        # Print JSON result to stdout (one per line) - only task_id and success boolean
        output = {
            "task_id": result["task_id"],
            "src_patch_apply_success": result.get("src_patch_apply_success", False)
        }
        print(json.dumps(output))
        
        # Track if any patch failed
        if not result.get("src_patch_apply_success", False):
            all_passed = False
    
    # Exit with non-zero code if any patch failed
    if not all_passed:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())


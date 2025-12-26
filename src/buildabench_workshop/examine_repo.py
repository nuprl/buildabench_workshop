# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
A script to check if a repository is a good candidate for an environment.
We use the following heuristics:

1. If the repository is tarballed, its size must not exceed 100MB.

2. The repository root must have two directories src and test.

3. The repository root must have a file called Project.toml.

4. The repository root must have a file called README file (any extension and any capitalization).

5. The repository root must have a file called LICENSE file (any extension and any capitalization).
"""

import json
import re
import sys
from pathlib import Path
from .repolib import tarball_or_repo


def initialize_result() -> dict:
    """Initialize result dictionary with all fields set to None."""
    return {
        "path": None,
        "repo": None,
        "OK": True,
        "size_mb": None,
        "has_src": None,
        "has_test": None,
        "has_project_toml": None,
        "has_readme": None,
        "readme_filename": None,
        "has_license": None,
        "license_filename": None,
        "src_size_bytes": None,
        "test_size_bytes": None,
        "num_functions": None,
        "error": None
    }


def get_tarball_size(result: dict, path: Path) -> None:
    """Update result dictionary with tarball size information."""
    if not path.is_file():
        result["size_mb"] = None
        return
    
    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    result["size_mb"] = round(size_mb, 2)


def find_file_case_insensitive(directory: Path, base_name: str) -> dict:
    """Find a file with the given base name (case-insensitive, any extension)."""
    base_name_lower = base_name.lower()
    for item in directory.iterdir():
        if item.is_file():
            item_name_lower = item.name.lower()
            if item_name_lower.startswith(base_name_lower):
                return {"found": True, "filename": item.name}
    return {"found": False, "filename": None}


def compute_jl_file_size(directory: Path) -> int:
    """Compute total size in bytes of all .jl files recursively in a directory."""
    if not directory.is_dir():
        return 0
    
    total_size = 0
    try:
        for jl_file in directory.rglob("*.jl"):
            if jl_file.is_file():
                total_size += jl_file.stat().st_size
    except (OSError, PermissionError):
        # If we can't access the directory, return 0
        pass
    
    return total_size

def num_functions_in_file(file: Path) -> int:
    """Count the number of functions in a file."""
    if not file.is_file():
        return 0
    
    text = file.read_text(encoding="utf-8", errors="ignore")
    
    # Match either "\nfunction " or " function " in a single pass
    pattern = r'(?:\n| )function '
    matches = re.findall(pattern, text)
    return len(matches)

def num_functions(directory: Path) -> int:
    """Count the number of functions in a directory."""
    total_functions = 0
    for jl_file in directory.rglob("*.jl"):
        if jl_file.is_file():
            total_functions += num_functions_in_file(jl_file)
    return total_functions

def check_repo_criteria(result: dict, repo_root: Path) -> None:
    """Check repository criteria and update result dictionary."""
    # Check src directory
    src_path = repo_root / "src"
    result["has_src"] = src_path.is_dir()
    result["src_size_bytes"] = compute_jl_file_size(src_path)
    result["num_functions"] = num_functions(src_path)
    # Check test directory
    test_path = repo_root / "test"
    result["has_test"] = test_path.is_dir()
    result["test_size_bytes"] = compute_jl_file_size(test_path)
    
    # Check Project.toml
    project_toml_path = repo_root / "Project.toml"
    result["has_project_toml"] = project_toml_path.is_file()
    
    # Check README
    readme_result = find_file_case_insensitive(repo_root, "README")
    result["has_readme"] = readme_result["found"]
    result["readme_filename"] = readme_result["filename"]
    
    # Check LICENSE
    license_result = find_file_case_insensitive(repo_root, "LICENSE")
    result["has_license"] = license_result["found"]
    result["license_filename"] = license_result["filename"]


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <repo_path_or_tarball>", file=sys.stderr)
        sys.exit(1)
    
    repo_path = Path(sys.argv[1])
    result = initialize_result()
    result["path"] = str(repo_path)

    if repo_path.name.endswith(".tar"):
       # Assume repo_path is ../owner/name.tar, so the repo is owner/name
       name = repo_path.name.removesuffix(".tar")
       result["repo"] = f"{repo_path.parent.name}/{name}"
    
    # Check tarball size if it's a file
    if repo_path.is_file():
        get_tarball_size(result, repo_path)
    
    try:
        with tarball_or_repo(repo_path) as repo_dir:
            check_repo_criteria(result, repo_dir)
        print(json.dumps(result))
    except Exception as e:
        result["OK"] = False
        result["error"] = str(e)
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()

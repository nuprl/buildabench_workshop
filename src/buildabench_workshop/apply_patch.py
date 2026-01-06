# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
Apply patches to a repository. Patches are in the SEARCH/REPLACE format described
in synth_task.py.
"""

import argparse
from pathlib import Path
from typing import List, Tuple, Dict
from collections import defaultdict
import sys
import logging
import os

from .repolib import tarball_or_repo


def parse_patch_content(patch_content: str, errors: List[str]) -> List[Tuple[str, str, str]]:
    """
    Parse patch content in the SEARCH/REPLACE format.

    Format (as specified in synth_task.py):
    ### file/path.py
    <<<<<<< SEARCH
    [search content]
    =======
    [replace content]
    >>>>>>> REPLACE

    Returns a list of tuples: (file_path, search_text, replace_text)
    """
    chunks = []
    lines = patch_content.splitlines(keepends=True)
    i = 0

    while i < len(lines):
        # Look for SEARCH marker (exactly 7 < characters)
        if lines[i].strip() != '<<<<<<< SEARCH':
            i += 1
            continue

        # Look back to find the file path (### line)
        file_path = None
        lookback_idx = i - 1
        while lookback_idx >= 0:
            path_line = lines[lookback_idx].strip()
            # Must have exactly 3 # characters followed by space
            if path_line.startswith('### '):
                file_path = path_line[4:].strip()  # Remove '### ' prefix
                break
            elif path_line:  # Non-empty line that's not a file path
                # Stop searching if we hit non-blank, non-file-path content
                lookback_idx -= 1
            else:  # Blank line
                lookback_idx -= 1

        if file_path is None:
            errors.append("Error: SEARCH marker found but no file path (### line) found before it, skipping")
            i += 1
            continue

        i += 1  # Skip SEARCH line

        # Collect search text until divider
        search_lines = []
        while i < len(lines):
            if lines[i].strip() == '=======':
                break
            search_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            errors.append(f"Error: No divider (=======) found for {file_path}, skipping")
            break

        # Divider found - skip it and collect replace text
        i += 1  # Skip divider line

        # Collect replace text until REPLACE marker
        replace_lines = []
        while i < len(lines):
            if lines[i].strip() == '>>>>>>> REPLACE':
                break
            replace_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            errors.append(f"Error: No REPLACE marker (>>>>>>> REPLACE) found for {file_path}, skipping")
            break

        i += 1  # Skip REPLACE line

        # Join lines, preserving exact content (including trailing newlines/spaces)
        search_text = ''.join(search_lines)
        replace_text = ''.join(replace_lines)

        chunks.append((file_path, search_text, replace_text))

    return chunks


def apply_patch_to_content(content: str, search_text: str, replace_text: str, file_path_str: str, errors: List[str]) -> Tuple[str, bool]:
    """
    Apply a single patch to a content string.
    
    Returns a tuple of (updated_content, success) where success is True if the patch was applied successfully.
    """
    # Try to find the search text in the content
    if search_text not in content:
        errors.append(f"Error: Search text not found in {file_path_str}")
        errors.append(f"Search text was:\n{repr(search_text[:200])}")
        return content, False
    
    # Replace the search text with replace text
    new_content = content.replace(search_text, replace_text, 1)  # Replace only first occurrence
    
    return new_content, True


def apply_patch(repo_dir: Path, patch_content: str, errors: List[str], dry_run: bool) -> bool:
    """
    Apply patch content to a repository directory.
    
    Args:
        repo_dir: Repository directory to apply patches to
        patch_content: Patch content string in SEARCH/REPLACE format
        errors: List to accumulate error messages
        dry_run: If True, only check if patches apply cleanly without writing files
    
    Returns True if all patches were applied successfully, False otherwise.
    """
    chunks = parse_patch_content(patch_content, errors)
    
    if not chunks:
        errors.append("Error: No valid patch chunks found in patch file")
        return False
    
    # Group patches by file path
    patches_by_file: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for file_path_str, search_text, replace_text in chunks:
        patches_by_file[file_path_str].append((search_text, replace_text))
    
    success = True
    for file_path_str, patches in patches_by_file.items():
        # Resolve file path relative to repo root
        file_path = repo_dir / file_path_str
        
        if not file_path.exists():
            errors.append(f"Error: File not found: {file_path}")
            success = False
            continue
        
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"Error reading {file_path}: {e}")
            success = False
            continue
        
        action = "Checking" if dry_run else "Applying"
        logging.info(f"{action} {len(patches)} patch(es) to {file_path_str}...")
        
        # Apply all patches for this file to the content string
        for search_text, replace_text in patches:
            content, patch_success = apply_patch_to_content(content, search_text, replace_text, file_path_str, errors)
            if not patch_success:
                success = False
        
        # Write the file only if not in dry-run mode
        if not dry_run:
            try:
                file_path.write_text(content, encoding="utf-8")
            except Exception as e:
                errors.append(f"Error writing {file_path}: {e}")
                success = False
    
    return success


def apply_patch_file(repo_dir: Path, patch_file: Path, errors: List[str], dry_run: bool) -> bool:
    """
    Apply a patch file to a repository directory.
    
    Args:
        repo_dir: Repository directory to apply patches to
        patch_file: Path to patch file in SEARCH/REPLACE format
        errors: List to accumulate error messages
        dry_run: If True, only check if patches apply cleanly without writing files
    
    Returns True if all patches were applied successfully, False otherwise.
    """
    try:
        patch_content = patch_file.read_text(encoding="utf-8")
    except Exception as e:
        errors.append(f"Error reading patch file {patch_file}: {e}")
        return False
    
    return apply_patch(repo_dir, patch_content, errors, dry_run=dry_run)


def _get_log_level() -> int:
    """
    Determine log level from LOGLEVEL environment variable.
    Defaults to WARNING if not set.
    """
    loglevel_env = os.getenv("LOGLEVEL", "").upper()
    if loglevel_env:
        level = getattr(logging, loglevel_env, None)
        if isinstance(level, int):
            return level
    
    # Default to WARNING if not set
    return logging.WARNING


def main_with_args(repo_path: Path, patch_file: Path, dry_run: bool = False):
    # Configure logging from LOGLEVEL environment variable
    log_level = _get_log_level()
    logging.basicConfig(
        level=log_level,
        format='%(message)s',
        stream=sys.stderr
    )
    
    errors: List[str] = []
    
    if not patch_file.exists():
        errors.append(f"Error: Patch file not found: {patch_file}")
    else:
        with tarball_or_repo(repo_path) as repo_dir:
            success = apply_patch_file(repo_dir, patch_file, errors, dry_run=dry_run)
            if not success:
                # Errors already accumulated in errors list
                pass
    
    # Log all accumulated errors at the end
    if errors:
        for error in errors:
            if error.startswith("Warning:"):
                logging.warning(error)
            else:
                logging.error(error)
        sys.exit(1)
    return


def main():
    parser = argparse.ArgumentParser(
        description="Apply patches to a repository (repo or tarball)"
    )
    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to tarball containing a bare git repository or an existing repository directory",
    )
    parser.add_argument(
        "patch_file",
        type=Path,
        help="Path to patch file in SEARCH/REPLACE format",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check that patches apply cleanly without actually applying them",
    )
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    main()


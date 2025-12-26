#!/usr/bin/env python3
"""
Apply patches to a repository. Patches are in the SEARCH/REPLACE format described
in synth_task.py.
"""

import argparse
from pathlib import Path
from typing import List, Tuple, Dict
from collections import defaultdict
import sys

from .repolib import tarball_or_repo


def parse_patch_file(patch_file: Path, errors: List[str]) -> List[Tuple[str, str, str]]:
    """
    Parse a patch file in the SEARCH/REPLACE format.
    
    Format:
    ### file/path.py  (optional ### prefix)
    <<<<<<< SEARCH
    [search content]
    =======
    [replace content]
    >>>>>>> REPLACE
    
    Returns a list of tuples: (file_path, search_text, replace_text)
    """
    content = patch_file.read_text(encoding="utf-8")
    
    chunks = []
    lines = content.splitlines(keepends=True)
    i = 0
    
    while i < len(lines):
        # Look for SEARCH marker
        if not lines[i].strip().startswith('<<<<<<< SEARCH'):
            i += 1
            continue
        
        # Look back 1 line for the file path
        if i == 0:
            errors.append("Warning: SEARCH marker found at start of file, no file path available, skipping")
            i += 1
            continue
        path_line = lines[i - 1].strip()
        
        # Remove leading ### characters if present
        if path_line.startswith('###'):
            file_path = path_line[3:].strip()
        else:
            # No ### prefix, use the line as-is
            file_path = path_line
        
        i += 1  # Skip SEARCH line
        
        # Collect search text until divider
        search_lines = []
        while i < len(lines) and not lines[i].strip().startswith('======='):
            search_lines.append(lines[i])
            i += 1
        
        if i >= len(lines):
            errors.append(f"Warning: No divider found for {file_path}, skipping")
            break
        
        i += 1  # Skip divider line
        
        # Collect replace text until REPLACE marker
        replace_lines = []
        while i < len(lines) and not lines[i].strip().startswith('>>>>>>> REPLACE'):
            replace_lines.append(lines[i])
            i += 1
        
        if i >= len(lines):
            errors.append(f"Warning: No REPLACE marker found for {file_path}, skipping")
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


def apply_patch(repo_dir: Path, patch_file: Path, errors: List[str]) -> bool:
    """
    Apply a patch file to a repository directory.
    
    Returns True if all patches were applied successfully, False otherwise.
    """
    chunks = parse_patch_file(patch_file, errors)
    
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
        
        print(f"Applying {len(patches)} patch(es) to {file_path_str}...", file=sys.stderr)
        
        # Apply all patches for this file to the content string
        for search_text, replace_text in patches:
            content, patch_success = apply_patch_to_content(content, search_text, replace_text, file_path_str, errors)
            if not patch_success:
                success = False
        
        # Write the file once after applying all patches
        try:
            file_path.write_text(content, encoding="utf-8")
        except Exception as e:
            errors.append(f"Error writing {file_path}: {e}")
            success = False
    
    return success


def main_with_args(repo_path: Path, patch_file: Path):
    errors: List[str] = []
    
    if not patch_file.exists():
        errors.append(f"Error: Patch file not found: {patch_file}")
    else:
        with tarball_or_repo(repo_path) as repo_dir:
            success = apply_patch(repo_dir, patch_file, errors)
            if not success:
                # Errors already accumulated in errors list
                pass
    
    # Print all accumulated errors at the end
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
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
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    main()


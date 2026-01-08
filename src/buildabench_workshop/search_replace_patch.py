# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
A representation of patches in roughly the Aider search/replace format.
"""

import argparse
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import sys
import logging
import os
from .repolib import tarball_or_repo


class SearchReplacePatch:
    """
    Represents a parsed patch with patches grouped by file.
    """
    
    def __init__(self, patches: Dict[str, List[Tuple[str, str]]]):
        # Patches is keyed by file name. Each file maps to a list of 
        # (old_string, new_string) tuples. We interpret this list as a series
        # of patches where we search for old_string and replace it with 
        # new_string. We do this in order, and only replace the first
        # occurrence.
        self.patches = patches
    
    @classmethod
    def from_string(cls, patch_content: str) -> Optional['SearchReplacePatch']:
        """
        patch_content must have a series of patches that look like this:
        
        ```
        ### file/path.py
        <<<<<<< SEARCH
        [search content]
        =======
        [replace content]
        >>>>>>> REPLACE
        ```

        There can be other arbitrary text in between the patches.
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
                break

            i += 1  # Skip REPLACE line

            # Join lines, preserving exact content (including trailing newlines/spaces)
            search_text = ''.join(search_lines)
            replace_text = ''.join(replace_lines)

            chunks.append((file_path, search_text, replace_text))

        if not chunks:
            return None
        
        # Group patches by file, keeping all patches in order
        # Filter out no-op patches (where old_string == new_string)
        result: Dict[str, List[Tuple[str, str]]] = {}
        
        for file_path, old_text, new_text in chunks:
            # Skip no-op patches (where old_string == new_string)
            if old_text == new_text:
                continue
            # Skip patches with empty search strings (invariant: search strings must be non-empty)
            if not old_text:
                continue
            if file_path not in result:
                result[file_path] = []
            result[file_path].append((old_text, new_text))
        
        return cls(result) if result else None
    
    def render(self) -> str:
        """
        Render parsed patch structure back to SEARCH/REPLACE text format.
        
        Returns:
            Patch content string in SEARCH/REPLACE format
        """
        if not self.patches:
            return ""
        
        parts = []
        for file_path, patches in self.patches.items():
            for old_string, new_string in patches:
                parts.append(f"### {file_path}\n")
                parts.append("<<<<<<< SEARCH\n")
                parts.append(old_string)  # Already includes newlines
                parts.append("=======\n")
                parts.append(new_string)  # Already includes newlines
                parts.append(">>>>>>> REPLACE\n")
                parts.append("\n")
        
        return "".join(parts)
    
    def apply(self, repo_dir: Path, dry_run: bool) -> bool:
        """
        Applies the patch. In dry_run mode, we don't actually modify any files.

        Without dry_run, in an error occurs, the patch may be partially written
        to disk. So, you should probably always use dry_run first.
        """
        for file_path_str, patches in self.patches.items():
            file_path = repo_dir / file_path_str            
            if not file_path.exists():
                return False
            
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return False
            
            for old_string, new_string in patches:
                if old_string not in content:
                    return False
                content = content.replace(old_string, new_string, 1)  # Replace only first occurrence
            
            # Write the file only if not in dry-run mode
            if not dry_run:
                try:
                    file_path.write_text(content, encoding="utf-8")
                except Exception as e:
                    return False

        return True


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
        try:
            patch_content = patch_file.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"Error reading patch file {patch_file}: {e}")
        else:
            patch = SearchReplacePatch.from_string(patch_content)
            if patch is None:
                errors.append("Error: Failed to parse patches")
            else:
                with tarball_or_repo(repo_path) as repo_dir:
                    success = patch.apply(repo_dir, dry_run=dry_run)
                    if not success:
                        errors.append("Error: Failed to apply patches")
    
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


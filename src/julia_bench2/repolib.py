#!/usr/bin/env python3
"""
Library for handling git repositories, either as directories or tarballs.
"""

import tarfile
from pathlib import Path
import shutil
import subprocess
from contextlib import contextmanager
import tempfile


def extract_bare_repo(tar_file_path: Path, tmp_path: Path) -> Path:
    """
    Extract a tarball containing a bare git repository to a temporary directory.
    Returns the path to the extracted bare repository directory.
    """
    with tarfile.open(tar_file_path, "r") as tar:
        tar.extractall(path=tmp_path)
    
    # The tarball should contain a single directory (the bare git repo)
    extracted_items = list(tmp_path.iterdir())
    if len(extracted_items) != 1:
        raise ValueError(
            f"Expected 1 item in {tmp_path}, got {len(extracted_items)}"
        )
    
    if not extracted_items[0].is_dir():
        raise ValueError(
            f"Expected directory in {tmp_path}, got {extracted_items[0]}"
        )
    
    bare_repo_dir = extracted_items[0]
    
    if not (bare_repo_dir / "HEAD").exists() or not (bare_repo_dir / "objects").exists():
        raise ValueError(
            f"Extracted directory {bare_repo_dir} does not appear to be a valid git repository"
        )
    
    return bare_repo_dir


def clone_bare_repo_to_working_tree(bare_repo_dir: Path, working_tree_dir: Path) -> None:
    """
    Clone a bare repository to a working tree directory.
    """
    if working_tree_dir.exists():
        shutil.rmtree(working_tree_dir)
    
    bare_repo_abs = bare_repo_dir.resolve()
    
    result = subprocess.run(
        ["git", "clone", str(bare_repo_abs), str(working_tree_dir)],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed: {result.stderr}"
        )


@contextmanager
def extracted_tarballed_repo(tarball: Path):
    """
    Context manager that extracts a tarball containing a bare git repository
    and clones it to a working tree. Returns the working tree directory.
    
    Args:
        tarball: Path to tarball containing a bare git repository
    
    Yields:
        Path to the working tree directory
    
    Raises:
        FileNotFoundError: If tarball doesn't exist
        ValueError: If tarball extraction fails
        RuntimeError: If git operations fail
    """
    if not tarball.is_file():
        raise FileNotFoundError(f"Tarball not found: {tarball}")
    
    with tempfile.TemporaryDirectory() as tmp_extract_dir, tempfile.TemporaryDirectory() as tmp_working_dir:
        tmp_extract_path = Path(tmp_extract_dir)
        working_tree_dir = Path(tmp_working_dir)
        
        bare_repo_dir = extract_bare_repo(tarball, tmp_extract_path)
        clone_bare_repo_to_working_tree(bare_repo_dir, working_tree_dir)
        
        yield working_tree_dir


@contextmanager
def tarball_or_repo(path: Path):
    """
    Context manager that handles either a tarball or an existing repository directory.
    If the path is a tarball, extracts and clones it (with cleanup on exit).
    If the path is an existing directory, yields it directly (no cleanup).
    
    Args:
        path: Path to either a tarball file or an existing repository directory
    
    Yields:
        Path to the working tree directory
    
    Raises:
        FileNotFoundError: If path doesn't exist
        ValueError: If path is neither a file nor a directory, or if tarball extraction fails
        RuntimeError: If git operations fail
    """
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    
    if path.is_file():
        # It's a tarball, use the extracted_tarballed_repo context manager
        with extracted_tarballed_repo(path) as repo_dir:
            yield repo_dir
    elif path.is_dir():
        # It's an existing directory, just yield it without cleanup
        yield path
    else:
        raise ValueError(f"Path is neither a file nor a directory: {path}")


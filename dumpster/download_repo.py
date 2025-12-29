"""
Download a repository from GitHub and create a tar archive.

This script clones a GitHub repository as a bare git repository, creates a tar
archive, and removes the cloned directory. It skips repositories that have
already been archived.

The script is designed to be used with GNU parallel for batch processing:

    parallel -j 10 --progress --bar --resume --joblog joblog.txt \\
        python3 download_repo.py --dir /path/to/output/dir :::: repo_urls.csv

Where repo_urls.csv contains one GitHub URL per line (e.g., 
https://github.com/owner/repo).

This is a standalone script with no dependencies other than the standard library.
"""

from pathlib import Path
import argparse
import subprocess
import os
import shutil
from urllib.parse import urlparse


def main_with_args(dir: Path, repo: str):
    # repo is expected to be a full GitHub URL
    url = repo
    if not url.endswith(".git"):
        url = f"{repo}.git"
    
    # Extract owner and repo name from URL
    # URL format: https://github.com/owner/repo.git
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    
    if len(path_parts) < 2:
        raise ValueError(f"Invalid GitHub URL format, expected 'https://github.com/owner/repo': {repo}")
    
    owner = path_parts[-2]  # Second-to-last component
    repo_name = path_parts[-1].removesuffix(".git")  # Last component without .git
    
    # Set up paths
    owner_dir = dir / owner
    target_dir = owner_dir / repo_name
    tar_path = owner_dir / f"{repo_name}.tar"
    
    # Set git environment variables to avoid prompts and disable LFS
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "true"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    
    # Create owner directory
    owner_dir.mkdir(parents=True, exist_ok=True)
    
    # If tar already exists, skip everything
    if tar_path.exists():
        return
    
    # If directory exists (from old workflow), delete it
    if target_dir.exists():
        shutil.rmtree(target_dir)
    
    # Fresh clone → full bare clone
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", url, str(target_dir)],
        check=True,
        env=env,
        capture_output=True
    )
    
    # Tarball and remove directory
    subprocess.run(
        ["tar", "-C", str(owner_dir), "-cf", str(tar_path), repo_name],
        check=True,
        env=env
    )
    shutil.rmtree(target_dir)
    


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("repo", type=str)
    args = parser.parse_args()

    main_with_args(**vars(args))
    
if __name__ == "__main__":
    main()
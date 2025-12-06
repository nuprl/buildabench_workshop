"""
Extract candidates from parquet file to directories.

Reads the parquet file produced by filter_commits_noexec.py and extracts each
candidate tarball to a directory named ROOT/repo_owner#repo_name#commit_hash.

This is 100% AI slop, but it works.
"""
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "duckdb",
#     "tqdm",
# ]
# ///

import argparse
import duckdb
from tqdm.auto import tqdm
import tarfile
from pathlib import Path
import shutil
import tempfile
import subprocess


def extract_candidate(tar_file_path: str, output_dir: Path, sha: str) -> None:
    """
    Extract a tarball to the output directory.
    
    The tarball contains a single directory which is a bare Git repository.
    We extract it to a temp location first, then clone it as a non-bare repository
    to the final output location, and checkout to the specified commit SHA.
    """
    # Extract to a temporary directory first
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
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
        
        # The extracted directory is a bare git repository
        bare_repo_dir = extracted_items[0]
        
        # Verify it's a valid git repository
        if not (bare_repo_dir / "HEAD").exists() or not (bare_repo_dir / "objects").exists():
            raise ValueError(
                f"Extracted directory {bare_repo_dir} does not appear to be a valid git repository"
            )
        
        # Remove output directory if it exists
        if output_dir.exists():
            shutil.rmtree(output_dir)
        
        # Clone the bare repository as a non-bare repository
        # Use absolute path for the source
        bare_repo_abs = bare_repo_dir.resolve()
        
        result = subprocess.run(
            ["git", "clone", str(bare_repo_abs), str(output_dir)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone failed: {result.stderr}"
            )
        
        # Checkout to the specific commit SHA
        result = subprocess.run(
            ["git", "-C", str(output_dir), "checkout", sha],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git checkout failed: {result.stderr}"
            )


def parse_repo_info(tar_file_path: str) -> tuple[str, str]:
    """
    Parse repo_owner and repo_name from tar_file_path.
    
    Expected format: .../repo_owner/repo_name.tar
    """
    path_parts = Path(tar_file_path).parts
    if len(path_parts) < 2:
        raise ValueError(f"Cannot parse repo info from {tar_file_path}")
    
    repo_name_with_tar = path_parts[-1]
    repo_name = repo_name_with_tar.replace(".tar", "")
    repo_owner = path_parts[-2]
    
    return repo_owner, repo_name


def main():
    parser = argparse.ArgumentParser(
        description="Extract candidates from parquet file to directories"
    )
    parser.add_argument(
        "--parquet-file",
        type=str,
        required=True,
        help="Path to parquet file produced by filter_commits_noexec.py",
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="Root directory where candidates will be extracted",
    )
    args = parser.parse_args()
    
    root_path = Path(args.root)
    root_path.mkdir(parents=True, exist_ok=True)
    
    # Connect to DuckDB and read the parquet file
    db = duckdb.connect(":memory:")
    
    # Read all candidates from the parquet file
    query = f"""
        SELECT tar_file_path, sha
        FROM '{args.parquet_file}'
    """
    
    cursor = db.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    
    print(f"Found {len(rows)} candidates to extract")
    
    # Extract each candidate
    for tar_file_path, sha in tqdm(rows, desc="Extracting candidates"):
        repo_owner, repo_name = parse_repo_info(tar_file_path)
        output_dir_name = f"{repo_owner}#{repo_name}#{sha}"
        output_dir = root_path / output_dir_name
        
        # Skip if already extracted
        if output_dir.exists():
            continue
        
        extract_candidate(tar_file_path, output_dir, sha)
    
    db.close()
    print(f"Extraction complete. Candidates extracted to {root_path}")


if __name__ == "__main__":
    main()


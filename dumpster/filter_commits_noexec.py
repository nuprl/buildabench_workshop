"""
Filter commits to likely candidates without any code execution.

We use the following criteria. For details, see the queries below.

1. Does the commit message seem to reference an issue with "fixes" or "closes"?
2. Does the commit message reference a pull request?
3. Is the commit *not* by a known rule-based bot?
4. Can we fetch the text of the issue?


"""
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "duckdb",
#     "tqdm",
#     "web-request-cache",
# ]
#
# [tool.uv.sources]
# web-request-cache = { path = "../web_request_cache", editable = true }
# ///

import argparse
import duckdb
from web_request_cache import WebRequestCache
from tqdm.auto import tqdm
import asyncio
import tarfile
import tempfile
import subprocess
from pathlib import Path
from typing import Optional
from concurrent.futures import ProcessPoolExecutor, as_completed


def _extract_and_find_repo_root(tarball_path: Path, tmp_untar_dir: Path) -> Path:
    """
    tarball_path should be tarball that contains a single directory, which is
    a bare Git repository. We return the path to that directory, and throw
    an exception if the tarball does not contain just one directory.
    """
    with tarfile.open(tarball_path, "r") as tar:
        tar.extractall(path=tmp_untar_dir)

    extracted_items = list(tmp_untar_dir.iterdir())
    if len(extracted_items) != 1:
        raise ValueError(
            f"Expected 1 item in {tmp_untar_dir}, got {len(extracted_items)}"
        )

    if not extracted_items[0].is_dir():
        raise ValueError(
            f"Expected directory in {tmp_untar_dir}, got {extracted_items[0]}"
        )

    return extracted_items[0]


def check_diff_contains_test(
    repo_root: Path, parent_sha: str, merge_sha: str
) -> bool:
    """
    Gets the diff from parent_sha to merge_sha and checks if it contains "@test".
    """
    try:
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(repo_root),
                "diff",
                parent_sha,
                merge_sha,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return "@test" in result.stdout
    except:
        # If diff fails (e.g., commits don't exist), return False
        return False


def get_test_dir_diff(
    repo_root: Path, parent_sha: str, merge_sha: str
) -> Optional[str]:
    """
    Gets the diff of the test/ directory from parent_sha to merge_sha.
    Returns None if the diff fails.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(repo_root),
                "diff",
                parent_sha,
                merge_sha,
                "--",
                "test/",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout if result.stdout else None
    except:
        # If diff fails (e.g., commits don't exist), return None
        return None


def get_non_test_diff(
    repo_root: Path, parent_sha: str, merge_sha: str
) -> Optional[str]:
    """
    Gets the diff of everything except the test/ directory from parent_sha to merge_sha.
    Returns None if the diff fails.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(repo_root),
                "diff",
                parent_sha,
                merge_sha,
                "--",
                ":!test/",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout if result.stdout else None
    except:
        # If diff fails (e.g., commits don't exist), return None
        return None


def commit_updates_tests(tar_file_path: str, sha: str, parent: Optional[str]) -> tuple[bool, str, str, Optional[str], Optional[str]]:
    """
    Checks if the commit updates tests. If parent is None, we return False.
    Returns: (adds_tests, tar_file_path, sha, test_diff, non_test_diff)
    If the diff doesn't contain tests, test_diff and non_test_diff are None.
    """
    if parent is None:
        return False, tar_file_path, sha, None, None
    try:
        with tempfile.TemporaryDirectory() as tmp_untar_str:
            repo_root = _extract_and_find_repo_root(Path(tar_file_path), Path(tmp_untar_str))
            adds_tests = check_diff_contains_test(repo_root, parent, sha)
            if adds_tests:
                test_diff = get_test_dir_diff(repo_root, parent, sha)
                non_test_diff = get_non_test_diff(repo_root, parent, sha)
                return adds_tests, tar_file_path, sha, test_diff, non_test_diff
            else:
                return adds_tests, tar_file_path, sha, None, None
    except:
        return False, tar_file_path, sha, None, None

async def main_with_args(all_commits: str, cache_file: str, output_file: str):
    db = duckdb.connect(":memory:")
    web_cache = WebRequestCache(cache_file)

    query = f"""
        CREATE TABLE candidates_before_fetching_issue_text AS SELECT
            tar_file_path,
            sha,
            parent,
            regexp_extract(body, '(?:closes|fixes)\\s+#(\\d+)', 1) AS issue_number,
            'https://api.github.com/repos/' ||
                split_part(tar_file_path, '/', -2) || '/' ||
                regexp_replace(split_part(tar_file_path, '/', -1), '\\.tar$', '') ||
                '/issues/' || issue_number AS issue_url,
            CAST(NULL AS VARCHAR) AS issue_text,
            CAST(NULL AS BOOLEAN) AS adds_tests,
            CAST(NULL AS VARCHAR) AS test_diff,
            CAST(NULL AS VARCHAR) AS non_test_diff
        FROM '{all_commits}' 
        WHERE regexp_full_match(subject, 'Merge pull request .*', 'i') 
        AND NOT regexp_full_match(subject, '.*(compathelper|dependabot|codecov).*', 'i')
        AND issue_number != '';
    """

    db.execute(query)
    num_candidates = db.sql("SELECT COUNT(*) FROM candidates_before_fetching_issue_text").fetchall()[0][0]
    print(f"num_candidates: {num_candidates}")
    # Fetch the issue text for each candidate. We break out of the loop as soon
    # as we get a 429 (rate limit exceeded) or 5xx error.
    select_cursor = db.cursor()
    select_cursor.execute("SELECT tar_file_path, sha, issue_url FROM candidates_before_fetching_issue_text;")
    with tqdm(total=num_candidates, desc="Fetching issue text") as pbar:
        while row := select_cursor.fetchone():
            pbar.update(1)
            tar_file_path, sha, issue_url = row
            resp = await web_cache.aget(issue_url)
            if resp.status >= 500 or resp.status == 429:
                print(f"Got code {resp.status} for {issue_url}")
                break
            if resp.status != 200:
                continue
            issue_text = resp.json()["body"]
            db.execute("UPDATE candidates_before_fetching_issue_text SET issue_text = ? WHERE tar_file_path = ? AND sha = ?", (issue_text, tar_file_path, sha))

    # For whatever we manage to get above, check that the commit actually adds tests.
    select_cursor.execute("SELECT tar_file_path, sha, parent FROM candidates_before_fetching_issue_text WHERE issue_text IS NOT NULL")
    candidates_with_issue_text = select_cursor.fetchall()
    with ProcessPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(commit_updates_tests, tar_file_path, sha, parent) for tar_file_path, sha, parent in candidates_with_issue_text]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Checking if commits add tests"):
            adds_tests, tar_file_path, sha, test_diff, non_test_diff = future.result()
            db.execute("UPDATE candidates_before_fetching_issue_text SET adds_tests = ?, test_diff = ?, non_test_diff = ? WHERE tar_file_path = ? AND sha = ?;", (adds_tests, test_diff, non_test_diff, tar_file_path, sha))
            assert db.fetchone()[0] == 1

    db.execute("SELECT COUNT(*) FROM candidates_before_fetching_issue_text WHERE adds_tests;")
    num_with_tests = db.fetchone()[0]
    print(f"num_with_tests: {num_with_tests}")

    write_query = f"""
        COPY (
            SELECT tar_file_path, sha, parent, issue_url, issue_text, test_diff, non_test_diff 
            FROM candidates_before_fetching_issue_text WHERE adds_tests) TO '{output_file}'
        """
    db.execute(write_query)
    db.close()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-commits", type=str, required=True)
    parser.add_argument("--cache-file", type=str, required=True)
    parser.add_argument("--output-file", type=str, required=True)
    args = parser.parse_args()
    await main_with_args(**vars(args))

if __name__ == "__main__":
    asyncio.run(main())

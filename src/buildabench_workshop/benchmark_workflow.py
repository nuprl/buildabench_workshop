# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
This is the standard workflow we use to create a benchmark using
BuildABench Workshop. To use it, we must receive as input two required arguments:

1. The repository, which is one of:

   - the URL of a GitHub repository: https://github.com/REPO_OWNER/REPO_NAME.git
   - The path to a local .tar file that contains a git repository

2. Several Python file globs, which specify the files to use for the
   synth_task agent.

There are several optional inputs:

1. The agent to use. Default is codex.

2. The model to use. Default is  openai/gpt-5.1.

3. The output directory. Defualt is ./outputs/REPO_OWNER#REPO_NAME, or
    ./outputs/TAR_FILE_NAME when the input is a .tar file.

4. The name of the container in which to run code. If omitted, we can
   infer it from from the repository name using the standard_container_name
   function.

5. For GitHub repos: a git ref (tag, branch, or commit hash) to checkout.
   If omitted, the default branch is used.

These are the steps we follow:

1. Create the output directory if it does not exist. We refer to it as
   OUTPUT_DIR below.

2. If working with a GitHub repository, clone it to a temporary directory,
   and create a .tar file of the repo at OUTPUT_DIR/REPO_OWNER#REPO_NAME.tar.

   If this output file already exists, we can skip this step.

3. We create a container for the repository using env_agent, as described in
   README.md. The env_agent JSON output (dockerfile, log, tips, etc.) is
   saved to OUTPUT_DIR/env_agent.jsonl.

   If the container already exists (determined by the standard_container_name
   function), we can skip this step.

4. Run synth_task as described in the README.md to create OUTPUT_DIR/tasks.jsonl.

  If this file already exists, we can skip this step.

5. Run validate_task as described in the README.md to create OUTPUT_DIR/validated_tasks.jsonl.

  If this file already exists, we can skip this step.

6. Run check_validated_tasks as described in the README.md
"""

import argparse
import re
import subprocess
import sys
import tempfile
from io import TextIOBase
from pathlib import Path

from .agentlib import container_exists, standard_container_name


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Parse GitHub URL to (owner, repo). Returns None if not a GitHub URL."""
    # https://github.com/owner/repo.git or https://github.com/owner/repo
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    # git@github.com:owner/repo.git
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    return None


def validate_single_task(
    task_line: str,
    outf: TextIOBase,
    *,
    project_root: Path,
    validate_tips: Path,
    container: str,
    agent: str,
) -> None:
    """Run validate_task on a single task and append result to outf. On failure, prints a warning and continues."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "buildabench_workshop.validate_task",
            "--tips-path",
            str(validate_tips),
            "--container",
            container,
            "--agent",
            agent,
            "--input-json",
            "--output-json",
        ],
        cwd=project_root,
        input=task_line + "\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Warning: validate_task failed: {result.stderr}", file=sys.stderr)
        return
    outf.write(result.stdout)
    if not result.stdout.endswith("\n"):
        outf.write("\n")


def clone_and_tar(url: str, output_tar: Path, ref: str | None = None) -> None:
    """Clone a GitHub repo and create a tarball. If ref is set, checkout that
    tag, branch, or commit hash before creating the tarball."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        subprocess.run(
            ["git", "clone", url, str(tmppath / "repo")],
            check=True,
            capture_output=True,
        )
        if ref is not None:
            subprocess.run(
                ["git", "-C", str(tmppath / "repo"), "checkout", ref],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["tar", "-cf", str(output_tar), "-C", str(tmppath), "repo"],
            check=True,
            capture_output=True,
        )


def main_with_args(
    repo: str,
    patterns: list[str],
    agent: str,
    model: str,
    output_dir: Path | None,
    container: str | None,
    env_tips_path: Path | None,
    validate_tips_path: Path | None,
    num_candidates: int,
    ref: str | None = None,
) -> int:
    gh = parse_github_url(repo)

    if gh is not None:
        owner, repo_name = gh
        out_name = f"{owner}#{repo_name}"
        output_dir = output_dir or Path("outputs") / out_name
        repo_tar = output_dir / f"{out_name}.tar"
    else:
        repo_path = Path(repo)
        if not repo_path.exists() or not repo_path.is_file():
            print(f"Error: {repo_path} is not an existing file", file=sys.stderr)
            return 1
        out_name = repo_path.stem
        output_dir = output_dir or Path("outputs") / out_name
        repo_tar = repo_path.resolve()

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if gh is not None and not repo_tar.exists():
        msg = f"Cloning {repo} -> {repo_tar}"
        if ref is not None:
            msg += f" (ref: {ref})"
        print(msg, file=sys.stderr)
        clone_and_tar(repo, repo_tar, ref=ref)

    container = container or standard_container_name(Path(repo_tar.stem))

    env_tips = env_tips_path or output_dir / "env_tips.txt"
    validate_tips = validate_tips_path or output_dir / "validate_tips.txt"
    for p in (env_tips, validate_tips):
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("No tips yet\n")

    project_root = Path(__file__).resolve().parents[2]

    if not container_exists(container):
        print(f"Running env_agent for container {container}", file=sys.stderr)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "buildabench_workshop.env_agent",
                "--repo",
                str(repo_tar),
                "--tips-path",
                str(env_tips),
                "--agent",
                agent,
                "--output-json",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return result.returncode
        (output_dir / "env_agent.jsonl").write_text(result.stdout)
    else:
        print(f"SKIP env_agent: container {container} already exists", file=sys.stderr)

    tasks_jsonl = output_dir / "tasks.jsonl"
    if not tasks_jsonl.exists():
        print("Running synth_task", file=sys.stderr)
        with tasks_jsonl.open("w") as f:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "buildabench_workshop.synth_task",
                    "--json",
                    "--num-candidates",
                    str(num_candidates),
                    "--model",
                    model,
                    str(repo_tar),
                    *patterns,
                ],
                cwd=project_root,
                stdout=f,
            )
        if result.returncode != 0:
            return result.returncode
    else:
        print(f"SKIP synth_task: {tasks_jsonl} already exists", file=sys.stderr)

    validated_jsonl = output_dir / "validated_tasks.jsonl"
    if not validated_jsonl.exists():
        tasks = tasks_jsonl.read_text().strip().split("\n")
        tasks = [t for t in tasks if t.strip()]
        if not tasks:
            print("Error: no tasks to validate", file=sys.stderr)
            return 1
        print(f"Running validate_task for {len(tasks)} tasks", file=sys.stderr)
        with validated_jsonl.open("w") as outf:
            for line in tasks:
                validate_single_task(
                    line,
                    outf,
                    project_root=project_root,
                    validate_tips=validate_tips,
                    container=container,
                    agent=agent,
                )
    else:
        print(
            f"SKIP validate_task: {validated_jsonl} already exists",
            file=sys.stderr,
        )

    print("Running check_validated_tasks", file=sys.stderr)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "buildabench_workshop.check_validated_tasks",
            str(validated_jsonl),
        ],
        cwd=project_root,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Build-A-Bench benchmark creation workflow"
    )
    parser.add_argument(
        "repo",
        help="GitHub URL (https://github.com/owner/repo) or path to .tar file",
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help="File glob patterns for synth_task (e.g., src/*.py)",
    )
    parser.add_argument("--agent", default="codex", help="Agent to use (default: codex)")
    parser.add_argument(
        "--model",
        default="openai/gpt-5.1",
        help="Model to use (default: openai/gpt-5.1)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: ./outputs/OWNER#REPO or ./outputs/TAR_STEM)",
    )
    parser.add_argument(
        "--container",
        help="Container name (default: from standard_container_name)",
    )
    parser.add_argument(
        "--env-tips-path",
        type=Path,
        dest="env_tips_path",
        help="Tips file for env_agent (default: OUTPUT_DIR/env_tips.txt)",
    )
    parser.add_argument(
        "--validate-tips-path",
        type=Path,
        dest="validate_tips_path",
        help="Tips file for validate_task (default: OUTPUT_DIR/validate_tips.txt)",
    )
    parser.add_argument(
        "--num-candidates",
        type=int,
        default=10,
        help="Number of tasks to synthesize (default: 1)",
    )
    parser.add_argument(
        "--ref",
        help="Git tag, branch, or commit hash to checkout (GitHub repos only)",
    )
    args = parser.parse_args()
    return main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

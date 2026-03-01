# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
Evaluate BuildABench tasks using Mini-SWE-Agent v2 in a derived container.

This script is self-contained and does not depend on eval_agent.py.

Use the `run` subcommand to evaluate one or more tasks and emit one JSON object
per task to stdout:

    uv run -m buildabench_workshop.eval_minisweagent run \
        --tasks tasks.jsonl \
        --validated-tasks validated_tasks.jsonl \
        --tarballs-dir /path/to/tarballs \
        --output-directory /path/to/output

With defaults, `run` uses:
- model: `openai/claude-haiku-4-5`
- cost limit: `$1` (mapped to mini `--cost-limit`)
- agent timeout: `1800` seconds
- test timeout: `600` seconds
- no per-container memory limit unless `--container-memory` is provided

Output layout under `--output-directory`:
- `<model>/results.jsonl`: aggregate JSONL for the run
- `<model>/results/*.jsonl`: one JSONL artifact per task
- `<model>/repos/<task_id>/`: extracted task repositories
- `<model>/container_build/`: build contexts for derived images

The model directory name is the model name with the `openai/` prefix removed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bounded_subprocess import run as bounded_run

from .repolib import tarball_or_repo


class EvalMiniSWEAgentError(Exception):
    """Base exception for eval_minisweagent errors."""


@dataclass
class CombinedTask:
    """Merged task/validated-task data for one task_id."""

    task_id: str
    task: dict
    validated: dict | None


def load_jsonl_map(path: Path, key: str = "task_id") -> dict[str, dict]:
    """
    Load a JSONL file into a dictionary keyed by a chosen field.
    """
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            if key in data and isinstance(data[key], str):
                out[data[key]] = data
    return out


def may_read(path: Path) -> str | None:
    """
    Read a text file and return None on any error.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def derive_minisweagent_image_name(base_image: str) -> str:
    """
    Return {ORIGINAL_CONTAINER_NAME}-minisweagent.

    If base image includes a tag, insert suffix before the tag.
    """
    slash = base_image.rfind("/")
    colon = base_image.rfind(":")
    if colon > slash:
        name = base_image[:colon]
        tag = base_image[colon + 1 :]
        return f"{name}-minisweagent:{tag}"
    return f"{base_image}-minisweagent"


def container_exists(image: str) -> bool:
    """
    Return whether a named Podman image exists in local storage.
    """
    return subprocess.run(
        ["podman", "image", "exists", image],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def ensure_minisweagent_container(base_image: str, working_path: Path) -> str:
    """
    Build a derived image that installs Python, git, and mini-swe-agent.
    """
    derived_image = derive_minisweagent_image_name(base_image)
    if container_exists(derived_image):
        return derived_image

    build_dir = working_path / "container_build" / derived_image.replace("/", "__").replace(":", "__")
    build_dir.mkdir(parents=True, exist_ok=True)
    containerfile = build_dir / "Containerfile"
    containerfile.write_text(
        f"""FROM {base_image}
USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    python3 python3-venv python3-pip git ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/minisweagent-venv && \\
    /opt/minisweagent-venv/bin/pip install --no-cache-dir mini-swe-agent==2.2.4 && \\
    ln -s /opt/minisweagent-venv/bin/mini /usr/local/bin/mini
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["podman", "build", "-f", str(containerfile), "-t", derived_image, str(build_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise EvalMiniSWEAgentError(
            f"Failed to build derived image {derived_image}: {result.stderr or result.stdout}"
        )
    return derived_image


def apply_git_diff(repo_dir: Path, diff_content: str) -> tuple[int, str]:
    """
    Apply a git diff string in a repository and capture failure output.
    """
    if not diff_content:
        return 0, ""
    result = subprocess.run(
        ["git", "apply"],
        cwd=repo_dir,
        input=diff_content,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stderr


def get_git_diff(repo_dir: Path) -> str | None:
    """
    Return the repository working-tree diff, or None if git fails.
    """
    result = subprocess.run(
        ["git", "diff"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def resolve_repo_source(
    combo: CombinedTask,
    tasks_path: Path,
    tarballs_dir: Path,
) -> tuple[Path | None, str | None]:
    """
    Resolve a local path to repo tarball for this task.
    """
    candidates: list[Path] = []
    validated_repo = (combo.validated or {}).get("repo")
    task_repo = combo.task.get("repo")

    if isinstance(validated_repo, str) and validated_repo:
        candidates.append(Path(validated_repo))
    if isinstance(task_repo, str) and task_repo:
        task_repo_path = Path(task_repo)
        candidates.append(task_repo_path)
        candidates.append(tasks_path.parent / task_repo_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve(), None

    tar_candidates: list[Path] = []
    if isinstance(task_repo, str) and task_repo:
        tar_candidates.append(tarballs_dir / Path(task_repo).name)
    if isinstance(validated_repo, str) and validated_repo:
        tar_candidates.append(tarballs_dir / Path(validated_repo).name)

    task_prefix = combo.task_id.split("/", 1)[0]
    if task_prefix.endswith(".tar"):
        tar_candidates.append(tarballs_dir / task_prefix)

    for candidate in tar_candidates:
        if candidate.exists():
            return candidate.resolve(), None

    return None, f"missing_repo_tarball_in_dir:{tarballs_dir}"


def run_minisweagent(
    repo_dir: Path,
    image: str,
    model: str,
    task_description: str,
    cost_limit: float,
    timeout_seconds: int,
    memory_limit: str | None,
) -> tuple[int, str, bool, str | None]:
    """
    Run mini-SWE-agent in a container with API credentials passed through.
    """
    openai_api_base = os.environ.get("OPENAI_API_BASE")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_base or not openai_api_key:
        raise EvalMiniSWEAgentError("OPENAI_API_BASE and OPENAI_API_KEY must be set")

    trajectory_path = repo_dir / "minisweagent_trajectory.jsonl"
    cmd = [
        "podman",
        "run",
        "--rm",
        "--workdir",
        "/repo",
        "-e",
        f"OPENAI_API_BASE={openai_api_base}",
        "-e",
        f"OPENAI_API_KEY={openai_api_key}",
        "-e",
        f"MSWEA_MODEL_NAME={model}",
        "-e",
        "MSWEA_CONFIGURED=1",
        "-e",
        "MSWEA_COST_TRACKING=ignore_errors",
        "-v",
        f"{repo_dir}:/repo:rw",
        image,
        "mini",
        "--model",
        model,
        "--task",
        task_description,
        "--yolo",
        "--cost-limit",
        str(cost_limit),
        "--exit-immediately",
        "--output",
        "/repo/minisweagent_trajectory.jsonl",
    ]
    if memory_limit:
        cmd[3:3] = ["--memory", memory_limit]

    result = bounded_run(
        cmd,
        timeout_seconds=timeout_seconds,
        max_output_size=2 * 1024 * 1024,
    )
    return result.exit_code, result.stdout + result.stderr, result.timeout, may_read(trajectory_path)


def run_tests_in_container(
    repo_dir: Path,
    image: str,
    timeout_seconds: int,
    memory_limit: str | None,
) -> tuple[int, str, bool]:
    """
    Run the benchmark test container against a prepared repository checkout.
    """
    cmd = ["podman", "run", "--rm", "--network", "none", "-v", f"{repo_dir}:/repo:rw", image]
    if memory_limit:
        cmd[3:3] = ["--memory", memory_limit]
    result = bounded_run(
        cmd,
        timeout_seconds=timeout_seconds,
        max_output_size=2 * 1024 * 1024,
    )
    return result.exit_code, result.stdout + result.stderr, result.timeout


def evaluate_one_task(
    combo: CombinedTask,
    tasks_path: Path,
    tarballs_dir: Path,
    model_working_path: Path,
    model: str,
    cost_limit: float,
    agent_timeout: int,
    test_timeout: int,
    container_memory: str | None,
) -> dict:
    """
    Evaluate one merged task row and return a structured result object.
    """
    result = {
        "task_id": combo.task_id,
        "subject": combo.task.get("subject"),
        "status": "error",
        "skip_reason": None,
        "repo_source": None,
        "base_container": None,
        "minisweagent_container": None,
        "src_diff_apply_exit_code": None,
        "src_diff_apply_stderr": None,
        "tests_diff_apply_exit_code": None,
        "tests_diff_apply_stderr": None,
        "agent_exit_code": None,
        "agent_timed_out": None,
        "agent_log": None,
        "agent_trajectory": None,
        "container_exit_code": None,
        "container_timed_out": None,
        "container_log": None,
        "git_diff": None,
        "error": None,
    }

    if combo.validated is None:
        result["status"] = "skipped"
        result["skip_reason"] = "missing_validated_row"
        return result

    container = combo.validated.get("container")
    src_diff = combo.validated.get("src.diff")
    tests_diff = combo.validated.get("tests.diff")
    task_description = combo.task.get("task_description")

    if not isinstance(container, str) or not container:
        result["status"] = "skipped"
        result["skip_reason"] = "missing_container"
        return result
    if not isinstance(src_diff, str) or not src_diff.strip():
        result["status"] = "skipped"
        result["skip_reason"] = "missing_src.diff"
        return result
    if not isinstance(tests_diff, str) or not tests_diff.strip():
        result["status"] = "skipped"
        result["skip_reason"] = "missing_tests.diff"
        return result
    if not isinstance(task_description, str) or not task_description.strip():
        result["status"] = "skipped"
        result["skip_reason"] = "missing_task_description"
        return result

    repo_source, repo_err = resolve_repo_source(combo, tasks_path, tarballs_dir)
    if repo_source is None:
        result["status"] = "skipped"
        result["skip_reason"] = repo_err
        return result
    result["repo_source"] = str(repo_source)
    result["base_container"] = container

    if not container_exists(container):
        result["status"] = "skipped"
        result["skip_reason"] = f"missing_base_container:{container}"
        return result

    try:
        mini_container = ensure_minisweagent_container(container, model_working_path)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"failed_to_prepare_minisweagent_container:{e}"
        return result
    result["minisweagent_container"] = mini_container

    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "_", combo.task_id)
    task_working_dir = model_working_path / "repos" / safe_task
    if task_working_dir.exists():
        shutil.rmtree(task_working_dir)

    with tarball_or_repo(repo_source, working_dir=task_working_dir) as repo_dir:
        repo_dir = repo_dir.resolve()

        src_exit, src_stderr = apply_git_diff(repo_dir, src_diff)
        result["src_diff_apply_exit_code"] = src_exit
        result["src_diff_apply_stderr"] = src_stderr
        if src_exit != 0:
            result["status"] = "error"
            result["error"] = f"failed_to_apply_src.diff:{src_stderr}"
            result["git_diff"] = get_git_diff(repo_dir)
            return result

        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True, check=False)
        subprocess.run(
            ["git", "commit", "-m", "apply src.diff baseline"],
            cwd=repo_dir,
            capture_output=True,
            check=False,
        )

        agent_exit, agent_log, agent_timed_out, trajectory = run_minisweagent(
            repo_dir=repo_dir,
            image=mini_container,
            model=model,
            task_description=task_description,
            cost_limit=cost_limit,
            timeout_seconds=agent_timeout,
            memory_limit=container_memory,
        )
        result["agent_exit_code"] = agent_exit
        result["agent_log"] = agent_log
        result["agent_timed_out"] = agent_timed_out
        result["agent_trajectory"] = trajectory

        tests_exit, tests_stderr = apply_git_diff(repo_dir, tests_diff)
        result["tests_diff_apply_exit_code"] = tests_exit
        result["tests_diff_apply_stderr"] = tests_stderr
        if tests_exit != 0:
            result["status"] = "error"
            result["error"] = f"failed_to_apply_tests.diff:{tests_stderr}"
            result["git_diff"] = get_git_diff(repo_dir)
            return result

        cont_exit, cont_log, cont_timed_out = run_tests_in_container(
            repo_dir=repo_dir,
            image=mini_container,
            timeout_seconds=test_timeout,
            memory_limit=container_memory,
        )
        result["container_exit_code"] = cont_exit
        result["container_log"] = cont_log
        result["container_timed_out"] = cont_timed_out
        result["git_diff"] = get_git_diff(repo_dir)

        if agent_exit == 0 and not agent_timed_out and cont_exit == 0 and not cont_timed_out:
            result["status"] = "pass"
        else:
            result["status"] = "fail"

    return result


def select_tasks(
    tasks_map: dict[str, dict],
    validated_map: dict[str, dict],
    task_prefix: str | None,
    task_ids: list[str],
    max_tasks: int | None,
) -> list[CombinedTask]:
    """
    Select task ids by explicit list or prefix and return merged task objects.
    """
    keys = list(tasks_map.keys())
    if task_ids:
        keys = [task_id for task_id in task_ids if task_id in tasks_map]
    elif task_prefix:
        keys = [task_id for task_id in keys if task_id.startswith(task_prefix)]
    keys = sorted(keys)
    if max_tasks is not None:
        keys = keys[:max_tasks]
    return [CombinedTask(task_id=k, task=tasks_map[k], validated=validated_map.get(k)) for k in keys]


def normalize_model_dir_name(model: str) -> str:
    """
    Convert a model name into the directory name used under output-directory.
    """
    if model.startswith("openai/"):
        return model[len("openai/") :]
    return model.replace("/", "__")


def cmd_run(args) -> int:
    """
    Execute task evaluation for the `run` subcommand and stream JSON output.
    """
    if not args.tasks.exists():
        raise EvalMiniSWEAgentError(f"Tasks file does not exist: {args.tasks}")
    if not args.validated_tasks.exists():
        raise EvalMiniSWEAgentError(f"Validated tasks file does not exist: {args.validated_tasks}")
    if not args.tarballs_dir.exists() or not args.tarballs_dir.is_dir():
        raise EvalMiniSWEAgentError(f"Tarballs directory does not exist: {args.tarballs_dir}")

    model_dir = args.output_directory / normalize_model_dir_name(args.model)
    results_dir = model_dir / "results"
    repos_dir = model_dir / "repos"
    container_build_dir = model_dir / "container_build"
    results_dir.mkdir(parents=True, exist_ok=True)
    repos_dir.mkdir(parents=True, exist_ok=True)
    container_build_dir.mkdir(parents=True, exist_ok=True)

    tasks_map = load_jsonl_map(args.tasks)
    validated_map = load_jsonl_map(args.validated_tasks)
    selected = select_tasks(
        tasks_map=tasks_map,
        validated_map=validated_map,
        task_prefix=args.task_prefix,
        task_ids=args.task_id,
        max_tasks=args.max_tasks,
    )
    if not selected:
        raise EvalMiniSWEAgentError("No tasks selected")

    aggregate_jsonl = model_dir / "results.jsonl"
    agg_fh = aggregate_jsonl.open("a", encoding="utf-8")

    had_failures = False
    try:
        for combo in selected:
            row = evaluate_one_task(
                combo=combo,
                tasks_path=args.tasks,
                tarballs_dir=args.tarballs_dir,
                model_working_path=model_dir,
                model=args.model,
                cost_limit=args.cost,
                agent_timeout=args.agent_timeout,
                test_timeout=args.test_timeout,
                container_memory=args.container_memory,
            )
            line = json.dumps(row, ensure_ascii=False)
            print(line)

            agg_fh.write(line + "\n")
            agg_fh.flush()

            safe_task = re.sub(r"[^A-Za-z0-9._-]+", "_", combo.task_id)
            per_task_path = results_dir / f"{safe_task}.jsonl"
            per_task_path.write_text(line + "\n", encoding="utf-8")

            if args.summary:
                status = row.get("status", "unknown").upper()
                task_id = row.get("task_id", "<unknown>")
                subject = row.get("subject") or "<missing subject>"
                detail = row.get("skip_reason") or row.get("error") or ""
                if detail:
                    detail = f" ({detail})"
                print(f"{task_id}: {subject}: {status}{detail}", file=sys.stderr)

            if row.get("status") in {"fail", "error"}:
                had_failures = True
    finally:
        agg_fh.close()

    return 1 if had_failures else 0


def _read_last_jsonl_row(path: Path) -> dict | None:
    """
    Read the last valid JSON object from a JSONL file.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _escape_md(text: str) -> str:
    """
    Escape markdown table control characters in cell content.
    """
    return text.replace("|", "\\|").replace("\n", "<br>")


def _status_cell(row: dict | None) -> str:
    """
    Render a concise markdown table cell for one task status.
    """
    if row is None:
        return "MISSING"
    status = str(row.get("status", "unknown")).upper()
    detail = row.get("skip_reason") or row.get("error")
    agent_exit = row.get("agent_exit_code")
    cont_exit = row.get("container_exit_code")
    parts = [status]
    if detail:
        parts.append(str(detail))
    codes = []
    if agent_exit is not None:
        codes.append(f"a={agent_exit}")
    if cont_exit is not None:
        codes.append(f"c={cont_exit}")
    if codes:
        parts.append(",".join(codes))
    return "<br>".join(_escape_md(p) for p in parts)


def _collect_results_by_task(results_dir: Path) -> dict[str, dict]:
    """
    Load per-task result rows from a results directory keyed by task id.
    """
    if not results_dir.exists() or not results_dir.is_dir():
        raise EvalMiniSWEAgentError(f"Results directory does not exist: {results_dir}")

    search_dirs: list[Path] = []
    nested = results_dir / "results"
    if nested.is_dir():
        search_dirs.append(nested)
    search_dirs.append(results_dir)

    out: dict[str, dict] = {}
    for d in search_dirs:
        for file in sorted(d.glob("*.jsonl")):
            row = _read_last_jsonl_row(file)
            if not isinstance(row, dict):
                continue
            task_id = row.get("task_id")
            if isinstance(task_id, str):
                out[task_id] = row
    return out


def cmd_summary(args) -> int:
    """
    Build markdown summary tables across one or more result directories.
    """
    labels = args.label or []
    if labels and len(labels) != len(args.results_dir):
        raise EvalMiniSWEAgentError("--label must appear exactly once per results directory")
    if not labels:
        labels = [d.name for d in args.results_dir]

    all_results = [_collect_results_by_task(d) for d in args.results_dir]
    task_ids: set[str] = set()
    for result_map in all_results:
        task_ids.update(result_map.keys())

    filtered_task_ids: list[str] = []
    for task_id in sorted(task_ids):
        skipped_any = False
        for result_map in all_results:
            row = result_map.get(task_id)
            if row is not None and str(row.get("status", "")).lower() == "skipped":
                skipped_any = True
                break
        if not skipped_any:
            filtered_task_ids.append(task_id)

    print("# Mini-SWE-Agent Evaluation Summary")
    print()
    print("## Per-Task Results")
    print()
    headers = ["Task ID", "Subject"] + labels
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for task_id in filtered_task_ids:
        subject = ""
        for result_map in all_results:
            row = result_map.get(task_id)
            if isinstance(row, dict) and isinstance(row.get("subject"), str):
                subject = row["subject"]
                break
        cells = [_escape_md(task_id), _escape_md(subject)] + [
            _status_cell(result_map.get(task_id)) for result_map in all_results
        ]
        print("| " + " | ".join(cells) + " |")

    print()
    print("## Overall Score")
    print()
    print("| Run | Pass | Fail | Error | Missing | Total | Score (Pass/Total) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for label, result_map in zip(labels, all_results):
        counts = Counter()
        for task_id in filtered_task_ids:
            row = result_map.get(task_id)
            if row is None:
                counts["missing"] += 1
                continue
            status = str(row.get("status", "unknown")).lower()
            if status in {"pass", "fail", "error"}:
                counts[status] += 1
            else:
                counts["error"] += 1
        total = len(filtered_task_ids)
        score_total = (counts["pass"] / total) if total else 0.0
        print(
            f"| {_escape_md(label)} | {counts['pass']} | {counts['fail']} | {counts['error']} | "
            f"{counts['missing']} | {total} | {score_total:.1%} |"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """
    Construct the command-line parser and register subcommands.
    """
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Evaluate tasks and emit per-task JSON rows")
    run_parser.add_argument("--tasks", required=True, type=Path, help="Path to tasks.jsonl")
    run_parser.add_argument(
        "--validated-tasks",
        required=True,
        type=Path,
        help="Path to validated_tasks.jsonl",
    )
    run_parser.add_argument(
        "--tarballs-dir",
        required=True,
        type=Path,
        help="Directory containing repository tarballs referenced by task rows",
    )
    run_parser.add_argument(
        "--output-directory",
        required=True,
        type=Path,
        help="Root output directory for model-specific artifacts",
    )
    run_parser.add_argument("--task-prefix", default=None, help="Only evaluate tasks with this prefix")
    run_parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Evaluate one specific task_id; can be repeated",
    )
    run_parser.add_argument(
        "--model",
        default="openai/claude-haiku-4-5",
        help="Mini-SWE-Agent model string",
    )
    run_parser.add_argument(
        "--cost",
        type=float,
        default=1.0,
        help="Cost limit in USD for mini-swe-agent (maps to mini --cost-limit, default: 1.0)",
    )
    run_parser.add_argument("--agent-timeout", type=int, default=1800, help="Agent timeout in seconds")
    run_parser.add_argument("--test-timeout", type=int, default=600, help="Test timeout in seconds")
    run_parser.add_argument(
        "--container-memory",
        default=None,
        help="Podman memory limit for each container run (e.g., 2g)",
    )
    run_parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Evaluate at most this many selected tasks",
    )
    run_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print one-line summaries to stderr while also emitting JSON lines",
    )
    run_parser.set_defaults(func=cmd_run)

    summary_parser = subparsers.add_parser(
        "summary",
        help="Summarize one or more result directories in markdown",
    )
    summary_parser.add_argument(
        "results_dir",
        nargs="+",
        type=Path,
        help="Directory containing per-task *.jsonl output files (can provide multiple)",
    )
    summary_parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Label for one results_dir (repeat once per directory; default is basename)",
    )
    summary_parser.set_defaults(func=cmd_summary)
    return parser


def main() -> int:
    """
    Parse CLI arguments and dispatch to the selected subcommand.
    """
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EvalMiniSWEAgentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

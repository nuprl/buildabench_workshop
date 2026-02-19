# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
Run the validated Build-A-Bench workflow end-to-end with checkpointed state.

This script orchestrates:
1. env_agent (build container)
2. synth_task (generate candidate tasks)
3. validate_task (produce validated task artifacts)
4. check_validated_tasks (LLM-free checks)

It is resumable: by default, it skips completed work in existing state files.
Use --force-fresh to remove state and rebuild everything from scratch.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounded_subprocess import run as bounded_run

from .agentlib import container_exists, standard_container_name


MAX_OUTPUT_SIZE = 64 * 1024 * 1024
STDIN_WRITE_TIMEOUT_SECONDS = 30


class WorkflowError(Exception):
    """Raised when a workflow step fails."""


@dataclass(frozen=True)
class WorkflowPaths:
    tasks: Path
    validated_tasks: Path
    check_results: Path
    workflow_log: Path


def _q(path_or_text: str | Path) -> str:
    return shlex.quote(str(path_or_text))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{utc_now_iso()}] {message}\n")


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """
    Read JSONL records from path.

    If the file contains an invalid trailing line (e.g., interrupted write),
    truncate the file to the last valid line so the next run can resume cleanly.
    """
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    valid_lines: list[str] = []
    parse_failed = False

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"Warning: stopping at invalid JSON in {path} line {line_num}; "
                    "truncating trailing data for resumability.",
                    file=sys.stderr,
                )
                parse_failed = True
                break
            if not isinstance(obj, dict):
                print(
                    f"Warning: ignoring non-object JSON in {path} line {line_num}.",
                    file=sys.stderr,
                )
                continue
            rows.append(obj)
            valid_lines.append(json.dumps(obj) + "\n")

    if parse_failed:
        with path.open("w", encoding="utf-8") as f:
            f.writelines(valid_lines)

    return rows


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run_shell(
    command: str,
    *,
    timeout_seconds: int,
    stdin_data: str | None = None,
    max_output_size: int = MAX_OUTPUT_SIZE,
) -> tuple[int, str, str, bool]:
    """
    Run a shell command via bounded_subprocess.

    Returns (exit_code, stdout, stderr, timed_out).
    """
    result = bounded_run(
        ["bash", "-lc", command],
        timeout_seconds=timeout_seconds,
        max_output_size=max_output_size,
        env=dict(os.environ),
        stdin_data=stdin_data,
        stdin_write_timeout=STDIN_WRITE_TIMEOUT_SECONDS if stdin_data is not None else None,
    )
    return result.exit_code, result.stdout, result.stderr, result.timeout


def run_shell_checked(
    command: str,
    *,
    timeout_seconds: int,
    log_path: Path,
    step_name: str,
    stdin_data: str | None = None,
) -> tuple[str, str]:
    append_log(log_path, f"START {step_name}: {command}")
    exit_code, stdout, stderr, timed_out = run_shell(
        command, timeout_seconds=timeout_seconds, stdin_data=stdin_data
    )
    append_log(
        log_path,
        f"END {step_name}: exit_code={exit_code} timed_out={timed_out} "
        f"stdout_bytes={len(stdout)} stderr_bytes={len(stderr)}",
    )
    if timed_out:
        raise WorkflowError(f"{step_name} timed out after {timeout_seconds} seconds")
    if exit_code != 0:
        preview = (stderr or stdout)[:2000]
        raise WorkflowError(f"{step_name} failed with exit code {exit_code}\n{preview}")
    return stdout, stderr


def parse_json_objects_from_stdout(stdout: str, *, context: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(
                f"Warning: skipping non-JSON line in {context}: {line[:160]}",
                file=sys.stderr,
            )
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def prepare_paths(state_dir: Path) -> WorkflowPaths:
    state_dir.mkdir(parents=True, exist_ok=True)
    return WorkflowPaths(
        tasks=state_dir / "tasks.jsonl",
        validated_tasks=state_dir / "validated_tasks.jsonl",
        check_results=state_dir / "check_results.jsonl",
        workflow_log=state_dir / "workflow.log",
    )


def force_fresh_reset(
    *,
    paths: WorkflowPaths,
    container: str,
    repo_root: Path,
    step_timeout: int,
) -> None:
    for path in [paths.tasks, paths.validated_tasks, paths.check_results, paths.workflow_log]:
        if path.exists():
            path.unlink()

    if container_exists(container):
        cmd = f"cd {_q(repo_root)} && podman image rm -f {_q(container)} || true"
        run_shell(
            cmd,
            timeout_seconds=max(step_timeout, 300),
        )


def ensure_container(
    *,
    repo_root: Path,
    repo_path: Path,
    env_tips_path: Path,
    agent_name: str,
    container: str,
    timeout_seconds: int,
    log_path: Path,
) -> None:
    if container_exists(container):
        append_log(log_path, f"SKIP env_agent: container {container} already exists")
        return

    cmd = (
        f"cd {_q(repo_root)} && "
        f"uv run python3 -m buildabench_workshop.env_agent "
        f"--repo {_q(repo_path)} "
        f"--tips-path {_q(env_tips_path)} "
        f"--agent {_q(agent_name)} "
        f"--container {_q(container)}"
    )
    run_shell_checked(
        cmd,
        timeout_seconds=timeout_seconds,
        log_path=log_path,
        step_name="env_agent",
    )

    if not container_exists(container):
        raise WorkflowError(
            f"env_agent completed but container {container} was not found afterward"
        )


def synthesize_tasks(
    *,
    repo_root: Path,
    repo_path: Path,
    patterns: list[str],
    model: str,
    num_candidates: int,
    flex_processing: bool,
    timeout_seconds: int,
    paths: WorkflowPaths,
) -> list[dict[str, Any]]:
    existing_tasks = read_jsonl_records(paths.tasks)
    if len(existing_tasks) >= num_candidates:
        append_log(
            paths.workflow_log,
            f"SKIP synth_task: have {len(existing_tasks)} tasks, target={num_candidates}",
        )
        return existing_tasks

    remaining = num_candidates - len(existing_tasks)
    avoid_subjects = [row["subject"] for row in existing_tasks if row.get("subject")]

    cmd_parts = [
        f"cd {_q(repo_root)} &&",
        "uv run python3 -m buildabench_workshop.synth_task",
        "--json",
        f"--num-candidates {remaining}",
        f"--model {_q(model)}",
    ]
    if flex_processing:
        cmd_parts.append("--flex-processing")
    if avoid_subjects:
        cmd_parts.append("--avoid " + " ".join(_q(s) for s in avoid_subjects))
    cmd_parts.append(_q(repo_path))
    cmd_parts.extend(_q(pat) for pat in patterns)
    cmd = " ".join(cmd_parts)

    stdout, _ = run_shell_checked(
        cmd,
        timeout_seconds=timeout_seconds,
        log_path=paths.workflow_log,
        step_name=f"synth_task(remaining={remaining})",
    )

    new_rows = parse_json_objects_from_stdout(stdout, context="synth_task stdout")
    for row in new_rows:
        append_jsonl_record(paths.tasks, row)

    all_rows = read_jsonl_records(paths.tasks)
    append_log(
        paths.workflow_log,
        f"synth_task produced {len(new_rows)} new tasks; total={len(all_rows)}",
    )
    return all_rows


def validate_tasks(
    *,
    repo_root: Path,
    validate_tips_path: Path,
    container: str,
    agent_name: str,
    timeout_seconds: int,
    paths: WorkflowPaths,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    validated_rows = read_jsonl_records(paths.validated_tasks)
    validated_ids = {row.get("task_id") for row in validated_rows if row.get("task_id")}

    for task in tasks:
        task_id = task.get("task_id")
        if not task_id:
            append_log(paths.workflow_log, "SKIP validate_task: task missing task_id")
            continue
        if task_id in validated_ids:
            append_log(paths.workflow_log, f"SKIP validate_task[{task_id}]: already done")
            continue

        cmd = (
            f"cd {_q(repo_root)} && "
            f"uv run python3 -m buildabench_workshop.validate_task "
            f"--tips-path {_q(validate_tips_path)} "
            f"--container {_q(container)} "
            f"--agent {_q(agent_name)} "
            "--input-json "
            "--output-json"
        )

        stdout, _ = run_shell_checked(
            cmd,
            timeout_seconds=timeout_seconds,
            stdin_data=json.dumps(task) + "\n",
            log_path=paths.workflow_log,
            step_name=f"validate_task[{task_id}]",
        )

        output_rows = parse_json_objects_from_stdout(stdout, context=f"validate_task[{task_id}] stdout")
        if not output_rows:
            raise WorkflowError(f"validate_task[{task_id}] produced no JSON output")

        validated = output_rows[-1]
        append_jsonl_record(paths.validated_tasks, validated)
        validated_ids.add(task_id)

    all_validated = read_jsonl_records(paths.validated_tasks)
    append_log(
        paths.workflow_log,
        f"validated tasks total={len(all_validated)}",
    )
    return all_validated


def check_validated_tasks(
    *,
    repo_root: Path,
    timeout_seconds: int,
    check_timeout_seconds: int,
    paths: WorkflowPaths,
    validated_tasks: list[dict[str, Any]],
) -> bool:
    check_rows = read_jsonl_records(paths.check_results)
    checked_ids = {row.get("task_id") for row in check_rows if row.get("task_id")}
    any_failures = any(not bool(row.get("success")) for row in check_rows)

    for task in validated_tasks:
        task_id = task.get("task_id")
        if not task_id:
            append_log(paths.workflow_log, "SKIP check_validated_tasks: validated row missing task_id")
            continue
        if task_id in checked_ids:
            append_log(paths.workflow_log, f"SKIP check_validated_tasks[{task_id}]: already checked")
            continue

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsonl",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(json.dumps(task) + "\n")
            tmp_jsonl = Path(tf.name)

        try:
            cmd = (
                f"cd {_q(repo_root)} && "
                f"uv run python3 -m buildabench_workshop.check_validated_tasks "
                f"{_q(tmp_jsonl)} "
                f"--timeout {check_timeout_seconds} "
                "--workers 1"
            )
            exit_code, stdout, stderr, timed_out = run_shell(
                cmd,
                timeout_seconds=timeout_seconds,
            )
        finally:
            tmp_jsonl.unlink(missing_ok=True)

        success = (not timed_out) and exit_code == 0
        if not success:
            any_failures = True

        append_jsonl_record(
            paths.check_results,
            {
                "task_id": task_id,
                "success": success,
                "checked_at": utc_now_iso(),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stderr_preview": (stderr or "")[:1000],
                "stdout_preview": (stdout or "")[:1000],
            },
        )
        checked_ids.add(task_id)
        append_log(paths.workflow_log, f"check_validated_tasks[{task_id}] success={success}")

    return not any_failures


def derive_default_state_dir(repo_path: Path) -> Path:
    repo_label = repo_path.stem if repo_path.is_file() else repo_path.name
    return Path("workflow_state") / repo_label


def main_with_args(
    repo_path: Path,
    patterns: list[str],
    env_tips_path: Path,
    validate_tips_path: Path,
    container: str | None,
    model: str,
    num_candidates: int,
    agent_name: str,
    state_dir: Path | None,
    force_fresh: bool,
    flex_processing: bool,
    step_timeout_seconds: int,
    check_timeout_seconds: int,
) -> int:
    repo_root = Path.cwd()
    repo_path = repo_path.absolute()
    env_tips_path = env_tips_path.absolute()
    validate_tips_path = validate_tips_path.absolute()

    if not repo_path.exists():
        print(f"Error: repository path does not exist: {repo_path}", file=sys.stderr)
        return 2
    if not env_tips_path.exists():
        print(f"Error: env tips path does not exist: {env_tips_path}", file=sys.stderr)
        return 2
    if not validate_tips_path.exists():
        print(f"Error: validate tips path does not exist: {validate_tips_path}", file=sys.stderr)
        return 2
    if num_candidates <= 0:
        print("Error: --num-candidates must be positive", file=sys.stderr)
        return 2

    if container is None:
        container = standard_container_name(repo_path)
    if state_dir is None:
        state_dir = derive_default_state_dir(repo_path)

    paths = prepare_paths(state_dir)
    append_log(paths.workflow_log, "==== run_validated_workflow start ====")
    append_log(
        paths.workflow_log,
        f"repo={repo_path} container={container} model={model} num_candidates={num_candidates}",
    )

    try:
        if force_fresh:
            append_log(paths.workflow_log, "force_fresh enabled: resetting workflow state")
            force_fresh_reset(
                paths=paths,
                container=container,
                repo_root=repo_root,
                step_timeout=step_timeout_seconds,
            )

        ensure_container(
            repo_root=repo_root,
            repo_path=repo_path,
            env_tips_path=env_tips_path,
            agent_name=agent_name,
            container=container,
            timeout_seconds=step_timeout_seconds,
            log_path=paths.workflow_log,
        )

        tasks = synthesize_tasks(
            repo_root=repo_root,
            repo_path=repo_path,
            patterns=patterns,
            model=model,
            num_candidates=num_candidates,
            flex_processing=flex_processing,
            timeout_seconds=step_timeout_seconds,
            paths=paths,
        )
        if not tasks:
            raise WorkflowError("No tasks available after synth_task step")

        validated_tasks = validate_tasks(
            repo_root=repo_root,
            validate_tips_path=validate_tips_path,
            container=container,
            agent_name=agent_name,
            timeout_seconds=step_timeout_seconds,
            paths=paths,
            tasks=tasks,
        )
        if not validated_tasks:
            raise WorkflowError("No validated tasks available after validate_task step")

        checks_ok = check_validated_tasks(
            repo_root=repo_root,
            timeout_seconds=step_timeout_seconds,
            check_timeout_seconds=check_timeout_seconds,
            paths=paths,
            validated_tasks=validated_tasks,
        )

        append_log(paths.workflow_log, "==== run_validated_workflow complete ====")
        print(f"State directory: {state_dir}")
        print(f"Tasks file: {paths.tasks}")
        print(f"Validated tasks file: {paths.validated_tasks}")
        print(f"Check results file: {paths.check_results}")
        print(f"Workflow log: {paths.workflow_log}")
        return 0 if checks_ok else 1
    except WorkflowError as e:
        append_log(paths.workflow_log, f"FAILED: {e}")
        print(f"Error: {e}", file=sys.stderr)
        print(f"Workflow log: {paths.workflow_log}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the validated Build-A-Bench workflow with resumable state. "
            "By default, completed steps are skipped."
        )
    )
    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to repository directory or tarball",
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help="File globs for synth_task context (e.g., 'src/*.py' 'tests/*.py')",
    )
    parser.add_argument(
        "--env-tips-path",
        type=Path,
        required=True,
        help="Tips file for env_agent",
    )
    parser.add_argument(
        "--validate-tips-path",
        type=Path,
        required=True,
        help="Tips file for validate_task",
    )
    parser.add_argument(
        "--container",
        type=str,
        default=None,
        help="Container name to use (default: derived from repo path)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai/gpt-5.1",
        help="Model for synth_task (default: openai/gpt-5.1)",
    )
    parser.add_argument(
        "--num-candidates",
        type=int,
        default=3,
        help="Target number of synthesized tasks (default: 3)",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default="codex",
        dest="agent_name",
        help="Agent for env_agent/validate_task (default: codex)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Directory for resumable state (default: workflow_state/<repo_name>)",
    )
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Delete state files and rebuild container before running",
    )
    parser.add_argument(
        "--flex-processing",
        action="store_true",
        help="Pass --flex-processing to synth_task",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=3600,
        help="Timeout per major step command (default: 3600)",
    )
    parser.add_argument(
        "--check-timeout-seconds",
        type=int,
        default=300,
        help="Timeout passed to check_validated_tasks --timeout (default: 300)",
    )
    args = parser.parse_args()
    return main_with_args(**vars(args))


if __name__ == "__main__":
    sys.exit(main())

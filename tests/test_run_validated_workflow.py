# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha

from pathlib import Path

import buildabench_workshop.run_validated_workflow as workflow
from buildabench_workshop.run_validated_workflow import (
    WorkflowPaths,
    append_jsonl_record,
    derive_default_state_dir,
    read_jsonl_records,
    validate_tasks,
)


def test_read_jsonl_records_recovers_trailing_invalid_line(tmp_path: Path):
    p = tmp_path / "rows.jsonl"
    p.write_text('{"a": 1}\n{"b": 2}\n{"broken":\n', encoding="utf-8")

    rows = read_jsonl_records(p)
    assert rows == [{"a": 1}, {"b": 2}]

    # File is truncated to valid JSONL rows so resumes are deterministic.
    assert p.read_text(encoding="utf-8") == '{"a": 1}\n{"b": 2}\n'


def test_append_jsonl_record_roundtrip(tmp_path: Path):
    p = tmp_path / "rows.jsonl"
    append_jsonl_record(p, {"task_id": "x/0", "ok": True})
    append_jsonl_record(p, {"task_id": "x/1", "ok": False})

    rows = read_jsonl_records(p)
    assert rows == [
        {"task_id": "x/0", "ok": True},
        {"task_id": "x/1", "ok": False},
    ]


def test_derive_default_state_dir_for_repo_dir(tmp_path: Path):
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    state = derive_default_state_dir(repo_dir)
    assert state == Path("workflow_state") / "my_repo"


def test_derive_default_state_dir_for_tarball(tmp_path: Path):
    tar_path = tmp_path / "my_repo.tar"
    tar_path.write_bytes(b"")
    state = derive_default_state_dir(tar_path)
    assert state == Path("workflow_state") / "my_repo"


def test_validate_tasks_passes_container_to_validate_task(monkeypatch, tmp_path: Path):
    tips_path = tmp_path / "validate_tips.txt"
    tips_path.write_text("No tips yet\n", encoding="utf-8")

    paths = WorkflowPaths(
        tasks=tmp_path / "tasks.jsonl",
        validated_tasks=tmp_path / "validated_tasks.jsonl",
        check_results=tmp_path / "check_results.jsonl",
        workflow_log=tmp_path / "workflow.log",
    )

    commands: list[str] = []

    def fake_run_shell_checked(command: str, **kwargs):
        commands.append(command)
        return (
            '{"task_id":"fixture/0","repo":"/tmp/repo.tar","container":"env_agent__fixture","src.diff":"a","tests.diff":"b"}\n',
            "",
        )

    monkeypatch.setattr(workflow, "run_shell_checked", fake_run_shell_checked)

    validated = validate_tasks(
        repo_root=tmp_path,
        validate_tips_path=tips_path,
        container="env_agent__fixture",
        agent_name="codex",
        timeout_seconds=30,
        paths=paths,
        tasks=[
            {
                "task_id": "fixture/0",
                "repo": "/tmp/repo.tar",
                "subject": "x",
                "task_description": "y",
                "patches": "z",
            }
        ],
    )

    assert len(validated) == 1
    assert commands
    assert "--container env_agent__fixture" in commands[0]

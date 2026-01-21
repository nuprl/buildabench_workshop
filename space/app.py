"""
Gradio app to view tasks.jsonl and validated_tasks.jsonl side by side.

Run with:
  uv --with gradio python -m buildabench_workshop.view_tasks_gradio tasks.jsonl validated_tasks.jsonl
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a task by removing unwanted fields and cleaning structure."""
    # Fields to remove completely
    fields_to_remove = {
        "matching_files",
        "commit_sha",
        "task_id",
        "repo",
        "log",
        "tips",
        "container",
    }
    
    def clean_dict(obj: Any) -> Any:
        """Recursively clean dictionaries."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                # Skip unwanted top-level fields
                if key in fields_to_remove:
                    continue
                # Skip nested fields with these names
                if "matching_files" in key or "log" in key or "tips" in key or "container" in key:
                    continue
                result[key] = clean_dict(value)
            return result
        elif isinstance(obj, list):
            return [clean_dict(item) for item in obj]
        else:
            return obj
    
    return clean_dict(task)


def load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file and return a list of normalized dictionaries."""
    data = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                task = json.loads(line)
                # Normalize the task (removes unwanted fields)
                task = normalize_task(task)
                data.append(task)
    return data


def load_and_join_tasks(tasks_path: Path, validated_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load both files, normalize, and join by task_id. Returns clean structure."""
    result: Dict[str, Dict[str, Any]] = {}
    
    # Load tasks.jsonl
    with open(tasks_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                task = json.loads(line)
                task_id = task["task_id"]
                normalized = normalize_task(task)
                # Preserve repo separately for descriptions
                repo = task["repo"]
                result[task_id] = {"task": normalized, "validated": None, "_repo": repo}
    
    # Load validated_tasks.jsonl
    with open(validated_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                validated = json.loads(line)
                task_id = validated["task_id"]
                normalized = normalize_task(validated)
                if task_id not in result:
                    repo = validated["repo"]
                    result[task_id] = {"task": None, "validated": normalized, "_repo": repo}
                else:
                    # Preserve repo if not already set
                    if "_repo" not in result[task_id] or not result[task_id]["_repo"]:
                        repo = validated["repo"]
                        result[task_id]["_repo"] = repo
                    result[task_id]["validated"] = normalized
    
    return result


def build_paths(
    data: Any,
    prefix: str = "",
    max_list_items: int = 5,
    paths: Optional[Set[str]] = None,
) -> Set[str]:
    """Collect dotted paths for dict/list structures."""
    if paths is None:
        paths = set()

    if isinstance(data, dict):
        for key in sorted(data.keys()):
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            build_paths(data[key], path, max_list_items=max_list_items, paths=paths)
    elif isinstance(data, list):
        for i, item in enumerate(data[:max_list_items]):
            path = f"{prefix}[{i}]" if prefix else f"[{i}]"
            paths.add(path)
            build_paths(item, path, max_list_items=max_list_items, paths=paths)

    return paths


def get_value_by_path(obj: Any, path: str) -> Any:
    """Get a value from nested dict/list using dot notation with [idx]."""
    if obj is None:
        return None
    if isinstance(obj, dict) and path in obj:
        return obj[path]

    parts = path.split(".") if path else []
    current = obj

    for i, part in enumerate(parts):
        if not isinstance(current, (dict, list)):
            return None

        # If a full remaining path exists as a key, return it
        if isinstance(current, dict) and part not in current:
            remaining = ".".join(parts[i:])
            if remaining in current:
                return current[remaining]

        key = part.split("[", 1)[0] if "[" in part else part
        if key:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]

        # Handle list indexes like key[0][1]
        indices: List[int] = []
        start = 0
        while True:
            open_idx = part.find("[", start)
            if open_idx == -1:
                break
            close_idx = part.find("]", open_idx)
            if close_idx == -1:
                break
            idx_str = part[open_idx + 1 : close_idx]
            if idx_str.isdigit():
                indices.append(int(idx_str))
            start = close_idx + 1

        for idx in indices:
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]

    return current


def format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def diff_values(left: Any, right: Any) -> str:
    left_text = format_value(left).splitlines()
    right_text = format_value(right).splitlines()
    diff = difflib.unified_diff(
        left_text,
        right_text,
        fromfile="task",
        tofile="validated",
        lineterm="",
    )
    return "\n".join(diff) if left is not None or right is not None else ""


def build_tree_items(
    data: Any,
    prefix: str = "",
    max_list_items: int = 5,
    items: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[str, str]]:
    """Build tree items as (path, display_label) tuples, matching view_tasks.py structure."""
    if items is None:
        items = []

    if isinstance(data, dict):
        for key in sorted(data.keys()):
            path = f"{prefix}.{key}" if prefix else key
            display = key

            # Special handling for 'task' and 'validated' wrappers - show their children directly
            if key in ("task", "validated"):
                value = data[key]
                if value and isinstance(value, dict):
                    wrapper_path = path
                    for sub_key in sorted(value.keys()):
                        sub_path = f"{wrapper_path}.{sub_key}"
                        sub_value = value[sub_key]

                        # For matching_files, flatten it
                        if sub_key == "matching_files" and isinstance(sub_value, list):
                            items.append((sub_path, sub_key))
                            continue

                        has_children = isinstance(sub_value, dict) and not isinstance(sub_value, list)
                        items.append((sub_path, sub_key))
                        if has_children:
                            build_tree_items(sub_value, sub_path, max_list_items=max_list_items, items=items)
                        elif isinstance(sub_value, list) and len(sub_value) > 0:
                            for i, item in enumerate(sub_value[:max_list_items]):
                                item_path = f"{sub_path}[{i}]"
                                items.append((item_path, f"[{i}]"))
                                if isinstance(item, (dict, list)):
                                    build_tree_items(item, item_path, max_list_items=max_list_items, items=items)
                            if len(sub_value) > max_list_items:
                                items.append((f"{sub_path}[...]", f"[... ({len(sub_value)} total)]"))
                continue

            # For matching_files, flatten it
            if key == "matching_files" and isinstance(data[key], list):
                items.append((path, display))
                continue

            value = data[key]
            has_children = isinstance(value, dict) and not isinstance(value, list)
            items.append((path, display))
            if has_children:
                build_tree_items(value, path, max_list_items=max_list_items, items=items)
            elif isinstance(value, list) and len(value) > 0:
                for i, item in enumerate(value[:max_list_items]):
                    item_path = f"{path}[{i}]"
                    items.append((item_path, f"[{i}]"))
                    if isinstance(item, (dict, list)):
                        build_tree_items(item, item_path, max_list_items=max_list_items, items=items)
                if len(value) > max_list_items:
                    items.append((f"{path}[...]", f"[... ({len(value)} total)]"))
    elif isinstance(data, list):
        for i, item in enumerate(data[:max_list_items]):
            path = f"{prefix}[{i}]" if prefix else f"[{i}]"
            items.append((path, f"[{i}]"))
            if isinstance(item, (dict, list)):
                build_tree_items(item, path, max_list_items=max_list_items, items=items)
        if len(data) > max_list_items:
            items.append((f"{prefix}[...]", f"[... ({len(data)} total)]"))

    return items


def build_app(joined_data: Dict[str, Dict[str, Any]]):
    import gradio as gr

    task_ids = sorted(joined_data.keys())

    def get_field_paths(task_id: str) -> List[str]:
        """Get list of field paths to display, in order."""
        entry = joined_data[task_id]
        
        # Get all paths
        paths: Set[str] = set()
        if entry["task"] is not None:
            paths |= build_paths(entry["task"])
        if entry["validated"] is not None:
            paths |= build_paths(entry["validated"])
        
        # Filter out unwanted fields
        filtered_paths = []
        reasoning_path = None
        
        for path in sorted(paths):
            path_parts = path.split(".")
            # Skip log, repo, tips, container, matching_files, commit_message, commit_sha, task_id, patches, subject
            if ("log" in path_parts or "repo" in path_parts or 
                "tips" in path_parts or "container" in path_parts or 
                "matching_files" in path_parts or 
                path == "commit_message" or path == "commit_sha" or path == "task_id" or
                path == "patches" or path == "subject"):
                continue
            
            # Track reasoning separately to put it last
            if path == "reasoning" or path.endswith(".reasoning"):
                reasoning_path = path
                continue
            
            filtered_paths.append(path)
        
        # Put task_description first
        if "task_description" in filtered_paths:
            filtered_paths.remove("task_description")
            filtered_paths.insert(0, "task_description")
        
        # Add reasoning last
        if reasoning_path:
            filtered_paths.append(reasoning_path)
        
        return filtered_paths

    def render_field(task_id: str, path: str) -> str:
        """Render a single field's content as markdown."""
        entry = joined_data[task_id]
        
        task_value = get_value_by_path(entry["task"], path)
        validated_value = get_value_by_path(entry["validated"], path)
        
        # Skip if both values are None
        if task_value is None and validated_value is None:
            return ""
        
        # Determine if this is a code field (diff or patch)
        is_diff = path.endswith(".diff") or "diff" in path.lower()
        is_patch = "patch" in path.lower() and not is_diff
        is_code_field = is_diff or is_patch
        
        # Check if values are identical
        task_str = format_value(task_value) if task_value is not None else None
        validated_str = format_value(validated_value) if validated_value is not None else None
        values_identical = task_str == validated_str
        
        lines = []
        
        # Special handling for task_description - plain text (no code fences)
        if path == "task_description":
            if task_value is not None:
                return task_str
            return ""
        
        # If values are identical, show only once
        if values_identical and task_value is not None:
            value_str = task_str
            if is_code_field:
                if is_diff:
                    lines.append(f"```diff\n{value_str}\n```")
                else:  # patch
                    lines.append(f"```patch\n{value_str}\n```")
            else:
                lines.append(value_str)
        else:
            # Values differ or one is missing - show both
            if task_value is not None:
                value_str = task_str
                if is_code_field:
                    if is_diff:
                        lines.append(f"```diff\n{value_str}\n```")
                    else:  # patch
                        lines.append(f"```patch\n{value_str}\n```")
                else:
                    lines.append(value_str)
            
            if validated_value is not None:
                value_str = validated_str
                if is_code_field:
                    if is_diff:
                        lines.append(f"```diff\n{value_str}\n```")
                    else:  # patch
                        lines.append(f"```patch\n{value_str}\n```")
                else:
                    lines.append(value_str)
            
            # Show diff if both values exist and differ
            if task_value is not None and validated_value is not None:
                diff_text = diff_values(task_value, validated_value)
                if diff_text.strip():
                    lines.append(f"```diff\n{diff_text}\n```")
        
        return "\n\n".join(lines)

    def extract_repo_from_task_id(task_id: str) -> str:
        """Extract and format repo from task_id.
        
        Example: "JuliaORNL#JACC.jl.tar/0" -> "JuliaORNL/JACC.jl"
        """
        try:
            # Split by "/" to get the part before the number
            parts = task_id.split("/")
            if len(parts) < 2:
                return task_id
            
            repo_part = parts[0]  # "JuliaORNL#JACC.jl.tar"
            # Split by # to separate org and repo name
            org_repo = repo_part.split("#")
            if len(org_repo) < 2:
                return task_id
            
            org = org_repo[0]  # "JuliaORNL"
            repo_with_ext = org_repo[1]  # "JACC.jl.tar"
            # Split by . and take first two parts (JACC.jl)
            repo_parts = repo_with_ext.split(".")
            if len(repo_parts) >= 2:
                repo_name = f"{repo_parts[0]}.{repo_parts[1]}"  # "JACC.jl"
                return f"{org}/{repo_name}"  # "JuliaORNL/JACC.jl"
            # Fallback: just use the repo_with_ext
            return f"{org}/{repo_with_ext}"
        except Exception:
            # If parsing fails, return task_id as-is
            return task_id

    def get_task_display_name(task_id: str) -> str:
        """Get display name for task including formatted repo and subject."""
        entry = joined_data[task_id]
        subject = None
        
        # Try to get subject from task (tasks.jsonl has "subject" field)
        if entry["task"] is not None and "subject" in entry["task"]:
            subject = entry["task"]["subject"]
        elif entry["validated"] is not None and "subject" in entry["validated"]:
            subject = entry["validated"]["subject"]
        
        # Extract and format repo from task_id
        repo = extract_repo_from_task_id(task_id)
        
        if subject:
            # Extract first line if it's multi-line
            subject_line = subject.split("\n")[0].strip()
            return f"{repo} - {subject_line}"
        return repo

    def get_field_description(task_id: str, path: str) -> str:
        """Get description text for a field."""
        entry = joined_data[task_id]
        
        if path == "task_description":
            return "*This is the prompt to the agent, asking it to implement an existing feature in the repository.*"
        elif path == "src.diff":
            return "*This patch removes the feature from the repository. The goal is to ensure the repo is in a working state.*"
        elif path == "tests.diff":
            return "*This patch adds tests for the feature to the repository. After the agent solves the task, we run these tests to see if it did it correctly*"
        elif path == "reasoning":
            return "*This is the model's reasoning for why this is a good task and how to do it. It's for debugging.*"
        return ""

    def update_task(task_id: str):
        """Update UI when task changes. Returns content for all field tabs."""
        if not task_id:
            return [""] * len(field_paths)
        
        return [render_field(task_id, path) for path in field_paths]

    # Create dropdown choices with display names
    task_choices = [(get_task_display_name(tid), tid) for tid in task_ids]
    
    # Get field paths from first task (or collect from all tasks)
    field_paths = []
    if task_ids:
        # Get fields from first task
        field_paths = get_field_paths(task_ids[0])
        # Also check other tasks to get all possible fields
        all_paths = set(field_paths)
        for tid in task_ids[1:]:
            all_paths.update(get_field_paths(tid))
        # Sort and maintain order: task_description first, reasoning last
        field_paths = []
        if "task_description" in all_paths:
            field_paths.append("task_description")
        for path in sorted(all_paths):
            if path not in ["task_description", "reasoning"]:
                field_paths.append(path)
        if "reasoning" in all_paths:
            field_paths.append("reasoning")

    with gr.Blocks(title="Task Viewer") as demo:
        gr.Markdown("# Task Viewer")
        
        # Task dropdown at the top
        task_list = gr.Dropdown(
            label="Task",
            choices=task_choices,
            value=task_ids[0] if task_ids else None,
            interactive=True,
        )

        # Tabs for each field
        if field_paths:
            with gr.Tabs() as field_tabs:
                field_components = []
                description_components = []
                for path in field_paths:
                    with gr.Tab(path):
                        # Description component (will be updated dynamically)
                        desc_comp = gr.Markdown(value="")
                        description_components.append(desc_comp)
                        # Use Markdown for all fields (supports code fences)
                        comp = gr.Markdown(value="")
                        field_components.append(comp)
        else:
            gr.Markdown("No fields available")
            field_components = []
            description_components = []

        def update_task_with_descriptions(task_id: str):
            """Update UI when task changes. Returns content for all field tabs and descriptions."""
            if not task_id:
                field_contents = [""] * len(field_paths)
                descriptions = [""] * len(field_paths)
            else:
                field_contents = [render_field(task_id, path) for path in field_paths]
                descriptions = [get_field_description(task_id, path) for path in field_paths]
            return field_contents + descriptions

        # Initialize with first task on load
        def on_load():
            if task_ids and field_components:
                return update_task_with_descriptions(task_ids[0])
            return [""] * (len(field_components) + len(description_components))

        if field_components:
            all_outputs = field_components + description_components
            demo.load(on_load, outputs=all_outputs)

            # Update fields and descriptions when task changes
            task_list.change(
                update_task_with_descriptions,
                inputs=[task_list],
                outputs=all_outputs,
            )

    return demo


def main() -> None:
    parser = argparse.ArgumentParser(description="View tasks.jsonl and validated_tasks.jsonl in Gradio")
    parser.add_argument("tasks", type=Path, help="Path to tasks.jsonl file")
    parser.add_argument("validated", type=Path, help="Path to validated_tasks.jsonl file")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host for web server")
    parser.add_argument("--port", type=int, default=7860, help="Port for web server")
    args = parser.parse_args()

    tasks_path = args.tasks.resolve()
    validated_path = args.validated.resolve()

    if not tasks_path.exists():
        raise SystemExit(f"Error: {tasks_path} not found")
    if not validated_path.exists():
        raise SystemExit(f"Error: {validated_path} not found")

    joined_data = load_and_join_tasks(tasks_path, validated_path)

    app = build_app(joined_data)
    app.launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()

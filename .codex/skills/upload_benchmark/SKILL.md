---
name: upload_benchmark
description: Add benchmark items to Hugging Face Spaces
---

We have a Hugging Face Space that host a viewer for benchmarks created
with BuildABench Workshop. After we generate new benchmarks with
`benchmark_workflow.py`, we can add them to this viewer as follows.


## Required Inputs

A directory (OUTDIR) that has the output of `benchmark_workflow.py`.

## Procedure

### 1. Schema check

*Abort if this check fails.* 

- OUTDIR must contain the files `tasks.jsonl`, `validated_tasks.jsonl`, and
  `env_agent.jsonl`
- `tasks.jsonl`: each row must contain:
  - `task_id`, `repo`, `commit_sha`, `subject`, `task_description`,
    `patches`, `reasoning`, `matching_files`
- `validated_tasks.jsonl`: each row must contain:
  - `task_id`, `repo`, `commit_message`, `container`, `log`, `tips`,
    `src.diff`, `tests.diff`
- `env_agent.jsonl`: each row must contain:
  - `repo`, `container`, `docker_image_hash`, `dockerfile`, `log`,
    `tips`
- `task_id` format in both task files: `<name>/<n>`
  - `<name>`: non-empty prefix string
  - `<n>`: non-negative integer suffix
- `task_id`s are unique, and there is a 1-1 mapping between rows
  in `tasks.jsonl` and `validated_tasks.jsonl` using `task_id`.

### 2. Download Files from Space

Create a temporary directory, WORK, and download the existing files from
the Space:

```bash
uvx hf download --repo-type=space --local-dir="$WORK/space" arjunguha/buildabench-workshop tasks.jsonl validated_tasks.jsonl envs.jsonl
```

### 3. Merge Data

The goal is to append the files from OUTDIR to the three files downloaded to
$WORK/space, while ensuring that the appended file does not have any duplicated
`task_id`s. Recall that `task_ids` are structured as `<name>/<n>`. If OUTDIR
has tasks with the same `task_id` as those already in the space, there are
possible outcomes, governed by the user:

1. Skip the duplicate task in OUTDIR
2. Renumber task in OUTDIR to `<name>/<n_max+1>`, where `<n_max>` is the
   highest numbered task with the prefix `<name>` in WORK/space.

### 4. Verify Locally

Before upload, verify locally that the updated files in $WORK/space follow
the schema above.

### 5. Upload to Space

Run these commands serially to avoid a race:

```bash
uvx hf upload --repo-type=space "$SPACE_ID" "$WORK/space/tasks.jsonl" tasks.jsonl
uvx hf upload --repo-type=space "$SPACE_ID" "$WORK/space/validated_tasks.jsonl" validated_tasks.jsonl
uvx hf upload --repo-type=space "$SPACE_ID" "$WORK/space/envs.jsonl" envs.jsonl
```

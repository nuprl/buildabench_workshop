# AGENTS.md

## BOA + LiteLLM Runbook (Scheme Interpreter CI Path)

This runbook documents how to run the Scheme interpreter synth/eval flow against a BOA-hosted vLLM model via LiteLLM, while ensuring BOA only has one active vLLM server.

### 1. Verify BOA access and current vLLM state

Check BOA server alias from MCP, then inspect running vLLM processes and listeners:

```bash
# On BOA
ps -eo pid,ppid,cmd | grep -E 'vllm serve|VLLM::EngineCore' | grep -v grep
ss -ltnp | grep -E ':8000|:8005|:8080' || true
```

Goal: exactly one active vLLM server listener.

### 2. Ensure there is at most one vLLM server on BOA

If multiple vLLM instances exist, stop extras. Keep only the intended `boa` server.

```bash
# On BOA (example: stop old qwen3_coder_30b_a3b_instruct_fp8 launch)
pkill -f 'vllm serve ~arjun/models/qwen3_coder_30b_a3b_instruct_fp8' || true
```

Re-check:

```bash
ps -eo pid,ppid,cmd | grep -E 'vllm serve|VLLM::EngineCore' | grep -v grep
ss -ltnp | grep -E ':8000|:8005|:8080' || true
```

### 3. Pick the BOA port/model naming that LiteLLM expects

For `litellm.guha-anderson.com` routing to model group `boa`, use:

- `--served-model-name boa`
- `--host 0.0.0.0`
- `--port 8000`
- `--enable-auto-tool-choice`
- `--tool-call-parser qwen3_xml`
- `--enable-prefix-caching`

(Using port `8080` worked for direct VPN calls but did not route through LiteLLM in our validated run.)

### 4. Launch vLLM on BOA in background with log file

Use `nohup` and a dedicated log:

```bash
# On BOA
nohup env CUDA_VISIBLE_DEVICES=3 \
  uvx --from vllm==0.15.0 vllm serve ~arjun/models/qwen3_coder_30b_a3b_instruct_fp8 \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name boa \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  --enable-prefix-caching \
  > ~/vllm_boa_8000.log 2>&1 < /dev/null &
```

Wait for readiness:

```bash
# On BOA
curl -s --max-time 3 http://localhost:8000/v1/models
tail -n 120 ~/vllm_boa_8000.log
```

### 5. Verify connectivity from this host

Direct VPN path (fallback):

```bash
curl -sS --max-time 8 http://192.168.50.7:8000/v1/models
```

LiteLLM path (preferred for this workflow):

```bash
curl -sS --max-time 20 https://litellm.guha-anderson.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"boa","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":8}'
```

### 6. Run full validated workflow from this repo

Always run against an isolated working copy so the checked-in fixture under
`test_projects/` stays unchanged:

```bash
WORK_REPO=$(mktemp -d /tmp/scheme_interpreter_work.XXXXXX)
cp -a test_projects/scheme_interpreter/. "$WORK_REPO/"
git -C "$WORK_REPO" init
git -C "$WORK_REPO" config user.name "workflow"
git -C "$WORK_REPO" config user.email "workflow@example.invalid"
git -C "$WORK_REPO" add .
git -C "$WORK_REPO" commit -m "Initial scheme interpreter fixture snapshot"
```

Then use the packaged resumable runner:

```bash
OPENAI_API_BASE=https://litellm.guha-anderson.com \
OPENAI_API_KEY=$OPENAI_API_KEY \
uv run python3 -m buildabench_workshop.run_validated_workflow \
  --env-tips-path "$WORK_REPO/env_agent_tips.txt" \
  --validate-tips-path "$WORK_REPO/validate_task_tips.txt" \
  --agent codex \
  --model openai/boa \
  --num-candidates 3 \
  --state-dir workflow_state/scheme_interpreter \
  "$WORK_REPO" \
  "src/scheme_interpreter/*.py" \
  "tests/*.py"
```

What resume means in this script:

1. If container already exists, it skips `env_agent`.
2. If `tasks.jsonl` already has `N` tasks, it only asks `synth_task` for the remaining `num_candidates - N`.
3. If `validated_tasks.jsonl` already contains some task IDs, it only runs `validate_task` for missing IDs.
4. If `check_results.jsonl` already contains checked task IDs, it only runs `check_validated_tasks` for unchecked IDs.

Force a fresh run:

```bash
OPENAI_API_BASE=https://litellm.guha-anderson.com \
OPENAI_API_KEY=$OPENAI_API_KEY \
uv run python3 -m buildabench_workshop.run_validated_workflow \
  --force-fresh \
  --env-tips-path "$WORK_REPO/env_agent_tips.txt" \
  --validate-tips-path "$WORK_REPO/validate_task_tips.txt" \
  --agent codex \
  --model openai/boa \
  --num-candidates 3 \
  --state-dir workflow_state/scheme_interpreter \
  "$WORK_REPO" \
  "src/scheme_interpreter/*.py" \
  "tests/*.py"
```

State files created in `--state-dir`:

1. `tasks.jsonl`
2. `validated_tasks.jsonl`
3. `check_results.jsonl`
4. `workflow.log`

Cleanup:

```bash
rm -rf "$WORK_REPO"
```

### 7. Manual validated workflow (human step-by-step)

If you want to run the pipeline manually and inspect each stage:

```bash
export OPENAI_API_BASE=https://litellm.guha-anderson.com
export OPENAI_API_KEY=$OPENAI_API_KEY
export CONTAINER_NAME=env_agent__scheme_interpreter_manual
export WORK_REPO=$(mktemp -d /tmp/scheme_interpreter_manual.XXXXXX)
cp -a test_projects/scheme_interpreter/. "$WORK_REPO/"
git -C "$WORK_REPO" init
git -C "$WORK_REPO" config user.name "workflow"
git -C "$WORK_REPO" config user.email "workflow@example.invalid"
git -C "$WORK_REPO" add .
git -C "$WORK_REPO" commit -m "Initial scheme interpreter fixture snapshot"
```

1. Build container only if missing:

```bash
podman image exists "$CONTAINER_NAME" || \
uv run python3 -m buildabench_workshop.env_agent \
  --repo "$WORK_REPO" \
  --tips-path "$WORK_REPO/env_agent_tips.txt" \
  --agent codex \
  --container "$CONTAINER_NAME"
```

2. Synthesize tasks:

```bash
uv run python3 -m buildabench_workshop.synth_task \
  --json \
  --num-candidates 3 \
  --model openai/boa \
  "$WORK_REPO" \
  "src/scheme_interpreter/*.py" \
  "tests/*.py" \
  > tasks.jsonl
```

3. Validate tasks sequentially:

```bash
start=$(( $(wc -l < validated_tasks.jsonl 2>/dev/null || echo 0) + 1 ))
end=$(wc -l < tasks.jsonl)
for i in $(seq "$start" "$end"); do
  sed -n "${i}p" tasks.jsonl | \
    uv run python3 -m buildabench_workshop.validate_task \
      --tips-path "$WORK_REPO/validate_task_tips.txt" \
      --agent codex \
      --input-json \
      --output-json \
      >> validated_tasks.jsonl
done
```

4. LLM-free validation:

```bash
uv run python3 -m buildabench_workshop.check_validated_tasks validated_tasks.jsonl
```

5. Cleanup the isolated repo and container:

```bash
podman image rm -f "$CONTAINER_NAME" || true
rm -rf "$WORK_REPO"
```
### 8. Quick diagnostics if LiteLLM fails for `model=boa`

1. Confirm BOA server is on `:8000` and model name is exactly `boa`.
2. Confirm only one BOA vLLM server is running.
3. Validate BOA local endpoint:
   - `curl http://localhost:8000/v1/models`
4. Validate from this host over VPN:
   - `curl http://192.168.50.7:8000/v1/models`
5. Retry LiteLLM completion call with `model=boa`.

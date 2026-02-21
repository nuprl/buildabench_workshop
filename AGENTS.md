## Creating A Benchmark

To create benchmark tasks for a repositoy end-to-end, use

```bash
uv run -m buildabench_workshop.benchmark_workflow
```

Use `--help` to see all the options. Tthe script can take several minutes
to run. If anything breaks, do not try to workaround or debug the issue
unless explicitly asked to do so.


The most common bit of preprocessing you may need to do is determine the glob
patterns argument to `benchmark_workflow.` To do this, either clone or browse
the repository yourself, and based on the primary programming langauge,
construct a glob patterns that include the sources files but excludes the test
files, and use these patterns as arguments to the `benchmark_workflow` script.
If you clone the repository, you should remove your clone after you're done,
and let the script create its own clone.

## Using a Boa-Hosted Model

If the OPENAI_API_BASE environment variable is set to https://litellm.guha-anderson.com, you can use
the model `openai/boa`. First, see if the model is reachable:

```bash
curl $OPENAI_API_BASE/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{ "model": "boa", "messages": [ {"role": "user", "content": "What is the capital of France?"} ] }'
```

If this works, you can run the workflow and don't need to do anything else in this section.

If not, you can start a model on Boa using the SSH MCP server. Follow these steps. Run
the commands below using the MCP server on Boa, and not on the local machine.

1. Check that the SSH MCP server is connected to Boa. Stop if it is not.

2. Check what GPUs are available with `nvidia-smi`. Stop if none are avaiable.

3. Start a VLLM server on that free GPU. I recommend using the model and
and command template below. The server runs in the background, so that the
MCP server doesn't block indefinitely. However, it sends it output to a
log file so that you can monitor:

```bash
nohup env CUDA_VISIBLE_DEVICES=GPU_ID \
  uvx --from vllm==0.15.0 vllm serve ~arjun/models/qwen3_coder_30b_a3b_instruct_fp8 \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name boa \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  > ~/vllm_boa_8000.log 2>&1 < /dev/null &
```

4. Monitor the log / port until the server is ready.

When the main task is done, teardown the server with `pkill` or `kill`.
vLLM creates lots of processes, so verify that the server is no longer on
the GPU by checking `nvidia-smi`.

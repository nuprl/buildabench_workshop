# Build-A-Bench Workshop 🧸

## Introduction

Build-A-Bench Workshop is a variation of [SWE-Smith], and I recommend at least
skimming that paper before reading what's below.

The opportunity here is that models and agents have come a long way since the
SWE-Smith work was done in early 2025. SWE-Smith used Claude Sonnet 3.7 and
the home-grown SWE-Agent. We now have better models and commercially-supported
agents such as Claude Code. A limitation of SWE-Smith that we seek to address
that it produces fairly small issues. Table 1 of the SWE-Smith paper shows that the
median gold bugfix is 3--24 lines, depending on the technique used to
synthesize bugs. We have the opportunity to push before bugs: *can we get models
to synthesize programming tasks that involve reimplementing existing features*?

Finally, I think a SWE-Smith style approach is necessary when working with a
low-resource language. I think the SWE-Bench approach does not scale to
low-resource languages, and I have a stalled project that shows why (See
`arjun/julia_bench`). ([TODO] I should explain the failure in that directory.)

There is a modicum of Julia-specific code in this project. But, the core
idea is language-agnostic. In fact, I have applied Build-A-Bench to an
[old Rust project](https://github.com/arjunguha/ilvm) of mine to verify that
it works.

## The Build-A-Bench Workflow

Build-A-Bench provides a set of agents that work together to create synthetic,
but realistic tasks in a repository. We use a combination of off-the-shelf coding
agents, such as Claude Code or Codex, as well as agents hand-written in DSPy.
If you haven't used off-the-shelf agents, you should know that they support
non-interactive use, e.g., so that an agent can review code in a CI workflow.
Our use of off-the-shelf agents is just a little more sophisticated. In principle,
it is possible to run Build-A-Bench end-to-end without any human intervention.
However, when the goal is to build a benchmark, it is important to look at the
generated data at each step.

At a high-level, Build-A-Bench uses the following three agents. First, we have a
state-of-the-art coding agent synthesize a Dockerfile that we can use to run the
tests for that repository. The agent takes care of identifying dependencies,
running tests to check that it works, and so on. Second, we have a agent
synthesize a set of challenging tasks to do in the repository. To ensure the
tasks are realistic, we will ask the LLM to remove a feature from the
repository, and create a task that involves adding the feature back. Each task
will have a problem statement and two patches: one that removes the feature
(including tests), and a patch that introduces tests for the feature. Finally,
we have an agent use the container from the first step to verify that every
synthesized task behaves as expected. For example, the tests that target the
feature should fail when the feature is removed, but pass when it is added back.
It is likely that we will encounter some small errors during this step. But,
we’ll have the agent cleanup the patches if it can.

It took me 3-4 days to design this workflow and the prompts needed for each step
to work. The best way to understand the technical approach is to read the
prompts linked below.

First, read
[env_agent.py](https://github.com/nuprl/prl_ml/blob/main/buildabench_workshop/src/buildabench_workshop/env_agent.py#L28).
Contrast this task to the much simpler task that SWE-Smith uses to install
packages (Figure 10). The SWE-Smith task has the agent install the repository
and run the tests, and just report back on what it did. The researchers then
manually construct a Dockerfile for the repository from the agent’s execution
trace. In contrast, we ask the agent to create Dockerfile directly, configure it
to load the code from the, and install dependencies within the container. These
last two requirements are difficult to satisfy simultaneously. See the prompt
for more information.

Second, read
[synth_task.py](https://github.com/nuprl/prl_ml/blob/main/buildabench_workshop/src/buildabench_workshop/synth_task.py#L46).
This was the hardest prompt to get right, and is very different from what
SWE-Smith does in several ways. SWE-Smith uses an LLM to synthesize tasks in
three ways. The simplest task it gives the model is to introduce a subtle bug in
a target function. Another task has the model reimplement existing functions
from just their signature and context; the hope is that the model will not do so
correctly. The final task has the model undo an old pull request onto the
current repository head. Our approach is quite different: the task that we give
the model asks it to come up with a feature to add to the program by backward
reasoning: we ask the model to remove an existing feature. We have additional
requirements, e.g., that the feature should be testable, that it should be
cross-cutting, and so on. These should probably be ablated at some point. In
addition, to avoid resampling the same feature repeatedly, we accumulate a list
of completed tasks on the repository that the model should not generate. This
trick is obvious in hindsight, but I have not seen it elsewhere. I'd be
surprised if it hasn't been used by others. Notice that this agent produces a
patch in the Aider search/replace format, and not a git diff. This is a
deliberate choice, as the Aider format is known to be easier for models to
reason about, and its likely that models today are trained to use this format.

Finally, read
[validate_task.py](https://github.com/nuprl/prl_ml/blob/main/buildabench_workshop/src/buildabench_workshop/validate_task.py#L19).
For every candidate task, SWE-Smith runs the task in a container to validate
that it behaves as expected. We have to do this as well, but instead of directly
running the container, we have an agent manage the process. If the patch to
construct the candidate task is not quite right, we allow the agent o clean it
up. Thus this agent regenerates the patch that it receives from the synth_task
agent and potentially fixes them. This ends up "saving" a lot of tasks that are
close to correct. Like SWE-Smith, this agentalso generates two separate patches
to remove the feature and to add the tests, which the previous agent does not
do.

I spent some time trying to have a single agent both synthesize and validate
tasks. It may be that doing both at once is too hard for current models, but
I'm not completely certain about that. However, an additional advantage of the
current design that separates task synthesis and validation is that we can
generate a large volume of unvalidated tasks, and as long as we have reason
to believe they are likely valid, we can use them for supervised fine-tuning.
However, our focus right now is on benchmarking.


## Preliminary Results

### Build-A-Bench on Rust

So, does it work? It is very hard to determine if the synthesized tasks are any
good unless you understand the target repository. I first applied Build-A-Bench
to [ILVM](https://github.com/arjunguha/ilvm), which a virtual machine that I
wrote years ago to teach a little unit on compilers in a programming languages
course. Models have likely been trained on this repository (BSD licensed from
2018), but its very unlikely that there is source code on the web that uses this
language.

Here are the steps to run it, and there are command-line flags that you can
use to select different models or agents.


1. Create the tips files:

   ```bash
   echo "No tips yet" > rust_env_agent_tips.txt
   echo "No tips yet" > rust_validate_task_tips.txt
   ```

2. Clone the repository and save it as a tarball:

   ```bash
   git clone https://github.com/arjunguha/ilvm.git
   tar -cf ilvm.tar ilvm
   ```

3. Run env_agent to create the execution environment:


   ```bash
   uv run python3 -m buildabench_workshop.env_agent \
       --tips-file rust_validate_task_tips.txt \
       --repo ilvm.tar \
       --output-json >> envs.jsonl
   ```

4. Run synth_task to create the tasks:

   ```bash
   uv run python3 -m buildabench_workshop.synth_task \
      --json \
      --num-candidates 10 \
      --flex-processing ilvm.tar "src/*.rs" >> tasks.jsonl
   ```

   The `--flex-processing` flag will save you money when using an OpenAI model.
   The `src/*.rs` argument specifies the files to include in the context. For
   now, you need to set this manually. Selecting the context automatically for
   this task may be interesting, but not on the critical path.

   At this point, one should read the generated generatedI'll write up the results later.
 tasks. The JSON file
   is large, and has long, multi-line strings that are difficult to read in
   a text editor. If you're on Linux, [slopjson](https://github.com/arjunguha/slopjson)
   is an incredible JSON viewer that's up to the task.

5. Run validate_task to clean up the patches and verify that they
   work:

   ```bash
    parallel -j 1 --progress --bar \
        'sed -n "{}p" tasks.jsonl | python3 -m buildabench_workshop.validate_task --tips-path rust_validate_task_tips.txt --agent codex --input-json --output-json' \
        ::: `seq 10` >> validated_tasks.jsonlI'll write up the results later.

    ```

    Once you have a few tips in place, you can run validation agents in parallel.

6. Finally, run an LLM-free validation step:

   ```bash
   uv run python3 -m buildabench_workshop.check_validated_tasks validated_tasks.jsonl
   ```

Are the tasks any good? For brevity, I've listed the task subjects below,
though I did read all the tasks carefully.

1. Heap allocation and free‑list memory management with malloc and free instructions

2. Implementing a print instruction (including array prI'll write up the results later.
inting) in the ILVM interpreter

3. Implementing conditional branching with the ifz instruction in the ILVM interpreter

4. Re‑implementing negative integer literal parsing and arithmetic in the ILVM interpreter

5. Implementing indirect control flow with a goto instruction in the ILVM interpreter

6. **This is non-sensical.** Re‑implementing structured error handling and
   propagation with a custom Error type across the ILVM interpreter

7. **Trivial.** Lexing, parsing, and evaluating string identifier arguments to
   the print instruction

8. **Silly-add parser when the core implementation already exists.**
   Re‑implementing core assignment and arithmetic instruction parsing in the
   ILVM interpreter

9. Re‑implementing the abort instruction handling in the ILVM interpreter

10.Re‑implementing pointer load/store instructions in the ILVM interpreter

The tasks are quite good. The difficulty is all over the place, and later tasks
are uniformly easier, which one might expect since the prompt keeps constraining
the search space. Some of the prompts still mention adding back a removed
feature, despite an explicit instruction not to do so.

The final step is to actually benchmark an agent on these tasks. I am actually
not interested in benchmarking Rust. But, I have started to benchmark agents
on Julia tasks.

### Build-A-Bench on Julia

My first target Julia project was ILVM translated to Julia, using Cursor
Composer 1. These are the tasks that we get, and the length of the gold patch.

| task_id       | subject                                                              | src_lines | tests_lines | Gold |
| ------------- | -------------------------------------------------------------------- | --------: | ----------: | ---: |
| ilvm_jl.tar/0 | Re-implementing heap allocation and free-list–based malloc/free      |       268 |          38 |  230 |
| ilvm_jl.tar/1 | Re-implementing the ILVM print instruction and output semantics      |       217 |          37 |  180 |
| ilvm_jl.tar/2 | Re-implementing conditional branching via the ILVM ifz instruction   |       210 |          45 |  165 |
| ilvm_jl.tar/3 | Re-implementing multi-block programs and goto-based control flow     |       227 |          82 |  145 |
| ilvm_jl.tar/4 | Adding binary arithmetic and comparison operators to the ILVM        |       279 |         100 |  179 |
| ilvm_jl.tar/5 | Implementing string literal printing in the ILVM                     |       124 |          44 |   80 |
| ilvm_jl.tar/6 | Re-implementing the ILVM exit instruction and process termination    |       317 |         201 |  116 |
| ilvm_jl.tar/7 | Re-implementing ILVM’s pointer dereference load and store operations |       209 |          42 |  167 |
| ilvm_jl.tar/8 | Re-implementing the ILVM command-line interface                      |        97 |          64 |   33 |
| ilvm_jl.tar/9 | Re-implementing array-printing support in the ILVM                   |       157 |          69 |   88 |

As you can see, these gold patches are an order of magnitude larger than the
gold patches that SWE-Smith produces. It is also interesting how they keep
getting smaller. Recall that each successive task generation is constrained to
not be one the previous tasks. 

It's interesting how there is such strong overlap between the Rust and Julia tasks. My critiques:

1. `ilvm_jl.tar/3`: this task is nonsensical, but distinct from any Rust task.
2. `ilvm_jl.tar/6`: this task is poorly described, and sort of silly.
3. `ilvm_jl.tar/7`: this task is poorly described
4. `ilvm_jl.tar/8`: this task is different from any Rust task, but well described.

**Results on Cursor Composer-1:** So, how do agents do? There are several ways
to configure an agent for automatic evaluation. My approach with
[eval_agent.py](https://github.com/nuprl/prl_ml/blob/main/buildabench_workshop/src/buildabench_workshop/eval_agent.py)
is arguably unfair to agents, because it prevents the agent from running any
code.

Here are the results:

| task_id       | subject                                                              | success |
| ------------- | -------------------------------------------------------------------- | ------- |
| ilvm_jl.tar/0 | Re-implementing heap allocation and free-list–based malloc/free      | ❌       |
| ilvm_jl.tar/1 | Re-implementing the ILVM print instruction and output semantics      | ✅       |
| ilvm_jl.tar/2 | Re-implementing conditional branching via the ILVM ifz instruction   | ❌       |
| ilvm_jl.tar/3 | Re-implementing multi-block programs and goto-based control flow     | ⏭️      |
| ilvm_jl.tar/4 | Adding binary arithmetic and comparison operators to the ILVM        | ❌       |
| ilvm_jl.tar/5 | Implementing string literal printing in the ILVM                     | ❌       |
| ilvm_jl.tar/6 | Re-implementing the ILVM exit instruction and process termination    | ✅       |
| ilvm_jl.tar/7 | Re-implementing ILVM’s pointer dereference load and store operations | ✅       |
| ilvm_jl.tar/8 | Re-implementing the ILVM command-line interface                      | ❌       |
| ilvm_jl.tar/9 | Re-implementing array-printing support in the ILVM                   | ✅       |

I had an earlier run where Composer-1 was able complete the first task successfully, so it
is not impossible. I started looking at the failures, and they seem to be typical flaws
like hallucinated method names, and not due to underspecification.


[SWE-Smith]: https://arxiv.org/abs/2504.21798
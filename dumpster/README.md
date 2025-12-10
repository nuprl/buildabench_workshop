## Introduction

This is a data processing pipeline to build a benchmark for Julia. We have
several saved artifacts from the run. But, do not expect to be able to run
end-to-end without some shepherding. We ran this pipeline on Celadon, a machine
with 48 cores, 64 GB RAM, and 44TB of spinning disks. You will need to adjust
constants and paths below for other systems.

The file `repo_names.txt` in this repository has a list of URLs to Julia
repositories. We built this list from the Julia package manager, where package
metadata includes the repository URL. (I have lost the code I used to build this,
but it was very trivial.)


We first checkout all the repositories and store each one as a tarball:

```bash
parallel -j 10 --progress --bar \
    python3 download_repo.py \
    --dir /hdd/datasets/julia/all_jl_repos :::: repo_names.txt
```

We extract every commit message from the main/master branch of every repository
and store the results in a Parquet file:

```bash
parallel -j 40 --progress --bar \
    python3 bin/commit_log_as_jsonl.py \
    ::: /hdd/datasets/julia/all_jl_repos/*/*.tar \
    | ./jsonl_to_parquet.sh /hdd/datasets/julia/all_jl_commits.parquet
```

We filter the commits to find likely candidates without executing any code. This
step also fetches the text of the pull request, the associated issue, the test
diff, and the non-test diff. Read the script `filter_commits_noexec.py` for
details on this procedure:

```bash
uv run filter_commits_noexec.py \
  --all-commits /hdd/datasets/julia/all_jl_commits.parquet \
  --output-file /hdd/datasets/julia/filter_commits_noexec.parquet \
  --cache-file /hdd/datasets/julia/github.duckdb
```

We then extract the candidates for execution-based filtering:

```bash
uv run extract_candidates.py \
  --parquet-file /hdd/datasets/julia/filter_commits_noexec.parquet \
  --root /hdd/datasets/julia/filter_commits_exec
```

At this point, I stopped, because there are only 153 candidates for
execution-based filtering:

```bash
$ ls /hdd/datasets/julia/filter_commits_exec | wc -l
153
```

We are very likely to get a lot of failures at the execution-filtering step,
so this won't work.
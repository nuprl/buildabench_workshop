# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
The purpose of this script is to synthesize non-trivial programming tasks to
do in a repository. The best way to understand what it does it to read the
instructions in the DSPy signature below.
"""

import dspy
import argparse
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import sys
import json
import logging
import os

from .repolib import tarball_or_repo, get_commit_sha


def find_matching_files(repo_root: Path, patterns: List[str]) -> List[Path]:
    matching_files = []
    for pat in patterns:
        matching_files.extend(repo_root.glob(pat))
    return sorted(matching_files)

def format_code_with_headers(files: List[Path], repo_root: Path) -> str:
    """
    Format code files with filenames as headers and code enclosed in markdown fences.
    """
    parts = []

    for file_path in files:
        rel_path = file_path.relative_to(repo_root)

        parts.append(f"## {rel_path}\n")

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            content = f"<Error reading file: {e}>\n"

        parts.append(f"```\n{content}\n```\n\n")

    return "".join(parts)


class MakeFeatureRequest(dspy.Signature):
    """
    I need to interview a candidate software engineer. I've included the code
    of the project on which they will work below, and during the interview, I
    want to test their programming ability by asking them to re-implement a
    feature that already exists in the project. I want you to:

    A. Identify an existing feature in the code for them to re-implement.
    B. Tell me how to remove the feature, leaving no trace that it ever existed.
    C. A one-sentence description of the feature. (Think subject of an email.)
    D. Give me a description of the feature that I can use verbatim in the
       interview. This description should not suggest that we deliberately removed
       the feature, and should give code examples that refer to the functions that
       we will use to test the implementation. (See point 3 below.)

    A good feature has the following properties:

    1. The feature must be cross-cutting, which means that its implementation must
       span multiple locations (e.g., files, functions, or classes) and be entangled
       with other features.

    2. The feature must be testable, which means that it should be possible to write
       unit tests to determine if the feature is working correctly.

    3. When you remove the feature, you may end up deleting several helper
       functions. However, there should be at least one function that does not get
       deleted. These functions are where the feature is entangled with other
       features. In your description, which I will use in the interview, you can
       reference these functions to give examples. We will later write unit tests
       for these functions that will fail after the feature is removed, and pass
       when the feature is added back.
       before the feature is removed and pass after the feature is added back.

    4. The feature should not critically depend on a GPU or a cluster, because
       we need to be able to run the code in a portable container.

    Describe how to edit the code to remove the feature by identifying chunks of
    edits. Each chunk should have the following lines:

    1. The file path
    2. The verbatim string block: <<<<<<< SEARCH
    3. A contiguous chunk of lines to search for in the existing source code
    4. The dividing line: =======
    5. The lines to replace into the source code
    6. The end of the replace block: >>>>>>> REPLACE

    Here is an example:

    ```python
    ### mathweb/flask/app.py
    <<<<<<< SEARCH
    from flask import Flask
    =======
    import math
    from flask import Flask
    >>>>>>> REPLACE
    ```

    Please note that the *SEARCH/REPLACE* edit REQUIRES PROPER INDENTATION. If you
    would like to add the line '        print(x)', you must fully write that out,
    with all those spaces before the code. To edit a file in multiple places, you
    must produce multiple SEARCH/REPLACE blocks.

    Finally, I will give you a list of subjects that I already have interview
    questions for. Pick a subject that is not at all similar to the subjects in the
    list.
    """

    code: str = dspy.InputField()
    avoid: str = dspy.InputField(description="List of existing subjects to avoid")
    subject: str = dspy.OutputField()
    task_description: str = dspy.OutputField(
        description="The description for the interview. It should sound like the feature never existed, so do not mention that we are asking for it to be reimplemented. Give at least one test case that should pass when the feature is implemented correctly."
    )
    patches: str = dspy.OutputField()


make_feature_request_cot = dspy.ChainOfThought(MakeFeatureRequest)


def _get_log_level() -> int:
    """
    Determine log level from LOGLEVEL environment variable.
    Defaults to WARNING if not set.
    """
    loglevel_env = os.getenv("LOGLEVEL", "").upper()
    if loglevel_env:
        level = getattr(logging, loglevel_env, None)
        if isinstance(level, int):
            return level
    
    # Default to WARNING if not set
    return logging.WARNING


def make_feature_request(
    repo_dir: Path, repo_path: Path, matching_files: List[Path], avoid: List[str], max_input_tokens: int
) -> Optional[Dict[str, Any]]:
    commit_sha = get_commit_sha(repo_dir)
    formatted_code = format_code_with_headers(matching_files, repo_dir)
    if len(formatted_code) > max_input_tokens * 3:
        logging.warning(f"Formatted code is too long, truncating to {max_input_tokens * 3} tokens")
        formatted_code = formatted_code[:(max_input_tokens * 3)]
    result = make_feature_request_cot(code=formatted_code, avoid=";".join(avoid))

    # Check if any feature request was generated
    if not result.subject and not result.task_description and not result.patches:
        return None

    return {
        "task_id": f"{repo_path.name}/{len(avoid)}",
        "matching_files": [str(file) for file in matching_files],
        "repo": str(repo_path),
        "commit_sha": commit_sha,
        # NOTE(arjun): stupid replacement is so that we can copy-paste in the shell.
        "subject": result.subject.replace("`", ""),
        "task_description": result.task_description,
        "patches": result.patches,
        "reasoning": result.reasoning,
    }


def process_single_request(
    repo_dir: Path, repo_path: Path, matching_files: List[Path], json_output: bool, avoid: List[str], max_input_tokens: int
):
    result = make_feature_request(repo_dir, repo_path, matching_files, avoid, max_input_tokens)

    if result is None:
        print("No feature request generated", file=sys.stderr)
        return None

    if json_output:
        print(json.dumps(result))
    else:
        if result["subject"]:
            print(f"Subject: {result['subject']}\n")
        if result["task_description"]:
            print(f"Task Description:\n{result['task_description']}\n")
        if result["patches"]:
            print("Patches:")
            for patch in result["patches"]:
                print(patch)
                print()

    return result


def main_with_args(
    repo_path: Path,
    patterns: List[str],
    json_output: bool,
    avoid: List[str],
    num_candidates: int,
    flex_processing: bool,
    model: str,
    max_input_tokens: int,
):
    # Configure logging from LOGLEVEL environment variable
    log_level = _get_log_level()
    logging.basicConfig(
        level=log_level,
        format='%(message)s',
        stream=sys.stderr
    )
    
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)

    lm_kwargs = {}

    if flex_processing:
        lm_kwargs["service_tier"] = "flex"
        lm_kwargs["allowed_openai_params"] = ["service_tier"]
        # Default timeout is 10 minutes. Increase morpe?

    lm = dspy.LM(
        model=model,
        **lm_kwargs,
    )
    dspy.configure(lm=lm)

    # Extract repository once before the loop
    with tarball_or_repo(repo_path) as repo_dir:
        # Find and print matching files once if DEBUG logging is enabled
        matching_files = find_matching_files(repo_dir, patterns)
        if not matching_files:
            raise ValueError(f"No files found matching patterns: {patterns}")
        
        for f in matching_files:
            logging.info(f"- {f}")
        
        for i in range(num_candidates):
            result = process_single_request(repo_dir, repo_path, matching_files, json_output, avoid, max_input_tokens)
            if result and result.get("subject"):
                avoid.append(result["subject"])


def main():
    parser = argparse.ArgumentParser(
        description="Generate a feature request for re-implementation from existing code"
    )
    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to tarball containing a bare git repository or an existing repository directory",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai/gpt-5.1",
        help="Model to use for the feature request",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=100000,
        help="Maximum number of input tokens to allow for the model",
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help="Wildcard patterns to match files (e.g., 'src/*.js' 'test/*.py')",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON format",
    )
    parser.add_argument(
        "--avoid",
        nargs="*",
        default=[],
        action="extend",
        help="List of existing subjects to avoid",
    )
    parser.add_argument(
        "--num-candidates",
        type=int,
        dest="num_candidates",
        default=1,
        help="Number of candidates to generate. Each candidate will avoid subjects from previous candidates. (default: 1)",
    )
    parser.add_argument(
        "--flex-processing",
        action="store_true",
        help="Enable flex processing (https://platform.openai.com/docs/guides/flex-processing)"
    )
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    main()

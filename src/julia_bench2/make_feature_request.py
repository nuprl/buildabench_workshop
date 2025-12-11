#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "dspy>=3.0.4",
# ]
# ///
import dspy
import argparse
from pathlib import Path
import fnmatch
from typing import List, Tuple, Optional, Dict, Any
import sys
import json

from .repolib import tarball_or_repo, get_commit_sha


def find_matching_files(repo_root: Path, patterns: List[str]) -> List[Path]:
    """
    Find all files in the repository that match any of the given wildcard patterns.
    Patterns are relative to the repository root.
    """
    matching_files = []
    
    for file_path in repo_root.rglob("*"):
        if not file_path.is_file():
            continue
        
        try:
            rel_path = file_path.relative_to(repo_root)
        except ValueError:
            continue
        
        for pattern in patterns:
            if fnmatch.fnmatch(str(rel_path), pattern):
                matching_files.append(file_path)
                break  # Only add once even if matches multiple patterns
    
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
            content = file_path.read_text(encoding='utf-8', errors='replace')
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
    interview. This description should not suggest that we deliberately removed the feature,
    and should give code examples that refer to the functions that we will
    use to test the implementation. (See point 3 below.)

A good feature has the following properties:

1. The feature must be cross-cutting, which means that its implementation
    must span multiple locations (e.g., files, functions, or classes) and be
    entangled with other features.

2. The feature must be testable, which means that it should be possible to
    write unit tests to determine if the feature is working correctly.

3. When you remove the feature, you may end up deleting several helper
    functions. However, there should be at least one function -- hopefully
    more than one --that do not get deleted completely. These functions
    are where the feature is entangled with other features. Moreover, these
    are the functions that we can use to write clear unit tests that fail
    before the feature is removed and pass after the feature is added back.

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
with all those spaces before the code.

Finally, I will give you a list of subjects that I already have interview questions for.
Pick a subject that is not at all similar to the subjects in the list.
    """

    code: str = dspy.InputField()
    avoid: str = dspy.InputField(description="List of existing subjects to avoid")
    subject: str = dspy.OutputField()
    task_description: str = dspy.OutputField(description="The description for the interview")
    patches: str = dspy.OutputField()

make_feature_request_cot = dspy.ChainOfThought(MakeFeatureRequest)


def make_feature_request(repo_path: Path, patterns: List[str], make_feature_request_module, avoid: List[str]) -> Optional[Dict[str, Any]]:
    """
    Make a feature request for files in a git repository.
    
    Args:
        repo_path: Path to either a tarball containing a bare git repository or an existing repository directory
        patterns: List of wildcard patterns to match files
        make_feature_request_module: DSPy module for making feature requests (e.g., dspy.ChainOfThought(MakeFeatureRequest))
        avoid: List of existing subjects to avoid when generating feature requests
    
    Returns:
        Dictionary with keys: subject, task_description, patches, commit_sha, repo_id
        Returns None if no feature request was generated
    
    Raises:
        FileNotFoundError: If repo_path doesn't exist
        ValueError: If tarball extraction fails or no files match patterns
        RuntimeError: If git operations fail
    """
    with tarball_or_repo(repo_path) as repo_dir:
        matching_files = find_matching_files(repo_dir, patterns)
        if not matching_files:
            raise ValueError(f"No files found matching patterns: {patterns}")
        
        # Get commit SHA
        commit_sha = get_commit_sha(repo_dir)
        formatted_code = format_code_with_headers(matching_files, repo_dir)
        result = make_feature_request_module(code=formatted_code, avoid=";".join(avoid))
        
        # Check if any feature request was generated
        if not result.subject and not result.task_description and not result.patches:
            return None
        
        return {
            "repo_id": str(repo_path),
            "commit_sha": commit_sha,
            # NOTE(arjun): stupid replacement is so that we can copy-paste in the shell.
            "subject": result.subject.replace("`", ""),
            "task_description": result.task_description,
            "patches": result.patches,
            "reasoning": result.reasoning
        }


def process_single_request(repo_path: Path, patterns: List[str], json_output: bool, avoid: List[str]):
    """
    Process a single feature request: handles DSPy initialization, exception handling, and printing.
    
    Args:
        repo_path: Path to repository
        patterns: List of file patterns to match
        json_output: Whether to output JSON format
        avoid: List of existing subjects to avoid (already flattened)
    
    Returns:
        Dictionary result if successful, None otherwise
    """
    try:
        dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
        
        # Configure DSPy with the specified model
        lm = dspy.LM(
            model="openai/gpt-5.1",
            # reasoning_effort="low",
            # allowed_openai_params=["reasoning_effort"],
        )
        dspy.configure(lm=lm)
        
        make_feature_request_module = dspy.ChainOfThought(MakeFeatureRequest)
        result = make_feature_request(repo_path, patterns, make_feature_request_module, avoid)
        
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
    
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


def main_with_args(repo_path: Path, patterns: List[str], json_output: bool, avoid: List[str], num_candidates: int):
    for _ in range(num_candidates):
        result = process_single_request(repo_path, patterns, json_output, avoid)
        if result and result.get("subject"):
            avoid.append(result["subject"])


def main():
    parser = argparse.ArgumentParser(
        description="Generate a feature request for re-implementation from existing code"
    )
    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to tarball containing a bare git repository or an existing repository directory"
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help="Wildcard patterns to match files (e.g., 'src/*.js' 'test/*.py')"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON format"
    )
    parser.add_argument(
        "--avoid",
        nargs="*",
        default=[],
        action="extend",
        help="List of existing subjects to avoid"
    )
    parser.add_argument(
        "--num-candidates",
        type=int,
        dest="num_candidates",
        default=1,
        help="Number of candidates to generate. Each candidate will avoid subjects from previous candidates. (default: 1)"
    )
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    main()


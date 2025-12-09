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
from typing import List, Tuple
import sys

from repolib import tarball_or_repo


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


class ExtractFeatures(dspy.Signature):
    """
    I've included below the source code of a project. I want you to read the
    code and give me a list of features that meet the following criteria:
    
    1. The feature has to be cross-cutting, which means that its implementation
       must span multiple locations (e.g., files, functions, or classes) and be
       entangled with other features. Do not include any feature that is
       implemented independently of all other features.
    
    2. The feature must be testable, which means that it should be possible to
       write unit tests to determine if the feature is working correctly.

    3. The feature must be secondary, which means that if it were removed from
       from the codebase, other independent features would continue to work.

    If you are not sure if a feature meets all these criteria, be conservative
    and do not include it in the list.
    """
    code: str = dspy.InputField()
    features: List[Tuple[str, str]] = dspy.OutputField(
        description="A list of pairs, where the first is the feature and the second is the locations where it is implemented"
    )


def extract_features(repo_path: Path, patterns: List[str], extract_features_module) -> List[str]:
    """
    Extract features from files in a git repository.
    
    Args:
        repo_path: Path to either a tarball containing a bare git repository or an existing repository directory
        patterns: List of wildcard patterns to match files
        extract_features_module: DSPy module for extracting features (e.g., dspy.ChainOfThought(ExtractFeatures))
    
    Returns:
        List of extracted features
    
    Raises:
        FileNotFoundError: If repo_path doesn't exist
        ValueError: If tarball extraction fails or no files match patterns
        RuntimeError: If git operations fail
    """
    with tarball_or_repo(repo_path) as repo_dir:
        matching_files = find_matching_files(repo_dir, patterns)
        
        if not matching_files:
            raise ValueError(f"No files found matching patterns: {patterns}")
        
        formatted_code = format_code_with_headers(matching_files, repo_dir)
        result = extract_features_module(code=formatted_code)
        
        if not result.features:
            return []
        
        return result.features


def main_with_args(repo_path: Path, patterns: List[str]):
    """
    Main CLI function that handles DSPy initialization, exception handling, and printing.
    """
    try:
        dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
        
        # Configure DSPy with the specified model
        lm = dspy.LM(
            model="openai/gpt-5.1",
            reasoning_effort="low",
            allowed_openai_params=["reasoning_effort"],
        )
        dspy.configure(lm=lm)
        
        extract_features_module = dspy.ChainOfThought(ExtractFeatures)
        features = extract_features(repo_path, patterns, extract_features_module)
        
        if features:
            for feature in features:
                print(feature)
        else:
            print("No features extracted", file=sys.stderr)
    
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


def main():

    parser = argparse.ArgumentParser(
        description="Extract features files the source code of a project"
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
    args = parser.parse_args()
    main_with_args(**vars(args))


if __name__ == "__main__":
    main()

"""
Extract commit history from a tarred bare Git repository and output as JSON Lines.

This script extracts a bare Git repository from a tar archive, parses the commit
history using git log, and outputs each commit as a JSON object on a single line
(JSONL format) to stdout. The script handles temporary extraction and cleanup
automatically.

    python3 commit_log_as_jsonl.py repo.git.tar > commits.jsonl

Each output line contains commit fields: sha, author_name, author_email,
timestamp, subject, body, parent, other_parents, and tar_file_path.

This is a standalone script with no dependencies other than the standard library
and the git command-line tool.
"""

import sys
import os
import json
import subprocess
import tarfile
import tempfile
import argparse
from pathlib import Path

def parse_git_log(repo_path: Path) -> list:
    """
    Executes 'git log' with a custom format string and parses the output
    into a list of commit dictionaries.

    The custom format uses unique delimiters to separate fields reliably.
    """
    # Define a highly structured format string for git log.
    # We use unique, unlikely-to-be-used delimiters for clean parsing.
    # Field meanings:
    # %H: Commit hash
    # %an: Author name
    # %ae: Author email
    # %at: Author timestamp (Unix epoch)
    # %s: Subject (first line of commit message)
    # %b: Body (rest of commit message)
    # %P: Parent hashes (space-separated)
    
    GIT_LOG_FORMAT = "<<SHA>>%H<<AUTHOR>>%an<<EMAIL>>%ae<<TIMESTAMP>>%at<<SUBJECT>>%s<<BODY>>%b<<PARENTS>>%P"
    
    # Execute the git log command. Must set the CWD to the repository path.
    try:
        result = subprocess.run(
            ['git', 'log', '--all', f'--pretty=format:{GIT_LOG_FORMAT}', '--date=iso'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Error executing git log: {e.stderr}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("Error: 'git' command not found. Ensure Git is installed and in your PATH.", file=sys.stderr)
        return []

    raw_output = result.stdout.strip()
    if not raw_output:
        print("No commits found in the repository.", file=sys.stderr)
        return []

    # The log output is one continuous string, with each commit separated by the
    # first delimiter (<<SHA>>). We split by that and discard the first empty item.
    commit_strings = raw_output.split('<<SHA>>')[1:]

    commits = []
    
    # Define the fields and their delimiters (must match the GIT_LOG_FORMAT)
    DELIMITERS = {
        'sha': '<<SHA>>',
        'author_name': '<<AUTHOR>>',
        'author_email': '<<EMAIL>>',
        'timestamp': '<<TIMESTAMP>>',
        'subject': '<<SUBJECT>>',
        'body': '<<BODY>>',
        'parents': '<<PARENTS>>'
    }

    # Helper function to extract a field between two delimiters
    def extract_field(data: str, start_delim: str, end_delim: str) -> str:
        start_index = data.find(start_delim) + len(start_delim)
        end_index = data.find(end_delim, start_index)
        if start_index != -1 and end_index != -1:
            return data[start_index:end_index].strip()
        return data[start_index:].strip() if start_index != -1 else ""

    for commit_str in commit_strings:
        # Since we split on <<SHA>>, the commit_str starts with the SHA itself.
        # The structure is: SHA<<AUTHOR>>AuthorName<<EMAIL>>...
        
        # 1. Parse all simple fields
        
        # To simplify parsing, we append a virtual start delimiter for the SHA
        # and ensure the body has a distinct end point.
        full_str = f"SHA:{commit_str}"
        
        sha = extract_field(full_str, "SHA:", DELIMITERS['author_name'])
        author_name = extract_field(full_str, DELIMITERS['author_name'], DELIMITERS['author_email'])
        author_email = extract_field(full_str, DELIMITERS['author_email'], DELIMITERS['timestamp'])
        timestamp = extract_field(full_str, DELIMITERS['timestamp'], DELIMITERS['subject'])
        subject = extract_field(full_str, DELIMITERS['subject'], DELIMITERS['body'])
        
        # 2. Handle Body and Parents last, as the body can contain newlines/whitespace
        # The body runs from <<BODY>> up to <<PARENTS>>
        
        body_start = full_str.find(DELIMITERS['body']) + len(DELIMITERS['body'])
        parents_start = full_str.find(DELIMITERS['parents'])
        
        if body_start != -1 and parents_start != -1:
            body_and_parents = full_str[body_start:]
            
            # The actual body is from its start up to the parent delimiter
            body = body_and_parents[:body_and_parents.find(DELIMITERS['parents'])].strip()
            
            # The parent list is after the parent delimiter
            parents_list = body_and_parents[body_and_parents.find(DELIMITERS['parents']) + len(DELIMITERS['parents']):].strip()
        else:
            body = ""
            parents_list = ""

        # Split parents and separate into parent and other_parents
        parents = parents_list.split() if parents_list else []
        parent = parents[0] if len(parents) > 0 else None
        other_parents = parents[1:] if len(parents) > 1 else []

        commit_data = {
            'sha': sha,
            'author_name': author_name,
            'author_email': author_email,
            'timestamp': int(timestamp) if timestamp.isdigit() else 0,
            'subject': subject,
            'body': body,
            'parent': parent,
            'other_parents': other_parents
        }
        commits.append(commit_data)
        
    return commits

def main():
    """
    Main function to handle CLI arguments, extraction, and file output.
    """
    parser = argparse.ArgumentParser(
        description="Convert a tarred bare Git repository's history into JSON Lines format (outputs to stdout).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        'tar_file_path', 
        type=str, 
        help="Path to the input .tar file (must be a bare Git repository archive, e.g., repo.git.tar)."
    )
    
    args = parser.parse_args()
    tar_file_path = Path(args.tar_file_path)

    if not tar_file_path.is_file():
        print(f"Error: Input file not found at '{tar_file_path}'", file=sys.stderr)
        sys.exit(1)

    # Use TemporaryDirectory for safe, automatic cleanup
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_repo_path = Path(tmpdir) / tar_file_path.stem.split('.')[0] 
        os.makedirs(tmp_repo_path)
        
        try:
            # Ensure the tar file is not compressed (.tar.gz)
            with tarfile.open(tar_file_path, 'r') as tar:
                tar.extractall(path=tmp_repo_path)
            
            # Check if it looks like a bare repo (should contain 'HEAD', 'objects', etc.)
            if not (tmp_repo_path / 'HEAD').exists():
                 # Handle cases where the tar contains an extra directory level
                extracted_contents = list(tmp_repo_path.iterdir())
                if len(extracted_contents) == 1 and extracted_contents[0].is_dir() and (extracted_contents[0] / 'HEAD').exists():
                    tmp_repo_path = extracted_contents[0]
                else:
                    print("Error: Extracted content does not appear to be a valid bare Git repository.", file=sys.stderr)
                    sys.exit(1)

            commits = parse_git_log(tmp_repo_path)
            
            if not commits:
                print("Process completed, but no commit data was generated.", file=sys.stderr)
                return

            # Write each commit object as a single line of JSON (JSONL format) to stdout
            tar_file_path_str = str(tar_file_path)
            for commit in commits:
                commit['tar_file_path'] = tar_file_path_str
                json_line = json.dumps(commit)
                print(json_line)

        except tarfile.ReadError:
            print(f"Error: Cannot read archive '{tar_file_path}'. Check if it is a valid .tar file (not .tar.gz).", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"An unexpected error occurred: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
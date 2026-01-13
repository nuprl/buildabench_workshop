"""
View and explore tasks.jsonl and validated_tasks.jsonl files.

This script provides a web interface to view joined task data with:
- Tree view on the left for navigation
- Value display on the right with syntax highlighting for diffs
- Two modes: HTML file output or web server
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse


def load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file and return a list of dictionaries."""
    data = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def join_tasks(tasks: List[Dict], validated: List[Dict]) -> Dict[str, Dict[str, Any]]:
    """Join tasks and validated_tasks by task_id."""
    result = {}
    
    # First, add all tasks
    for task in tasks:
        task_id = task.get('task_id')
        if task_id:
            result[task_id] = {'task': task, 'validated': None}
    
    # Then, add/update with validated data
    for validated_task in validated:
        task_id = validated_task.get('task_id')
        if task_id:
            if task_id not in result:
                result[task_id] = {'task': None, 'validated': validated_task}
            else:
                result[task_id]['validated'] = validated_task
    
    return result


def build_tree(data: Dict[str, Any], prefix: str = '') -> List[tuple]:
    """Build a tree structure from nested dictionary."""
    items = []
    if isinstance(data, dict):
        for key, value in sorted(data.items()):
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                items.append((path, key, True))  # True means has children
                if isinstance(value, dict):
                    items.extend(build_tree(value, path))
                elif isinstance(value, list) and len(value) > 0:
                    # For lists, show first few items
                    for i, item in enumerate(value[:5]):
                        items.append((f"{path}[{i}]", f"[{i}]", isinstance(item, (dict, list))))
                        if isinstance(item, (dict, list)):
                            items.extend(build_tree(item, f"{path}[{i}]"))
                    if len(value) > 5:
                        items.append((f"{path}[...]", f"[... ({len(value)} total)]", False))
            else:
                items.append((path, key, False))
    elif isinstance(data, list):
        for i, item in enumerate(data[:10]):
            items.append((f"{prefix}[{i}]", f"[{i}]", isinstance(item, (dict, list))))
            if isinstance(item, (dict, list)):
                items.extend(build_tree(item, f"{prefix}[{i}]"))
        if len(data) > 10:
            items.append((f"{prefix}[...]", f"[... ({len(data)} total)]", False))
    return items


def get_value_by_path(data: Dict[str, Any], path: str) -> Any:
    """Get a value from nested dictionary/list using dot notation path."""
    parts = path.split('.')
    current = data
    
    for part in parts:
        if '[' in part and ']' in part:
            # Handle list indexing like "key[0]"
            key_part, index_part = part.split('[', 1)
            index = int(index_part.rstrip(']'))
            if key_part:
                current = current[key_part]
            current = current[index]
        else:
            current = current[part]
    
    return current


def format_value(value: Any) -> str:
    """Format a value for display."""
    if value is None:
        return "null"
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, str):
        return value
    elif isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    else:
        return str(value)


def is_diff_field(path: str) -> bool:
    """Check if a field path looks like a diff field."""
    return 'diff' in path.lower() or 'patch' in path.lower()


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))


def highlight_diff(text: str) -> str:
    """Add syntax highlighting to diff text."""
    lines = text.split('\n')
    highlighted = []
    
    for line in lines:
        escaped = escape_html(line)
        if line.startswith('+++') or line.startswith('---'):
            highlighted.append(f'<span class="diff-header">{escaped}</span>')
        elif line.startswith('+'):
            highlighted.append(f'<span class="diff-add">{escaped}</span>')
        elif line.startswith('-'):
            highlighted.append(f'<span class="diff-remove">{escaped}</span>')
        elif line.startswith('@@'):
            highlighted.append(f'<span class="diff-hunk">{escaped}</span>')
        elif line.startswith('<<<<<<<') or line.startswith('=======') or line.startswith('>>>>>>>'):
            highlighted.append(f'<span class="diff-conflict">{escaped}</span>')
        else:
            highlighted.append(escaped)
    
    return '\n'.join(highlighted)


def generate_html(joined_data: Dict[str, Dict[str, Any]], output_file: Optional[Path] = None) -> str:
    """Generate HTML for viewing the data."""
    
    # Convert to list for easier iteration
    task_list = list(joined_data.items())
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Task Viewer</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            display: flex;
            height: 100vh;
            overflow: hidden;
            background: #f5f5f5;
        }}
        
        .sidebar {{
            width: 300px;
            background: #2d2d2d;
            color: #e0e0e0;
            overflow-y: auto;
            border-right: 1px solid #444;
        }}
        
        .sidebar-header {{
            padding: 20px;
            background: #1e1e1e;
            border-bottom: 1px solid #444;
            font-weight: 600;
            font-size: 16px;
        }}
        
        .task-item {{
            padding: 12px 20px;
            cursor: pointer;
            border-bottom: 1px solid #333;
            transition: background 0.2s;
        }}
        
        .task-item:hover {{
            background: #3d3d3d;
        }}
        
        .task-item.active {{
            background: #0066cc;
            color: white;
        }}
        
        .task-id {{
            font-weight: 500;
            font-size: 14px;
        }}
        
        .main-content {{
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        
        .content-header {{
            padding: 20px;
            background: white;
            border-bottom: 1px solid #ddd;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .content-header h2 {{
            margin-bottom: 10px;
            color: #333;
        }}
        
        .tree-view {{
            width: 300px;
            background: #f9f9f9;
            border-right: 1px solid #ddd;
            overflow-y: auto;
            padding: 10px;
        }}
        
        .tree-item {{
            padding: 6px 10px;
            cursor: pointer;
            font-size: 13px;
            color: #555;
            border-radius: 4px;
            margin: 2px 0;
            transition: background 0.2s;
        }}
        
        .tree-item:hover {{
            background: #e8e8e8;
        }}
        
        .tree-item.active {{
            background: #e3f2fd;
            color: #1976d2;
            font-weight: 500;
        }}
        
        .tree-item.has-children {{
            font-weight: 500;
        }}
        
        .tree-item.has-children::before {{
            content: '▶ ';
            font-size: 10px;
        }}
        
        .content-area {{
            flex: 1;
            display: flex;
            overflow: hidden;
        }}
        
        .value-display {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            background: white;
            color: #333;
        }}
        
        .value-content {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: #333;
        }}
        
        @media (prefers-color-scheme: dark) {{
            .value-display {{
                background: #1e1e1e;
                color: #e0e0e0;
            }}
            
            .value-content {{
                color: #e0e0e0;
            }}
        }}
        
        .value-content.markdown {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            white-space: normal;
        }}
        
        .value-content.markdown h1 {{
            font-size: 24px;
            font-weight: 600;
            margin: 20px 0 10px 0;
            color: #333;
        }}
        
        .value-content.markdown h2 {{
            font-size: 20px;
            font-weight: 600;
            margin: 18px 0 8px 0;
            color: #333;
        }}
        
        .value-content.markdown h3 {{
            font-size: 16px;
            font-weight: 600;
            margin: 16px 0 6px 0;
            color: #444;
        }}
        
        .value-content.markdown p {{
            margin: 10px 0;
            line-height: 1.6;
        }}
        
        .value-content.markdown ul, .value-content.markdown ol {{
            margin: 10px 0;
            padding-left: 30px;
        }}
        
        .value-content.markdown li {{
            margin: 5px 0;
        }}
        
        .value-content.markdown code {{
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
            font-size: 12px;
            color: #333;
        }}
        
        .value-content.markdown pre {{
            background: #f5f5f5;
            padding: 12px;
            border-radius: 4px;
            overflow-x: auto;
            margin: 10px 0;
            color: #333;
        }}
        
        .value-content.markdown pre code {{
            background: none;
            padding: 0;
            color: #333;
        }}
        
        @media (prefers-color-scheme: dark) {{
            .value-content.markdown code {{
                background: #2d2d2d;
                color: #e0e0e0;
            }}
            
            .value-content.markdown pre {{
                background: #2d2d2d;
                color: #e0e0e0;
            }}
            
            .value-content.markdown pre code {{
                color: #e0e0e0;
            }}
        }}
        
        .value-content.markdown a {{
            color: #0066cc;
            text-decoration: none;
        }}
        
        .value-content.markdown a:hover {{
            text-decoration: underline;
        }}
        
        .value-content.markdown strong {{
            font-weight: 600;
        }}
        
        .value-content.markdown em {{
            font-style: italic;
        }}
        
        .value-content.diff-plain {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
            font-size: 12px;
            line-height: 1.4;
            white-space: pre;
            overflow-x: auto;
            color: #333;
            background: #fff;
        }}
        
        @media (prefers-color-scheme: dark) {{
            .value-content.diff-plain {{
                color: #e0e0e0;
                background: #1e1e1e;
            }}
        }}
        
        .diff-add {{
            background: #d4edda;
            color: #155724;
        }}
        
        .diff-remove {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .diff-header {{
            color: #6c757d;
            font-weight: 600;
        }}
        
        .diff-hunk {{
            background: #d1ecf1;
            color: #0c5460;
        }}
        
        .diff-conflict {{
            background: #fff3cd;
            color: #856404;
            font-weight: 600;
        }}
        
        .empty-state {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #999;
            font-size: 16px;
        }}
        
        .search-box {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid #444;
            background: #1e1e1e;
            color: #e0e0e0;
            border-radius: 4px;
            margin-top: 10px;
            font-size: 14px;
        }}
        
        .search-box:focus {{
            outline: none;
            border-color: #0066cc;
        }}
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">Tasks ({len(task_list)})</div>
        <input type="text" class="search-box" id="taskSearch" placeholder="Search tasks...">
        <div id="taskList">
"""
    
    for task_id, _ in task_list:
        html += f'            <div class="task-item" data-task-id="{escape_html(task_id)}">'
        html += f'                <div class="task-id">{escape_html(task_id)}</div>'
        html += '            </div>\n'
    
    html += """        </div>
    </div>
    
    <div class="main-content">
        <div class="content-header">
            <h2 id="currentTask">Select a task</h2>
        </div>
        <div class="content-area">
            <div class="tree-view" id="treeView">
                <div class="empty-state">Select a task to view</div>
            </div>
            <div class="value-display" id="valueDisplay">
                <div class="empty-state">Select a field to view</div>
            </div>
        </div>
    </div>
    
    <script>
        const taskData = """
    
    # Serialize the data for JavaScript
    html += json.dumps(joined_data, ensure_ascii=False, default=str)
    
    html += """;
        
        let currentTaskId = null;
        let currentPath = null;
        
        // Task list functionality
        const taskItems = document.querySelectorAll('.task-item');
        const taskSearch = document.getElementById('taskSearch');
        
        taskSearch.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            taskItems.forEach(item => {
                const taskId = item.dataset.taskId.toLowerCase();
                if (taskId.includes(query)) {
                    item.style.display = '';
                } else {
                    item.style.display = 'none';
                }
            });
        });
        
        taskItems.forEach(item => {
            item.addEventListener('click', () => {
                taskItems.forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                currentTaskId = item.dataset.taskId;
                loadTask(currentTaskId);
            });
        });
        
        function loadTask(taskId) {
            const task = taskData[taskId];
            if (!task) return;
            
            document.getElementById('currentTask').textContent = taskId;
            
            // Build tree view
            const treeView = document.getElementById('treeView');
            treeView.innerHTML = '';
            
            const tree = buildTree(task);
            tree.forEach(([path, label, hasChildren]) => {
                const div = document.createElement('div');
                div.className = 'tree-item' + (hasChildren ? ' has-children' : '');
                div.textContent = label;
                div.dataset.path = path;
                div.addEventListener('click', () => {
                    document.querySelectorAll('.tree-item').forEach(i => i.classList.remove('active'));
                    div.classList.add('active');
                    currentPath = path;
                    loadValue(task, path);
                });
                treeView.appendChild(div);
            });
        }
        
        function buildTree(obj, prefix = '') {
            const items = [];
            if (typeof obj === 'object' && obj !== null && !Array.isArray(obj)) {
                Object.keys(obj).sort().forEach(key => {
                    // Skip 'task' and 'validated' wrapper objects - they're just containers
                    if (key === 'task' || key === 'validated') {
                        // Instead, directly show their children, but use the wrapper key in the path
                        const value = obj[key];
                        if (value && typeof value === 'object' && !Array.isArray(value)) {
                            const wrapperPath = prefix ? prefix + '.' + key : key;
                            Object.keys(value).forEach(subKey => {
                                const subPath = wrapperPath + '.' + subKey;
                                const subValue = value[subKey];
                                
                                // For matching_files, flatten it - show as a single item
                                if (subKey === 'matching_files' && Array.isArray(subValue)) {
                                    items.push([subPath, subKey, false]);
                                    return;
                                }
                                
                                const hasChildren = typeof subValue === 'object' && subValue !== null && !Array.isArray(subValue);
                                items.push([subPath, subKey, hasChildren]);
                                if (hasChildren) {
                                    items.push(...buildTree(subValue, subPath));
                                } else if (Array.isArray(subValue) && subValue.length > 0) {
                                    // For other arrays, show first few items
                                    subValue.slice(0, 5).forEach((item, i) => {
                                        const itemPath = subPath + '[' + i + ']';
                                        const itemHasChildren = typeof item === 'object' && item !== null && !Array.isArray(item);
                                        items.push([itemPath, '[' + i + ']', itemHasChildren]);
                                        if (itemHasChildren) {
                                            items.push(...buildTree(item, itemPath));
                                        }
                                    });
                                    if (subValue.length > 5) {
                                        items.push([subPath + '[...]', '[... (' + subValue.length + ' total)]', false]);
                                    }
                                }
                            });
                        }
                        return;
                    }
                    
                    const path = prefix ? prefix + '.' + key : key;
                    const value = obj[key];
                    
                    // For matching_files, flatten it - show as a single item
                    if (key === 'matching_files' && Array.isArray(value)) {
                        items.push([path, key, false]);
                        return;
                    }
                    
                    const hasChildren = typeof value === 'object' && value !== null && !Array.isArray(value);
                    items.push([path, key, hasChildren]);
                    if (hasChildren) {
                        items.push(...buildTree(value, path));
                    } else if (Array.isArray(value) && value.length > 0) {
                        // For other arrays, show first few items
                        value.slice(0, 5).forEach((item, i) => {
                            const itemPath = path + '[' + i + ']';
                            const itemHasChildren = typeof item === 'object' && item !== null && !Array.isArray(item);
                            items.push([itemPath, '[' + i + ']', itemHasChildren]);
                            if (itemHasChildren) {
                                items.push(...buildTree(item, itemPath));
                            }
                        });
                        if (value.length > 5) {
                            items.push([path + '[...]', '[... (' + value.length + ' total)]', false]);
                        }
                    }
                });
            }
            return items;
        }
        
        function loadValue(task, path) {
            const valueDisplay = document.getElementById('valueDisplay');
            let value = getValueByPath(task, path);
            const isDiff = path.toLowerCase().includes('diff') || path.toLowerCase().includes('patch');
            const isMarkdown = path.toLowerCase().includes('task_description') || path.toLowerCase().includes('description');
            
            let content = '';
            if (value === null || value === undefined) {
                content = '<div class="value-content">null</div>';
            } else if (typeof value === 'string') {
                // Ensure newlines are preserved
                if (isDiff) {
                    // For diffs, use fixed-width font, no highlighting
                    content = '<div class="value-content diff-plain">' + highlightDiff(value) + '</div>';
                } else if (isMarkdown) {
                    content = '<div class="value-content markdown">' + renderMarkdown(value) + '</div>';
                } else {
                    content = '<div class="value-content">' + escapeHtml(value) + '</div>';
                }
            } else {
                // For non-string values, stringify them
                let textValue = JSON.stringify(value, null, 2);
                // If it's a diff field but was stringified, try to parse it back
                if (isDiff && typeof value === 'object' && value !== null) {
                    // Check if it's actually a string that got wrapped
                    textValue = JSON.stringify(value, null, 2);
                }
                if (isDiff) {
                    content = '<div class="value-content diff-plain">' + highlightDiff(textValue) + '</div>';
                } else {
                    content = '<div class="value-content">' + escapeHtml(textValue) + '</div>';
                }
            }
            
            valueDisplay.innerHTML = content;
        }
        
        function getValueByPath(obj, path) {
            // Handle keys that contain dots (like 'src.diff', 'tests.diff')
            // We need to check if a key exists with dots before splitting
            let current = obj;
            const parts = path.split('.');
            
            // Try to find the value by progressively checking if keys exist
            let remainingPath = path;
            let found = false;
            
            // First, try the full path as a single key (for keys with dots like 'src.diff')
            if (current && current.hasOwnProperty(remainingPath)) {
                return current[remainingPath];
            }
            
            // Otherwise, traverse the path
            for (let i = 0; i < parts.length; i++) {
                if (!current || typeof current !== 'object') {
                    return undefined;
                }
                
                const part = parts[i];
                
                // Handle array indexing
                if (part.includes('[') && part.includes(']')) {
                    const [key, indexStr] = part.split('[');
                    const index = parseInt(indexStr.replace(']', ''));
                    if (key) {
                        if (!current.hasOwnProperty(key)) {
                            return undefined;
                        }
                        current = current[key];
                    }
                    if (!Array.isArray(current) || index >= current.length) {
                        return undefined;
                    }
                    current = current[index];
                } else {
                    // Check if this part exists as a key
                    if (!current.hasOwnProperty(part)) {
                        // Maybe the remaining path (including this part) is a single key with dots
                        const remaining = parts.slice(i).join('.');
                        if (current.hasOwnProperty(remaining)) {
                            return current[remaining];
                        }
                        return undefined;
                    }
                    current = current[part];
                }
            }
            
            return current;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function renderMarkdown(text) {
            // Use marked library for markdown rendering
            if (typeof marked !== 'undefined') {
                return marked.parse(text);
            } else {
                // Fallback if library didn't load
                return escapeHtml(text);
            }
        }
        
        function highlightDiff(text) {
            // Simple diff renderer - just escape and preserve formatting
            // Split by actual newlines (handles both \\n and \\r\\n)
            const lines = text.split(/\\r?\\n/);
            return lines.map(line => {
                return escapeHtml(line);
            }).join('\\n');
        }
    </script>
</body>
</html>"""
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"HTML file written to: {output_file}")
    
    return html


class TaskViewerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the web server mode."""
    
    def __init__(self, html_content, *args, **kwargs):
        self.html_content = html_content
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(self.html_content.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def main():
    parser = argparse.ArgumentParser(description='View tasks.jsonl and validated_tasks.jsonl')
    parser.add_argument('tasks', type=Path,
                       help='Path to tasks.jsonl file')
    parser.add_argument('validated', type=Path,
                       help='Path to validated_tasks.jsonl file')
    parser.add_argument('--output', type=Path, help='Output HTML file (if not provided, starts web server)')
    parser.add_argument('--port', type=int, default=8000, help='Port for web server (default: 8000)')
    parser.add_argument('--host', type=str, default='localhost', help='Host for web server (default: localhost)')
    
    args = parser.parse_args()
    
    # Resolve paths relative to script location
    script_dir = Path(__file__).parent
    tasks_path = args.tasks.resolve()
    validated_path = args.validated.resolve()
    
    if not tasks_path.exists():
        print(f"Error: {tasks_path} not found", file=sys.stderr)
        sys.exit(1)
    
    if not validated_path.exists():
        print(f"Error: {validated_path} not found", file=sys.stderr)
        sys.exit(1)
    
    print(f"Loading {tasks_path}...")
    tasks = load_jsonl(tasks_path)
    print(f"Loaded {len(tasks)} tasks")
    
    print(f"Loading {validated_path}...")
    validated = load_jsonl(validated_path)
    print(f"Loaded {len(validated)} validated tasks")
    
    print("Joining data...")
    joined_data = join_tasks(tasks, validated)
    print(f"Joined {len(joined_data)} tasks")
    
    print("Generating HTML...")
    html = generate_html(joined_data, args.output)
    
    if args.output:
        # Message already printed by generate_html
        pass
    else:
        print(f"Starting web server on http://{args.host}:{args.port}")
        print("Press Ctrl+C to stop")
        
        def handler(*args, **kwargs):
            TaskViewerHandler(html, *args, **kwargs)
        
        server = HTTPServer((args.host, args.port), handler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            server.shutdown()


if __name__ == '__main__':
    main()

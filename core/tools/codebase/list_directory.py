from pathlib import Path


class ListDirectoryTool:
    name = "list_directory"
    definition = {
        "name": "list_directory",
        "description": "List files and directories at a given path. Use this to explore the codebase structure.",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
                "max_depth": {"type": "integer", "description": "Max depth to recurse (default 2)", "default": 2}
            }
        }
    }

    async def execute(self, arguments: dict) -> dict:
        path = Path(arguments["path"])
        max_depth = arguments.get("max_depth", 2)
        if not path.exists() or not path.is_dir():
            return {"error": f"Not a directory: {path}"}

        tree = []
        self._walk(path, tree, depth=0, max_depth=max_depth)
        return {"path": str(path), "tree": tree}

    def _walk(self, path, tree, depth, max_depth):
        IGNORE = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".venv", "venv"}
        if depth > max_depth:
            return
        try:
            for item in sorted(path.iterdir()):
                if item.name in IGNORE or item.name.startswith("."):
                    continue
                entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
                if item.is_dir():
                    entry["children"] = []
                    self._walk(item, entry["children"], depth + 1, max_depth)
                tree.append(entry)
        except PermissionError:
            pass


list_directory_tool = ListDirectoryTool()

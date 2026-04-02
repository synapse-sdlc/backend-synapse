class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._register_builtins()

    def _register_builtins(self):
        from core.tools.codebase.read_file import read_file_tool
        from core.tools.codebase.list_directory import list_directory_tool
        from core.tools.codebase.search_codebase import search_codebase_tool
        from core.tools.codebase.grep_codebase import grep_codebase_tool
        from core.tools.codebase.analyze_ast import analyze_ast_tool
        from core.tools.artifacts.store_artifact import store_artifact_tool
        from core.tools.artifacts.get_artifact import get_artifact_tool

        for tool in [
            read_file_tool, list_directory_tool, search_codebase_tool,
            grep_codebase_tool, analyze_ast_tool, store_artifact_tool,
            get_artifact_tool
        ]:
            self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict]:
        return [t.definition for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await self._tools[name].execute(arguments)
        except Exception as e:
            print(f"  [tool exception] {name}: {e}")
            return {"error": f"Tool {name} failed: {e}"}

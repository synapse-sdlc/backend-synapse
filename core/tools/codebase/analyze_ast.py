class AnalyzeASTTool:
    name = "analyze_ast"
    definition = {
        "name": "analyze_ast",
        "description": "Analyze a source file using AST parsing. Extracts functions, classes, imports, and exports. Use this for structured understanding of code.",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Path to source file to analyze"}
            }
        }
    }

    async def execute(self, arguments: dict) -> dict:
        from indexer.static_analyzer import analyze_file
        path = arguments["path"]
        try:
            result = analyze_file(path)
            return result
        except Exception as e:
            return {"error": str(e)}


analyze_ast_tool = AnalyzeASTTool()

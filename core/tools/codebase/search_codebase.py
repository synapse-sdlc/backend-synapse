class SearchCodebaseTool:
    name = "search_codebase"
    definition = {
        "name": "search_codebase",
        "description": "Search the indexed codebase using semantic vector search. Use this to find relevant code snippets for a given query.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "n_results": {"type": "integer", "description": "Number of results to return (default 5)", "default": 5}
            }
        }
    }

    async def execute(self, arguments: dict) -> dict:
        query = arguments["query"]
        n_results = arguments.get("n_results", 5)
        try:
            from indexer.vector_store import VectorStore
            store = VectorStore()
            results = store.search(query, n_results=n_results)
            return {"query": query, "results": results}
        except Exception as e:
            return {"error": str(e), "query": query}


search_codebase_tool = SearchCodebaseTool()

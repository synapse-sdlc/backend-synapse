class SearchCodebaseTool:
    name = "search_codebase"
    definition = {
        "name": "search_codebase",
        "description": (
            "Search the indexed codebase using semantic vector search. "
            "Use this to find relevant code snippets for a given query. "
            "Optionally scope to a specific repo or search across all repos in a project. "
            "Set include_knowledge=true to also search the project's knowledge base."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "n_results": {"type": "integer", "description": "Number of results to return (default 5)", "default": 5},
                "repo_id": {"type": "string", "description": "Search only this repo's collection (optional)"},
                "project_id": {"type": "string", "description": "Search all repos in this project (optional)"},
                "include_knowledge": {"type": "boolean", "description": "Also search the knowledge base", "default": False},
            }
        }
    }

    # Context set by the agent service before running the loop
    _context_project_id = None
    _context_repo_ids = None

    @classmethod
    def set_context(cls, project_id: str = None, repo_ids: list = None):
        """Set project/repo context for scoped search. Called by agent_service before agent_loop."""
        cls._context_project_id = project_id
        cls._context_repo_ids = repo_ids or []

    async def execute(self, arguments: dict) -> dict:
        query = arguments["query"]
        n_results = arguments.get("n_results", 5)
        repo_id = arguments.get("repo_id")
        project_id = arguments.get("project_id") or self._context_project_id
        include_knowledge = arguments.get("include_knowledge", False)

        try:
            from core.indexer.vector_store import VectorStore
            store = VectorStore()
            results = []

            if repo_id and project_id:
                search_mode = f"repo-scoped ({repo_id[:8]})"
                results = store.search_repo(project_id, repo_id, query, n_results=n_results)
            elif project_id and self._context_repo_ids:
                search_mode = f"cross-repo ({len(self._context_repo_ids)} repos)"
                results = store.search_all_repos(project_id, self._context_repo_ids, query, n_results=n_results)
            else:
                search_mode = "default collection"
                results = store.search(query, n_results=n_results)

            print(f"  [search_codebase] mode={search_mode}, query=\"{query[:60]}\", results={len(results)}")

            # Optionally include knowledge base results
            knowledge_results = []
            if include_knowledge and project_id:
                knowledge_results = store.search_knowledge(project_id, query, n_results=3)

            return {
                "query": query,
                "results": results,
                "knowledge_results": knowledge_results if knowledge_results else None,
            }
        except Exception as e:
            return {"error": str(e), "query": query}


search_codebase_tool = SearchCodebaseTool()

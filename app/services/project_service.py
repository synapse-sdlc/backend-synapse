"""Project service: handles GitHub clone and codebase analysis."""

# TODO: implement analyze_codebase()
# Reuses code from code-to-arc/main.py cmd_analyze():
# 1. Clone repo (git clone or use local path)
# 2. Run core.indexer.static_analyzer.analyze_directory()
# 3. Chunk via core.indexer.chunker.chunk_analysis_results()
# 4. Index in vector store
# 5. Run agent_loop with codebase-analysis skill
# 6. Save architecture artifact to DB

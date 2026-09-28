"""Multi-source crypto intelligence layer (v0.4).

Pipeline: providers -> normalize -> store -> features -> narratives -> LLM.
Raw posts/articles never reach the LLM; only structured signals do.
"""

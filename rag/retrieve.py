"""
Exact-match category retrieval over Chroma, with semantic fallback.

Since audit parameters match SOP subcategories word-for-word (same taxonomy),
we use exact metadata filtering first, then fall back to pure semantic search.

Public API:
  retrieve_context(query_text, parameter, subparameter, k=5) -> str
      Returns a single SOP context STRING (LLM-ready).

  retrieve_documents(query_text, query_category, k=5) -> list[Document]
      Returns raw ranked Documents (debugging / advanced use).
"""

import threading
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from client import embeddings
from rag.metadata import normalize_metadata_value

print(">>> LOADED NEW retrieve.py (exact-match + semantic fallback) <<<")

VECTOR_DB_DIR = Path(__file__).resolve().parent.parent / "vectordb"
COLLECTION_NAME = "agent_guidelines"

MAX_CONTEXT_CHARS = 3500


# ---------------------------------------------------------
# Load DB — thread-safe singleton
# ---------------------------------------------------------
_db_lock = threading.Lock()
_db_instance = None


def get_db() -> Chroma:
    """Thread-safe, cached persistent Chroma client."""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
                _db_instance = Chroma(
                    client=client,
                    collection_name=COLLECTION_NAME,
                    embedding_function=embeddings,
                )
    return _db_instance


def _tag(docs, layer: str):
    for d in docs:
        d.metadata["_match_layer"] = layer
    return docs


# ---------------------------------------------------------
# Core: return ranked Documents
# ---------------------------------------------------------
def retrieve_documents(query_text: str, query_category: str,
                       k: int = 5, verbose: bool = False) -> list[Document]:
    """
    Exact category+subcategory match first (via normalized category_combined),
    then semantic fallback if no exact match.
    """
    db = get_db()
    q_norm = normalize_metadata_value(query_category)

    # ---- Stage 0: exact native filter on category_combined ----
    if q_norm:
        exact = db.similarity_search(
            query_text, k=k, filter={"category_combined": q_norm}
        )
        if exact:
            if verbose:
                print(f"[EXACT] matched category_combined = '{q_norm}'")
            return _tag(exact, "exact")

    # ---- Stage 1: semantic fallback (no exact match) ----
    if verbose:
        print(f"[FALLBACK] no exact match for '{q_norm}' -> semantic search")
    semantic = db.similarity_search(query_text, k=k)
    return _tag(semantic, "semantic")


# ---------------------------------------------------------
# Public: return a single SOP context STRING (for LLM prompts)
# ---------------------------------------------------------
def retrieve_context(query_text: str, parameter: str = "", subparameter: str = "",
                     k: int = 5, verbose: bool = False) -> str:
    """
    Bridges ptk_builder -> retrieval.
    parameter + subparameter form the exact category filter;
    query_text is the semantic question (used for ranking/fallback).
    Returns a single formatted context string (empty if nothing found).
    """
    query_category = f"{parameter} {subparameter}".strip()

    docs = retrieve_documents(query_text, query_category, k=k, verbose=verbose)
    if verbose:
        print("retrieved docs:", [d.metadata.get("subcategory") for d in docs])

    if not docs:
        return ""

    context = "\n\n---\n\n".join(
        f"[{d.metadata.get('category_display', d.metadata.get('category', ''))} > "
        f"{d.metadata.get('subcategory_display', d.metadata.get('subcategory', ''))}]\n"
        f"{d.page_content}"
        for d in docs
    )
    return context[:MAX_CONTEXT_CHARS]


# ---------------------------------------------------------
# Demo
# ---------------------------------------------------------
if __name__ == "__main__":
    print("#" * 80)
    print("QUERY: 'REGULATORY AND DISCLOSURE / COMPLIANT WITH LEGAL/FINANCIAL DIRECTIVES'")
    ctx = retrieve_context(
        query_text="How should an agent follow legal and financial directive compliance?",
        parameter="REGULATORY AND DISCLOSURE",
        subparameter="COMPLIANT WITH LEGAL/FINANCIAL DIRECTIVES",
        verbose=True,
    )
    print(ctx[:600], "...")
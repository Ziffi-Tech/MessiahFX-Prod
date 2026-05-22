"""
Answer synthesiser — uses Claude to generate grounded answers from retrieved chunks.

The synthesiser:
1. Receives a user question + list of retrieved text chunks
2. Builds a prompt with the chunks as context
3. Calls Claude Haiku with strict instructions to answer from context only
4. Returns the answer and the source chunks used

Grounding rules (in system prompt):
- Only answer using the provided context
- Say "I don't have enough information in my knowledge base" if context is insufficient
- Always cite which document the answer is drawn from
- Never speculate beyond what the context says

This prevents hallucination and keeps the assistant focused on indexed knowledge.
"""

import asyncio

import anthropic
import structlog

from .config import Settings

log = structlog.get_logger()

_SYSTEM_PROMPT = """You are MeznaQuantFX's research assistant, specialising in quantitative
trading strategies, market microstructure, and algorithmic finance.

Your ONLY source of information is the context provided below. You must:
1. Answer ONLY using information present in the context passages.
2. If the context does not contain enough information, say:
   "I don't have enough information in my knowledge base to answer that."
3. Cite the document title or source when making a specific claim.
4. Never speculate, hallucinate, or draw on outside knowledge.
5. Be concise and precise — this is a trading system, not a chatbot.

Keep answers under 300 words unless a longer explanation is truly needed."""


def _build_prompt(question: str, chunks: list[dict]) -> str:
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("title") or chunk.get("source_id", f"Document {i}")
        score = chunk.get("score", 0)

        # Include page reference when available (from upgraded PDF parser)
        page_ref = ""
        p_start = chunk.get("page_start")
        p_end = chunk.get("page_end")
        if p_start is not None:
            page_ref = (
                f", p.{p_start}" if p_start == p_end
                else f", pp.{p_start}–{p_end}"
            )

        table_flag = " [TABLE]" if chunk.get("is_table") else ""
        context_parts.append(
            f"[{i}] SOURCE: {title}{page_ref}{table_flag} (relevance: {score:.2f})\n"
            f"{chunk['text']}"
        )

    context = "\n\n---\n\n".join(context_parts)
    return f"CONTEXT:\n\n{context}\n\n---\n\nQUESTION: {question}"


async def synthesise(
    question: str,
    chunks: list[dict],
    settings: Settings,
    client: anthropic.AsyncAnthropic | None,
) -> dict:
    """
    Generate an answer from retrieved chunks using Claude.

    Returns:
        {
            "answer": str,
            "sources": [{"title", "score", "chunk_index"}],
            "chunks_used": int,
            "model": str,
            "timed_out": bool,
        }
    """
    if not chunks:
        return {
            "answer": "No relevant documents found in the knowledge base for this question.",
            "sources": [],
            "chunks_used": 0,
            "model": "none",
            "timed_out": False,
        }

    if not settings.anthropic_configured or client is None:
        # Return context-only response (no synthesis)
        context_preview = "\n\n".join(
            f"[{i+1}] {c.get('title','')}: {c['text'][:200]}..."
            for i, c in enumerate(chunks)
        )
        return {
            "answer": (
                "Anthropic API not configured — returning raw context.\n\n"
                + context_preview
            ),
            "sources": [
                {"title": c.get("title", ""), "score": c.get("score", 0), "chunk_index": c.get("chunk_index", 0)}
                for c in chunks
            ],
            "chunks_used": len(chunks),
            "model": "none",
            "timed_out": False,
        }

    prompt = _build_prompt(question, chunks)
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.SYNTHESIS_MODEL,
                max_tokens=settings.SYNTHESIS_MAX_TOKENS,
                # Prompt caching: system prompt billed once per 5-min TTL window.
                # RAG gets many repeated queries with the same system prompt.
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=settings.SYNTHESIS_TIMEOUT_SECONDS,
        )
        answer = response.content[0].text if response.content else ""
        log.info(
            "synthesiser.answered",
            question_preview=question[:60],
            chunks_used=len(chunks),
            answer_len=len(answer),
        )
        return {
            "answer": answer,
            "sources": [
                {"title": c.get("title", ""), "score": c.get("score", 0), "chunk_index": c.get("chunk_index", 0)}
                for c in chunks
            ],
            "chunks_used": len(chunks),
            "model": settings.SYNTHESIS_MODEL,
            "timed_out": False,
        }

    except asyncio.TimeoutError:
        log.warning("synthesiser.timeout", question=question[:60])
        return {
            "answer": "Answer synthesis timed out. Try a more specific question.",
            "sources": [],
            "chunks_used": len(chunks),
            "model": settings.SYNTHESIS_MODEL,
            "timed_out": True,
        }
    except Exception as exc:
        log.error("synthesiser.error", error=str(exc))
        return {
            "answer": f"Synthesis error: {str(exc)[:100]}",
            "sources": [],
            "chunks_used": len(chunks),
            "model": settings.SYNTHESIS_MODEL,
            "timed_out": False,
        }

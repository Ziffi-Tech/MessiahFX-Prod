# RAG knowledge service

A Qdrant-backed knowledge base for grounding answers in your trading library —
ingest PDFs/notes, embed them, and ask questions that are answered from the
retrieved context (not the model's memory). The service was fully built; it is now
**wired into the platform** (the gateway proxies `/rag/*`), so the terminal's RAG
Studio works end to end.

## Pipeline

```
PDF / text ─► chunker ─► OpenAI embeddings ─► Qdrant (mezna_knowledge)
                                                  │
query ─► embed ─► Qdrant top-k retrieve ─► Claude synthesis ─► grounded answer + sources
```

Book uploads are also analysed (Claude) into **strategy profiles** stored in Redis.

## Activation (what changed)

- Gateway proxy now fronts `rag` → `RAG_URL` (the only missing link — `/api/gateway/rag/*`
  previously 404'd). `RAG_URL` added to the gateway config; locked by a proxy-map test.
- `rag` + `qdrant` were already in the compose stack (default, no profile); the RAG
  service depends on a healthy Qdrant.

## Endpoints (via the gateway, `/api/gateway/rag/*`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/rag/ingest` | Ingest raw text → chunk → embed → upsert |
| POST | `/rag/ingest/pdf` | Upload a PDF → extract → ingest (+ strategy analysis) |
| POST | `/rag/query` | Embed the question, retrieve, synthesise a grounded answer |
| GET | `/rag/strategies` | List extracted strategy profiles |
| GET/DELETE | `/rag/strategies/{id}` | Get / remove a profile |
| GET | `/rag/health/stats` | Qdrant collection stats |

The terminal's **RAG Studio** page uses these.

## Required keys (`.env`)

| Var | For |
|---|---|
| `OPENAI_API_KEY` | embeddings (`text-embedding-3-small`, 1536-dim) |
| `ANTHROPIC_API_KEY` | query synthesis + book analysis (Claude) |
| `QDRANT_URL` | vector store (default `http://qdrant:6333`) |

Without `OPENAI_API_KEY`, embedding/ingest/query degrade (no real vectors) — set it
to use the service for real.

## Strategy-knowledge grounding — next

Today the RAG service answers operator questions and extracts strategy profiles.
The deeper grounding — having the ai-filter consult RAG context before scoring an
opportunity — is the natural follow-up now that the query path is reachable.

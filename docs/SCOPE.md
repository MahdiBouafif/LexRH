# LexRH scope

**Pitch:** LexRH answers French labour-law questions with verifiable, date-aware citations. It also runs HR cases through an agent that reads uploaded documents and calls business tools. It never acts without human approval and never sends personal data to the LLM.

## MVP (end of week 4, deployed)
- Code du travail only, ingested from DILA open data
- Date-aware hybrid retrieval (pgvector + full-text), cited answers, refusal when evidence is weak
- JWT auth, golden set, CI evaluation report, live HTTPS URL

## Advanced (weeks 5-8)
- Collective agreements (KALI), reranker, caching
- MCP server + LangGraph case agent with human approval and audit log
- Docling document extraction + review screen, PII masking, budgets

## Cut order if behind
1. React review screen (use Streamlit)
2. Reranker and caching
3. KALI beyond one or two agreements
4. NER-based name masking (keep regex)
5. Document pipeline (one document type only)

## Never cut
Golden set and CI evaluation gate; citation verification and refusal; approval step and audit log; tests, Docker, deployment.

## Layout note
The specification sketched `apps/` and `packages/`. For simplicity the code is one installable package, `src/lexrh/`, with sub-packages (`api`, `db`, later `rag`, `llm`, `agent`, `docs`, `worker`). The boundaries are the same; there is just one `pyproject.toml`.

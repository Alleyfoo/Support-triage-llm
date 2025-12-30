## Technical Spec: Dynamic Few-Shot Triage (Milestone E2)

### Context
We now have a `data/learning/golden_dataset.jsonl` containing high-quality, human-verified triage examples. We need to inject the most relevant past examples into the LLM prompt at runtime to improve accuracy, tone, and JSON adherence without fine-tuning.

### 1. Architecture: Local-First RAG
To maintain our "no external deps" philosophy, we will use Ollama for embeddings and a simple in-memory vector store (NumPy) instead of adding a heavy database like Chroma/Pinecone.

Flow:
- Startup: Load `golden_dataset.jsonl`.
- Indexing: Generate embeddings for the `input_redacted` (symptoms) field using Ollama. Cache them locally.
- Runtime: receive new ticket text -> generate embedding -> cosine similarity against the index -> select top k (e.g., 3) matches.
- Prompting: Insert these matches into the system or user prompt as "Context".

### 2. New Module: `app/vector_store.py`
Class: `TriageVectorStore`
- Init: accepts path to `golden_dataset.jsonl` and a cache path `data/embeddings_cache.json`.
- `refresh()`: reads the dataset; checks cache for existing embeddings (key = hash of input text); calls `_embed(text)` for missing items; updates cache.
- `retrieve(text, k=3, threshold=0.5)`: embeds input text; computes cosine similarity against cached vectors; returns top k examples that meet the threshold.
- `_embed(text)`: uses `app.config.OLLAMA_HOST + /api/embeddings`. Model: use `nomic-embed-text` if available, or fall back to the main `llama3` model (most LLMs handle embeddings via the API). Ensure the vector dimension matches (don't mix models).

### 3. Integration: `app/triage_service.py`
#### 3.1 Prompt Engineering
Modify `_triage_llm` to accept dynamic context.

Current prompt:
```
Customer message:
{text}
Return ONLY JSON...
```

New prompt structure:
```
You are a support triage assistant. Use the following examples of correct triage for reference:
Example 1
Input: {retrieved_1.input}
Output: {retrieved_1.output_json}
Example 2
...

Now triage this new message:
Input: {current_text}
Output:
```

#### 3.2 Logic Flow
- Initialize `TriageVectorStore` (singleton or cached).
- Inside `triage()`: call `store.retrieve(text)`.
- If matches found, format them into the prompt string.
- If no matches (or store empty), fall back to the existing zero-shot prompt.

### 4. Operational Requirements
#### 4.1 Embedding Model
- Add `OLLAMA_EMBED_MODEL` to env (default: `nomic-embed-text` or `all-minilm`).
- Preflight: update `tools/preflight_check.py` to warn if the embedding model is not pulled.

#### 4.2 Performance
- Latency: embedding calculation adds latency (~200ms).
- Optimization: compute embeddings parallel to other checks if possible, but sequential is acceptable for V1.
- Store size: for <10,000 examples, brute-force NumPy similarity is instant (<10ms). No need for FAISS/Annoy yet.

### 5. Security
- Redaction: the vector store MUST ONLY index `input_redacted` from the golden dataset. Never index raw payloads.
- Leakage: ensure the retrieval logic doesn't crash if the golden dataset contains malformed JSON.

### 6. Implementation Steps
- Add `app/vector_store.py`: implement the embedding loop and cache.
- Update `app/config.py`: add `OLLAMA_EMBED_MODEL`.
- Modify `app/triage_service.py`: inject the Few-Shot block.
- Test: create a test where you "teach" the bot a specific weird classification via `golden_dataset.jsonl` and verify it picks it up in a subsequent run.

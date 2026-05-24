# Corpus Forge — Streamlit Prototype

This Streamlit app handles document ingestion, local metadata storage, vector retrieval, and Gemini-powered workflows.

## Features
- Upload documents (.txt, .md, .pdf, .py, .js, .json, .html, .css)
- Store files in `data/docs/` and metadata in SQLite (`data/corpus_forge.db`)
- Toggle active documents, preview snippets, and delete items
- Lazy embeddings written to ChromaDB on query
- Retrieval-grounded Gemini workflows (chat, flashcards, quizzes, code analysis)

## Run Locally

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Environment

Set your Gemini key before running queries:

```bash
set GEMINI_API_KEY=your_key_here
```

## Notes
- ChromaDB data is stored under `data/chroma_db/`.
- SQLite tables are created automatically at startup.

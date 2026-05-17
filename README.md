# Corpus Forge — Streamlit Prototype

This is a small Streamlit prototype implementing the Layer 1 document upload and management features for the Corpus Forge project.

Features:
- Upload multiple documents (.txt, .md, .pdf, .py, .js, etc.)
- Save uploaded files to `data/docs/`
- Show document library with Active toggle, Preview and Delete
- Basic preview extraction (text and PDF first pages)

Run locally:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Notes:
- This is an offline-first prototype. Metadata is stored at `data/docs.json` and files under `data/docs/`.
- The `Process all` button in the UI is a placeholder for later ingestion/embedding steps.

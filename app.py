import streamlit as st
import os
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from vector_store import embed_and_store_document, delete_document_embeddings

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_FILE = DATA_DIR / "corpus_forge.db"


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            type TEXT,
            ext TEXT,
            size INTEGER,
            uploaded_at TEXT,
            preview TEXT,
            status TEXT,
            active INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

    # migrate existing JSON metadata if present
    old_meta = DATA_DIR / "docs.json"
    try:
        if old_meta.exists():
            with open(old_meta, "r", encoding="utf-8") as f:
                items = json.load(f) or []
            if items:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                for m in items:
                    # skip if already present
                    cursor.execute("SELECT 1 FROM documents WHERE id = ?", (m.get("id"),))
                    if cursor.fetchone():
                        continue
                    filename = m.get("filename") or f"{m.get('id')}_{m.get('name')}"
                    path = DOCS_DIR / filename
                    cursor.execute(
                        "INSERT OR IGNORE INTO documents (id, name, filename, path, type, ext, size, uploaded_at, preview, status, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            m.get("id"),
                            m.get("name"),
                            filename,
                            str(path),
                            m.get("type"),
                            m.get("ext"),
                            m.get("size"),
                            m.get("uploaded_at"),
                            m.get("preview"),
                            m.get("status"),
                            1 if m.get("active") else 0,
                        ),
                    )
                conn.commit()
                conn.close()
            # backup old metadata file to avoid re-import
            try:
                old_meta.rename(DATA_DIR / "docs.json.bak")
            except Exception:
                pass
    except Exception:
        # non-critical; continue silently
        pass


def load_metadata():
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documents")
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def add_document_to_db(doc_id, name, filename, path, doc_type, ext, size, uploaded_at, preview, status, active=0):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO documents (id, name, filename, path, type, ext, size, uploaded_at, preview, status, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, name, filename, str(path), doc_type, ext, size, uploaded_at, preview, status, active)
    )
    conn.commit()
    conn.close()


def toggle_document_active(doc_id, current_active_status):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Flip the status: if it's 1 it becomes 0, if it's 0 it becomes 1
    new_status = 0 if current_active_status == 1 else 1
    cursor.execute("UPDATE documents SET active = ? WHERE id = ?", (new_status, doc_id))
    conn.commit()
    conn.close()


def delete_document_from_db(doc_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()


def extract_text_from_pdf(path, max_chars=2000):
    if PyPDF2 is None:
        return "(PyPDF2 not installed; saved file only)"
    try:
        reader = PyPDF2.PdfReader(str(path))
        text = []
        for p in reader.pages[:3]:
            try:
                text.append(p.extract_text() or "")
            except Exception:
                pass
        joined = "\n".join(text)
        return joined[:max_chars]
    except Exception as e:
        return f"(failed to extract PDF text: {e})"


def read_text_file(path, max_chars=2000):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()[:max_chars]
    except Exception as e:
        return f"(failed to read file: {e})"


def save_upload(uploaded_file):
    ext = Path(uploaded_file.name).suffix.lower()
    doc_id = uuid.uuid4().hex
    dest_name = f"{doc_id}_{uploaded_file.name}"
    dest_path = DOCS_DIR / dest_name
    with open(dest_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    # 1. Gather a quick snippet for the UI container preview
    preview = ""
    if ext in (".txt", ".md"):
        preview = read_text_file(dest_path, max_chars=2000)
        doc_type = "text"
    elif ext in (".py", ".js", ".java", ".c", ".cpp"):
        preview = read_text_file(dest_path, max_chars=2000)
        doc_type = "code"
    elif ext == ".pdf":
        preview = extract_text_from_pdf(dest_path, max_chars=2000)
        doc_type = "pdf"
    else:
        doc_type = "other"

    uploaded_at = datetime.utcnow().isoformat() + "Z"

    # 2. Extract FULL text across the whole file for vector database ingestion
    if ext in (".txt", ".md", ".py", ".js", ".java", ".c", ".cpp"):
        # Read entire content without char restrictions
        with open(dest_path, "r", encoding="utf-8", errors="ignore") as f:
            full_text = f.read()
    elif ext == ".pdf" and PyPDF2 is not None:
        try:
            reader = PyPDF2.PdfReader(str(dest_path))
            full_text = "\n".join([p.extract_text() or "" for p in reader.pages])
        except Exception:
            full_text = preview
    else:
        full_text = preview

    # 3. Add to standard SQLite DB
    add_document_to_db(
        doc_id=doc_id,
        name=uploaded_file.name,
        filename=dest_name,
        path=dest_path,
        doc_type=doc_type,
        ext=ext,
        size=uploaded_file.size,
        uploaded_at=uploaded_at,
        preview=preview,
        status="saved",
        active=0,
    )

    # 4. CRITICAL: Automatically chunk, embed, and store into ChromaDB
    try:
        embed_and_store_document(doc_id=doc_id, filename=uploaded_file.name, full_text=full_text)
    except Exception as e:
        st.warning(f"Metadata saved, but failed vector embedding: {e}. Check your GEMINI_API_KEY.")

    return {
        "id": doc_id,
        "name": uploaded_file.name,
        "filename": dest_name,
        "type": doc_type,
        "ext": ext,
        "size": uploaded_file.size,
        "uploaded_at": uploaded_at,
        "status": "saved",
        "preview": preview,
        "active": False,
    }


def delete_doc(doc_id):
    meta = load_metadata()
    doc = next((m for m in meta if m["id"] == doc_id), None)
    if doc:
        try:
            p = DOCS_DIR / doc.get("filename", "")
            if p.exists():
                p.unlink()
        except Exception:
            pass
            
    # 1. Wipe out any embedded chunks belonging to this document from ChromaDB
    try:
        delete_document_embeddings(doc_id)
    except Exception as e:
        print(f"Error removing embeddings: {e}")

    # 2. Remove the standard row record from SQLite
    delete_document_from_db(doc_id)


def toggle_active(doc_id, value):
    # set active flag in DB
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE documents SET active = ? WHERE id = ?", (1 if value else 0, doc_id))
    conn.commit()
    conn.close()


def main():
    init_db()
    st.set_page_config(page_title="Corpus Forge — Upload", layout="wide")

    # Inject lightweight CSS for improved visuals
    st.markdown(
        """
        <style>
        .stButton>button { background: linear-gradient(90deg,#06b6d4,#0ea5a4); color: white; border-radius:6px; padding:6px 10px; font-size:13px; line-height:1.2; }
        .doc-row { border:1px solid #e6eef0; padding:10px; margin-bottom:8px; border-radius:6px; background:#fbfcfd }
        .doc-meta { color:#475569; font-size:13px }
        .summary { background:#f8fafc; padding:10px; border-radius:6px }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Corpus Forge")
    st.caption(
        "Upload documents to build your active corpus. Supported: .txt, .md, .pdf, .py, .js"
    )

    # initialize session state helper to avoid double-saving uploads across reruns
    if "saved_upload_fingerprints" not in st.session_state:
        st.session_state["saved_upload_fingerprints"] = []

    col1, col2 = st.columns([2, 3])

    with col1:
        st.header("Upload")
        uploaded = st.file_uploader(
            "Choose files",
            accept_multiple_files=True,
            type=["txt", "md", "pdf", "py", "js", "java", "c", "cpp"],
        )
        if uploaded:
            for u in uploaded:
                # create a simple fingerprint to avoid saving the same uploaded file multiple times
                fingerprint = f"{u.name}:{u.size}"
                if fingerprint in st.session_state["saved_upload_fingerprints"]:
                    # already handled this file in a previous run
                    continue
                try:
                    m = save_upload(u)
                    st.success(f"Saved {m['name']}")
                    st.session_state["saved_upload_fingerprints"].append(fingerprint)
                except Exception as e:
                    st.error(f"Failed saving {u.name}: {e}")

        st.markdown("---")
        st.header("Document Library")
        meta = load_metadata()
        if not meta:
            st.info("No documents uploaded yet.")
        else:
            # show simple table and controls
            for m in meta:
                with st.container():
                    cols = st.columns([3, 1, 1, 1])
                    cols[0].markdown(
                        f"<div class='doc-row'><div><strong>{m['name']}</strong></div><div class='doc-meta'>Type: {m['type']} • Size: {m['size']} bytes</div></div>",
                        unsafe_allow_html=True,
                    )
                    active = cols[1].checkbox(
                        "Active", value=m.get("active", False), key=f"active_{m['id']}"
                    )
                    if active != m.get("active", False):
                        toggle_active(m["id"], active)
                    if cols[2].button("Preview", key=f"preview_{m['id']}"):
                        st.session_state["preview_doc"] = m["id"]
                    if cols[3].button("Delete", key=f"del_{m['id']}"):
                        delete_doc(m["id"])

    with col2:
        st.header("Preview & Exploration")
        preview_id = st.session_state.get("preview_doc")
        meta = load_metadata()
        if preview_id:
            doc = next((x for x in meta if x["id"] == preview_id), None)
        else:
            doc = meta[0] if meta else None

        if doc:
            st.subheader(doc["name"])
            st.write(f"Type: {doc['type']} • Uploaded: {doc['uploaded_at']}")
            if doc["type"] in ("text", "code"):
                st.code(
                    doc.get("preview", "(no preview)"),
                    language="python" if doc["type"] == "code" else None,
                )
            elif doc["type"] == ".pdf":
                st.text_area(
                    "Preview (first pages)",
                    value=doc.get("preview", "(no preview)"),
                    height=200,
                )
        else:
            st.info("Select a document from the library to preview its contents.")

        st.markdown("---")
        
        st.header("Interaction Layer")

        system_prompt = st.text_area(
            "System Prompt / Persona Instructions",
            value="You are a helpful AI assistant analyzing the active document corpus. Use the provided context to answer user queries accurately.",
            help="This configures the behavior guidelines for the Gemini model."
        )
        
        st.subheader("Retrieval Settings")
        active_meta = [m for m in meta if m.get("active")]
        if active_meta:
            st.caption(f"Currently querying across **{len(active_meta)}** active document(s).")
        else:
            st.warning("⚠️ No documents are marked 'Active' in the library. Gemini will answer without local context.")
            
        user_query = st.text_input("Ask a question about your corpus:", placeholder="e.g., Summarize the main constraints found in these files...")
        

        if st.button("Generate Answer", use_container_width=True):
            if not user_query.strip():
                st.error("Please enter a query first!")
            else:
                with st.spinner("Retrieving relevant context and invoking Gemini API..."):
                    st.info("Frontend captured query perfectly! Ready to pass to Google Gemini API.")
                    
                    st.markdown("### AI Response")
                    st.write("*(Gemini API response will render here once backend connection is coded in the next step!)*")


if __name__ == "__main__":
    main()

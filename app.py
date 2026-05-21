import streamlit as st
import os
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
import re

# Try importing PyPDF2 safely for PDF processing
try:
    import PyPDF2
except Exception:
    PyPDF2 = None

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_FILE = DATA_DIR / "corpus_forge.db"

def init_db():
    """Ensures directories exist and initializes the SQLite database tables."""
    DATA_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Documents Metadata Table (Matches your UI column structure)
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
            active INTEGER DEFAULT 1
        )
    """)
    
    # 2. Relational Chunks Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            chunk_index INTEGER,
            text_content TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()
    conn.close()


def load_metadata():
    """Fetches all documents from the SQLite database."""
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # Access columns by name like a dict
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documents")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_document_to_db(doc_id, name, filename, path, file_type, ext, size, uploaded_at, preview, status):
    """Inserts a new document record into the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO documents (id, name, filename, path, type, ext, size, uploaded_at, preview, status, active) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (doc_id, name, filename, str(path), file_type, ext, size, uploaded_at, preview, status))
    conn.commit()
    conn.close()


def toggle_document_active(doc_id, current_active_status):
    """Toggles the document active state between 1 (True) and 0 (False)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    new_status = 0 if current_active_status == 1 else 1
    cursor.execute("UPDATE documents SET active = ? WHERE id = ?", (new_status, doc_id))
    conn.commit()
    conn.close()


def delete_document_from_db(doc_id):
    """Deletes a document record and its associated text chunks from the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    cursor.execute("DELETE FROM document_chunks WHERE document_id = ?", (doc_id,))
    conn.commit()
    conn.close()



def extract_all_text(file_path):
    """Detects file extension and extracts pure text string."""
    ext = Path(file_path).suffix.lower()
    
    # Format 1 & 2: Plain text, Markdown, or Code Source files (.py, .js, etc.)
    if ext in [".txt", ".md", ".py", ".js", ".json", ".html", ".css"]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            return f"Error reading text/code file: {str(e)}"
            
    # Format 3: PDF Files
    elif ext == ".pdf":
        if PyPDF2 is None:
            return "PyPDF2 library is missing. Cannot parse PDF contents."
        try:
            reader = PyPDF2.PdfReader(str(file_path))
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n".join(text_parts)
        except Exception as e:
            return f"Error reading PDF file: {str(e)}"
            
    return ""


def chunk_text(text, chunk_size=1000, overlap=200):
    """Splits text into overlapping chunks to maintain conversational context boundaries."""
    chunks = []
    if not text:
        return chunks
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks


def format_preview_for_ui(text, max_lines=2):
    """Return a preview limited to max_lines, appending an indicator for omitted lines."""
    if not text:
        return "(no preview)"
    # Normalize and split into lines
    lines = str(text).splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    shown = lines[:max_lines]
    rest = len(lines) - max_lines
    return "\n".join(shown) + f"\n... ({rest} more lines)"


def clean_display_name(filename: str) -> str:
    """Return a human-friendly display name by removing a leading 32-char hex prefix if present."""
    if not filename:
        return ""
    # remove leading 32 hex chars followed by underscore or hyphen
    cleaned = re.sub(r'^[0-9a-fA-F]{32}[_-]', '', filename)
    return cleaned


def process_and_store_chunks(doc_id, file_path):
    """Extracts raw text, chunks it, and writes chunks to the relational database table."""
    full_text = extract_all_text(file_path)
    text_chunks = chunk_text(full_text)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    for index, chunk_content in enumerate(text_chunks):
        chunk_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO document_chunks (id, document_id, chunk_index, text_content)
            VALUES (?, ?, ?, ?)
        """, (chunk_id, doc_id, index, chunk_content))
    conn.commit()
    conn.close()


def main():
    # Automatically initialize tables on start
    init_db()
    
    st.set_page_config(
        page_title="Project Corpus Forge - Core",
        page_icon="🛠️",
        layout="wide"
    )

    st.title("Project Corpus Forge")

    # Sidebar parameters for generation controls
    with st.sidebar:
        st.header("Prompt Steering Controls")
        audience = st.selectbox("Target Audience Level", ["Beginner", "Intermediate", "Advanced / Technical"])
        response_format = st.selectbox("Response Format", ["Detailed Essay", "Bullet Point Summary", "Actionable Code Blocks"])
        creativity = st.slider("Creativity (Temperature)", min_value=0.0, max_value=1.0, value=0.3, step=0.1)
        
        st.divider()
        st.header("Cost Observability Dashboard")
        st.metric("Total API Requests", "0")
        st.metric("Token Consumption", "0 tokens")

    # Main dashboard tabs
    tab_library, tab_chat = st.tabs(["Corpus Library Management", "Context-Grounded Chat"])

    with tab_library:
        st.subheader("Ingest New Document Artifacts")
        uploaded_file = st.file_uploader(
            "Upload files to build your knowledge corpus", 
            type=["txt", "md", "pdf", "py", "js"],
            help="Supported formats: plain text, markdown, source code files, and PDFs."
        )

        if uploaded_file is not None:
            col_btn1, col_btn2 = st.columns([1, 4])
            if col_btn1.button("Ingest Document", use_container_width=True):
                doc_id = str(uuid.uuid4())
                ext = Path(uploaded_file.name).suffix.lower()
                dest_path = DOCS_DIR / f"{doc_id}{ext}"
                
                # Save the raw file down to disk
                with open(dest_path, "wb") as f:
                    f.write(uploaded_file.getvalue())

                # Grab sizing and setup structural fields
                size_bytes = len(uploaded_file.getvalue())
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Create a small text preview for the UI card
                raw_text = extract_all_text(dest_path)
                preview_text = raw_text[:120] + "..." if len(raw_text) > 120 else raw_text

                # 1. Update relational metadata table
                add_document_to_db(
                    doc_id=doc_id,
                    name=uploaded_file.name.split(".")[0],
                    filename=uploaded_file.name,
                    path=dest_path,
                    file_type="Source Code" if ext in [".py", ".js"] else "Standard Text",
                    ext=ext,
                    size=size_bytes,
                    uploaded_at=timestamp,
                    preview=preview_text if preview_text.strip() else "Binary/Empty content parsed.",
                    status="Processed Successfully"
                )

                # 2. Run chunking text pipeline immediately
                with st.spinner("Executing pipeline: parsing, splitting, and storing text chunks..."):
                    process_and_store_chunks(doc_id, dest_path)

                st.success(f"Successfully processed and chunked: {uploaded_file.name}")
                st.rerun()

        st.divider()
        st.subheader("Active Knowledge Base Corpus")

        meta = load_metadata()

        if not meta:
            st.info("Your document library is currently empty. Upload a file above to activate your data processing pipeline.")
        else:
            for idx, item in enumerate(meta):
                with st.container():
                    # Visual Card Layout matching your colleague's initial template
                    display_name = clean_display_name(item.get('filename', ''))
                    st.markdown(f"### {display_name}")
                    
                    col1, col2, col3, col4 = st.columns([1.5, 3.5, 2.5, 0.5])
                    
                    # Column 1: Active Selection Toggle Switch
                    is_active = col1.checkbox("Active Context", value=bool(item.get("active")), key=f"act_{item['id']}")
                    if is_active != bool(item.get("active")):
                        toggle_document_active(item['id'], item.get("active"))
                        st.rerun()
                    
                    # Column 2: Context Preview snippet (limit to two lines)
                    preview_text = item.get('preview', 'No preview available')
                    formatted_preview = format_preview_for_ui(preview_text, max_lines=2)
                    col2.markdown("**Preview:**")
                    col2.write(formatted_preview)
                    
                    # Column 3: Metrics details
                    col3.caption(f"Uploaded: {item['uploaded_at']}  \n⚖️ Size: {item['size']/1024:.2f} KB | Format: `{item['ext']}`")
                    
                    # Column 4: Document Wipe Actions
                    if col4.button("🗑️", key=f"del_{item['id']}", help="Delete from library"):
                        if os.path.exists(item["path"]):
                            os.remove(item["path"])
                        delete_document_from_db(item['id'])
                        st.warning(f"Removed {item['filename']} from database.")
                        st.rerun()
                st.divider()

    with tab_chat:
        st.subheader("Grounded RAG Exploration Arena")
        
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
            st.warning("No documents are marked 'Active' in the library. Gemini will answer without local context.")
            
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
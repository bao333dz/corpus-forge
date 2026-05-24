"""Streamlit UI for document ingestion, context retrieval, and Gemini workflows."""

import streamlit as st
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
import re
import google.generativeai as genai
from vector_store import ensure_document_embeddings, delete_document_embeddings, query_active_context

# Try importing PyPDF2 safely for PDF processing
try:
    import PyPDF2
except Exception:
    PyPDF2 = None

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_FILE = DATA_DIR / "corpus_forge.db"
GENERATIVE_MODEL = os.getenv("GENERATIVE_MODEL", "models/gemini-2.5-flash")

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER
        )
    """)
    
    conn.commit()
    conn.close()


def load_metadata():
    """Return all document metadata rows from SQLite."""
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
    conn.commit()
    conn.close()

def save_artifact(artifact_type, content):
    """Save (Flashcard/Quiz/Report) to database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO artifacts (id, type, content, created_at)
        VALUES (?, ?, ?, ?)
    """, (str(uuid.uuid4()), artifact_type, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def load_artifacts(artifact_type):
    """Load saved artifacts (flashcards, quizzes, reports) by type."""
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content, created_at FROM artifacts WHERE type = ? ORDER BY created_at DESC", (artifact_type,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def log_token_usage(prompt_tokens, completion_tokens, total_tokens):
    """Logs token usage into SQLite for Cost Observability."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cost_logs (timestamp, prompt_tokens, completion_tokens, total_tokens)
        VALUES (?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), prompt_tokens, completion_tokens, total_tokens))
    conn.commit()
    conn.close()

def get_aggregated_costs():
    """Retrieves total requests and token consumption."""
    if not DB_FILE.exists():
        return 0, 0
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*), SUM(total_tokens) FROM cost_logs")
        row = cursor.fetchone()
        return (row[0] or 0, row[1] or 0)
    except Exception:
        return 0, 0
    finally:
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
            return "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
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

def ensure_all_active_embeddings(active_meta):
    """
    Safely checks and generates embeddings for all currently active documents.
    Prevents querying an empty vector database.
    """
    for doc in active_meta:
        # Ensure the document has been embedded into ChromaDB
        ensure_document_embeddings(doc["id"], doc["filename"], extract_all_text(doc["path"]))

def generate_gemini_content(prompt, context, system_prompt, temperature):
    """Call Gemini with context and return the response text plus success flag."""
    if "GEMINI_API_KEY" not in os.environ:
        return "No API KEY", 0
    full_prompt = f"Context from documents:\n{context}\n\nUser Request: {prompt}"
    
    try:
        model = genai.GenerativeModel(
            model_name=GENERATIVE_MODEL,
            system_instruction=system_prompt,
            generation_config={"temperature": temperature}
        )
        response = model.generate_content(full_prompt)
        
        # Log token usage nếu có
        if hasattr(response, 'usage_metadata'):
            log_token_usage(
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count,
                response.usage_metadata.total_token_count
            )
            
        return response.text, 1
    except Exception as e:
        return f"Gemini API call error: {str(e)}", 0

def main():
    """Run the Streamlit app and render all UI sections."""
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
        total_req, total_tok = get_aggregated_costs()
        
        st.divider()
        st.header("Cost Observability Dashboard")
        st.metric("Total API Requests", total_req)
        st.metric("Token Consumption", total_tok)

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
            col_btn1, _ = st.columns([1, 4])
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
                        delete_document_embeddings(item['id'])
                        st.warning(f"Removed {item['filename']} from database.")
                        st.rerun()
                st.divider()

    with tab_chat:
        st.subheader("Retrieval-Grounded AI Workflows")
        
        active_meta = [m for m in meta if m.get("active")]
        active_doc_ids = [doc.get("id") for doc in active_meta]
        has_source_code = any(doc.get("type") == "Source Code" for doc in active_meta)
        
        if not active_meta:
            st.warning("⚠️ No active documents found. Please go to the Library tab to enable your Active Context.")
        else:
            st.caption(f"Currently utilizing **{len(active_meta)}** document(s) as context.")
            
            # Sub-tabs layout mapping out the course requirements
            sub_chat, sub_flash, sub_quiz, sub_code = st.tabs(["💬 Chat", "🗂️ Flashcards", "📝 Quizzes", "💻 Code Analysis"])

            # Base Persona matching the Prompt Steering variables from the Sidebar
            base_system_prompt = f"""
            You are an AI assistant analyzing the active document corpus. 
            Audience Level: {audience}. 
            Format Style: {response_format}.
            Strictly base your answers on the provided context. If information is missing, state it clearly.
            """

            # ================= 1. CHAT WORKFLOW =================
            with sub_chat:
                user_query = st.text_input("Ask anything about your documents:")
                if st.button("Generate Answer", key="btn_chat", use_container_width=True):
                    if user_query:
                        with st.spinner("Retrieving context and invoking Gemini API..."):
                            # Lazy check to guarantee embeddings exist
                            ensure_all_active_embeddings(active_meta)
                            
                            context = query_active_context(user_query, active_doc_ids)
                            answer, success = generate_gemini_content(user_query, context, base_system_prompt, creativity)
                            
                            st.markdown("### AI Response")
                            st.write(answer)
                            with st.expander("View Retrieved Context"):
                                st.write(context)

            # ================= 2. FLASHCARDS WORKFLOW =================
            with sub_flash:
                st.write("Automatically extract and compile study flashcards from your corpus.")
                if st.button("Generate New Flashcards", key="btn_flash", use_container_width=True):
                    with st.spinner("Synthesizing flashcards..."):
                        ensure_all_active_embeddings(active_meta)
                        context = query_active_context("Summarize key concepts", active_doc_ids, n_results=5)
                        prompt = "Extract key terms and concepts. Generate 5 Flashcards formatted cleanly in Markdown (Format: **Q:** [Question] \n **A:** [Answer])."
                        
                        flashcards, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                        if success:
                            save_artifact("flashcard", flashcards)
                            st.success("Flashcards successfully generated and archived!")
                            st.rerun()
                
                # Persistence History render
                saved_flashcards = load_artifacts("flashcard")
                for i, (content, time) in enumerate(saved_flashcards):
                    with st.expander(f"Flashcard Deck - {time}"):
                        st.markdown(content)

            # ================= 3. QUIZZES WORKFLOW =================
            with sub_quiz:
                st.write("Generate interactive multiple-choice assessment tests based on your data.")
                if st.button("Generate New Quiz", key="btn_quiz", use_container_width=True):
                    with st.spinner("Generating quiz questions..."):
                        ensure_all_active_embeddings(active_meta)
                        context = query_active_context("Extract important facts and logic", active_doc_ids, n_results=5)
                        prompt = "Generate a multiple-choice quiz with 3 questions based on the context. Provide the correct answers at the very end."
                        
                        quiz, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                        if success:
                            save_artifact("quiz", quiz)
                            st.success("Quiz successfully generated and archived!")
                            st.rerun()

                # Persistence History render
                saved_quizzes = load_artifacts("quiz")
                for i, (content, time) in enumerate(saved_quizzes):
                    with st.expander(f"Quiz Evaluation - {time}"):
                        st.markdown(content)

            # ================= 4. SOURCE CODE SPECIALIZED WORKFLOW =================
            with sub_code:
                if not has_source_code:
                    st.info("This advanced section unlocks automatically when you set a source code file (.py, .js, etc.) as part of your Active Context.")
                else:
                    st.write("Deep engineering analysis for your source code infrastructure.")
                    col1, col2 = st.columns(2)
                    
                    # 4.1. Code Review Report
                    if col1.button("Generate Code Review Report", use_container_width=True):
                        with st.spinner("Reviewing syntax structures and testing code quality..."):
                            ensure_all_active_embeddings(active_meta)
                            context = query_active_context("source code functions classes", active_doc_ids, n_results=5)
                            prompt = "Analyze the provided source code context. Generate a Code Review Report focusing on potential bugs, security vulnerabilities, and code quality improvements."
                            report, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                            if success:
                                save_artifact("code_review", report)
                                st.rerun()

                    # 4.2. Architecture Report
                    if col2.button("Generate Architecture Report", use_container_width=True):
                        with st.spinner("Mapping execution flow charts and abstract data patterns..."):
                            ensure_all_active_embeddings(active_meta)
                            context = query_active_context("architecture design patterns control flow", active_doc_ids, n_results=5)
                            prompt = "Analyze the provided source code context. Generate an Architecture and Control Flow Report explaining how the components interact and the overall system design."
                            report, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                            if success:
                                save_artifact("architecture", report)
                                st.rerun()
                                
                    # Persistence History render
                    st.divider()
                    st.markdown("#### Archived System Reports")
                    for r_type, icon in [("code_review", "🐛 Code Review"), ("architecture", "🏗️ Architecture")]:
                        saved_reports = load_artifacts(r_type)
                        for content, time in saved_reports:
                            with st.expander(f"{icon} Report - {time}"):
                                st.markdown(content)


if __name__ == "__main__":
    main()
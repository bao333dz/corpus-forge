"""Streamlit UI for document ingestion, context retrieval, and Gemini workflows."""
import streamlit as st
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
import re
import google.generativeai as genai
import json
from pyecharts import options as opts
from pyecharts.charts import WordCloud
from streamlit_echarts import st_pyecharts
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
    """Fixed-size, overlapping chunking"""
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
    
def parse_flashcards_text(text):
    """Parses Gemini string output into a structured list of flashcard dicts."""
    cards = []
    blocks = text.split("---")
    for block in blocks:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        q_text, a_text = "", ""
        for line in lines:
            if line.startswith(("Q:", "**Q:**", "Question:")):
                q_text = line.replace("**Q:**", "").replace("Q:", "").replace("Question:", "").strip()
            elif line.startswith(("A:", "**A:**", "Answer:")):
                a_text = line.replace("**A:**", "").replace("A:", "").replace("Answer:", "").strip()
        if q_text and a_text:
            cards.append({"q": q_text, "a": a_text})
    return cards

def parse_quiz_text(text):
    """Parses Gemini string output into a structured list of multiple-choice questions."""
    questions = []
    blocks = text.split("---")
    for block in blocks:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        q_text, correct = "", ""
        options = []
        for line in lines:
            if line.startswith(("Q:", "Question:")):
                q_text = line.replace("Q:", "").replace("Question:", "").strip()
            elif line.startswith(("1)", "2)", "3)", "4)")):
                options.append(line)
            elif line.startswith("Correct:"):
                correct = line.replace("Correct:", "").strip()
        if q_text and len(options) >= 2 and correct:
            questions.append({"q": q_text, "options": options, "correct": correct})
    return questions

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
            sub_chat, sub_flash, sub_quiz, sub_code, sub_viz = st.tabs(["💬 Chat", "🗂️ Flashcards", "📝 Quizzes", "💻 Code Analysis", "📊 Visualizations"])

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
                
                # Initialize Session States for Flashcards
                if "flashcards" not in st.session_state:
                    st.session_state.flashcards = []
                if "flash_index" not in st.session_state:
                    st.session_state.flash_index = 0
                if "flipped" not in st.session_state:
                    st.session_state.flipped = False

                if st.button("Generate New Flashcards", key="btn_flash", use_container_width=True):
                    with st.spinner("Synthesizing flashcards..."):
                        ensure_all_active_embeddings(active_meta)
                        context = query_active_context("Summarize key concepts", active_doc_ids, n_results=5)
                        
                        # Strict formatting prompt to ensure parsing works perfectly
                        prompt = (
                            "Extract key terms and concepts. Generate 5 Flashcards. "
                            "Separate each flashcard block explicitly with a line containing '---'. "
                            "Inside each block, format exactly like this:\n"
                            "Q: [Question text Here]\nA: [Answer text Here]"
                        )
                        
                        report, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                        if success:
                            parsed_cards = parse_flashcards_text(report)
                            if parsed_cards:
                                st.session_state.flashcards = parsed_cards
                                st.session_state.flash_index = 0
                                st.session_state.flipped = False
                            else:
                                st.error("Failed to parse flashcards format. Please try generating again.")

                # Render Interactive Flashcard Deck View
                if st.session_state.flashcards:
                    st.divider()
                    idx = st.session_state.flash_index
                    current_card = st.session_state.flashcards[idx]
                    
                    st.caption(f"Card {idx + 1} of {len(st.session_state.flashcards)}")
                    
                    # Visual Container Box
                    with st.container(border=True):
                        st.markdown(f"#### **Question:**\n{current_card['q']}")
                        st.write("")
                        
                        if st.session_state.flipped:
                            st.success(f"**Answer:**\n{current_card['a']}")
                            if st.button("Hide Answer", use_container_width=True):
                                st.session_state.flipped = False
                                st.rerun()
                        else:
                            if st.button("Flip Card 🔄", use_container_width=True):
                                st.session_state.flipped = True
                                st.rerun()

                    # Navigation deck controls
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("◀ Previous Card", disabled=(idx == 0), use_container_width=True):
                            st.session_state.flash_index -= 1
                            st.session_state.flipped = False
                            st.rerun()
                    with col2:
                        if st.button("Next Card ▶", disabled=(idx == len(st.session_state.flashcards) - 1), use_container_width=True):
                            st.session_state.flash_index += 1
                            st.session_state.flipped = False
                            st.rerun()

            # ================= 3. QUIZZES WORKFLOW =================
            with sub_quiz:
                st.write("Generate interactive multiple-choice assessment tests based on your data.")
                
                # Initialize Session States for Quiz
                if "quiz_questions" not in st.session_state:
                    st.session_state.quiz_questions = []
                if "quiz_index" not in st.session_state:
                    st.session_state.quiz_index = 0
                if "quiz_score" not in st.session_state:
                    st.session_state.quiz_score = 0
                if "quiz_feedback" not in st.session_state:
                    st.session_state.quiz_feedback = None

                if st.button("Generate New Quiz", key="btn_quiz", use_container_width=True):
                    with st.spinner("Generating quiz questions..."):
                        ensure_all_active_embeddings(active_meta)
                        context = query_active_context("Extract important facts and logic", active_doc_ids, n_results=5)
                        
                        # Strict prompt ensuring predictable text outputs for options parsing
                        prompt = (
                            "Generate a multiple-choice quiz with 3 questions based on the context. "
                            "Separate each question block explicitly with a line containing '---'. "
                            "Format each question block exactly like this:\n"
                            "Q: [Question Text]\n1) [Option 1]\n2) [Option 2]\n3) [Option 3]\n4) [Option 4]\nCorrect: [Digit 1-4]"
                        )
                        
                        report, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                        if success:
                            parsed_questions = parse_quiz_text(report)
                            if parsed_questions:
                                st.session_state.quiz_questions = parsed_questions
                                st.session_state.quiz_index = 0
                                st.session_state.quiz_score = 0
                                st.session_state.quiz_feedback = None
                            else:
                                st.error("Failed to parse quiz schema safely. Please try again.")

                # Render Stateful Interactive Quiz Assessment View
                if st.session_state.quiz_questions:
                    st.divider()
                    q_idx = st.session_state.quiz_index
                    current_q = st.session_state.quiz_questions[q_idx]
                    
                    st.caption(f"Question {q_idx + 1} of {len(st.session_state.quiz_questions)} | Current Score: {st.session_state.quiz_score}/{len(st.session_state.quiz_questions)}")
                    
                    with st.container(border=True):
                        st.markdown(f"### **{current_q['q']}**")
                        
                        # Radio input changes state directly, safe via indexed key parameters
                        user_choice = st.radio(
                            "Select your answer option:", 
                            current_q['options'], 
                            key=f"quiz_radio_state_{q_idx}"
                        )
                        
                        st.write("")
                        if st.button("Submit Answer Check", use_container_width=True):
                            chosen_digit = user_choice[0] # Extracts "1", "2", etc. from prefix
                            if chosen_digit == current_q['correct']:
                                st.session_state.quiz_feedback = ("success", "🎉 Correct answer! Well done.")
                                st.session_state.quiz_score += 1
                            else:
                                st.session_state.quiz_feedback = ("error", f"❌ Incorrect. The correct alternative was choice option {current_q['correct']}.")
                    
                    # Display submission status evaluations safely 
                    if st.session_state.quiz_feedback:
                        status_type, msg = st.session_state.quiz_feedback
                        if status_type == "success":
                            st.success(msg)
                        else:
                            st.error(msg)
                            
                        # Progression logic step transitions
                        if q_idx < len(st.session_state.quiz_questions) - 1:
                            if st.button("Proceed to Next Question ▶", use_container_width=True):
                                st.session_state.quiz_index += 1
                                st.session_state.quiz_feedback = None
                                st.rerun()
                        else:
                            st.info(f"🏁 Quiz Finished! Your final score is: {st.session_state.quiz_score} out of {len(st.session_state.quiz_questions)}")
                            if st.button("Restart/Clear Quiz", use_container_width=True):
                                st.session_state.quiz_questions = []
                                st.session_state.quiz_index = 0
                                st.session_state.quiz_score = 0
                                st.session_state.quiz_feedback = None
                                st.rerun()

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

            # ================= 5. VISUALIZATION WORKFLOW =================
            with sub_viz:
                st.write("Explore the core keywords in your documents through an interactive visualization.")

                if st.button("Generate Interactive Word Cloud", use_container_width=True):
                    with st.spinner("Asking AI to analyze and extract keywords..."):
                        
                        # Ensure the documents are embedded before querying
                        ensure_all_active_embeddings(active_meta)
                        
                        # 1. Retrieve context from active files
                        context = query_active_context("Extract main keywords and concepts", active_doc_ids, n_results=10)
                        
                        # 2. Strict prompt forcing Gemini to return a valid JSON array
                        prompt = """
                        Analyze the provided context and extract the 30 most important keywords or concepts.
                        Assign a weight to each word from 10 to 100 based on its importance.
                        Return ONLY a valid JSON array of arrays in this exact format:
                        [["Keyword1", 100], ["Keyword2", 85], ["Keyword3", 40]]
                        Do not include markdown tags like ```json. Just the raw array.
                        """
                        
                        report, success = generate_gemini_content(prompt, context, base_system_prompt, creativity)
                        
                        if success:
                            try:
                                # 3. Process the JSON string returned by Gemini
                                clean_json = report.replace('```json', '').replace('```', '').strip()
                                words_data = json.loads(clean_json)
                                
                                # 4. Render the Word Cloud using Pyecharts
                                wordcloud = (
                                    WordCloud()
                                    .add(
                                        series_name="Importance Weight",
                                        data_pair=words_data,
                                        word_size_range=[15, 80],
                                        shape="circle"
                                    )
                                    .set_global_opts(
                                        title_opts=opts.TitleOpts(
                                            title="Corpus Keyword Cloud",
                                            subtitle="Hover over a keyword to view its weight",
                                            pos_left="center"
                                        ),
                                        tooltip_opts=opts.TooltipOpts(is_show=True)
                                    )
                                )
                                
                                # 5. Display the chart in the Streamlit UI
                                st.divider()
                                st_pyecharts(wordcloud, height="500px")
                                
                            except json.JSONDecodeError:
                                st.error("Error: AI did not return a valid JSON format. Please try generating again!")
                            except Exception as e:
                                st.error(f"An error occurred while rendering the chart: {e}")

if __name__ == "__main__":
    main()
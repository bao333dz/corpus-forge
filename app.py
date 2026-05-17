import streamlit as st
import os
import json
import uuid
from datetime import datetime
from pathlib import Path

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"
METADATA_FILE = DATA_DIR / "docs.json"


def ensure_dirs():
    DATA_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)


def load_metadata():
    if not METADATA_FILE.exists():
        return []
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_metadata(meta):
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


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

    # quick content extract for preview
    preview = ""
    if ext in (".txt", ".md"):
        preview = read_text_file(dest_path)
        doc_type = "text"
    elif ext in (".py", ".js", ".java", ".c", ".cpp"):
        preview = read_text_file(dest_path)
        doc_type = "code"
    elif ext == ".pdf":
        preview = extract_text_from_pdf(dest_path)
        doc_type = "pdf"
    else:
        doc_type = "other"

    meta = {
        "id": doc_id,
        "name": uploaded_file.name,
        "filename": dest_name,
        "type": doc_type,
        "ext": ext,
        "size": uploaded_file.size,
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
        "status": "saved",
        "preview": preview,
        "active": False,
    }
    all_meta = load_metadata()
    all_meta.append(meta)
    save_metadata(all_meta)
    return meta


def delete_doc(doc_id):
    meta = load_metadata()
    new_meta = []
    for m in meta:
        if m["id"] == doc_id:
            # remove file
            try:
                p = DOCS_DIR / m["filename"]
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        else:
            new_meta.append(m)
    save_metadata(new_meta)


def toggle_active(doc_id, value):
    meta = load_metadata()
    for m in meta:
        if m["id"] == doc_id:
            m["active"] = value
    save_metadata(meta)


def main():
    ensure_dirs()
    st.set_page_config(page_title="Corpus Forge — Upload", layout="wide")

    # Inject lightweight CSS for improved visuals
    st.markdown(
        """
        <style>
        .stButton>button { background: linear-gradient(90deg,#06b6d4,#0ea5a4); color: white; border-radius:6px; }
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
        st.header("Preview & Actions")
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
            elif doc["type"] == "pdf":
                st.text_area(
                    "Preview (first pages)",
                    value=doc.get("preview", "(no preview)"),
                    height=300,
                )
            else:
                st.write("No preview available for this file type.")

        st.markdown("---")
        st.header("Actions")
        st.write(
            "Process documents to prepare for retrieval and AI (this prototype saves files and extracts basic preview)."
        )
        if st.button("Process all (prototype)"):
            st.success("Processing completed (prototype).")

        st.markdown("---")
        st.header("Summary")
        meta = load_metadata()
        total = len(meta)
        active = sum(1 for m in meta if m.get("active"))
        st.write(f"Total documents: {total}")
        st.write(f"Active documents: {active}")


if __name__ == "__main__":
    main()

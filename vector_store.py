import os
import chromadb
import streamlit as st
import google.generativeai as genai
from pathlib import Path

BASE_DIR = Path(__file__).parent
CHROMA_DIR = BASE_DIR / "data" / "chroma_db"

# Ensure the vector storage directory exists
CHROMA_DIR.parent.mkdir(exist_ok=True)

# 1. Initialize Persistent ChromaDB Client
# This ensures embeddings are saved to disk and don't vanish when the app restarts
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

# Use a supported Google GenAI embedding model (override with EMBEDDING_MODEL env var)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-2-preview")

# Configure your Gemini API key (Make sure you set this in your environment variables or Streamlit secrets)
api_key = st.secrets.get("GEMINI_API_KEY")
genai.configure(api_key=api_key)

def get_or_create_collection():
    """Retrieves or initializes the main document corpus collection."""
    return chroma_client.get_or_create_collection(name="corpus_collection")


def chunk_text(text, chunk_size=1000, chunk_overlap=200):
    """
    Splits long strings into smaller overlapping text segments.
    This helps keep retriever blocks contextually relevant and fits LLM window limits.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - chunk_overlap)
    return chunks


def embed_and_store_document(doc_id, filename, full_text):
    """
    Chunks a text string, generates Google embeddings, and writes to ChromaDB.
    """
    if not full_text.strip():
        return
        
    collection = get_or_create_collection()
    chunks = chunk_text(full_text)
    
    documents = []
    embeddings = []
    ids = []
    metadatas = []
    # The code loops through every single text chunk and sends it to the Google Gemini Embedding API
    for i, chunk in enumerate(chunks):
        response = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=chunk,
            task_type="retrieval_document"
        )
        
        vector = response['embedding']
        
        documents.append(chunk)
        embeddings.append(vector)
        ids.append(f"{doc_id}_chunk_{i}")
        metadatas.append({
            "doc_id": doc_id,
            "filename": filename,
            "chunk_index": i
        })
        
    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas
    )


def document_embeddings_exist(doc_id):
    """Check if any embeddings exist for a document id."""
    collection = get_or_create_collection()
    try:
        results = collection.get(where={"doc_id": doc_id})
        return bool(results and results.get("ids"))
    except Exception:
        return False


def ensure_document_embeddings(doc_id, filename, full_text):
    """Create embeddings only if they do not already exist for the document."""
    if not full_text or not full_text.strip():
        return False
    if document_embeddings_exist(doc_id):
        return False
    embed_and_store_document(doc_id, filename, full_text)
    return True


def delete_document_embeddings(doc_id):
    """Removes all stored vectors associated with a deleted file."""
    collection = get_or_create_collection()
    collection.delete(where={"doc_id": doc_id})

def query_active_context(query_text, active_doc_ids, n_results=3):
    """
    Performs semantic similarity vector search in ChromaDB,
    filtering results strictly by the provided active document IDs.
    """
    if not active_doc_ids:
        return ""

    try:
        collection = get_or_create_collection()
        
        # 1. Generate an embedding vector for the user query
        response = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=query_text,
            task_type="retrieval_query"
        )
        query_vector = response['embedding']

        # 2. Query ChromaDB with a metadata filter restricting results to active documents
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
            where={"doc_id": {"$in": active_doc_ids}}
        )

        # 3. Assemble matches into a single context string
        context_blocks = []
        if results and 'documents' in results and results['documents']:
            for document_list in results['documents']:
                for block in document_list:
                    context_blocks.append(block)
                    
        return "\n\n---\n\n".join(context_blocks)
    except Exception as e:
        return f"(Error retrieving vector context: {e})"
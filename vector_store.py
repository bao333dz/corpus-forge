import os
import chromadb
from chromadb.config import Settings
import google.generativeai as genai
from pathlib import Path

BASE_DIR = Path(__file__).parent
CHROMA_DIR = BASE_DIR / "data" / "chroma_db"

# Ensure the vector storage directory exists
CHROMA_DIR.parent.mkdir(exist_ok=True)

# 1. Initialize Persistent ChromaDB Client
# This ensures embeddings are saved to disk and don't vanish when the app restarts
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

# Use the official Google GenAI Text Embedding model
EMBEDDING_MODEL = "models/text-embedding-004"

# Configure your Gemini API key (Make sure you set this in your environment variables or Streamlit secrets)
if "GEMINI_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])


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
    
    for i, chunk in enumerate(chunks):
        response = genai.embed_content(
            model=EMBEDDING_MODEL,
            contents=chunk,
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


def delete_document_embeddings(doc_id):
    """Removes all stored vectors associated with a deleted file."""
    collection = get_or_create_collection()
    collection.delete(where={"doc_id": doc_id})
# Project Report

#### The Team members

* Names, epita email addresses, and GitHub usernames of all team members.
Tuan Minh HOANG - tuan-minh.hoang@epita.fr - tuanminhhoang
Abdulaziz Eusman - abdulaziz.eusman@epita.fr - azoz-eu
Bao Duong - bao.duong@epita.fr - bao333dz
---

#### Initial Design

**Initial Architecture:**
- **Three-Layer Stack**: Frontend (Streamlit UI) → Backend (Python processing) → Data Layer (SQLite + ChromaDB)
- **Two-Database Approach**: SQLite for structured metadata/chunks, ChromaDB for vector embeddings
- **Lazy Embedding Generation**: Only create embeddings when user queries (not on upload)
- **Modular Separation**: `app.py` (UI logic), `vector_store.py` (embedding/retrieval), separate utility functions

**Assumptions:**
- Users would upload 10-100 documents (not thousands)
- Queries would be relatively short (~50-100 tokens)
- Gemini API would be consistently available and responsive
- Document sizes would be reasonable (<50MB per file)
- Users would primarily interact via web UI (not API)

**Technical Choices:**
- **Streamlit** for UI: Rapid development without HTML/CSS/JS overhead
- **SQLite** for metadata: Zero-config, file-based, no server required
- **ChromaDB** for vectors: Lightweight, persistent, perfect for embeddings
- **Google Gemini API**: State-of-the-art LLM with low latency and cost
- **PyPDF2** for PDF parsing: Simple, reliable for text extraction
- **Pyecharts** for visualizations: Interactive charts with Streamlit integration

---

#### Engineering Decisions

For each major decision:

* what alternatives were considered?
* why was this solution chosen?

---

#### Who Did What?

* Document how the project was originally divided among each team member.
- Abdulaziz took care of the UI first, Minh and Bao took care of the backend
* Document how responsibilities possibly evolved over time.
- 
---

#### AI Collaboration

Document how AI tools were used.

* What tools were used for what purposes?
- Copilot was mostly used for bugs detection, but it also did help in setting up the logic back bone and first layer of the UI.

* How did AI influence design and implementation decisions?
- You can feel the design is "AI" with all the icon and things, however, the logical parts are intiallized by us and if we need AI's help, we try to steer it to our way. 

* How did AI impact your learning and development process?
* How did you evaluate AI-generated suggestions?
* How did you detect and handle AI errors or limitations?

---

#### Failures and Iterations

Document:

* what failed?
* what surprised you?
* what required redesign?

---

#### “When AI Failed or Was Wrong”

Document cases where AI-generated advice, code, or explanations were:

* incomplete
* misleading
* incorrect
* inefficient

Explain how you detected the issue and how you resolved it.

---

#### Lessons Learned

Reflect on:

* technical growth
* workflow improvements
* Strengths and limitations of AI-assisted development

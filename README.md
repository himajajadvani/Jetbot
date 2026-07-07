# ✈️ JetBot – AI-Powered Private Jet Charter Assistant

JetBot is an AI-powered private jet charter assistant that enables users to search for one-way, round-trip, and multi-leg private jet flights through natural language conversations. It combines **LangGraph**, **LangChain**, **Groq LLM**, **FastAPI**, and the **Avinode API** to provide an intelligent flight search experience with real-time aircraft recommendations.

The system automatically resolves airports, supports multiple currencies, remembers conversations using Redis, and provides an interactive chat interface built with Streamlit.

---

## 🚀 Features

- Natural language flight search
- One-way, round-trip, and multi-leg trip support
- Automatic airport resolution
- Alternative airport suggestions
- Aircraft recommendations with pricing
- Multi-currency price conversion (USD, INR, EUR, GBP, CHF, AED, JPY, CAD, AUD)
- Persistent conversation memory using Redis
- Retry middleware for improved reliability
- Interactive Streamlit chat interface

---

## 🛠️ Tech Stack

### Backend

- Python
- FastAPI
- LangChain
- LangGraph

### AI

- Groq Llama 4
- Prompt Engineering

### APIs

- Avinode API
- ExchangeRate API

### Database & Memory

- Redis

### Frontend

- Streamlit

### Other Libraries

- Requests
- Pydantic
- python-dotenv

---

## 📂 Project Structure

```text
jetbot/
│
├── agent/
│   ├── __init__.py      # Package initializer
│   ├── core.py          # Agent memory, middleware, and logic
│   └── prompts.py       # LLM System prompts and rules
│
├── config/
│   ├── __init__.py      # Package initializer
│   └── llm_config.py    # LLM configuration (Groq)
│
├── tools/
│   ├── __init__.py      # Package initializer
│   └── avinode_tool.py  # Avinode flight search and airport resolution tool
│
├── .env                 # Environment variables (local config)
├── .gitignore           # Git ignore configurations
├── currency.py          # Currency rates fetching and conversions
├── favicon.png          # UI page icon
├── main.py              # FastAPI server (Backend)
├── redis_store.py       # Redis checkpointer & UI message store implementation
├── requirements.txt     # Python dependencies
└── streamlit_app.py     # Streamlit app (Frontend UI)
```

---

## 🔑 Environment Variables

Create a `.env` file in the project root with the following keys:

```env
GROQ_API_KEY=your_groq_api_key

AVINODE_AUTH_TOKEN=your_avinode_token

EXCHANGE_RATE_API_KEY=your_exchange_rate_api_key

REDIS_URL=redis://localhost:6379/0
```

---

## ⚙️ Setup & Installation

### 1. Prerequisites

- **Python 3.10+**
- **Docker** (for running Redis)

### 2. Install Dependencies
Install the required libraries:

```bash
pip install -r requirements.txt
```

---

## 🏃 Running the Project

Follow these steps to run the backend server and frontend client locally:

### 1. Start Redis

Ensure you have Redis running (via Docker):

```bash
docker run -d --name redis -p 6379:6379 redis
```

### 2. Start FastAPI Server (Backend)

Run the backend server on port `8000`:

```bash
uvicorn main:app --reload --port 8000
```

### 3. Start Streamlit Application (Frontend)

Run the client UI:

```bash
streamlit run streamlit_app.py
```

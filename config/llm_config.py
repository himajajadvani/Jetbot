from langchain_groq import ChatGroq
from dotenv import load_dotenv
import os

# 🔹 load .env file
load_dotenv()

groq_llm = ChatGroq(
    model="meta-llama/llama-4-scout-17b-16e-instruct",
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0
)
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://tutor:tutor@localhost:5432/tutor_db")
RAG_DIR = os.getenv("RAG_DIR", "/app/app/storage/rag")

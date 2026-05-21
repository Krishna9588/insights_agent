"""
vector_db.py
============
Supabase pgvector integration for storing and retrieving chat embeddings.

The system stores chat messages as embeddings to enable semantic search
across past conversations and provide rich context to the LLM.

Setup required:
1. Create Supabase project
2. Enable pgvector extension
3. Set SUPABASE_URL and SUPABASE_KEY env vars
"""

import logging
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
import os

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

log = logging.getLogger("vector_db")


class VectorDB:
    """Supabase pgvector wrapper for semantic search over chat history."""

    def __init__(self):
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_KEY")
        self.embedding_model = None
        self.client = None

        if not self.url or not self.key:
            log.warning("SUPABASE_URL or SUPABASE_KEY not set. Vector DB disabled.")
            return

        try:
            from supabase import create_client

            self.client = create_client(self.url, self.key)

            # Initialize embedding model (all-MiniLM-L6-v2 is lightweight, free)
            if SentenceTransformer:
                self.embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

            self._init_tables()
        except Exception as e:
            log.error(f"Failed to initialize Supabase: {e}")

    def _init_tables(self):
        """Create tables if they don't exist."""
        if not self.client:
            return

        try:
            # Create table via SQL if needed
            # This is a one-time setup that should be done via Supabase dashboard
            log.info("Assuming pgvector tables already created in Supabase")
        except Exception as e:
            log.warning(f"Could not verify tables: {e}")

    def is_enabled(self) -> bool:
        """Check if vector DB is properly configured."""
        return self.client is not None and self.embedding_model is not None

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text."""
        if not self.is_enabled():
            return None

        try:
            embedding = self.embedding_model.encode(text)
            return embedding.tolist()
        except Exception as e:
            log.error(f"Failed to generate embedding: {e}")
            return None

    def store_chat_message(
        self,
        project_name: str,
        session_id: str,
        message_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store a chat message with its embedding."""
        if not self.is_enabled():
            return True  # Silently skip if vector DB disabled

        try:
            embedding = self.embed_text(content)
            if not embedding:
                return False

            record = {
                "project_name": project_name,
                "session_id": session_id,
                "message_id": message_id,
                "role": role,
                "content": content,
                "embedding": embedding,
                "metadata": json.dumps(metadata or {}),
                "created_at": datetime.utcnow().isoformat(),
            }

            self.client.table("chat_embeddings").insert(record).execute()
            return True
        except Exception as e:
            log.error(f"Failed to store embedding: {e}")
            return False

    def search_similar(
        self,
        project_name: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Search for similar past messages using semantic similarity."""
        if not self.is_enabled():
            return []

        try:
            query_embedding = self.embed_text(query)
            if not query_embedding:
                return []

            # Use Supabase RPC to call pgvector similarity search
            response = self.client.rpc(
                "search_chat_embeddings",
                {
                    "p_project_name": project_name,
                    "p_query_embedding": query_embedding,
                    "p_limit": limit,
                    "p_threshold": threshold,
                },
            ).execute()

            results = []
            if response.data:
                for row in response.data:
                    results.append({
                        "message_id": row.get("message_id"),
                        "role": row.get("role"),
                        "content": row.get("content"),
                        "session_id": row.get("session_id"),
                        "similarity": row.get("similarity"),
                        "created_at": row.get("created_at"),
                    })
            return results
        except Exception as e:
            log.error(f"Search failed: {e}")
            return []

    def get_project_context(
        self,
        project_name: str,
        limit: int = 20,
    ) -> str:
        """Get recent context from all chats in a project."""
        if not self.is_enabled():
            return ""

        try:
            response = (
                self.client.table("chat_embeddings")
                .select("role, content, created_at")
                .eq("project_name", project_name)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

            context_lines = []
            if response.data:
                for row in reversed(response.data):  # Reverse to chronological
                    role = "User" if row["role"] == "user" else "Assistant"
                    context_lines.append(f"{role}: {row['content'][:200]}")

            return "\n".join(context_lines)
        except Exception as e:
            log.error(f"Failed to get project context: {e}")
            return ""

    def delete_session_embeddings(self, session_id: str) -> bool:
        """Delete all embeddings for a session."""
        if not self.is_enabled():
            return True

        try:
            self.client.table("chat_embeddings").delete().eq(
                "session_id", session_id
            ).execute()
            return True
        except Exception as e:
            log.error(f"Failed to delete embeddings: {e}")
            return False


# Global instance
_vector_db_instance: Optional[VectorDB] = None


def get_vector_db() -> VectorDB:
    """Get or create singleton vector DB instance."""
    global _vector_db_instance
    if _vector_db_instance is None:
        _vector_db_instance = VectorDB()
    return _vector_db_instance


def setup_vector_db_sql():
    """
    SQL to run in Supabase dashboard to set up pgvector tables.
    Run this once in your Supabase project:

    1. Enable pgvector extension
    2. Run the SQL below
    """
    return """
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Create chat embeddings table
CREATE TABLE IF NOT EXISTS chat_embeddings (
    id BIGSERIAL PRIMARY KEY,
    project_name TEXT NOT NULL,
    session_id UUID NOT NULL,
    message_id UUID NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(384),  -- all-MiniLM-L6-v2 produces 384-dim embeddings
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(message_id)
);

-- Create index for faster similarity search
CREATE INDEX ON chat_embeddings USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Create index on project_name for filtering
CREATE INDEX ON chat_embeddings (project_name, created_at DESC);

-- Search function using cosine similarity
CREATE OR REPLACE FUNCTION search_chat_embeddings(
    p_project_name TEXT,
    p_query_embedding VECTOR(384),
    p_limit INT DEFAULT 5,
    p_threshold FLOAT DEFAULT 0.5
)
RETURNS TABLE (
    message_id UUID,
    role TEXT,
    content TEXT,
    session_id UUID,
    similarity FLOAT,
    created_at TIMESTAMP
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ce.message_id,
        ce.role,
        ce.content,
        ce.session_id,
        (1 - (ce.embedding <=> p_query_embedding))::FLOAT as similarity,
        ce.created_at
    FROM chat_embeddings ce
    WHERE ce.project_name = p_project_name
    AND (1 - (ce.embedding <=> p_query_embedding)) > p_threshold
    ORDER BY ce.embedding <=> p_query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;
"""


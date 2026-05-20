-- Enable pgvector for the Data Ladder (RAG)
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. CONFIGURATIONS
-- Stores user settings, multiple drive links, and API keys
CREATE TABLE system_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL, -- If you ever add basic auth, otherwise can be omitted
    drive_links JSONB DEFAULT '[]', -- Array of Google Drive folder URLs
    huggingface_key TEXT,
    gemini_key TEXT,
    theme_preference TEXT DEFAULT 'dark',
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 2. ACCOUNTS (Company Name / File Name / Project)
-- This is your main "History Tab" container
CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL, -- e.g., "Groww", "Q3 Earnings Call", "Batch Analysis 1"
    type TEXT NOT NULL, -- 'company', 'single_transcript', 'merged_transcripts'
    created_at TIMESTAMP DEFAULT NOW()
);

-- 3. DOCUMENTS (Google Drive Files & Raw Text)
-- Tracks the status of files being pulled from Drive
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    drive_file_id TEXT, -- The ID from Google Drive
    file_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- 'pending', 'downloading', 'processed', 'failed'
    raw_text TEXT, -- The extracted transcript text
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. THE DATA LADDER (Insights & Vectors)
-- Stores structured facts and embeddings for Copilot retrieval
CREATE TABLE data_ladder (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    insight_type TEXT NOT NULL, -- 'problem', 'competitor_mention', 'summary'
    content TEXT NOT NULL, -- The structured JSON or Markdown extracted by Gemini
    embedding VECTOR(384), -- 384 dimensions for all-MiniLM-L6-v2 HuggingFace model
    created_at TIMESTAMP DEFAULT NOW()
);

-- 5. COPILOT CHAT LOGS
-- Maintains history for each specific account/company
CREATE TABLE chat_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    role TEXT NOT NULL, -- 'user' or 'assistant'
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
--
-- Daygle Mail Archiver – Database Schema
-- This file is executed automatically on first run by the Postgres container.
--

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,

    -- IMAP metadata
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid BIGINT NOT NULL,

    -- Email metadata
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    date TIMESTAMP,
    storage_path TEXT NOT NULL,

    -- Internal metadata
    created_at TIMESTAMP DEFAULT NOW(),

    -- Full-text search vector
    search_vector tsvector
);

-- Ensure uniqueness of messages per account/folder/uid
CREATE UNIQUE INDEX IF NOT EXISTS messages_unique_idx
    ON messages (account, folder, uid);

-- Full-text search trigger function
CREATE OR REPLACE FUNCTION messages_search_vector_update()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        to_tsvector(
            'simple',
            coalesce(NEW.subject, '') || ' ' ||
            coalesce(NEW.sender, '') || ' ' ||
            coalesce(NEW.recipients, '')
        );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to maintain search_vector on insert/update
CREATE TRIGGER messages_search_vector_trigger
BEFORE INSERT OR UPDATE ON messages
FOR EACH ROW EXECUTE FUNCTION messages_search_vector_update();

-- GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS messages_search_vector_idx
    ON messages
    USING GIN (search_vector);
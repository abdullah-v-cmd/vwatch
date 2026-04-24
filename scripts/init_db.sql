-- V-Watch Database Initialization Script
-- Run automatically on first PostgreSQL startup via docker-compose

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Indexes for better query performance (created after SQLAlchemy creates tables)
-- These will be applied after the app's create_tables() runs

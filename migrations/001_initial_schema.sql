-- ============================================================
-- Voice Khata — Supabase Migration #001: Initial Schema
--
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- Creates cloud-side tables mirroring the local IndexedDB schema.
-- Server uses service_role key (bypasses RLS); anon key gets ZERO access.
-- ============================================================

-- 1. Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. Tables

CREATE TABLE IF NOT EXISTS customers (
  id                 TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  city               TEXT NOT NULL,
  phone_enc          TEXT NOT NULL DEFAULT '',
  address_enc        TEXT NOT NULL DEFAULT '',
  opt_in_whatsapp    BOOLEAN NOT NULL DEFAULT false,
  preferred_language TEXT NOT NULL DEFAULT 'hi',
  total_due          NUMERIC(12,2) NOT NULL DEFAULT 0,
  due_since          TIMESTAMPTZ,
  last_payment_at    TIMESTAMPTZ,
  last_reminder_at   TIMESTAMPTZ,
  last_credit_at     TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  customer_id       TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  type              TEXT NOT NULL CHECK (type IN ('credit','payment','adjustment')),
  amount            NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
  "timestamp"       TIMESTAMPTZ NOT NULL DEFAULT now(),
  note              TEXT DEFAULT '',
  voice_transcript  TEXT DEFAULT '',
  source            TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('voice','manual')),
  reverses_txn_id   UUID REFERENCES transactions(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reminder_log (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  customer_id   TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  channel       TEXT NOT NULL DEFAULT 'whatsapp',
  kind          TEXT NOT NULL CHECK (kind IN ('first','repeat','weekly','high_due','thank_you')),
  sent_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  status        TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','failed','pending')),
  text_sent     TEXT DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Indexes
CREATE INDEX IF NOT EXISTS idx_customers_name    ON customers (name);
CREATE INDEX IF NOT EXISTS idx_customers_due     ON customers (total_due) WHERE total_due > 0;
CREATE INDEX IF NOT EXISTS idx_customers_updated ON customers (updated_at);
CREATE INDEX IF NOT EXISTS idx_txn_customer_ts   ON transactions (customer_id, "timestamp");
CREATE INDEX IF NOT EXISTS idx_txn_created       ON transactions (created_at);
CREATE INDEX IF NOT EXISTS idx_reminder_customer  ON reminder_log (customer_id, sent_at);

-- 4. Row-Level Security (enabled, no anon policies → locked down)
ALTER TABLE customers    ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminder_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings     ENABLE ROW LEVEL SECURITY;

-- 4b. Role Grants (gives service_role full read/write for server-side sync)
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT ALL ON ALL ROUTINES IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON ROUTINES TO service_role;

-- 5. Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customers_updated_at
  BEFORE UPDATE ON customers
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_settings_updated_at
  BEFORE UPDATE ON settings
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

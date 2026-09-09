-- AI Commerce Operator — core schema (Postgres + pgvector)
-- Run once on a fresh DB. ORM models in app/db/models.py mirror this.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector, for later semantic search

CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email       VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stores (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    marketplace           VARCHAR(16) NOT NULL DEFAULT 'US',
    seller_id             VARCHAR(128),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stores_user ON stores(user_id);

CREATE TABLE IF NOT EXISTS product_evaluations (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    store_id     UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name         VARCHAR(255) NOT NULL,
    inputs       JSONB NOT NULL,
    economics    JSONB NOT NULL,
    subscores    JSONB NOT NULL,
    score        INTEGER NOT NULL,
    verdict      VARCHAR(16) NOT NULL,
    explanation  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_user ON product_evaluations(user_id);
CREATE INDEX IF NOT EXISTS idx_eval_store ON product_evaluations(store_id);

CREATE TABLE IF NOT EXISTS products (
    id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id  UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    asin      VARCHAR(16) NOT NULL,
    title     TEXT,
    price     DOUBLE PRECISION,
    cogs      DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id);
CREATE INDEX IF NOT EXISTS idx_products_asin ON products(asin);

CREATE TABLE IF NOT EXISTS recommendations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id    UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    module      VARCHAR(32) NOT NULL,        -- product_hunter | ppc | inventory | listing
    severity    VARCHAR(16) NOT NULL DEFAULT 'info',
    title       VARCHAR(255) NOT NULL,
    detail      JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reco_store ON recommendations(store_id);

CREATE TABLE IF NOT EXISTS integration_credentials (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id          UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    provider          VARCHAR(32) NOT NULL,
    encrypted_secret  TEXT NOT NULL,
    nonce             VARCHAR(64) NOT NULL,
    key_version       VARCHAR(32) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_credentials_store ON integration_credentials(store_id);

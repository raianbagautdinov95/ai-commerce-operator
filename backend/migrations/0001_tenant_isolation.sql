-- Add tenant ownership to evaluations created by older versions.
-- Run inside a transaction before deploying the tenant-aware application.
ALTER TABLE product_evaluations
    ADD COLUMN IF NOT EXISTS store_id UUID REFERENCES stores(id) ON DELETE CASCADE;

UPDATE product_evaluations AS evaluation
SET store_id = (
    SELECT stores.id
    FROM stores
    WHERE stores.user_id = evaluation.user_id
    ORDER BY stores.created_at ASC
    LIMIT 1
)
WHERE evaluation.store_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM product_evaluations WHERE store_id IS NULL) THEN
        RAISE EXCEPTION 'Cannot migrate evaluations without a matching store';
    END IF;
END $$;

ALTER TABLE product_evaluations ALTER COLUMN store_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eval_store ON product_evaluations(store_id);

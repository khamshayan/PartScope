-- PartScope — initial schema
--
-- Three tables, each earning its place in the relational store:
--   parts_price_history  the time series the forecaster trains on
--   rfqs                 one submitted request, kept verbatim
--   quote_line_items     the enriched result rows, one per RFQ line
--
-- The parts catalog is NOT here. Catalog specs differ per category (an op-amp
-- has slew rate, a connector has shell size) and would need either a wide
-- sparse table or an EAV join; that document lives in Mongo instead.

-- ---------------------------------------------------------------------------
-- Price history
-- ---------------------------------------------------------------------------
-- ~2.6M rows. No surrogate primary key: rows are only ever read in bulk by
-- (part_mpn, week_start) and never addressed individually, so a BIGSERIAL would
-- cost write throughput during the COPY and buy nothing back.
CREATE TABLE IF NOT EXISTS parts_price_history (
    part_mpn       TEXT           NOT NULL,
    week_start     DATE           NOT NULL,
    broker_id      TEXT           NOT NULL,
    quoted_price   NUMERIC(12, 4) NOT NULL,
    quoted_qty     INTEGER        NOT NULL,
    lead_time_days INTEGER        NOT NULL
);

-- Every pricing read is "give me all quotes for one part over a week range",
-- so this composite covers the access pattern exactly.
-- The seeder drops and recreates these around the bulk load; see scripts/seed.py.
CREATE INDEX IF NOT EXISTS idx_price_history_mpn_week
    ON parts_price_history (part_mpn, week_start);

CREATE INDEX IF NOT EXISTS idx_price_history_week
    ON parts_price_history (week_start);

-- ---------------------------------------------------------------------------
-- RFQs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rfqs (
    id          BIGSERIAL   PRIMARY KEY,
    -- The raw input is kept exactly as submitted. When a parse goes wrong, the
    -- original text is the only thing that lets you see why.
    raw_input   TEXT        NOT NULL,
    source_type TEXT        NOT NULL
        CHECK (source_type IN ('text', 'email', 'spreadsheet')),
    source_name TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfqs_created_at ON rfqs (created_at DESC);

-- ---------------------------------------------------------------------------
-- Quote line items
-- ---------------------------------------------------------------------------
-- One row per line of the RFQ, holding the full enriched result: what it
-- matched to, what it costs, how volatile the market is, and how much
-- counterfeit testing it warrants.
CREATE TABLE IF NOT EXISTS quote_line_items (
    id                  BIGSERIAL      PRIMARY KEY,
    rfq_id              BIGINT         NOT NULL
        REFERENCES rfqs (id) ON DELETE CASCADE,
    line_no             INTEGER        NOT NULL,

    -- what came in
    input_string        TEXT           NOT NULL,
    quantity            INTEGER,

    -- phase 1: matching. All nullable — a no_match line is a real result that
    -- must still be persisted and shown, not an error to be dropped.
    matched_mpn         TEXT,
    manufacturer        TEXT,
    confidence          NUMERIC(4, 3),
    match_method        TEXT,
    alternates          JSONB          NOT NULL DEFAULT '[]'::jsonb,
    near_misses         JSONB          NOT NULL DEFAULT '[]'::jsonb,

    -- phase 2: pricing
    price_p25           NUMERIC(12, 4),
    price_p50           NUMERIC(12, 4),
    price_p75           NUMERIC(12, 4),
    forecast_price      NUMERIC(12, 4),
    forecast_model      TEXT,
    heat_index          NUMERIC(8, 3),
    volatility_flag     TEXT
        CHECK (volatility_flag IS NULL
               OR volatility_flag IN ('STABLE', 'ELEVATED', 'VOLATILE')),

    -- phase 6: test-flow routing. risk_reasons holds the itemised breakdown
    -- ("Obsolete: +30"); the score alone is not auditable, and auditability is
    -- the entire reason this step is rules-based rather than learned.
    risk_score          INTEGER,
    test_recommendation TEXT,
    risk_reasons        JSONB          NOT NULL DEFAULT '[]'::jsonb,
    est_turnaround_hours INTEGER,

    created_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),

    UNIQUE (rfq_id, line_no)
);

CREATE INDEX IF NOT EXISTS idx_line_items_rfq ON quote_line_items (rfq_id);
CREATE INDEX IF NOT EXISTS idx_line_items_mpn ON quote_line_items (matched_mpn);

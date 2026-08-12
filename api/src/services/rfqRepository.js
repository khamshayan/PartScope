import { query, withTransaction } from '../db/postgres.js';

/**
 * Persistence for RFQs and their enriched line items.
 *
 * The RFQ and its lines are written in one transaction. A half-saved analysis
 * -- an RFQ row with no lines, or lines whose parent vanished -- is worse than
 * a failed save, because it looks like a successful one.
 */

const INSERT_RFQ = `
  INSERT INTO rfqs (raw_input, source_type, source_name)
  VALUES ($1, $2, $3)
  RETURNING id, created_at
`;

const INSERT_LINE = `
  INSERT INTO quote_line_items (
    rfq_id, line_no, input_string, quantity,
    matched_mpn, manufacturer, confidence, match_method, alternates, near_misses,
    price_p25, price_p50, price_p75, forecast_price, forecast_model,
    heat_index, volatility_flag
  ) VALUES (
    $1, $2, $3, $4,
    $5, $6, $7, $8, $9::jsonb, $10::jsonb,
    $11, $12, $13, $14, $15,
    $16, $17
  )
  RETURNING id
`;

/** Null out a numeric that carries no information, so the UI shows "no data". */
const numberOrNull = (value) =>
  value === undefined || value === null || Number.isNaN(value) ? null : value;

/** A price of 0.00 is not a price. Persist absence as absence. */
const priceOrNull = (value) => {
  const parsed = numberOrNull(value);
  return parsed === null || parsed <= 0 ? null : parsed;
};

export async function saveRfq({ rawInput, sourceType, sourceName, items }) {
  return withTransaction(async (client) => {
    const { rows } = await client.query(INSERT_RFQ, [
      rawInput,
      sourceType,
      sourceName ?? null,
    ]);
    const rfq = rows[0];

    for (const [index, item] of items.entries()) {
      await client.query(INSERT_LINE, [
        rfq.id,
        index + 1,
        item.input_string,
        numberOrNull(item.quantity),
        item.matched_mpn ?? null,
        item.manufacturer ?? null,
        numberOrNull(item.confidence),
        item.match_method ?? null,
        JSON.stringify(item.alternates ?? []),
        JSON.stringify(item.near_misses ?? []),
        priceOrNull(item.price_p25),
        priceOrNull(item.price_p50),
        priceOrNull(item.price_p75),
        priceOrNull(item.forecast_price),
        item.forecast_model ?? null,
        numberOrNull(item.heat_index),
        item.volatility_flag ?? null,
      ]);
    }

    return { id: rfq.id, createdAt: rfq.created_at, lineCount: items.length };
  });
}

const SELECT_RFQ = `
  SELECT id, raw_input, source_type, source_name, created_at
  FROM rfqs WHERE id = $1
`;

const SELECT_LINES = `
  SELECT line_no, input_string, quantity,
         matched_mpn, manufacturer, confidence, match_method,
         alternates, near_misses,
         price_p25, price_p50, price_p75,
         forecast_price, forecast_model, heat_index, volatility_flag,
         risk_score, test_recommendation, risk_reasons, est_turnaround_hours
  FROM quote_line_items
  WHERE rfq_id = $1
  ORDER BY line_no
`;

export async function getRfq(id) {
  const { rows } = await query(SELECT_RFQ, [id]);
  if (!rows.length) return null;
  const { rows: lines } = await query(SELECT_LINES, [id]);
  return { ...rows[0], items: lines };
}

const LIST_RFQS = `
  SELECT r.id,
         r.source_type,
         r.source_name,
         r.created_at,
         count(l.id)                                    AS line_count,
         count(l.id) FILTER (WHERE l.matched_mpn IS NOT NULL) AS matched_count,
         left(r.raw_input, 120)                         AS preview
  FROM rfqs r
  LEFT JOIN quote_line_items l ON l.rfq_id = r.id
  GROUP BY r.id
  ORDER BY r.created_at DESC
  LIMIT $1 OFFSET $2
`;

export async function listRfqs({ limit = 20, offset = 0 } = {}) {
  const { rows } = await query(LIST_RFQS, [limit, offset]);
  const { rows: totals } = await query('SELECT count(*)::int AS total FROM rfqs');
  return { items: rows, total: totals[0].total, limit, offset };
}

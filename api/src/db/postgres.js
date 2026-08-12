import pg from 'pg';

import { config } from '../config.js';

// Postgres NUMERIC arrives as a string by default, because it can hold values
// no JS number can represent. Every NUMERIC in this schema is a price or a
// score that the frontend charts, so parse them to numbers here rather than
// leaving "12.3400" strings to surface in the UI.
const NUMERIC_OID = 1700;
pg.types.setTypeParser(NUMERIC_OID, (value) => (value === null ? null : Number.parseFloat(value)));
// BIGINT (int8). Row ids here are far below Number.MAX_SAFE_INTEGER.
pg.types.setTypeParser(20, (value) => (value === null ? null : Number.parseInt(value, 10)));

export const pool = new pg.Pool({
  ...config.postgres,
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

pool.on('error', (error) => {
  console.error('[postgres] idle client error:', error.message);
});

export const query = (text, params) => pool.query(text, params);

/** Run a function inside a transaction, rolling back on any throw. */
export async function withTransaction(fn) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

export async function pingPostgres() {
  const { rows } = await query('SELECT 1 AS ok');
  return rows[0]?.ok === 1;
}

export async function closePostgres() {
  await pool.end();
}

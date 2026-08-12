import request from 'supertest';
import { describe, expect, it, vi } from 'vitest';

// The ML service and both datastores are stubbed. These tests are about the
// HTTP contract -- validation, status codes, error shape -- and should pass
// without anything running.
vi.mock('../src/services/mlClient.js', () => ({
  analyzeItems: vi.fn(async (items) => ({
    items: items.map((item) => ({
      input_string: item.input_string,
      quantity: item.quantity,
      matched_mpn: 'STM32F130ZBT6',
      manufacturer: 'STMicroelectronics',
      confidence: 1.0,
      match_method: 'exact',
      alternates: [],
      near_misses: [],
      price_p25: 1.0,
      price_p50: 1.1,
      price_p75: 1.2,
      forecast_price: 1.15,
      forecast_model: 'gbm',
      heat_index: 1.0,
      volatility_flag: 'STABLE',
      sparkline: [],
    })),
    count: items.length,
    model: 'gbm',
    elapsed_ms: 1,
  })),
  fetchPartDetail: vi.fn(async () => null),
  mlHealth: vi.fn(async () => ({ reachable: true, ready: true })),
}));

vi.mock('../src/db/mongo.js', () => ({
  findPartsByMpn: vi.fn(async () => new Map()),
  findPartByMpn: vi.fn(async () => null),
  pingMongo: vi.fn(async () => true),
  closeMongo: vi.fn(async () => {}),
}));

vi.mock('../src/services/rfqRepository.js', () => ({
  saveRfq: vi.fn(async ({ items }) => ({
    id: 1, createdAt: new Date().toISOString(), lineCount: items.length,
  })),
  getRfq: vi.fn(async (id) => (id === 1 ? { id: 1, items: [] } : null)),
  listRfqs: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })),
}));

vi.mock('../src/db/postgres.js', () => ({
  pingPostgres: vi.fn(async () => true),
  closePostgres: vi.fn(async () => {}),
  query: vi.fn(),
  withTransaction: vi.fn(),
  pool: { on: vi.fn() },
}));

const { createApp } = await import('../src/app.js');
const app = createApp();

describe('POST /api/rfq', () => {
  it('analyses raw text and returns 201', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .send({ raw_text: 'STM32F130ZBT6 x 500' });

    expect(res.status).toBe(201);
    expect(res.body.rfq_id).toBe(1);
    expect(res.body.items).toHaveLength(1);
    expect(res.body.items[0].quantity).toBe(500);
  });

  it('accepts an explicit items array', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .send({ items: [{ input_string: 'STM32F130ZBT6', quantity: 5 }] });
    expect(res.status).toBe(201);
  });

  it('rejects an empty body with a structured error', async () => {
    const res = await request(app).post('/api/rfq').send({});
    expect(res.status).toBe(400);
    expect(res.body.error.code).toBe('BAD_REQUEST');
    expect(res.body).not.toHaveProperty('stack');
  });

  it('rejects an unknown model name', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .send({ raw_text: 'STM32F130ZBT6', model: 'crystal-ball' });
    expect(res.status).toBe(400);
    expect(res.body.error.details[0].path).toBe('model');
  });

  it('explains itself when no part numbers can be read', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .send({ raw_text: 'hello there\nhow are you' });
    expect(res.status).toBe(400);
    expect(res.body.error.message).toMatch(/no part numbers/i);
    expect(res.body.error.details.skipped_lines).toContain('hello there');
  });

  it('refuses a spreadsheet until Phase 5 rather than mangling it', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .attach('file', Buffer.from('PKbinary'), 'bom.xlsx');
    expect(res.status).toBe(400);
    expect(res.body.error.message).toMatch(/spreadsheet/i);
  });

  it('reads an uploaded text file', async () => {
    const res = await request(app)
      .post('/api/rfq')
      .attach('file', Buffer.from('STM32F130ZBT6 x 25\n'), 'rfq.txt');
    expect(res.status).toBe(201);
    expect(res.body.source_name).toBe('rfq.txt');
  });
});

describe('GET /api/rfq', () => {
  it('lists', async () => {
    const res = await request(app).get('/api/rfq');
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('total');
  });

  it('validates pagination', async () => {
    const res = await request(app).get('/api/rfq?limit=9999');
    expect(res.status).toBe(400);
  });

  it('404s an unknown id', async () => {
    const res = await request(app).get('/api/rfq/999');
    expect(res.status).toBe(404);
    expect(res.body.error.code).toBe('NOT_FOUND');
  });

  it('rejects a non-numeric id', async () => {
    const res = await request(app).get('/api/rfq/abc');
    expect(res.status).toBe(400);
  });
});

describe('GET /api/part', () => {
  it('404s an unknown part', async () => {
    const res = await request(app).get('/api/part/NOT-A-PART');
    expect(res.status).toBe(404);
  });

  it('routes a part number containing a slash', async () => {
    // D38999/24JA103SN and MCP645-I/SN are real shapes; a plain :mpn route
    // would 404 on a large slice of the catalog.
    const { fetchPartDetail } = await import('../src/services/mlClient.js');
    fetchPartDetail.mockResolvedValueOnce({ mpn: 'D38999/24JA103SN' });

    const res = await request(app).get('/api/part/D38999%2F24JA103SN');
    expect(res.status).toBe(200);
    expect(fetchPartDetail).toHaveBeenCalledWith('D38999/24JA103SN');
  });
});

describe('routing', () => {
  it('404s an unknown route in the standard shape', async () => {
    const res = await request(app).get('/api/nope');
    expect(res.status).toBe(404);
    expect(res.body.error.code).toBe('NOT_FOUND');
  });
});

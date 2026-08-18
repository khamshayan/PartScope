import request from 'supertest';
import { describe, expect, it, vi } from 'vitest';

// Set before config.js reads the environment. This file pins an allowlist with
// one exact origin and one preview pattern, which is the shape a real Vercel
// project ends up with.
process.env.AUTH_USER = 'buyer';
process.env.AUTH_PASSWORD = 'correct-horse';
process.env.AUTH_SESSION_SECRET = 'test-secret-not-a-real-one';
process.env.AUTH_COOKIE_SAMESITE = 'none';
process.env.WEB_ORIGIN = 'https://partscope.vercel.app,https://partscope-*.vercel.app';

vi.mock('../src/db/mongo.js', () => ({
  findPartsByMpn: vi.fn(async () => new Map()),
  findPartByMpn: vi.fn(async () => null),
  pingMongo: vi.fn(async () => true),
  closeMongo: vi.fn(async () => {}),
}));
vi.mock('../src/services/mlClient.js', () => ({
  fetchPartDetail: vi.fn(async () => null),
  mlHealth: vi.fn(async () => ({ reachable: true, ready: true })),
  parseText: vi.fn(),
  parseFile: vi.fn(),
  analyzeItems: vi.fn(),
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

const preflight = (origin) =>
  request(app)
    .options('/api/login')
    .set('Origin', origin)
    .set('Access-Control-Request-Method', 'POST')
    .set('Access-Control-Request-Headers', 'content-type');

describe('CORS with credentials', () => {
  it('echoes an allowed origin back specifically, never as a wildcard', async () => {
    const res = await preflight('https://partscope.vercel.app');

    expect(res.headers['access-control-allow-origin']).toBe('https://partscope.vercel.app');
    expect(res.headers['access-control-allow-origin']).not.toBe('*');
    expect(res.headers['access-control-allow-credentials']).toBe('true');
  });

  it('matches a Vercel preview URL through the wildcard pattern', async () => {
    const res = await preflight('https://partscope-git-feat-auth-khamshayan.vercel.app');

    expect(res.headers['access-control-allow-origin']).toBe(
      'https://partscope-git-feat-auth-khamshayan.vercel.app',
    );
    expect(res.headers['access-control-allow-credentials']).toBe('true');
  });

  it('varies on Origin, so a cache cannot cross-serve the headers', async () => {
    const res = await preflight('https://partscope.vercel.app');
    expect(res.headers.vary).toMatch(/Origin/i);
  });

  it('sends no allow-origin for an origin outside the list', async () => {
    const res = await preflight('https://evil.example.com');
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('does not let a wildcard label swallow a whole domain', async () => {
    // "partscope-*.vercel.app" must not match an extra label, or anyone able to
    // deploy under vercel.app could craft a matching host.
    const res = await preflight('https://partscope-x.evil.vercel.app');
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('does not treat a dot in the pattern as "any character"', async () => {
    const res = await preflight('https://partscopeXvercel.app');
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('still serves requests that carry no Origin header at all', async () => {
    // curl, uptime probes, server-to-server. CORS only ever binds a browser.
    const res = await request(app).get('/');
    expect(res.status).toBe(200);
  });

  it('rejects a disallowed origin without turning it into a 500', async () => {
    const res = await preflight('https://evil.example.com');
    expect(res.status).toBeLessThan(500);
  });
});

describe('the session cookie under a cross-site deployment', () => {
  it('is SameSite=None and Secure so a cross-site fetch carries it', async () => {
    const res = await request(app)
      .post('/api/login')
      .set('Origin', 'https://partscope.vercel.app')
      .send({ username: 'buyer', password: 'correct-horse' });

    expect(res.status).toBe(200);

    const cookie = res.headers['set-cookie'][0];
    expect(cookie).toContain('SameSite=None');
    // None without Secure is rejected by every current browser, so these two
    // travel together or the cookie is never stored at all.
    expect(cookie).toContain('Secure');
    expect(cookie).toContain('HttpOnly');
  });

  it('clears with the same attributes, or the browser keeps the original', async () => {
    const res = await request(app).post('/api/logout');
    const cookie = res.headers['set-cookie'][0];

    expect(cookie).toContain('SameSite=None');
    expect(cookie).toContain('Secure');
  });
});

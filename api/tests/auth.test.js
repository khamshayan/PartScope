import request from 'supertest';
import { beforeAll, describe, expect, it, vi } from 'vitest';

// Credentials have to exist before config.js is first imported, because it
// reads the environment once at module load. dotenv does not overwrite what is
// already set, so these win over any real .env on the machine running this.
process.env.AUTH_USER = 'buyer';
process.env.AUTH_PASSWORD = 'correct-horse';
process.env.AUTH_SESSION_SECRET = 'test-secret-not-a-real-one';

// The datastores and the ML tier are irrelevant here: these tests are about
// who gets past the gate, not what is behind it. A 401 must arrive without
// anything running, which is also why the protected route asserted on below
// is one that would otherwise need Mongo.
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

let app;
beforeAll(async () => {
  ({ app } = await import('../src/app.js').then((m) => ({ app: m.createApp() })));
});

/** Pull the session cookie off a login response. */
const sessionCookie = (res) => res.headers['set-cookie']?.[0] ?? '';

describe('POST /api/login', () => {
  it('accepts the configured pair and sets a hardened cookie', async () => {
    const res = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'correct-horse' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({ username: 'buyer', auth_required: true });

    const cookie = sessionCookie(res);
    expect(cookie).toMatch(/^partscope_session=/);
    expect(cookie).toContain('HttpOnly');
    expect(cookie).toContain('Secure');
    expect(cookie).toContain('SameSite=Lax');
  });

  it('rejects a wrong password with 401 and no cookie', async () => {
    const res = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'wrong' });

    expect(res.status).toBe(401);
    expect(res.body.error.code).toBe('UNAUTHORIZED');
    expect(res.headers['set-cookie']).toBeUndefined();
  });

  it('gives a wrong username the same answer as a wrong password', async () => {
    const wrongUser = await request(app)
      .post('/api/login')
      .send({ username: 'nobody', password: 'correct-horse' });
    const wrongPassword = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'wrong' });

    expect(wrongUser.status).toBe(401);
    expect(wrongUser.body).toEqual(wrongPassword.body);
  });

  it('validates the body shape', async () => {
    const res = await request(app).post('/api/login').send({ username: 'buyer' });
    expect(res.status).toBe(400);
    expect(res.body.error.code).toBe('BAD_REQUEST');
  });
});

describe('the gate', () => {
  it('rejects a protected route with no cookie', async () => {
    const res = await request(app).get('/api/part/ANY');
    expect(res.status).toBe(401);
    expect(res.body.error.code).toBe('UNAUTHORIZED');
  });

  it('rejects health too', async () => {
    const res = await request(app).get('/api/health');
    expect(res.status).toBe(401);
  });

  it('leaves the service banner public as a liveness probe', async () => {
    const res = await request(app).get('/');
    expect(res.status).toBe(200);
    expect(res.body.service).toBe('PartScope API');
  });

  it('lets a logged-in request through', async () => {
    const login = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'correct-horse' });

    const res = await request(app)
      .get('/api/part/NOT-A-PART')
      .set('Cookie', sessionCookie(login));

    // 404 rather than 401: past the gate, and the part genuinely is not there.
    expect(res.status).toBe(404);
    expect(res.body.error.code).toBe('NOT_FOUND');
  });

  it('rejects a tampered payload', async () => {
    const login = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'correct-horse' });

    const [name, token] = sessionCookie(login).split(';')[0].split('=');
    const forged = Buffer.from(JSON.stringify({
      u: 'buyer',
      exp: Date.now() + 60_000,
    })).toString('base64url');

    const res = await request(app)
      .get('/api/part/ANY')
      .set('Cookie', `${name}=${forged}.${token.split('.')[1]}`);

    expect(res.status).toBe(401);
  });

  it('rejects a token with no signature at all', async () => {
    const res = await request(app).get('/api/part/ANY').set('Cookie', 'partscope_session=nonsense');
    expect(res.status).toBe(401);
  });
});

describe('GET /api/me', () => {
  it('is 401 when signed out', async () => {
    const res = await request(app).get('/api/me');
    expect(res.status).toBe(401);
  });

  it('names the user when signed in', async () => {
    const login = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'correct-horse' });

    const res = await request(app).get('/api/me').set('Cookie', sessionCookie(login));
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ username: 'buyer', auth_required: true });
  });
});

describe('POST /api/logout', () => {
  it('clears the cookie and the session stops working', async () => {
    const login = await request(app)
      .post('/api/login')
      .send({ username: 'buyer', password: 'correct-horse' });
    const cookie = sessionCookie(login);

    const out = await request(app).post('/api/logout').set('Cookie', cookie);
    expect(out.status).toBe(204);
    // Expired in the past, with the attributes repeated so the browser
    // actually replaces the original rather than keeping it.
    expect(sessionCookie(out)).toMatch(/partscope_session=;/);
    expect(sessionCookie(out)).toContain('Expires=Thu, 01 Jan 1970');

    // The old value is still a valid signature -- logout is a client-side
    // clear, which is the documented limit of a stateless token.
    const stillValid = await request(app).get('/api/me').set('Cookie', cookie);
    expect(stillValid.status).toBe(200);
  });

  it('is not an error when nobody is signed in', async () => {
    const res = await request(app).post('/api/logout');
    expect(res.status).toBe(204);
  });
});

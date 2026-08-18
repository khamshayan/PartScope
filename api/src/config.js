import { randomBytes } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import dotenv from 'dotenv';

const here = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(here, '..', '..');

// A missing .env is fine: every default below matches what docker-compose
// brings up, so a clean clone runs with no configuration at all.
dotenv.config({ path: resolve(REPO_ROOT, '.env') });

const int = (value, fallback) => {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const bool = (value, fallback) => {
  if (value === undefined || value.trim() === '') return fallback;
  return !/^(0|false|no)$/i.test(value.trim());
};

// Trimmed, because a credential with a stray trailing space from a copy-paste
// into .env would fail to match and give no clue why.
const authUser = (process.env.AUTH_USER ?? '').trim();
const authPassword = (process.env.AUTH_PASSWORD ?? '').trim();

// Origins permitted to send credentialed cross-origin requests. Comma
// separated, and `*` inside an entry matches one label, so a single
// "https://partscope-*.vercel.app" covers every preview deployment without
// naming them.
const webOrigins = (process.env.WEB_ORIGIN ?? '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean);

const SAMESITE_VALUES = new Set(['none', 'lax', 'strict']);
const requestedSameSite = (process.env.AUTH_COOKIE_SAMESITE ?? '').trim().toLowerCase();
// A cross-site deployment (Vercel frontend, Render API -- different registrable
// domains) needs None, because Lax is not sent on cross-site fetch at all.
// Local dev is same-origin behind the Vite proxy, where Lax is both sufficient
// and the safer of the two, so the default follows NODE_ENV.
const cookieSameSite = SAMESITE_VALUES.has(requestedSameSite)
  ? requestedSameSite
  : (process.env.NODE_ENV === 'production' ? 'none' : 'lax');

export const config = {
  port: int(process.env.API_PORT, 3000),

  auth: {
    user: authUser,
    password: authPassword,
    // Both halves have to be present. A blank pair means the API is open,
    // which is what keeps a clean clone runnable with no configuration at all
    // -- the same rule every other default here follows. Setting both turns
    // protection on; index.js says which state it booted in.
    enabled: Boolean(authUser && authPassword),
    cookieName: process.env.AUTH_COOKIE_NAME ?? 'partscope_session',
    // Unset means a fresh secret per boot, so a restart signs everyone out.
    // That is the safe default: a hardcoded fallback secret would let anyone
    // holding this source forge a session against a deployed instance.
    secret: (process.env.AUTH_SESSION_SECRET ?? '').trim() || randomBytes(32).toString('hex'),
    // Browsers accept Secure cookies over http://localhost, so this can stay
    // on in development. It is a knob only because Safari has historically
    // disagreed; turn it off for plain-http testing, never in production.
    //
    // Forced on under SameSite=None because the spec requires it: a None
    // cookie without Secure is rejected outright, so honouring an explicit
    // `false` here would mean issuing a cookie no browser will ever store.
    secure: cookieSameSite === 'none' ? true : bool(process.env.AUTH_COOKIE_SECURE, true),
    sameSite: cookieSameSite,
  },

  web: {
    origins: webOrigins,
    // Unset, or an explicit "*", means every origin is reflected back. That is
    // what makes credentialed requests work from a Vercel preview URL nobody
    // has listed yet -- and it is also the loosest setting there is, so
    // index.js says so at boot.
    allowsAnyOrigin: webOrigins.length === 0 || webOrigins.includes('*'),
  },

  postgres: {
    host: process.env.POSTGRES_HOST ?? 'localhost',
    port: int(process.env.POSTGRES_PORT, 5433),
    database: process.env.POSTGRES_DB ?? 'partscope',
    user: process.env.POSTGRES_USER ?? 'partscope',
    password: process.env.POSTGRES_PASSWORD ?? 'partscope',
  },

  mongo: {
    uri: process.env.MONGO_URI ?? 'mongodb://localhost:27018',
    database: process.env.MONGO_DB ?? 'partscope',
  },

  mlService: {
    url: process.env.ML_SERVICE_URL ?? 'http://localhost:8000',
    // Generous: a cold SARIMA order search on an unseen part legitimately
    // takes seconds. The client gets a structured 503 rather than a hang.
    timeoutMs: int(process.env.ML_SERVICE_TIMEOUT_MS, 30000),
  },

  upload: {
    maxBytes: int(process.env.UPLOAD_MAX_BYTES, 5 * 1024 * 1024),
  },
};

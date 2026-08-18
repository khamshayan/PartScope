/**
 * Single-user session auth.
 *
 * One username/password pair from the environment, and a signed cookie proving
 * you presented it. There is no user table, no signup and no reset, because
 * there is exactly one account and it lives in `.env`.
 *
 * The token is signed rather than stored: `base64url(claims).base64url(hmac)`,
 * HMAC-SHA256 over the claims with a server-side secret. That needs no session
 * store and no new dependency -- `node:crypto` and Express's own `res.cookie`
 * cover it. The trade is that a token cannot be revoked individually; changing
 * AUTH_USER or restarting the process invalidates every outstanding one, which
 * for a single-account tool is the only revocation anyone would want.
 *
 * Cookie parsing is done here by hand for the same reason: it is six lines
 * against a dependency, and `cookie-parser` would be pulled in to read exactly
 * one cookie.
 */

import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

import { config } from './config.js';
import { unauthorized } from './errors.js';

/** Long enough for a working day, short enough that a forgotten tab expires. */
const SESSION_TTL_MS = 12 * 60 * 60 * 1000;

/**
 * Constant-time string compare.
 *
 * `timingSafeEqual` throws on a length mismatch, so length is checked first --
 * which does leak the length, and does not matter: an attacker learning that
 * the password is eight characters has learned nothing they could not guess.
 */
function constantTimeEquals(a, b) {
  const left = Buffer.from(String(a), 'utf8');
  const right = Buffer.from(String(b), 'utf8');
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

function sign(payload) {
  return createHmac('sha256', config.auth.secret).update(payload).digest('base64url');
}

/** Are these the configured credentials? False whenever auth is not configured. */
export function verifyCredentials(username, password) {
  if (!config.auth.enabled) return false;
  // Both halves are always compared, so the answer takes the same time whether
  // the username was wrong, the password was wrong, or both.
  const userOk = constantTimeEquals(username, config.auth.user);
  const passwordOk = constantTimeEquals(password, config.auth.password);
  return userOk && passwordOk;
}

export function issueToken(username) {
  const claims = JSON.stringify({
    u: username,
    exp: Date.now() + SESSION_TTL_MS,
    // Makes two tokens issued in the same millisecond differ, so a cookie is
    // never a stable fingerprint of the account.
    n: randomBytes(9).toString('base64url'),
  });
  const payload = Buffer.from(claims, 'utf8').toString('base64url');
  return `${payload}.${sign(payload)}`;
}

/** Validated claims, or null. Null covers every failure: this is a gate. */
export function readToken(token) {
  if (typeof token !== 'string' || !token) return null;

  const separator = token.indexOf('.');
  if (separator < 1) return null;
  const payload = token.slice(0, separator);
  const signature = token.slice(separator + 1);

  // Signature first. Nothing inside an unverified payload is worth parsing.
  if (!constantTimeEquals(signature, sign(payload))) return null;

  let claims;
  try {
    claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
  } catch {
    return null;
  }

  if (typeof claims?.exp !== 'number' || claims.exp <= Date.now()) return null;
  // A token minted for a username that is no longer the configured one is
  // dead, so editing AUTH_USER logs out whoever was holding the old name.
  if (claims.u !== config.auth.user) return null;

  return claims;
}

/** Read one cookie off the request. Express does not parse these on its own. */
export function readCookie(req, name) {
  const header = req.headers?.cookie;
  if (!header) return null;

  for (const pair of header.split(';')) {
    const equals = pair.indexOf('=');
    if (equals === -1) continue;
    if (pair.slice(0, equals).trim() !== name) continue;
    try {
      return decodeURIComponent(pair.slice(equals + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Cookie attributes, shared by the set and the clear so they cannot drift.
 *
 * `sameSite` is configuration rather than a constant because the two
 * deployments genuinely need different values, and neither works in the
 * other's place:
 *
 * - Local dev is same-origin behind the Vite proxy. 'lax' is correct there and
 *   keeps the browser's own cross-site protection, which is worth having.
 * - Vercel frontend against a Render API is cross-*site*, not merely
 *   cross-origin: different registrable domains. A 'lax' cookie is never sent
 *   on cross-site fetch, so the session would appear to work at login and then
 *   silently not exist on the next request.
 *
 * 'none' hands the whole cross-site question to CORS, which is why the origin
 * check in app.js is the thing actually holding the line in production.
 */
export function sessionCookieOptions() {
  return {
    httpOnly: true,
    secure: config.auth.secure,
    sameSite: config.auth.sameSite,
    path: '/',
  };
}

export function setSessionCookie(res, token) {
  res.cookie(config.auth.cookieName, token, {
    ...sessionCookieOptions(),
    maxAge: SESSION_TTL_MS,
  });
}

/** Clearing has to repeat the attributes, or the browser keeps the original. */
export function clearSessionCookie(res) {
  res.clearCookie(config.auth.cookieName, sessionCookieOptions());
}

/** The session on this request, or null. */
export function currentSession(req) {
  return readToken(readCookie(req, config.auth.cookieName));
}

/**
 * Gate for every route that is not login, logout or the session check.
 *
 * Passes straight through when no credentials are configured -- see the note
 * in config.js. When they are, a missing or invalid cookie is a 401 in the
 * same `{ error: { code, message } }` shape as every other failure, so the
 * client can branch on it exactly like the rest.
 */
export function requireAuth(req, _res, next) {
  if (!config.auth.enabled) return next();

  const session = currentSession(req);
  if (!session) {
    return next(unauthorized('Sign in to use this endpoint.'));
  }

  req.session = session;
  return next();
}

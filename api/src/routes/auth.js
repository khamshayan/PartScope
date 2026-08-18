import { Router } from 'express';
import { z } from 'zod';

import {
  clearSessionCookie,
  currentSession,
  issueToken,
  setSessionCookie,
  verifyCredentials,
} from '../auth.js';
import { config } from '../config.js';
import { ApiError, fromZod, unauthorized } from '../errors.js';

export const authRouter = Router();

// Bounded so a login attempt cannot be used to push a megabyte through the
// constant-time comparison.
const credentialsSchema = z.object({
  username: z.string().min(1).max(200),
  password: z.string().min(1).max(400),
});

/**
 * POST /api/login
 *
 * One deliberate imprecision: a wrong username and a wrong password give the
 * same 401 and the same message. Telling the caller which half was right is
 * telling them half the credential.
 */
authRouter.post('/login', (req, res, next) => {
  if (!config.auth.enabled) {
    return next(
      new ApiError(
        503,
        'AUTH_NOT_CONFIGURED',
        'This server has no credentials configured, so there is nothing to sign in to.',
        { hint: 'Set AUTH_USER and AUTH_PASSWORD in .env and restart the API.' },
      ),
    );
  }

  const parsed = credentialsSchema.safeParse(req.body ?? {});
  if (!parsed.success) return next(fromZod(parsed.error));

  const { username, password } = parsed.data;
  if (!verifyCredentials(username, password)) {
    return next(unauthorized('Incorrect username or password.'));
  }

  setSessionCookie(res, issueToken(config.auth.user));
  return res.json({ username: config.auth.user, auth_required: true });
});

/**
 * POST /api/logout
 *
 * Unconditionally 204. Asking to be signed out when you already are is not an
 * error, and reporting it as one gives a caller a failure they cannot act on.
 */
authRouter.post('/logout', (_req, res) => {
  clearSessionCookie(res);
  res.status(204).end();
});

/**
 * GET /api/me
 *
 * The frontend's "am I logged in" check, answered before the dashboard renders.
 *
 * 200 with `auth_required: false` when no credentials are configured: the API
 * is open, so the client should show the dashboard rather than a login form it
 * could never get past. `username` is null in that case, which is what the
 * header uses to decide whether a "Log out" control makes any sense.
 */
authRouter.get('/me', (req, res, next) => {
  if (!config.auth.enabled) {
    return res.json({ username: null, auth_required: false });
  }

  const session = currentSession(req);
  if (!session) return next(unauthorized('Not signed in.'));

  return res.json({ username: session.u, auth_required: true });
});

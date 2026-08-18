import cors from 'cors';
import express from 'express';

import { requireAuth } from './auth.js';
import { config } from './config.js';
import { errorHandler, notFoundHandler } from './errors.js';
import { authRouter } from './routes/auth.js';
import { healthRouter } from './routes/health.js';
import { partRouter } from './routes/part.js';
import { rfqRouter } from './routes/rfq.js';

/**
 * Turn one WEB_ORIGIN entry into a predicate.
 *
 * `*` stands for a single label, so "https://partscope-*.vercel.app" matches
 * every preview deployment of that project and nothing outside it. Every other
 * regex metacharacter is escaped, so a dot in a hostname means a dot and not
 * "any character" -- without that, "https://api.example.com" would also match
 * "https://apiXexample.com", which somebody could register.
 */
function toOriginMatcher(pattern) {
  const escaped = pattern
    .replace(/[.+?^${}()|[\]\\]/g, '\\$&')
    .replace(/\*/g, '[^.]*');
  const expression = new RegExp(`^${escaped}$`, 'i');
  return (origin) => expression.test(origin);
}

/** Built as a factory so tests can mount the app without binding a port. */
export function createApp() {
  const app = express();

  const originMatchers = config.web.origins
    .filter((pattern) => pattern !== '*')
    .map(toOriginMatcher);

  /**
   * Reflects the requesting origin instead of sending a wildcard.
   *
   * `credentials: true` and `Access-Control-Allow-Origin: *` are mutually
   * exclusive -- a browser rejects that pair outright and drops the cookie --
   * so the allowed origin has to be echoed back specifically. The cors package
   * sets `Vary: Origin` for a dynamic origin on its own, which keeps a shared
   * cache from serving one origin's headers to another.
   *
   * Rejection passes `false`, not an Error: the response then simply carries no
   * CORS headers and the browser blocks it, which is the honest outcome. An
   * Error here would turn a disallowed origin into a 500 and bury the reason.
   */
  const originCheck = (origin, callback) => {
    // No Origin header at all: a same-origin request, curl, a health probe, or
    // anything server-to-server. There is nothing to reflect and nothing for
    // CORS to protect, since the rule only ever binds a browser.
    if (!origin) return callback(null, true);
    if (config.web.allowsAnyOrigin) return callback(null, true);
    return callback(null, originMatchers.some((matches) => matches(origin)));
  };

  app.use(cors({ origin: originCheck, credentials: true }));
  app.use(express.json({ limit: '1mb' }));
  app.use(express.urlencoded({ extended: true, limit: '1mb' }));

  app.get('/', (_req, res) => {
    res.json({
      service: 'PartScope API',
      version: '0.3.0',
      data: 'synthetic - see docs/data-sources.md',
      endpoints: [
        'POST /api/login        { username, password }',
        'POST /api/logout',
        'GET  /api/me',
        'GET  /api/health',
        'POST /api/rfq          { raw_text } | { items } | multipart file',
        'GET  /api/rfq          ?limit&offset',
        'GET  /api/rfq/:id',
        'GET  /api/part/:mpn',
      ],
    });
  });

  // Public: you cannot require a session in order to establish one. This is
  // mounted at /api rather than at three paths so the three sibling endpoints
  // stay in one file; anything it does not define falls through to the gated
  // routers below.
  app.use('/api', authRouter);

  // Everything else is behind the gate, health included -- its response names
  // which datastore is down and why, which is a diagnostic for whoever runs
  // this, not for anyone who can reach the port. The `/` banner above stays
  // public as the liveness probe.
  app.use('/api/health', requireAuth, healthRouter);
  app.use('/api/rfq', requireAuth, rfqRouter);
  app.use('/api/part', requireAuth, partRouter);

  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}

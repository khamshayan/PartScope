import { createApp } from './app.js';
import { config } from './config.js';
import { closeMongo } from './db/mongo.js';
import { closePostgres } from './db/postgres.js';

const app = createApp();

const server = app.listen(config.port, () => {
  console.log(`[api] listening on http://localhost:${config.port}`);
  console.log(`[api] ml service at ${config.mlService.url}`);
  // Which of the two states this booted in is never left to be inferred: an
  // API that is open when its operator believes it is gated is the whole
  // failure mode this feature exists to prevent.
  if (config.auth.enabled) {
    console.log(`[api] session auth enabled for user "${config.auth.user}"`);
    console.log(
      `[api] session cookie: SameSite=${config.auth.sameSite}; ` +
        `${config.auth.secure ? 'Secure' : 'NOT Secure'}; HttpOnly`,
    );
    if (config.web.allowsAnyOrigin) {
      // The dangerous pair, and the only combination worth shouting about:
      // reflecting every origin while also accepting cookies means any page a
      // signed-in user visits can call this API as them and read the answer.
      console.warn(
        '[api] CORS is reflecting ANY origin with credentials. Any site a ' +
          'signed-in user visits can call this API as them. Set WEB_ORIGIN to ' +
          'your frontend origins (comma separated, * matches one label) to close this.',
      );
    } else {
      console.log(`[api] CORS origins allowed: ${config.web.origins.join(', ')}`);
    }
  } else {
    console.warn(
      '[api] AUTH_USER/AUTH_PASSWORD are unset - the API is NOT protected. ' +
        'Set both in .env to require a login.',
    );
  }
  console.log('[api] all data is synthetic - see docs/data-sources.md');
});

async function shutdown(signal) {
  console.log(`\n[api] ${signal} received, shutting down`);
  server.close();
  await Promise.allSettled([closePostgres(), closeMongo()]);
  process.exit(0);
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => shutdown(signal));
}

import { useState } from 'react';

import { ApiFailure, login } from '../api/client';
import type { Session } from '../api/types';

interface Props {
  onSignedIn: (session: Session) => void;
}

/**
 * The whole app when signed out.
 *
 * Deliberately plain: same tokens, same control styling and the same error
 * treatment as the dashboard, so it reads as the same product rather than a
 * bolted-on gate.
 */
export function LoginForm({ onSignedIn }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<ApiFailure | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      onSignedIn(await login(username, password));
    } catch (caught) {
      setError(
        caught instanceof ApiFailure
          ? caught
          : new ApiFailure('Could not sign in.', 'UNKNOWN', 0),
      );
      setPassword('');
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass =
    'w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink ' +
    'placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 ' +
    'focus:ring-accent-ring';

  return (
    <div className="flex min-h-screen flex-col bg-page text-ink">
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-6">
            <h1 className="text-lg font-semibold tracking-tight text-ink">PartScope</h1>
            <p className="text-sm text-ink-secondary">
              RFQ triage and price-band recommendation
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            className="flex flex-col gap-4 rounded-lg border border-hairline bg-surface p-5"
          >
            <div className="flex flex-col gap-1.5">
              <label htmlFor="username" className="text-sm font-medium text-ink">
                Username
              </label>
              <input
                id="username"
                name="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                required
                className={inputClass}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-sm font-medium text-ink">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
                className={inputClass}
              />
            </div>

            {/* role="alert" so the failure is announced, not just recoloured --
                the message is the only feedback a wrong password gets. */}
            {error && (
              <div
                role="alert"
                className="rounded-md border border-status-bad/30 bg-status-badSoft px-3 py-2 text-xs text-status-badInk"
              >
                {error.message}
                {error.hint && <div className="mt-1 text-status-badInk/80">{error.hint}</div>}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !username || !password}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:bg-hairline disabled:text-ink-muted"
            >
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <p className="mt-4 text-xs text-ink-muted">
            Demo instance. Parts, prices and shortages are produced by a seeded
            generator and do not correspond to real components or real market data.
          </p>
        </div>
      </main>
    </div>
  );
}

import type { ApiErrorBody, PartDetail, RfqResponse, Session } from './types';

/**
 * Thin API client.
 *
 * Its one real job is turning every failure into an ApiFailure carrying a
 * message worth showing a user. A dashboard that renders "Failed to fetch" is
 * telling the person in front of it nothing they can act on.
 */

/**
 * Where the API lives, as seen from the browser.
 *
 * Empty in local dev. `vite.config.ts` proxies /api to the Node process, so a
 * relative path keeps the browser on one origin -- which is also why the
 * session cookie works there without any CORS involved at all. That proxy is a
 * dev-server feature and does not survive `npm run build`, so a static host
 * needs the real API origin instead.
 *
 * Note this is read at BUILD time, not at runtime: Vite inlines VITE_* values
 * into the bundle when it builds. On Vercel it has to be set as a project
 * environment variable before the build, and changing it later means a rebuild,
 * not a restart.
 *
 * The trailing-slash strip is not decoration: a base pasted from a dashboard
 * URL bar arrives as "https://api.example.com/" and would otherwise produce
 * "https://api.example.com//api/me".
 */
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

const apiUrl = (path: string) => `${API_BASE_URL}${path}`;

/**
 * Every request carries the session cookie.
 *
 * `credentials: 'include'` rather than the default 'same-origin', because on a
 * static host the API is a different origin and the default would silently drop
 * the cookie -- turning every gated route into a 401 with nothing on screen to
 * explain why. Same-origin dev is unaffected: 'include' behaves identically
 * there.
 *
 * This is on all six calls, not just the login ones. Every route behind
 * `requireAuth` needs the cookie, which is all of them -- only the `/` service
 * banner is public.
 */
const WITH_SESSION = { credentials: 'include' } as const;

export class ApiFailure extends Error {
  readonly code: string;
  readonly status: number;
  readonly hint?: string;

  constructor(message: string, code: string, status: number, hint?: string) {
    super(message);
    this.name = 'ApiFailure';
    this.code = code;
    this.status = status;
    this.hint = hint;
  }
}

async function toFailure(response: Response): Promise<ApiFailure> {
  let body: ApiErrorBody | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // A non-JSON error body is itself the diagnostic; fall through.
  }

  const details = body?.error?.details as { hint?: string } | undefined;
  return new ApiFailure(
    body?.error?.message ?? `Request failed (HTTP ${response.status})`,
    body?.error?.code ?? 'UNKNOWN',
    response.status,
    typeof details?.hint === 'string' ? details.hint : undefined,
  );
}

function networkFailure(error: unknown): ApiFailure {
  // The useful half of this hint is *where* the client was pointed. "Is the API
  // running on port 3000?" is the right question locally and actively
  // misleading on a deployed build, where the answer is whether the configured
  // origin is reachable and whether it allows this one.
  const where = API_BASE_URL
    ? `Is ${API_BASE_URL} reachable, and does it allow requests from this origin?`
    : 'Is the API running on port 3000?';

  return new ApiFailure(
    'Could not reach the PartScope API.',
    'NETWORK',
    0,
    error instanceof Error && error.message ? `${error.message}. ${where}` : where,
  );
}

/**
 * "Am I signed in?", asked once before the dashboard renders.
 *
 * Returns null for signed-out rather than throwing, because that is an ordinary
 * answer to this question and not a failure. A server with no credentials
 * configured answers 200 with `auth_required: false`, so an unconfigured clone
 * goes straight to the dashboard instead of a login form it could never pass.
 *
 * A network failure also reads as signed-out: the login form is the only thing
 * that can be usefully shown, and the attempt from it surfaces the real error
 * with a message worth reading.
 */
export async function fetchSession(): Promise<Session | null> {
  let response: Response;
  try {
    response = await fetch(apiUrl('/api/me'), { ...WITH_SESSION });
  } catch {
    return null;
  }
  if (response.status === 401) return null;
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as Session;
}

export async function login(username: string, password: string): Promise<Session> {
  let response: Response;
  try {
    response = await fetch(apiUrl('/api/login'), {
      ...WITH_SESSION,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
  } catch (error) {
    throw networkFailure(error);
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as Session;
}

/**
 * Never rejects. The cookie is httpOnly, so the browser cannot clear it on its
 * own and there is nothing useful for the user to do about a failed logout --
 * the caller drops the session either way and lands back on the login form.
 */
export async function logout(): Promise<void> {
  try {
    await fetch(apiUrl('/api/logout'), { ...WITH_SESSION, method: 'POST' });
  } catch {
    // Deliberately swallowed; see above.
  }
}

export async function analyzeText(
  rawText: string,
  signal?: AbortSignal,
): Promise<RfqResponse> {
  let response: Response;
  try {
    response = await fetch(apiUrl('/api/rfq'), {
      ...WITH_SESSION,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_text: rawText, source_type: 'text' }),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw networkFailure(error);
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as RfqResponse;
}

export async function analyzeFile(
  file: File,
  signal?: AbortSignal,
): Promise<RfqResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('source_type', 'email');

  let response: Response;
  try {
    // No Content-Type header on purpose: the browser sets it with the
    // multipart boundary, which cannot be written by hand.
    response = await fetch(apiUrl('/api/rfq'), {
      ...WITH_SESSION,
      method: 'POST',
      body: form,
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw networkFailure(error);
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as RfqResponse;
}

/**
 * Used when a buyer clicks one of the near-miss suggestions on a no-match row.
 * Deliberately not a second POST /api/rfq -- accepting a suggestion should not
 * persist a whole new RFQ.
 */
export async function fetchPart(mpn: string): Promise<PartDetail> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/part/${encodeURIComponent(mpn)}`), {
      ...WITH_SESSION,
    });
  } catch (error) {
    throw networkFailure(error);
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as PartDetail;
}

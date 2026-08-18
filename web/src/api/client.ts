import type { ApiErrorBody, PartDetail, RfqResponse, Session } from './types';

/**
 * Thin API client.
 *
 * Its one real job is turning every failure into an ApiFailure carrying a
 * message worth showing a user. A dashboard that renders "Failed to fetch" is
 * telling the person in front of it nothing they can act on.
 */

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
  return new ApiFailure(
    'Could not reach the PartScope API.',
    'NETWORK',
    0,
    error instanceof Error && error.message
      ? `${error.message}. Is the API running on port 3000?`
      : 'Is the API running on port 3000?',
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
    response = await fetch('/api/me');
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
    response = await fetch('/api/login', {
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
    await fetch('/api/logout', { method: 'POST' });
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
    response = await fetch('/api/rfq', {
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
    response = await fetch('/api/rfq', { method: 'POST', body: form, signal });
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
    response = await fetch(`/api/part/${encodeURIComponent(mpn)}`);
  } catch (error) {
    throw networkFailure(error);
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as PartDetail;
}

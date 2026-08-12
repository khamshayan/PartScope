import type { ApiErrorBody, PartDetail, RfqResponse } from './types';

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
    'Could not reach the Source Scope API.',
    'NETWORK',
    0,
    error instanceof Error && error.message
      ? `${error.message}. Is the API running on port 3000?`
      : 'Is the API running on port 3000?',
  );
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

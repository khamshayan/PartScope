/**
 * Structured errors.
 *
 * Every failure leaves this API as `{ error: { code, message, details? } }`.
 * A stack trace is not an API response: the client cannot branch on it, and it
 * leaks internals to whoever asked.
 */

export class ApiError extends Error {
  constructor(status, code, message, details = undefined) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }

  toJSON() {
    return {
      error: {
        code: this.code,
        message: this.message,
        ...(this.details ? { details: this.details } : {}),
      },
    };
  }
}

export const badRequest = (message, details) =>
  new ApiError(400, 'BAD_REQUEST', message, details);

export const notFound = (message) => new ApiError(404, 'NOT_FOUND', message);

export const payloadTooLarge = (message) =>
  new ApiError(413, 'PAYLOAD_TOO_LARGE', message);

export const serviceUnavailable = (message, details) =>
  new ApiError(503, 'ML_SERVICE_UNAVAILABLE', message, details);

export const internal = (message) => new ApiError(500, 'INTERNAL_ERROR', message);

/** Turn a zod failure into a flat, readable list of field problems. */
export function fromZod(error) {
  const details = error.issues.map((issue) => ({
    path: issue.path.join('.') || '(root)',
    message: issue.message,
  }));
  return badRequest('Request body failed validation', details);
}

/** Express error handler. Must keep all four parameters to be recognised. */
// eslint-disable-next-line no-unused-vars
export function errorHandler(error, _req, res, _next) {
  if (error instanceof ApiError) {
    return res.status(error.status).json(error.toJSON());
  }

  if (error?.type === 'entity.too.large') {
    return res.status(413).json(payloadTooLarge('Request body is too large').toJSON());
  }

  if (error?.code === 'LIMIT_FILE_SIZE') {
    return res.status(413).json(payloadTooLarge('Uploaded file is too large').toJSON());
  }

  console.error('[api] unhandled error:', error);
  return res.status(500).json(internal('An unexpected error occurred').toJSON());
}

export function notFoundHandler(req, res) {
  res.status(404).json(notFound(`No route for ${req.method} ${req.path}`).toJSON());
}

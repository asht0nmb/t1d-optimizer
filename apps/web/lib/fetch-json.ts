/**
 * fetch + parse JSON that never throws a raw `SyntaxError: Unexpected token '<'`
 * on a non-JSON response (e.g. a 404/500 HTML error page, or a gateway error).
 *
 * On any failure it resolves to an `{ error }` object, so callers keep using
 * their existing `body.error` checks and surface a clean message instead of a
 * cryptic JSON-parse error.
 */
export interface MaybeError {
  error?: string;
}

export async function fetchJson<T>(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<T & MaybeError> {
  let res: Response;
  try {
    res = await fetch(input, init);
  } catch (e) {
    return {
      error: e instanceof Error ? e.message : "Network error",
    } as T & MaybeError;
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return {
      error: `Server returned a non-JSON ${res.status} response`,
    } as T & MaybeError;
  }

  if (!res.ok) {
    const apiError = (body as MaybeError | null)?.error;
    return {
      ...(body as object),
      error: apiError ?? `Request failed (${res.status})`,
    } as T & MaybeError;
  }

  return body as T & MaybeError;
}

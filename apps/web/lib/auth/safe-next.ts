/**
 * Resolve a post-login `next` parameter to a same-origin absolute URL.
 *
 * Only a single-slash relative path is accepted. Anything else (protocol-
 * relative `//x`, `/\x`, backslashes, schemes, `@host` userinfo tricks,
 * whitespace/control characters) falls back to `/`. The pre-checks matter
 * because the WHATWG URL parser silently strips tabs/newlines and treats
 * `\` as `/`; the final origin comparison is the backstop.
 */
export function safeNextUrl(next: string | null, origin: string): URL {
  const fallback = new URL("/", origin);
  if (!next) return fallback;
  if (/[\u0000- \u007f\\]/.test(next)) return fallback;
  if (!/^\/(?!\/)/.test(next)) return fallback;
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return fallback;
  }
  return url.origin === new URL(origin).origin ? url : fallback;
}

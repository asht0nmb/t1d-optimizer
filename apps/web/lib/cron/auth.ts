import { timingSafeEqual } from "node:crypto";

/**
 * Shared secret check for Vercel Cron and manual cron triggers.
 *
 * Uses a constant-time comparison (`crypto.timingSafeEqual`) to avoid leaking
 * the secret via response-timing side channels. `timingSafeEqual` throws when
 * the two buffers differ in length, so lengths are compared (and an early
 * return taken) before the constant-time check.
 */
export function verifyCronAuth(request: Request): boolean {
  const secret = process.env.CRON_SECRET;
  if (!secret) {
    return false;
  }
  const auth = request.headers.get("authorization") ?? "";
  const expected = `Bearer ${secret}`;
  const authBuf = Buffer.from(auth);
  const expectedBuf = Buffer.from(expected);
  if (authBuf.length !== expectedBuf.length) {
    return false;
  }
  return timingSafeEqual(authBuf, expectedBuf);
}

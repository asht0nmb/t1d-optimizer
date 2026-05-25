"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { createClient } from "@/lib/supabase/client";

function LoginForm() {
  const searchParams = useSearchParams();
  const callbackError = searchParams.get("error");

  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSent(false);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback`,
        shouldCreateUser: true,
      },
    });
    if (err) {
      const msg = err.message.toLowerCase();
      if (msg.includes("rate") || msg.includes("429")) {
        setError(
          "Supabase email rate limit hit (about 4/hour on free tier). Wait an hour or use a different email.",
        );
      } else {
        setError(err.message);
      }
    } else {
      setSent(true);
    }
  }

  return (
    <div className="mx-auto max-w-md rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
      <h1 className="text-xl font-semibold text-slate-900">Sign in</h1>
      <p className="mt-2 text-sm text-slate-600">
        Magic link via Supabase. Use a personal inbox (Gmail) if{" "}
        <span className="font-medium">@uw.edu</span> does not deliver — many
        schools block <code className="text-xs">noreply@mail.app.supabase.io</code>.
      </p>

      {callbackError && (
        <p className="mt-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {callbackError}
        </p>
      )}

      {sent ? (
        <div className="mt-4 space-y-2 text-sm text-green-800">
          <p>
            If Supabase accepted the request, a link was sent to{" "}
            <span className="font-medium">{email}</span>.
          </p>
          <p className="text-slate-600">
            Check spam/junk. Open the link once — clicking twice invalidates it.
            No mail after 5 minutes usually means the address was filtered; try
            Gmail or add custom SMTP in Supabase.
          </p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <label className="block text-sm font-medium text-slate-700">
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
              placeholder="you@gmail.com"
            />
          </label>
          {error && (
            <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          )}
          <button
            type="submit"
            className="w-full rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Send magic link
          </button>
        </form>
      )}
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<p className="text-slate-500">Loading…</p>}>
      <LoginForm />
    </Suspense>
  );
}

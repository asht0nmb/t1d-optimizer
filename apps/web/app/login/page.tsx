"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";

function LoginForm() {
  const searchParams = useSearchParams();
  const callbackError = searchParams.get("error");

  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSent(false);
    setSubmitting(true);
    try {
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
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="mx-auto max-w-md space-y-6 p-8">
      <div className="space-y-2">
        <h1 className="text-xl font-semibold text-foreground">Sign in</h1>
        <p className="text-sm text-muted-foreground">
          Magic link via Supabase. Use a personal inbox (Gmail) if{" "}
          <span className="font-medium text-foreground">@uw.edu</span> does
          not deliver — many schools block{" "}
          <code className="text-xs">noreply@mail.app.supabase.io</code>.
        </p>
      </div>

      {callbackError && <ErrorState title="Sign-in error" message={callbackError} />}

      {sent ? (
        <div className="space-y-2 text-sm" role="status">
          <p className="text-success">
            If Supabase accepted the request, a link was sent to{" "}
            <span className="font-medium text-foreground">{email}</span>.
          </p>
          <p className="text-muted-foreground">
            Check spam/junk. Open the link once — clicking twice invalidates
            it. No mail after 5 minutes usually means the address was
            filtered; try Gmail or add custom SMTP in Supabase.
          </p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block text-sm font-medium text-foreground">
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="you@gmail.com"
            />
          </label>
          {error && <ErrorState message={error} />}
          <Button type="submit" disabled={submitting} className="w-full">
            {submitting ? "Sending…" : "Send magic link"}
          </Button>
        </form>
      )}
    </Card>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <p role="status" className="text-sm text-muted-foreground">
          Loading…
        </p>
      }
    >
      <LoginForm />
    </Suspense>
  );
}

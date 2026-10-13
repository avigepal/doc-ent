import { Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { button, errorText, input } from "../ui";

export function Login() {
  const [tokenInput, setTokenInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // the server checks the password and answers with a session that expires
      login(await api.login(tokenInput));
      navigate("/status");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Check that it is running and try again.",
      );
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-[var(--line)] p-7 shadow-[0_1px_0_var(--line)]"
      >
        <h1 className="font-display text-center text-xl font-semibold tracking-tight text-[var(--edit)]">
          DOC<span className="text-[var(--locator)]">·</span>ENT
        </h1>

        {/* The field has no visible label, so it carries one for screen readers. */}
        <input
          id="token"
          type="password"
          aria-label="Password"
          placeholder="Enter the password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          autoFocus
          className={`${input} mt-6 mb-4 w-full`}
        />
        {error && (
          <p role="alert" className={`mb-3 ${errorText}`}>
            {error}
          </p>
        )}
        <button type="submit" disabled={!tokenInput || busy} className={`${button} inline-flex w-full items-center justify-center gap-2`}>
          {busy && <Loader2 size={14} className="animate-spin" />} Enter
        </button>
      </form>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { button, input } from "../ui";

export function Login() {
  const [tokenInput, setTokenInput] = useState("");
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    // Not validated here on purpose — the token is only ever checked by
    // the server on the next real request. If it's wrong, that request's
    // 401 bounces the user back here (see RequireAuth in App.tsx).
    login(tokenInput.trim());
    navigate("/status");
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-[var(--line)] p-7 shadow-[0_1px_0_var(--line)]"
      >
        <h1 className="font-display text-center text-xl font-semibold tracking-tight">
          DOC<span className="text-[var(--index)]">·</span>ENT
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
        <button type="submit" disabled={!tokenInput} className={`${button} w-full`}>
          Enter
        </button>
      </form>
    </div>
  );
}

import {
  AlertCircle,
  Check,
  CircleHelp,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  Pin,
  PinOff,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, type ConversationSummary, type FolderStatus } from "../api/client";
import { buttonSecondary, errorText, label, muted } from "../ui";

const FOLDER_POLL_MS = 4000;
const CONVERSATION_POLL_MS = 5000;
// Sits right under the fixed Header (h-12 — see Header.tsx's HEADER_HEIGHT_CLASS).
const SIDEBAR_TOP_CLASS = "lg:top-12";

/** Modal explaining how Ask works. Rendered into document.body rather than
 * inside the sidebar: the sidebar is its own stacking context (lg:z-10),
 * which would cap the modal below the fixed Header (z-20) and leave the
 * top 48px of the backdrop uncovered. */
function HelpModal({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-[var(--line)] bg-[var(--paper)] p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="help-title" className="font-display text-base font-semibold tracking-tight">
            How Ask works
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="cursor-pointer rounded p-0.5 text-[var(--ink-soft)] hover:text-[var(--ink)]"
          >
            <X size={16} />
          </button>
        </div>
        <ul className="mt-3 space-y-2.5 text-[14px] leading-relaxed text-[var(--ink-soft)]">
          <li>
            <span className="font-medium text-[var(--ink)]">Ask about your documents.</span> Pick{" "}
            <span className="font-medium text-[var(--ink)]">All</span> or one or more folders under Scope. You get
            grounded answers with citations, plus cross-document and statistical findings when they apply.
          </li>
          <li>
            <span className="font-medium text-[var(--ink)]">Just chat.</span>{" "}
            <span className="font-medium text-[var(--ink)]">Chat</span> is selected by default: you talk to the model
            directly, with no documents involved. Pick All or a folder to search your documents instead.
          </li>
          <li>
            <span className="font-medium text-[var(--ink)]">Add files.</span> Use the upload button next to the
            message box, or drag files onto it. They go into the "uploads" folder and are processed into the
            corpus.
          </li>
          <li>Needs a live LLM server.</li>
        </ul>
      </div>
    </div>,
    document.body,
  );
}

/** Asks for a new folder's name. Rendered into document.body like HelpModal
 * (the sidebar is its own stacking context). `onCreate` throws on failure so
 * the error can be shown right here and the dialog stays open to retry. */
function NewFolderModal({ onClose, onCreate }: { onClose: () => void; onCreate: (name: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !creating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, creating]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    try {
      await onCreate(trimmed);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setCreating(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={() => !creating && onClose()}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-folder-title"
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-lg border border-[var(--line)] bg-[var(--paper)] p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="new-folder-title" className="font-display text-base font-semibold tracking-tight">
            New folder
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            aria-label="Close"
            className="cursor-pointer rounded p-0.5 text-[var(--ink-soft)] hover:text-[var(--ink)] disabled:cursor-not-allowed"
          >
            <X size={16} />
          </button>
        </div>
        <p className={`mt-1 text-xs ${muted}`}>
          Name it, then add documents to it. Letters, numbers, dashes and underscores work best.
        </p>
        <label htmlFor="new-folder-name" className={`${label} mt-4 block`}>
          Folder name
        </label>
        <input
          id="new-folder-name"
          type="text"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. contracts"
          disabled={creating}
          className="mt-1.5 w-full rounded border border-[var(--line)] bg-[var(--paper)] px-2.5 py-1.5 text-[14px] text-[var(--ink)] placeholder:text-[var(--ink-soft)] focus:border-[var(--index)] focus:outline-none disabled:opacity-60"
        />
        {error && <p className={`mt-2 ${errorText}`}>{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={creating} className={buttonSecondary}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={!name.trim() || creating}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded bg-[var(--index)] px-3 py-1 text-xs font-medium text-[var(--paper)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {creating && <Loader2 size={12} className="animate-spin" />}
            Create
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/** Chat list + Scope, shown only on the Ask page (see App.tsx's Layout).
 * Both are scoped to that page via its URL (?c=, ?all=, ?folders=) rather
 * than component state — Sidebar and Ask.tsx are siblings under Layout,
 * not parent/child, so the URL is the only state both can read AND write
 * without threading a context through Layout for something only one page
 * uses. */
export function Sidebar() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [newFolderOpen, setNewFolderOpen] = useState(false);

  const activeConversationId = searchParams.get("c");
  const scopeAll = searchParams.get("all") === "1";
  const selectedFolders = (searchParams.get("folders") ?? "").split(",").filter(Boolean);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      api.listFolders().then(
        (r) => {
          if (!cancelled) setFolders(r.folders);
        },
        () => {}, // keep the last known-good list on a transient failure rather than flashing empty
      );
    };
    poll();
    const interval = setInterval(poll, FOLDER_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const navigate = useNavigate();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const refreshConversations = () =>
    api.listConversations().then(
      (r) => setConversations(r.conversations),
      () => {},
    );

  const togglePin = async (c: ConversationSummary) => {
    await api.setConversationPinned(c.conversation_id, !c.pinned).catch(() => {});
    refreshConversations();
  };

  const removeConversation = async (id: string) => {
    setConfirmingId(null);
    await api.deleteConversation(id).catch(() => {});
    // deleting the chat you're looking at would leave its thread on screen
    // under an id that no longer exists — land on a fresh chat instead
    if (id === activeConversationId) navigate("/ask");
    refreshConversations();
  };

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      api.listConversations().then(
        (r) => {
          if (!cancelled) setConversations(r.conversations);
        },
        () => {},
      );
    };
    poll();
    const interval = setInterval(poll, CONVERSATION_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const toggleAll = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("folders");
    if (scopeAll) next.delete("all");
    else next.set("all", "1");
    setSearchParams(next, { replace: true });
  };

  // Chat = no documents in scope, which is also what an empty Scope means; picking
  // All or a folder leaves it, and un-picking the last one comes back to it.
  const directChat = !scopeAll && selectedFolders.length === 0;
  const chatDirectly = () => {
    if (directChat) return;
    const next = new URLSearchParams(searchParams);
    next.delete("all");
    next.delete("folders");
    setSearchParams(next, { replace: true });
  };

  const toggleFolder = (name: string) => {
    const next = new URLSearchParams(searchParams);
    next.delete("all");
    const updated = selectedFolders.includes(name)
      ? selectedFolders.filter((f) => f !== name)
      : [...selectedFolders, name];
    if (updated.length > 0) next.set("folders", updated.join(","));
    else next.delete("folders");
    setSearchParams(next, { replace: true });
  };

  const createFolder = async (name: string) => {
    await api.createFolder(name);
    const r = await api.listFolders();
    setFolders(r.folders);
  };

  return (
    <aside
      className={`shrink-0 overflow-y-auto border-b border-[var(--line)] bg-[var(--paper)] px-4 py-4 lg:fixed lg:bottom-0 lg:left-0 lg:z-10 lg:w-[220px] lg:border-b-0 lg:border-r ${SIDEBAR_TOP_CLASS}`}
    >
      <div>
        <Link
          to="/ask"
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-[14px] text-[var(--ink-soft)] transition-colors hover:bg-[var(--index-soft)] hover:text-[var(--ink)]"
        >
          <MessageSquarePlus size={14} /> New chat
        </Link>
        {conversations.length > 0 && (
          <div className="mt-2 flex flex-col gap-1.5">
            {conversations.map((c) => {
              const active = c.conversation_id === activeConversationId;
              const confirming = confirmingId === c.conversation_id;
              const iconButton = "cursor-pointer rounded p-1 text-[var(--ink-soft)] hover:text-[var(--ink)]";
              return (
                <div
                  key={c.conversation_id}
                  // Every chat is a light bordered card (a faint tint of the text color over
                  // the page, so it works in light and dark mode); hover deepens it a little,
                  // and the open chat switches to the accent colors so it stands apart.
                  className={`group flex items-center rounded border transition-colors ${
                    active
                      ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
                      : "border-[var(--line)] bg-[color-mix(in_srgb,var(--ink)_4%,var(--paper))] text-[var(--ink-soft)] hover:border-[var(--ink-soft)] hover:bg-[color-mix(in_srgb,var(--ink)_8%,var(--paper))] hover:text-[var(--ink)]"
                  }`}
                >
                  <Link
                    to={`/ask?c=${c.conversation_id}`}
                    title={c.title}
                    className={`flex min-w-0 flex-1 items-center gap-1.5 px-3 py-1.5 text-[12.5px] ${active ? "font-medium" : ""}`}
                  >
                    {c.pinned && <Pin size={11} className="shrink-0 text-[var(--index)]" />}
                    <span className="truncate">{c.title}</span>
                  </Link>
                  {confirming ? (
                    <span className="flex shrink-0 items-center pr-1">
                      <button
                        title="Confirm delete"
                        onClick={() => removeConversation(c.conversation_id)}
                        className={`${iconButton} !text-[var(--danger)]`}
                      >
                        <Check size={13} />
                      </button>
                      <button title="Cancel" onClick={() => setConfirmingId(null)} className={iconButton}>
                        <X size={13} />
                      </button>
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center pr-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                      <button title={c.pinned ? "Unpin chat" : "Pin chat"} onClick={() => togglePin(c)} className={iconButton}>
                        {c.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                      </button>
                      <button title="Delete chat" onClick={() => setConfirmingId(c.conversation_id)} className={iconButton}>
                        <Trash2 size={13} />
                      </button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="mt-5 border-t border-[var(--line)] pt-4">
        <div className="flex items-center gap-1.5">
          <span className={label}>Scope</span>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            title="How Ask works"
            aria-label="How Ask works"
            className="cursor-pointer rounded text-[var(--ink-soft)] transition-colors hover:text-[var(--index)]"
          >
            <CircleHelp size={14} />
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {/* Chat is the default: nothing selected under Scope means talking to the model directly. */}
          <button
            type="button"
            onClick={chatDirectly}
            aria-pressed={directChat}
            title="Talk to the model directly — your documents are not searched"
            className={`inline-flex cursor-pointer items-center gap-1 rounded border px-2 py-0.5 text-xs transition-colors ${
              directChat
                ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
                : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]"
            }`}
          >
            <MessageSquare size={11} aria-hidden="true" /> Chat
          </button>
          <button
            type="button"
            onClick={toggleAll}
            title="Search the whole ingested corpus"
            className={`cursor-pointer rounded border px-2 py-0.5 text-xs transition-colors ${
              scopeAll
                ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
                : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]"
            }`}
          >
            All
          </button>
          {folders.map((f) => {
            const isSelected = selectedFolders.includes(f.name);
            return (
              <button
                key={f.name}
                type="button"
                onClick={() => toggleFolder(f.name)}
                className={`cursor-pointer rounded border px-2 py-0.5 text-xs transition-colors ${
                  isSelected
                    ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
                    : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]"
                }`}
              >
                {f.name}
                {f.processing && (
                  <Loader2
                    size={11}
                    aria-label="Processing"
                    className="ml-1 inline animate-spin align-[-1px] text-[var(--locator)]"
                  >
                    <title>Files in this folder are still being processed</title>
                  </Loader2>
                )}
                {f.has_failures && (
                  <AlertCircle size={11} aria-label="Failures" className="ml-1 inline align-[-1px] text-[var(--danger)]">
                    <title>At least one file failed to process (e.g. a password-protected PDF)</title>
                  </AlertCircle>
                )}
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => setNewFolderOpen(true)}
            title="New folder"
            aria-label="New folder"
            className="inline-flex cursor-pointer items-center rounded border border-dashed border-[var(--line)] px-2 py-0.5 text-[var(--ink-soft)] transition-colors hover:border-[var(--index)] hover:text-[var(--index)]"
          >
            <Plus size={13} />
          </button>
          {folders.length === 0 && <span className={muted}>No folders yet</span>}
        </div>

        {scopeAll && <p className={`mt-2 text-xs ${muted}`}>whole corpus</p>}
        {!scopeAll && selectedFolders.length === 0 && (
          <p className={`mt-2 text-xs ${muted}`}>chatting directly with the model</p>
        )}
      </div>
      {helpOpen && <HelpModal onClose={() => setHelpOpen(false)} />}
      {newFolderOpen && <NewFolderModal onClose={() => setNewFolderOpen(false)} onCreate={createFolder} />}
    </aside>
  );
}

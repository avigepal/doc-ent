import { FileText } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { Highlighted, highlightRegex } from "./DocumentViewer";

/** What a [n] citation points at, as far as the hover card needs it. */
interface Preview {
  name: string;
  heading: string;
  text: string;
  terms: string[];
  phrase: boolean;
}

/** Where the question's best-matching section starts to read, and how much of it fits the card. */
const SNIPPET_CHARS = 420;
const SHOW_DELAY_MS = 250;
const HIDE_DELAY_MS = 100;
const CARD_WIDTH = 360;
const CARD_MARGIN = 8;

// One request per (file, question), shared by every citation that points at the file.
const cache = new Map<string, Promise<Preview | null>>();

function loadPreview(path: string, question: string): Promise<Preview | null> {
  const key = `${path}\u0000${question}`;
  let pending = cache.get(key);
  if (!pending) {
    pending = api
      .getFileSections({ path, q: question, span: 1 })
      .then((r): Preview | null => {
        const section = r.sections.find((s) => s.index === r.anchor) ?? r.sections[0];
        if (!section) return null;
        return { name: r.file.name, heading: section.heading, text: section.text, terms: r.terms, phrase: r.phrase };
      })
      .catch(() => {
        cache.delete(key); // a failed lookup shouldn't stick; try again next hover
        return null;
      });
    cache.set(key, pending);
  }
  return pending;
}

/** A window of the section that starts a little before the first matched word,
 * so the highlighted words are in view. */
function snippetOf(text: string, pattern: RegExp | null): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= SNIPPET_CHARS) return flat;
  const hit = pattern ? flat.search(pattern) : -1;
  const start = hit > 60 ? hit - 60 : 0;
  const end = Math.min(flat.length, start + SNIPPET_CHARS);
  return `${start > 0 ? "…" : ""}${flat.slice(start, end)}${end < flat.length ? "…" : ""}`;
}

/** "contracts/q2.txt" instead of the full container path. */
function fileLabel(path: string): string {
  const marker = "/raw/";
  const idx = path.indexOf(marker);
  return idx === -1 ? path : path.slice(idx + marker.length);
}

type Placement = { left: number; top?: number; bottom?: number };

function placeCard(anchor: DOMRect): Placement {
  const left = Math.min(Math.max(anchor.left, CARD_MARGIN), window.innerWidth - CARD_WIDTH - CARD_MARGIN);
  // open upwards when there is more room above than below
  return anchor.top > window.innerHeight - anchor.bottom
    ? { left, bottom: window.innerHeight - anchor.top + 6 }
    : { left, top: anchor.bottom + 6 };
}

/** One number inside a "[1]" badge. Hovering (or focusing) it shows the file
 * and the passage that best matches the question; clicking opens the viewer. */
export function CitationNumber({
  n,
  path,
  question,
  onOpen,
}: {
  n: number;
  /** The cited file; without it the number is just a label with no preview. */
  path: string | undefined;
  question: string;
  onOpen: ((n: number) => void) | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null | undefined>(undefined); // undefined = loading
  const [placement, setPlacement] = useState<Placement | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const cardId = useId();

  const show = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (!button.current || !path) return;
      setPlacement(placeCard(button.current.getBoundingClientRect()));
      setOpen(true);
    }, SHOW_DELAY_MS);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(false), HIDE_DELAY_MS);
  };

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // fetched only once a card is actually opened
  useEffect(() => {
    if (!open || !path) return;
    let cancelled = false;
    void loadPreview(path, question).then((p) => {
      if (!cancelled) setPreview(p);
    });
    return () => {
      cancelled = true;
    };
  }, [open, path, question]);

  // a card follows its badge only until the page moves under it: close on scroll or Escape
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!onOpen && !path) return <>{n}</>;

  const pattern = preview ? highlightRegex(preview.terms, preview.phrase) : null;

  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={() => {
          setOpen(false);
          onOpen?.(n);
        }}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        aria-describedby={open ? cardId : undefined}
        title={path ? undefined : `Open source ${n}`}
        className={`underline-offset-2 hover:text-[var(--index)] hover:underline ${onOpen ? "cursor-pointer" : "cursor-default"}`}
      >
        {n}
      </button>
      {open &&
        placement &&
        createPortal(
          <div
            id={cardId}
            role="tooltip"
            style={{ ...placement, width: CARD_WIDTH, maxWidth: `calc(100vw - ${CARD_MARGIN * 2}px)` }}
            // not interactive, so moving the pointer off the badge simply closes it
            className="pointer-events-none fixed z-40 rounded border border-[var(--line)] bg-[var(--paper)] p-3 text-left shadow-lg"
          >
            <p className="flex items-center gap-1.5 text-[13px] font-medium text-[var(--ink)]">
              <FileText size={13} className="shrink-0 text-[var(--ink-soft)]" aria-hidden="true" />
              <span className="min-w-0 truncate">{preview?.name ?? (path ? fileLabel(path) : "")}</span>
            </p>
            {preview === undefined && <p className="mt-2 text-xs text-[var(--ink-soft)]">Loading the matching passage…</p>}
            {preview === null && <p className="mt-2 text-xs text-[var(--ink-soft)]">No preview available for this source.</p>}
            {preview && (
              <>
                {preview.heading && <p className="mt-2 text-xs font-semibold text-[var(--ink)]">{preview.heading}</p>}
                <p className="mt-1 text-xs leading-relaxed text-[var(--ink-soft)]">
                  <Highlighted text={snippetOf(preview.text, pattern)} pattern={pattern} />
                </p>
              </>
            )}
            {onOpen && <p className="font-mono mt-2 text-[10px] tracking-wider text-[var(--ink-soft)] uppercase">Click to open</p>}
          </div>,
          document.body,
        )}
    </>
  );
}

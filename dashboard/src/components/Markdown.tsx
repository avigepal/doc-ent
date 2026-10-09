import { Check, Copy } from "lucide-react";
import { isValidElement, useMemo, useState } from "react";
import type { ComponentProps, ReactElement, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { CitationNumber } from "./CitationPreview";
import { parseViewHref, useViewer } from "./DocumentViewer";
import type { ViewTarget } from "./DocumentViewer";

// "[1]", "[1][2]" and "[1, 2]" citation markers the model writes.
const CITATION = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

/** Turns "[n]" markers inside plain text into small badges so they read as
 * citations instead of stray brackets. They match the numbers in the
 * Sources dialog. */
function withCitations(children: ReactNode, onCitation?: (n: number) => void, preview?: CitationPreviewInfo): ReactNode {
  if (typeof children === "string") {
    const parts: ReactNode[] = [];
    let last = 0;
    for (const match of children.matchAll(CITATION)) {
      const start = match.index ?? 0;
      if (start > last) parts.push(children.slice(last, start));
      const numbers = match[1].split(",").map((n) => Number(n.trim()));
      parts.push(
        <span
          key={start}
          className="font-mono mx-0.5 rounded-sm bg-[var(--locator-soft)] px-1 py-px text-[10px] text-[var(--ink-soft)]"
        >
          {onCitation || preview
            ? numbers.map((n, i) => (
                <span key={i}>
                  {i > 0 && ", "}
                  <CitationNumber
                    n={n}
                    path={preview?.sourceFor(n)}
                    question={preview?.question ?? ""}
                    onOpen={onCitation}
                  />
                </span>
              ))
            : match[1]}
        </span>,
      );
      last = start + match[0].length;
    }
    if (parts.length === 0) return children;
    if (last < children.length) parts.push(children.slice(last));
    return parts;
  }
  if (Array.isArray(children)) {
    return children.map((child, i) => <span key={i}>{withCitations(child, onCitation, preview)}</span>);
  }
  return children;
}

/** Lets a [n] badge show the cited file and the passage that matches the question. */
export interface CitationPreviewInfo {
  sourceFor: (n: number) => string | undefined;
  question: string;
}

type P<T extends keyof React.JSX.IntrinsicElements> = ComponentProps<T> & { node?: unknown };

/** Flattens highlighted code (nested <span>s) back to the plain text, for
 * the copy button. */
function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) return nodeText((node.props as { children?: ReactNode }).children);
  return "";
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // navigator.clipboard only exists on secure origins (https / localhost);
    // the dashboard is often opened over a plain-http LAN address.
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  }
}

/** Fenced code block: language label, copy button and syntax colors, like a
 * chat assistant's. */
function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);

  const codeElement = isValidElement(children) ? (children as ReactElement<{ className?: string; children?: ReactNode }>) : null;
  const language = /language-([\w+#-]+)/.exec(codeElement?.props.className ?? "")?.[1];
  const text = nodeText(codeElement ? codeElement.props.children : children).replace(/\n$/, "");

  const copy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    }
  };

  return (
    <div className="overflow-hidden rounded border border-[var(--line)] bg-[var(--locator-soft)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] px-3 py-1">
        <span className="font-mono text-[11px] tracking-wider text-[var(--ink-soft)] uppercase">
          {language ?? "code"}
        </span>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? "Copied" : "Copy code"}
          className="font-mono inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-[var(--ink-soft)] transition-colors hover:text-[var(--ink)]"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="font-mono overflow-x-auto p-3 text-xs leading-relaxed">{children}</pre>
    </div>
  );
}

function buildComponents(
  onCitation?: (n: number) => void,
  openView?: (target: ViewTarget) => void,
  preview?: CitationPreviewInfo,
) {
  return {
  // prose is held to a readable line length (~70 characters); tables and code keep the full width
  p: ({ node: _n, children, ...rest }: P<"p">) => (
    <p {...rest} className="max-w-[72ch]">
      {withCitations(children, onCitation, preview)}
    </p>
  ),
  li: ({ node: _n, children, ...rest }: P<"li">) => <li {...rest}>{withCitations(children, onCitation, preview)}</li>,
  td: ({ node: _n, children, ...rest }: P<"td">) => (
    <td {...rest} className="border border-[var(--line)] px-2.5 py-1.5 align-top">
      {withCitations(children, onCitation, preview)}
    </td>
  ),
  th: ({ node: _n, children, ...rest }: P<"th">) => (
    <th
      {...rest}
      className="border border-[var(--line)] bg-[var(--locator-soft)] px-2.5 py-1.5 text-left font-semibold"
    >
      {withCitations(children, onCitation, preview)}
    </th>
  ),
  table: ({ node: _n, ...rest }: P<"table">) => (
    <div className="overflow-x-auto">
      <table {...rest} className="w-full border-collapse text-[14px]" />
    </div>
  ),
  ul: ({ node: _n, ...rest }: P<"ul">) => <ul {...rest} className="max-w-[72ch] list-disc space-y-1 pl-5" />,
  ol: ({ node: _n, ...rest }: P<"ol">) => <ol {...rest} className="max-w-[72ch] list-decimal space-y-1 pl-5" />,
  h1: ({ node: _n, ...rest }: P<"h1">) => <h3 {...rest} className="font-display mt-1 text-[15px] font-semibold" />,
  h2: ({ node: _n, ...rest }: P<"h2">) => <h3 {...rest} className="font-display mt-1 text-[15px] font-semibold" />,
  h3: ({ node: _n, ...rest }: P<"h3">) => <h4 {...rest} className="font-display mt-1 text-sm font-semibold" />,
  h4: ({ node: _n, ...rest }: P<"h4">) => <h4 {...rest} className="font-display mt-1 text-sm font-semibold" />,
  strong: ({ node: _n, ...rest }: P<"strong">) => <strong {...rest} className="font-semibold" />,
  a: ({ node: _n, ...rest }: P<"a">) => {
    // "open" links in search results: show that part of the file in the viewer
    const target = parseViewHref(rest.href);
    if (target && openView) {
      return (
        <button
          type="button"
          onClick={() => openView(target)}
          className="cursor-pointer text-[var(--index)] underline underline-offset-2"
        >
          {rest.children}
        </button>
      );
    }
    return <a {...rest} target="_blank" rel="noreferrer" className="text-[var(--index)] underline underline-offset-2" />;
  },
  blockquote: ({ node: _n, ...rest }: P<"blockquote">) => (
    <blockquote {...rest} className="max-w-[72ch] border-l-2 border-[var(--line)] pl-3 text-[var(--ink-soft)]" />
  ),
  hr: () => <hr className="border-[var(--line)]" />,
  // Fenced blocks arrive as <pre><code>; inline code is a bare <code>.
  pre: ({ node: _n, children }: P<"pre">) => <CodeBlock>{children}</CodeBlock>,
  code: ({ node: _n, ...rest }: P<"code">) => (
    <code {...rest} className={rest.className ? rest.className : "font-mono rounded-sm bg-[var(--locator-soft)] px-1 py-px text-[0.85em]"} />
  ),
  };
}

/** Renders a model answer as Markdown (headings, lists, tables, code,
 * bold) like a chat assistant, instead of showing the raw symbols. */
export function Markdown({
  children,
  onCitation,
  preview,
}: {
  children: string;
  onCitation?: (n: number) => void;
  /** Keep this object stable (useMemo): a new one rebuilds the rendered answer. */
  preview?: CitationPreviewInfo;
}) {
  const viewer = useViewer();
  const open = viewer?.open;
  const components = useMemo(() => buildComponents(onCitation, open, preview), [onCitation, open, preview]);
  return (
    <div className="space-y-3 text-[15px] leading-relaxed break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

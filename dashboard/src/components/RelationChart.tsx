import type { RelationGraph } from "../api/client";

// A relation chart from the emails (see backend/app/search/email_graph.py:build_graph).
// Named people sit on a ring; in "around" mode the one person asked about is in the middle.
// Drawn as plain SVG, so no chart library is needed.

const WIDTH = 560;
const HEIGHT = 380;
const CX = WIDTH / 2;
const CY = HEIGHT / 2 - 10;
const RADIUS = 130;
const LABEL_CHARS = 22;

function place(graph: RelationGraph): Map<string, { x: number; y: number }> {
  const at = new Map<string, { x: number; y: number }>();
  const ring = graph.mode === "around" ? graph.nodes.filter((n) => n.id !== "p0") : graph.nodes;
  if (graph.mode === "around") at.set("p0", { x: CX, y: CY });
  ring.forEach((node, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(ring.length, 1);
    at.set(node.id, { x: CX + RADIUS * Math.cos(angle), y: CY + RADIUS * Math.sin(angle) });
  });
  return at;
}

function shorten(text: string): string {
  return text.length <= LABEL_CHARS ? text : text.slice(0, LABEL_CHARS - 1) + "…";
}

/** A person silhouette (head and shoulders) centred on (x, y). Named people are drawn in the accent colour,
 * everyone else in the muted one. */
function PersonIcon({ x, y, scale, highlighted }: { x: number; y: number; scale: number; highlighted: boolean }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`} style={{ fill: highlighted ? "var(--index)" : "var(--ink-soft)" }}>
      <circle cx={0} cy={-8} r={7} />
      <path d="M-13 14 C-13 4 -7 1 0 1 C7 1 13 4 13 14 Z" />
    </g>
  );
}

/** Where a node's label goes: just outside the node, on the side away from the middle of the chart,
 * so a label never sits on a line running into its node. The centre node's label goes below it. */
function labelAt(p: { x: number; y: number }, r: number): { x: number; y: number; anchor: "start" | "middle" | "end" } {
  const dx = p.x - CX;
  const dy = p.y - CY;
  const len = Math.hypot(dx, dy);
  const ux = len < 1 ? 0 : dx / len;
  const uy = len < 1 ? 1 : dy / len;
  const anchor = Math.abs(ux) < 0.3 ? "middle" : ux > 0 ? "start" : "end";
  return { x: p.x + ux * (r + 8), y: p.y + uy * (r + 8) + 4, anchor };
}

export function RelationChart({ graph }: { graph: RelationGraph }) {
  const at = place(graph);
  const names = new Map(graph.nodes.map((n) => [n.id, n.label]));

  return (
    <figure>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Relation chart of the people in the emails"
        className="h-auto w-full max-w-[560px]"
      >
        {graph.edges.map((edge) => {
          const a = at.get(edge.source);
          const b = at.get(edge.target);
          if (!a || !b) return null;
          const from = names.get(edge.source) ?? edge.source;
          const to = names.get(edge.target) ?? edge.target;
          const subjects = edge.subjects.length ? `\nSubjects: ${edge.subjects.join("; ")}` : "";
          return (
            <g key={`${edge.source}-${edge.target}`}>
              <title>{`${from} → ${to}: ${edge.forward}\n${to} → ${from}: ${edge.backward}\n${edge.first} to ${edge.last}${subjects}`}</title>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                strokeLinecap="round"
                style={{ stroke: "var(--ink-soft)", strokeOpacity: 0.45, strokeWidth: 1.5 + Math.min(4, edge.emails) }}
              />
              <text
                x={(a.x + b.x) / 2}
                y={(a.y + b.y) / 2}
                textAnchor="middle"
                dominantBaseline="middle"
                style={{ fill: "var(--ink)", stroke: "var(--surface)", strokeWidth: 4, paintOrder: "stroke", fontSize: 11 }}
              >
                {edge.emails}
              </text>
            </g>
          );
        })}

        {graph.nodes.map((node) => {
          const p = at.get(node.id);
          if (!p) return null;
          const r = node.person ? 20 : 15;
          const text = labelAt(p, r);
          return (
            <g key={node.id}>
              <title>{node.label}</title>
              <PersonIcon x={p.x} y={p.y} scale={node.person ? 1.3 : 1} highlighted={node.person} />
              <text
                x={text.x}
                y={text.y}
                textAnchor={text.anchor}
                style={{ fill: "var(--ink)", fontSize: 12, fontWeight: node.person ? 600 : 400 }}
              >
                {shorten(node.label)}
              </text>
            </g>
          );
        })}
      </svg>

      <figcaption className="mt-2 text-[12px] text-[var(--ink-soft)]">
        Blue people were named in your question; grey ones were found in the emails. Line thickness and the number on
        each line show how many emails passed between them. Hover a line for the direction, dates and subjects.
        {graph.missing.length > 0 && <> No emails found for: {graph.missing.join(", ")}.</>}
      </figcaption>
    </figure>
  );
}

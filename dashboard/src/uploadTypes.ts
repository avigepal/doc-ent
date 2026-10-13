/** The file types the backend can read (see backend/app/handlers/registry.py): the
 * upload buttons offer only these, and anything else dropped on them is skipped
 * with a message instead of being uploaded to fail later. */

const TYPES: { label: string; extensions: string[] }[] = [
  { label: "PDF", extensions: [".pdf"] },
  { label: "Word", extensions: [".doc", ".docx"] },
  { label: "Excel", extensions: [".xls", ".xlsx"] },
  { label: "PowerPoint", extensions: [".ppt", ".pptx"] },
  { label: "text, Markdown and CSV", extensions: [".txt", ".md", ".csv"] },
  { label: "HTML", extensions: [".html", ".htm"] },
  { label: "JSON, YAML, XML and logs", extensions: [".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".xml", ".log"] },
  { label: "images", extensions: [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"] },
  { label: "email", extensions: [".eml", ".msg", ".mbox", ".pst"] },
];

const EXTENSIONS = new Set(TYPES.flatMap((t) => t.extensions));

/** For the file input's `accept`: the picker then shows only these files. */
export const UPLOAD_ACCEPT = [...EXTENSIONS].join(",");

/** "PDF, Word, Excel, …" for messages and tooltips. */
export const UPLOAD_TYPES_TEXT = TYPES.map((t) => t.label).join(", ");

const extensionOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
};

/** Splits a selection into the files that can be uploaded and the names of those that can't. */
export function splitSupportedFiles(files: File[]): { supported: File[]; skipped: string[] } {
  const supported: File[] = [];
  const skipped: string[] = [];
  for (const file of files) {
    if (EXTENSIONS.has(extensionOf(file.name))) supported.push(file);
    else skipped.push(file.name);
  }
  return { supported, skipped };
}

/** The sentence shown when some of the chosen files were left out. */
export function skippedMessage(skipped: string[]): string {
  const shown = skipped.slice(0, 3).join(", ") + (skipped.length > 3 ? ` and ${skipped.length - 3} more` : "");
  return `Skipped ${skipped.length === 1 ? "a file" : `${skipped.length} files`} that can't be read (${shown}). Supported: ${UPLOAD_TYPES_TEXT}.`;
}

/** Does this Markdown contain a pipe table (a header line, then a `|---|---|` line)? Decides whether
 * an answer offers the XLSX and CSV downloads. */
export function hasMarkdownTable(markdown: string): boolean {
  const lines = markdown.split("\n");
  for (let i = 0; i + 1 < lines.length; i++) {
    const separator = lines[i + 1];
    if (lines[i].includes("|") && separator.includes("|") && separator.includes("-") && /^[\s|:-]+$/.test(separator)) {
      return true;
    }
  }
  return false;
}

// Converts docs/legal/privacy-policy.md into public/legal/privacy-policy.html.
// Runs as a prebuild step (see package.json) so the HTML is always in sync with
// the markdown source. No external deps — uses a block-aware renderer sufficient
// for the flat structure of the privacy policy.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const src = resolve(root, "docs/legal/privacy-policy.md");
const dest = resolve(root, "apps/pwa/public/legal/privacy-policy.html");

let raw = readFileSync(src, "utf8");

// Strip YAML front-matter and HTML comments
raw = raw.replace(/^---[\s\S]*?---\n/, "").trim();
raw = raw.replace(/<!--[\s\S]*?-->/g, "");

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

// --- Block parser ---
// Collapse the markdown into logical blocks before rendering, so multi-line
// paragraphs and list items (continuation lines, nested items) work correctly.

const BLANK = /^\s*$/;

// Join physical lines into logical lines: a line that starts with whitespace
// and follows a non-blank line is a continuation — append it to the previous.
function joinContinuations(lines) {
  const out = [];
  for (const line of lines) {
    if (out.length && /^\s+\S/.test(line) && out[out.length - 1] !== "") {
      out[out.length - 1] += ` ${line.trim()}`;
    } else {
      out.push(line);
    }
  }
  return out;
}

const lines = joinContinuations(raw.split("\n").map((l) => l.trimEnd()));

// Render line by line after continuations are folded
const html = [];
let inUl = false;
let inOl = false;
let paraLines = [];

function flushPara() {
  if (paraLines.length) {
    const text = paraLines.join(" ").trim();
    if (text) {
      // Detect italic-only lines (footnote style: *...*)
      if (/^\*[^*].*[^*]\*$/.test(text)) {
        html.push(`<p class="note">${inline(text.slice(1, -1))}</p>`);
      } else {
        html.push(`<p>${inline(text)}</p>`);
      }
    }
    paraLines = [];
  }
}

function closeList() {
  flushPara();
  if (inUl) {
    html.push("</ul>");
    inUl = false;
  }
  if (inOl) {
    html.push("</ol>");
    inOl = false;
  }
}

for (const line of lines) {
  if (BLANK.test(line)) {
    flushPara();
    continue;
  }

  if (/^#{1} /.test(line)) {
    closeList();
    html.push(`<h1>${inline(line.replace(/^# /, ""))}</h1>`);
    continue;
  }

  if (/^#{2} /.test(line)) {
    closeList();
    html.push(`<h2>${inline(line.replace(/^## /, ""))}</h2>`);
    continue;
  }

  if (/^---+$/.test(line)) {
    closeList();
    html.push("<hr />");
    continue;
  }

  // Top-level list item: "- text"
  if (/^- /.test(line)) {
    flushPara();
    if (!inUl) {
      html.push("<ul>");
      inUl = true;
    }
    const rest = line.slice(2);
    // Detect inline nested list markers already folded as "text  - sub1  - sub2"
    // (shouldn't happen after joinContinuations, but guard anyway)
    html.push(`<li>${inline(rest)}</li>`);
    continue;
  }

  // Nested list item already folded into the parent line via joinContinuations;
  // if one still arrives as a standalone line it means it was at a blank-line
  // boundary — treat as a plain paragraph item.
  closeList();
  paraLines.push(line);
}
closeList();
flushPara();

const page = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Privacy Notice — RASID</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 680px;
      margin: 2rem auto;
      padding: 0 1.25rem 4rem;
      color: #1a1a1a;
      line-height: 1.6;
    }
    h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
    h2 { font-size: 1.05rem; margin-top: 2rem; }
    p, li { font-size: 0.95rem; }
    ul { padding-left: 1.4rem; }
    hr { border: none; border-top: 1px solid #e0e0e0; margin: 2rem 0; }
    .note { font-size: 0.8rem; color: #555; font-style: italic; }
    code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
  </style>
</head>
<body>
${html.join("\n")}
</body>
</html>
`;

mkdirSync(dirname(dest), { recursive: true });
writeFileSync(dest, page, "utf8");
console.log(`privacy-policy.html written to ${dest}`);

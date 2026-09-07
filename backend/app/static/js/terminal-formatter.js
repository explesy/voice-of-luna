/**
 * Terminal text formatter, markdown link parsing, and speech text sanitization.
 */

function formatTerminalText(element) {
  if (!element) return;
  const rawText = element.dataset.rawText || element.textContent || "";
  if (!rawText) return;
  element.dataset.rawText = rawText;

  let escaped = rawText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  const links = [];
  escaped = escaped.replace(/\[([^\]]+)\]\(((?:https?:\/\/)[^)\s]+)\)/g, (match, title, url) => {
    const idx = links.length;
    links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer" class="term-link">${title}&nbsp;<span class="ext-glyph">↗</span></a>`);
    return `@@@LINK_${idx}@@@`;
  });

  escaped = escaped.replace(/(https?:\/\/[^\s<)]+)/g, (url) => {
    const idx = links.length;
    links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer" class="term-link">${url}&nbsp;<span class="ext-glyph">↗</span></a>`);
    return `@@@LINK_${idx}@@@`;
  });

  let sourcesPart = "";
  const sourcesPlaceholderRegex = /(?:(?:\n|^)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?[\s\S]*$|(?<=[.!?…\n])\s*(?:(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?\s*)?(?:(?:[-*•·]|\d+\.)?\s*[\(\[]?\s*@@@LINK_\d+@@@[\)\]]?\s*[,;•·–—\-/\n\s]*)+$)/i;
  const sourcesMatch = sourcesPlaceholderRegex.exec(escaped);
  if (sourcesMatch) {
    const before = escaped.slice(0, sourcesMatch.index).trim();
    const rawSources = escaped.slice(sourcesMatch.index).trim();
    const headerMatch = /^\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?/i.exec(rawSources);
    let header = "источники";
    let body = rawSources;
    if (headerMatch) {
      header = headerMatch[0].trim().replace(/^[#/*_~-\s]+/, "").replace(/:$/, "");
      body = rawSources.slice(headerMatch[0].length).trim();
    }
    if (body.startsWith("(") && body.endsWith(")")) {
      body = body.slice(1, -1).trim();
    }
    sourcesPart = `<div class="log-sources"><div class="sources-tag">// ${header.toUpperCase()}:</div><div class="sources-body">${body}</div></div>`;
    escaped = before;
  }

  // 1. Unescape escaped markdown characters if present
  escaped = escaped.replace(/\\([*_`~[\]])/g, "$1");

  // 2. Extract inline code
  const codeSnippets = [];
  escaped = escaped.replace(/`([^`\n]+)`/g, (match, code) => {
    const idx = codeSnippets.length;
    codeSnippets.push(`<code>${code}</code>`);
    return `@@@CODE_${idx}@@@`;
  });

  // 3. Bold (**...** or __...__)
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  escaped = escaped.replace(/__(.+?)__/g, "<strong>$1</strong>");

  // 4. Strikethrough (~~...~~)
  escaped = escaped.replace(/~~(.+?)~~/g, "<del>$1</del>");

  // 5. Italics (*...* or _..._)
  escaped = escaped.replace(/(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)/g, "<em>$1</em>");
  escaped = escaped.replace(/(?:\b|(?<=\s)|^)_(?!\s)(.+?)(?<!\s)_(?:\b|(?=\s)|$)/g, "<em>$1</em>");

  // 6. Clean any remaining stray backslashes
  escaped = escaped.replace(/\\+/g, "");

  // 7. Restore code snippets
  codeSnippets.forEach((html, idx) => {
    escaped = escaped.replace(`@@@CODE_${idx}@@@`, html);
  });

  // 8. Restore links
  links.forEach((html, idx) => {
    escaped = escaped.replace(`@@@LINK_${idx}@@@`, html);
    if (sourcesPart) {
      sourcesPart = sourcesPart.replace(`@@@LINK_${idx}@@@`, html);
    }
  });

  if (sourcesPart) {
    escaped += sourcesPart;
  }

  element.innerHTML = escaped;
}

function isPureCitation(text) {
  if (!text) return true;
  let stripped = text.replace(/\[[^\]]+\]\([^)]+\)/g, "");
  stripped = stripped.replace(/https?:\/\/\S+/g, "");
  stripped = stripped.replace(/[-*•·\d.,;:/\\|()\[\]\s–—\"'«»“”„!?>#~`]/g, "");
  return stripped.trim().length === 0;
}

function stripCitationParens(s) {
  const result = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    if (s[i] === "(" && (i === 0 || s[i - 1] !== "]")) {
      let depth = 1;
      let j = i + 1;
      while (j < n && depth > 0) {
        if (s[j] === "(") depth++;
        else if (s[j] === ")") depth--;
        j++;
      }
      const parenContent = s.slice(i, j);
      const lower = parenContent.toLowerCase();
      if (
        parenContent.includes("http://") ||
        parenContent.includes("https://") ||
        lower.includes("источник") ||
        lower.includes("ссылк")
      ) {
        while (result.length && (result[result.length - 1] === " " || result[result.length - 1] === "\t")) {
          result.pop();
        }
        i = j;
        continue;
      } else {
        result.push(parenContent);
        i = j;
        continue;
      }
    }
    result.push(s[i]);
    i++;
  }
  return result.join("");
}

function sanitizeForSpeech(text) {
  if (!text) return "";
  if (isPureCitation(text)) return "";

  let clean = text.split(/(?:\n|^)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?/i)[0];
  const trailingRegex = /(?<=[.!?…\n])\s*(?:(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?\s*)?(?:(?:[-*•·]|\d+\.)?\s*[\(\[]?\s*(?:\[[^\]]+\]\((?:https?:\/\/)[^)\s]+\)|https?:\/\/\S+)[\)\]]?\s*[,;•·–—\-/\n\s]*)+$/i;
  clean = clean.replace(trailingRegex, "");
  clean = stripCitationParens(clean);
  clean = clean.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  clean = clean.replace(/https?:\/\/\S+/g, "");
  clean = clean.replace(/\\+/g, "");
  clean = clean.replace(/[«»“”„]/g, '"');
  clean = clean.replace(/[*_`#~>]/g, "");
  clean = clean.replace(/^[ \t]*[-*•·][ \t]+/gm, "");
  return clean.replace(/\s+([.,;:!?])/g, "$1").replace(/[ \t]+/g, " ").trim();
}

window.formatTerminalText = formatTerminalText;
window.isPureCitation = isPureCitation;
window.stripCitationParens = stripCitationParens;
window.sanitizeForSpeech = sanitizeForSpeech;

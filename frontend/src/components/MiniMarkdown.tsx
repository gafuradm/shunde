/** Минимальный Markdown-рендер для ответов ИИ (без внешних зависимостей). */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">");
}

function inline(text: string): string {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code class="rounded bg-ink-100 px-1 py-[1px] text-[13px]">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\W)\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(
      /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a class="text-seal-600 underline" href="$2" target="_blank" rel="noreferrer noopener">$1</a>',
    );
}

function toHtml(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      out.push("</ul>");
      listOpen = false;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 5);
      out.push(
        `<h${level} class="mt-3 mb-1 font-semibold text-ink-900">${inline(heading[2])}</h${level}>`,
      );
      continue;
    }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) {
      if (!listOpen) {
        out.push('<ul class="my-1 list-disc space-y-1 pl-5">');
        listOpen = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      if (!listOpen) {
        out.push('<ul class="my-1 list-decimal space-y-1 pl-5">');
        listOpen = true;
      }
      out.push(`<li>${inline(numbered[1])}</li>`);
      continue;
    }
    closeList();
    out.push(`<p class="my-1">${inline(line)}</p>`);
  }
  closeList();
  return out.join("");
}

export default function MiniMarkdown({
  text,
  className = "",
}: {
  text: string;
  className?: string;
}) {
  return (
    <div
      className={`text-sm leading-relaxed text-ink-800 ${className}`}
      dangerouslySetInnerHTML={{ __html: toHtml(text || "") }}
    />
  );
}

/** Небольшая обёртка над localStorage (токен преподавателя, язык интерфейса). */

const TOKEN_KEY = "shunde.teacherToken";
const LANG_KEY = "shunde.uiLang";
const LEVEL_KEY = "shunde.explainLevel";
const NOTES_KEY = "shunde.notes";

export function getTeacherToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setTeacherToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* приватный режим — игнорируем */
  }
}

export function getUiLang(): string {
  try {
    return localStorage.getItem(LANG_KEY) || "en";
  } catch {
    return "en";
  }
}

export function setUiLang(lang: string): void {
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch {
    /* noop */
  }
}

export function getExplainLevel(): string {
  try {
    return localStorage.getItem(LEVEL_KEY) || "standard";
  } catch {
    return "standard";
  }
}

export function setExplainLevel(level: string): void {
  try {
    localStorage.setItem(LEVEL_KEY, level);
  } catch {
    /* noop */
  }
}

/** Конспект студента по документу: id документа -> текст. */
export function loadNotes(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(NOTES_KEY) || "{}");
  } catch {
    return {};
  }
}

export function saveNote(docId: string, text: string): void {
  try {
    const all = loadNotes();
    all[docId] = text;
    localStorage.setItem(NOTES_KEY, JSON.stringify(all));
  } catch {
    /* noop */
  }
}

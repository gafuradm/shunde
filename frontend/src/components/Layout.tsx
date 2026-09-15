import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { getHealth } from "../lib/api";
import { useI18n, type UiLang } from "../lib/i18n";
import { getTeacherToken, setTeacherToken } from "../lib/storage";
import type { Health } from "../lib/types";

const LANGS: Array<{ id: UiLang; label: string }> = [
  { id: "ru", label: "RU" },
  { id: "en", label: "EN" },
  { id: "zh", label: "中文" },
];

export default function Layout() {
  const { t, lang, setLang } = useI18n();
  const location = useLocation();
  const [token, setToken] = useState(getTeacherToken());
  const [showToken, setShowToken] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    setShowToken(false);
  }, [location.pathname]);

  const navItem = ({ isActive }: { isActive: boolean }) =>
    `inline-flex min-h-[34px] items-center rounded-lg px-3 py-1.5 text-sm font-medium transition ${
      isActive ? "bg-seal-500 text-white" : "text-ink-600 hover:bg-ink-100"
    }`;

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-ink-200/70 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2.5">
          <Link to="/" className="flex min-h-[34px] items-baseline gap-2">
            <span className="font-han text-lg font-bold tracking-tight">顺德</span>
            <span className="text-sm font-semibold text-ink-800">{t("app.title")}</span>
          </Link>

          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navItem}>
              {t("nav.home")}
            </NavLink>
            <NavLink to="/documents" className={navItem}>
              {t("nav.documents")}
            </NavLink>
            <NavLink to="/lectures" className={navItem}>
              {t("nav.lectures")}
            </NavLink>
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {health && !health.features.llm && (
              <span className="badge hidden bg-amber-100 text-amber-700 sm:inline">
                degraded
              </span>
            )}
            <div className="flex overflow-hidden rounded-lg border border-ink-200">
              {LANGS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setLang(item.id)}
                  className={`min-h-[34px] px-2.5 py-1.5 text-xs font-semibold transition ${
                    lang === item.id ? "bg-ink-800 text-white" : "bg-white text-ink-600 hover:bg-ink-100"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="relative">
              <button
                type="button"
                className={token ? "btn-ghost" : "btn-primary"}
                onClick={() => setShowToken((v) => !v)}
                title={t("common.teacherTokenHint")}
              >
                {token ? `${t("nav.teacher")} ✓` : t("nav.teacher")}
              </button>
              {showToken && (
                <div className="absolute right-0 z-40 mt-2 w-72 animate-fade-in rounded-xl border border-ink-200 bg-white p-3 shadow-lg">
                  <label className="text-xs font-semibold text-ink-600">
                    {t("common.teacherToken")}
                  </label>
                  <input
                    className="input mt-1"
                    value={token}
                    autoFocus
                    placeholder="teacher"
                    onChange={(event) => setToken(event.target.value)}
                  />
                  <p className="mt-1.5 text-[11px] leading-snug text-ink-600">
                    {t("common.teacherTokenHint")}
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() => {
                        setTeacherToken(token.trim());
                        setShowToken(false);
                        window.location.reload();
                      }}
                    >
                      {t("common.save")}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => {
                        setToken("");
                        setTeacherToken("");
                        setShowToken(false);
                      }}
                    >
                      {t("common.delete")}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-5">
        <Outlet />
      </main>

      <footer className="border-t border-ink-200/70 bg-white py-3">
        <div className="mx-auto max-w-7xl px-4 text-xs text-ink-600">
          {t("footer.hint")}
          {health && (
            <span className="ml-2">
              · LLM: {health.features.llm ? health.features.llm_model : "off"} · search:{" "}
              {Object.entries(health.features.web_search)
                .filter(([, v]) => v)
                .map(([k]) => k)
                .join(", ") || "—"}
            </span>
          )}
        </div>
      </footer>
    </div>
  );
}

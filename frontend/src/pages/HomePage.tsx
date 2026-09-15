import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useI18n } from "../lib/i18n";
import { getTeacherToken } from "../lib/storage";

export default function HomePage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const teacherToken = getTeacherToken();

  const join = (event: React.FormEvent) => {
    event.preventDefault();
    const clean = code.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (clean) navigate(`/live/${clean}`);
  };

  return (
    <div className="space-y-6">
      <section className="card overflow-hidden">
        <div className="grid gap-6 p-6 md:grid-cols-[1.4fr_1fr]">
          <div>
            <h1 className="text-2xl font-bold leading-snug text-ink-900 sm:text-3xl">
              {t("home.hero.title")}
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-600">
              {t("home.hero.text")}
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link to="/documents" className="btn-primary">
                {t("home.cta.documents")}
              </Link>
              <Link to="/lectures" className="btn-ghost">
                {t("home.cta.lectures")}
              </Link>
            </div>
            <p className="mt-4 rounded-lg bg-ink-50 px-3 py-2 text-xs leading-snug text-ink-600">
              {t("home.note")}
            </p>
          </div>

          <div className="space-y-4">
            <form onSubmit={join} className="card p-4">
              <h2 className="text-sm font-semibold text-ink-900">{t("home.join.title")}</h2>
              <p className="mt-1 text-xs text-ink-600">{t("home.join.text")}</p>
              <input
                className="input mt-2 text-center font-han text-lg tracking-[0.3em]"
                placeholder="AB12CD"
                value={code}
                maxLength={12}
                onChange={(event) => setCode(event.target.value.toUpperCase())}
              />
              <button type="submit" className="btn-primary mt-2 w-full justify-center" disabled={!code.trim()}>
                {t("home.join.enter")}
              </button>
            </form>

            <div className="card p-4">
              <h2 className="text-sm font-semibold text-ink-900">{t("home.teacher.title")}</h2>
              <p className="mt-1 text-xs text-ink-600">{t("home.teacher.text")}</p>
              <div className="mt-2 flex items-center gap-2 text-xs">
                <span
                  className={`badge ${
                    teacherToken ? "bg-emerald-100 text-emerald-700" : "bg-ink-100 text-ink-600"
                  }`}
                >
                  {teacherToken ? "token ok" : "token —"}
                </span>
                <span className="text-ink-600">{t("common.teacherTokenHint")}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {[
          { title: "📚", key: "docs.upload", text: "docs.uploadHint" },
          { title: "🖍️", key: "reader.annotateAi", text: "explain.hint" },
          { title: "🎧", key: "live.translation", text: "lectures.createHint" },
        ].map((item) => (
          <div key={item.key} className="card p-4">
            <div className="text-2xl">{item.title}</div>
            <div className="mt-1 text-sm font-semibold text-ink-900">{t(item.key)}</div>
            <div className="mt-1 text-xs leading-snug text-ink-600">{t(item.text)}</div>
          </div>
        ))}
      </section>
    </div>
  );
}

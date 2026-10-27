"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { appDay, appToday } from "@/lib/timezone";
import type { Mood } from "@/lib/types";

const OPTIONS = [
  { score: 1, label: "Ужасно", color: "#f4b8c9", mouth: "frown" as const },
  { score: 2, label: "Плохо", color: "#f7cdb6", mouth: "softFrown" as const },
  { score: 3, label: "Норм", color: "#f6e7a4", mouth: "neutral" as const },
  { score: 4, label: "Хорошо", color: "#b6eb9e", mouth: "smile" as const },
  { score: 5, label: "Супер", color: "#7eebc8", mouth: "happy" as const },
];

function Face({
  color,
  mouth,
}: {
  color: string;
  mouth: (typeof OPTIONS)[number]["mouth"];
}) {
  const paths = {
    frown: "M23 43.5 Q32 36.5 41 43.5",
    softFrown: "M24 42 Q32 39 40 42",
    neutral: "M26 41 H38",
    smile: "M23.5 39.5 Q32 46 40.5 39.5",
    happy: "M22.5 38.5 Q32 47 41.5 38.5",
  };
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" r="32" fill={color} />
      <circle cx="23.2" cy="26.5" r="2.05" fill="#1f1f1f" />
      <circle cx="40.8" cy="26.5" r="2.05" fill="#1f1f1f" />
      <path
        d={paths[mouth]}
        fill="none"
        stroke="#1f1f1f"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function MoodPage() {
  const [selected, setSelected] = useState<number | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<{ items: Mood[] }>("/api/moods")
      .then((data) => {
        const today = appToday();
        const latest = data.items.find((item) => appDay(item.created_at) === today);
        if (latest) {
          setSelected(latest.score);
          setSaved(latest.score);
        }
      })
      .catch((error) => setStatus(error.message));
  }, []);

  async function choose(score: number) {
    if (saving) return;
    setSaving(true);
    setSelected(score);
    setStatus("Сохранение…");
    try {
      await api("/api/moods", {
        method: "POST",
        body: JSON.stringify({ score, emotions: [], note: "" }),
      });
      setSaved(score);
      setStatus("Сохранили. Оценка появится на графике со следующего дня.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить настроение.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>Настроение</h1>
      <p className="muted">
        Отметьте, как вы себя чувствуете сегодня. График на главной покажет оценку со следующего дня.
      </p>

      <section className="mood-card" aria-label="Как ваше настроение?">
        <h2>Как ваше настроение?</h2>
        <div className="mood-faces">
          {OPTIONS.map((option) => {
            const isSelected = selected === option.score;
            return (
              <button
                key={option.score}
                type="button"
                className={isSelected ? "mood-face selected" : "mood-face"}
                disabled={saving}
                aria-pressed={isSelected}
                aria-label={`${option.label}, оценка ${option.score} из 5`}
                onClick={() => choose(option.score)}
              >
                <Face color={option.color} mouth={option.mouth} />
                <span>{option.label}</span>
              </button>
            );
          })}
        </div>
      </section>

      <div className="status">{status}</div>
      {saved != null && (
        <p className="muted">Сегодня отмечено: {OPTIONS.find((item) => item.score === saved)?.label}.</p>
      )}
      <p>
        <Link href="/" className="quiet-link">
          К графикам
        </Link>
      </p>
    </>
  );
}

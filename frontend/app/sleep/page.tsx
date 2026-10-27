"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { appToday } from "@/lib/timezone";
import type { SleepEntry, SleepQuality } from "@/lib/types";

const OPTIONS: { quality: SleepQuality; label: string }[] = [
  { quality: "good", label: "Хорошо" },
  { quality: "ok", label: "Средне" },
  { quality: "bad", label: "Плохо" },
];

export default function SleepDiaryPage() {
  const [items, setItems] = useState<SleepEntry[]>([]);
  const [today, setToday] = useState(appToday());
  const [quality, setQuality] = useState<SleepQuality | null>(null);
  const [hours, setHours] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<{ today: string; items: SleepEntry[] }>("/api/sleep")
      .then((data) => {
        setToday(data.today);
        setItems(data.items);
        const latest = data.items.find((item) => item.date === data.today);
        if (latest) {
          setQuality(latest.quality);
          setHours(latest.hours == null ? "" : String(latest.hours));
          setNote(latest.note || "");
        }
      })
      .catch((error) => setStatus(error.message))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    if (!quality || saving) return;
    const parsedHours = hours.trim() === "" ? null : Number(hours.replace(",", "."));
    if (parsedHours != null && (Number.isNaN(parsedHours) || parsedHours < 0 || parsedHours > 24)) {
      setStatus("Укажите часы сна числом от 0 до 24, или оставьте поле пустым.");
      return;
    }
    setSaving(true);
    setStatus("Сохранение…");
    try {
      const data = await api<{ ok: boolean; item: SleepEntry }>("/api/sleep", {
        method: "POST",
        body: JSON.stringify({
          quality,
          hours: parsedHours,
          note,
          date: today,
        }),
      });
      setItems((current) => {
        const next = current.filter((item) => item.date !== data.item.date);
        return [data.item, ...next];
      });
      setStatus("Запись сохранена. Врач увидит её в карте.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить дневник сна.");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !status) return <div className="card muted">Загрузка…</div>;

  return (
    <>
      <h1>Дневник сна</h1>
      <p className="muted">
        Отметьте, как вы спали этой ночью. Можно добавить часы и короткую заметку. Это не медицинская рекомендация.
      </p>
      <section className="card">
        <h2>Сегодня</h2>
        <p className="muted">{today.split("-").reverse().join(".")}</p>
        <div className="row" style={{ margin: "12px 0" }}>
          {OPTIONS.map((option) => (
            <button
              key={option.quality}
              type="button"
              className={quality === option.quality ? "full" : "full secondary"}
              aria-pressed={quality === option.quality}
              onClick={() => setQuality(option.quality)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label htmlFor="sleep-hours">Часы сна (необязательно)</label>
        <input
          id="sleep-hours"
          type="number"
          min={0}
          max={24}
          step={0.5}
          inputMode="decimal"
          value={hours}
          placeholder="например 7.5"
          onChange={(event) => setHours(event.target.value)}
        />
        <label htmlFor="sleep-note">Заметка (необязательно)</label>
        <textarea
          id="sleep-note"
          rows={3}
          maxLength={2000}
          value={note}
          placeholder="Как засыпали, просыпались ли ночью…"
          onChange={(event) => setNote(event.target.value)}
        />
        <button className="full" disabled={saving || !quality} onClick={save}>
          Сохранить
        </button>
        <div className="status">{status}</div>
      </section>
      {items.length > 0 && (
        <section className="card">
          <h2>Последние записи</h2>
          {items.map((item) => {
            const label = OPTIONS.find((option) => option.quality === item.quality)?.label || item.quality;
            const extra = [
              item.hours != null ? `${item.hours} ч` : "",
              item.note,
            ].filter(Boolean).join(" — ");
            return (
              <div className="entry" key={item.date}>
                <b>{item.date.split("-").reverse().join(".")} — {label}</b>
                {extra && <div className="muted">{extra}</div>}
              </div>
            );
          })}
        </section>
      )}
      <p>
        <Link href="/" className="quiet-link">
          К графикам
        </Link>
      </p>
    </>
  );
}

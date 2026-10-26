"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PillItem, PillsRegimen } from "@/lib/types";

type Draft = { name: string; time: string; note: string };

function emptyDraft(): Draft {
  return { name: "", time: "", note: "" };
}

function toDrafts(items: PillItem[]): Draft[] {
  const drafts = items.map((item) => ({
    name: item.name || "",
    time: item.time || "",
    note: item.note || "",
  }));
  return drafts.length ? drafts : [emptyDraft()];
}

function formatItem(item: PillItem) {
  const extra = [item.time, item.note].filter(Boolean).join(", ");
  return extra ? `${item.name} — ${extra}` : item.name;
}

export default function PillsDiaryPage() {
  const [regimen, setRegimen] = useState<PillsRegimen | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([emptyDraft()]);
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<PillsRegimen>("/api/pills")
      .then((data) => {
        setRegimen(data);
        if (data.configured) setDrafts(toDrafts(data.items));
      })
      .catch((error) => setStatus(error.message));
  }, []);

  function updateDraft(index: number, field: keyof Draft, value: string) {
    setDrafts((current) => current.map((item, i) => (i === index ? { ...item, [field]: value } : item)));
  }

  async function save(confirm = false) {
    const items = drafts
      .map((item) => ({
        name: item.name.trim(),
        time: item.time.trim(),
        note: item.note.trim(),
      }))
      .filter((item) => item.name);
    if (!items.length) {
      setStatus("Добавьте хотя бы одно лекарство с названием.");
      return;
    }
    setSaving(true);
    setStatus("Сохранение…");
    try {
      const data = await api<PillsRegimen>("/api/pills", {
        method: "POST",
        body: JSON.stringify({ items, confirm }),
      });
      setRegimen(data);
      setDrafts(toDrafts(data.items));
      setEditing(false);
      setStatus("Схема сохранена. Дальше бот спросит, получилось ли принять лекарства сегодня.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить схему.");
    } finally {
      setSaving(false);
    }
  }

  function requestEdit() {
    if (!window.confirm("Схема заполняется один раз. Изменить только если в списке ошибка?")) {
      return;
    }
    setDrafts(toDrafts(regimen?.items || []));
    setEditing(true);
    setStatus("");
  }

  if (!regimen && status) return <div className="card">{status}</div>;
  if (!regimen) return <div className="card muted">Загрузка схемы…</div>;

  const showForm = !regimen.configured || editing;

  return (
    <>
      <h1>Дневник таблеток</h1>
      <p className="muted">
        Страница открывается один раз: укажите, какие лекарства вы принимаете в течение дня.
        Ежедневные отметки приходят сообщением от бота. Это не медицинская рекомендация.
      </p>

      {regimen.configured && !editing && (
        <section className="card">
          <h2>Ваша схема</h2>
          {regimen.items.map((item, index) => (
            <div className="entry" key={`${item.name}-${index}`}>
              <b>{formatItem(item)}</b>
            </div>
          ))}
          <p className="muted">
            Схема уже заполнена. Бот вечером (по UTC) спросит, получилось ли принять лекарства,
            и запишет ответ в дневник.
          </p>
          <button className="full secondary" onClick={requestEdit}>
            Изменить схему
          </button>
          <div className="status">{status}</div>
        </section>
      )}

      {showForm && (
        <section className="card">
          <h2>{regimen.configured ? "Исправить схему" : "Какие лекарства вы принимаете?"}</h2>
          <p className="muted">Название обязательно. Время — например «утро», «вечер» или 20:00.</p>
          {drafts.map((item, index) => (
            <fieldset key={index} style={{ border: 0, margin: 0, padding: 0 }}>
              <label htmlFor={`pill-name-${index}`}>Лекарство {index + 1}</label>
              <input
                id={`pill-name-${index}`}
                type="text"
                value={item.name}
                placeholder="Название"
                onChange={(event) => updateDraft(index, "name", event.target.value)}
              />
              <label htmlFor={`pill-time-${index}`}>Время (необязательно)</label>
              <input
                id={`pill-time-${index}`}
                type="text"
                value={item.time}
                placeholder="утро, вечер или 20:00"
                onChange={(event) => updateDraft(index, "time", event.target.value)}
              />
              <label htmlFor={`pill-note-${index}`}>Заметка / доза (необязательно)</label>
              <input
                id={`pill-note-${index}`}
                type="text"
                value={item.note}
                placeholder="например 25 мг"
                onChange={(event) => updateDraft(index, "note", event.target.value)}
              />
              {drafts.length > 1 && (
                <button
                  className="full secondary"
                  type="button"
                  onClick={() => setDrafts((current) => current.filter((_, i) => i !== index))}
                >
                  Убрать это лекарство
                </button>
              )}
            </fieldset>
          ))}
          <button className="full secondary" type="button" onClick={() => setDrafts((current) => [...current, emptyDraft()])}>
            Добавить лекарство
          </button>
          <button className="full" disabled={saving} onClick={() => save(regimen.configured)}>
            Сохранить схему
          </button>
          {editing && (
            <button className="full secondary" type="button" onClick={() => { setEditing(false); setStatus(""); }}>
              Отмена
            </button>
          )}
          <div className="status">{status}</div>
        </section>
      )}
    </>
  );
}

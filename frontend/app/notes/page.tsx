"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "@/lib/api";
import { formatAppDateTime } from "@/lib/timezone";
import type { PatientNote } from "@/lib/types";

export default function NotesPage() {
  const [notes, setNotes] = useState<PatientNote[]>([]);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ items: PatientNote[] }>("/api/notes")
      .then((data) => setNotes(data.items))
      .catch((error) => setStatus(error.message || "Не удалось загрузить заметки."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [notes]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = text.trim();
    if (!message || saving) return;

    setSaving(true);
    setStatus("");
    try {
      const note = await api<PatientNote>("/api/notes", {
        method: "POST",
        body: JSON.stringify({ text: message }),
      });
      setNotes((current) => [...current, note]);
      setText("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить заметку.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>Заметки</h1>
      <p className="muted">Личный дневник для ваших мыслей. Эти записи видны только вам.</p>
      <section className="card notes-card" aria-label="Личные заметки">
        <div className="notes-chat" aria-live="polite">
          {loading && <p className="muted">Загрузка заметок…</p>}
          {!loading && !status && notes.length === 0 && (
            <p className="muted empty">Здесь пока пусто. Напишите первую заметку.</p>
          )}
          {notes.map((note) => (
            <article className="note-message" key={note.id}>
              <p>{note.text}</p>
              <time className="note-time" dateTime={note.created_at}>
                {formatAppDateTime(note.created_at)}
              </time>
            </article>
          ))}
          <div ref={chatEnd} />
        </div>
        <form className="notes-form" onSubmit={save}>
          <label className="sr-only" htmlFor="note-text">Текст заметки</label>
          <textarea
            id="note-text"
            rows={3}
            maxLength={5000}
            value={text}
            placeholder="Напишите что-нибудь для себя…"
            onChange={(event) => setText(event.target.value)}
          />
          <button type="submit" disabled={saving || !text.trim()}>
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
        </form>
        <div className="status" role="status">{status}</div>
      </section>
    </>
  );
}

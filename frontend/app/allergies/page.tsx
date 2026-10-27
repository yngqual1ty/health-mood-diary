"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Allergies } from "@/lib/types";

export default function AllergiesPage() {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<Allergies>("/api/allergies")
      .then((data) => {
        setText(data.text);
        setSaved(data.text);
      })
      .catch((error) => setStatus(error.message))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setSaving(true);
    setStatus("Сохранение…");
    try {
      const data = await api<Allergies>("/api/allergies", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      setText(data.text);
      setSaved(data.text);
      setStatus("Аллергии сохранены. Врач увидит их в карте пациента.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить аллергии.");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !status) return <div className="card muted">Загрузка…</div>;

  return (
    <>
      <h1>Аллергии</h1>
      <p className="muted">
        Укажите аллергии, о которых должен знать врач. Можно оставить поле пустым.
      </p>
      <section className="card">
        <label htmlFor="allergies-text">Аллергии</label>
        <textarea
          id="allergies-text"
          rows={6}
          value={text}
          maxLength={2000}
          placeholder="Например: пенициллин, пыльца берёзы"
          onChange={(event) => setText(event.target.value)}
        />
        <button className="full" disabled={saving} onClick={save}>
          Сохранить
        </button>
        <div className="status">{status}</div>
        {saved && !status && <p className="muted">Сейчас указано: {saved}</p>}
      </section>
      <p>
        <Link href="/" className="quiet-link">
          К графикам
        </Link>
      </p>
    </>
  );
}

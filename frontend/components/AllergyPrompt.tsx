"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Allergies } from "@/lib/types";

export function AllergyPrompt() {
  const [text, setText] = useState("");
  const [visible, setVisible] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    api<Allergies>("/api/allergies")
      .then((data) => {
        setText(data.text);
        if (data.completed || data.text) setVisible(false);
      })
      .catch((error) => {
        setLoadFailed(true);
        setStatus(error.message || "Не удалось загрузить аллергии.");
      })
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
      setVisible(false);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось сохранить аллергии.");
    } finally {
      setSaving(false);
    }
  }

  if (!visible) return null;

  return (
    <section className="card allergy-prompt">
      <label className="allergy-prompt-text" htmlFor="allergy-prompt-input">
        Если у вас есть известные аллергические реакции на препараты — укажите их.
      </label>
      <div className="allergy-prompt-controls">
        <textarea
          id="allergy-prompt-input"
          aria-label="Известные аллергические реакции на препараты"
          rows={2}
          maxLength={2000}
          value={text}
          placeholder="Например: пенициллин"
          onChange={(event) => setText(event.target.value)}
        />
        <button type="button" disabled={saving || loading || loadFailed} onClick={save}>
          {loading ? "Загрузка…" : "Сохранить"}
        </button>
      </div>
      <div className="allergy-prompt-status" role="status">{status}</div>
    </section>
  );
}

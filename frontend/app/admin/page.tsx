"use client";

import { useEffect, useState } from "react";
import { api, displayName } from "@/lib/api";
import type { Doctor } from "@/lib/types";

export default function AdminPage() {
  const [items, setItems] = useState<Doctor[]>([]);
  const [telegramId, setTelegramId] = useState("");
  const [status, setStatus] = useState("");

  async function load() {
    const data = await api<{ items: Doctor[] }>("/api/admin/doctors");
    setItems(data.items);
  }

  useEffect(() => {
    load().catch((error) => setStatus(error.message));
  }, []);

  async function create() {
    const id = Number(telegramId.trim());
    if (!Number.isSafeInteger(id) || id <= 0) {
      setStatus("Введите корректный Telegram ID.");
      return;
    }
    await api("/api/admin/doctors", {
      method: "POST",
      body: JSON.stringify({ telegram_id: id }),
    });
    setTelegramId("");
    setStatus("Врач добавлен.");
    await load();
  }

  async function remove(id: number) {
    if (!confirm(`Удалить врача ${id}?`)) return;
    await api(`/api/admin/doctors/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <>
      <h1>Врачи</h1>
      <section className="card">
        <label htmlFor="doctorId">Telegram ID нового врача</label>
        <input
          id="doctorId"
          type="number"
          value={telegramId}
          onChange={(event) => setTelegramId(event.target.value)}
        />
        <button className="full" onClick={create}>Добавить врача</button>
        <div className="status">{status}</div>
      </section>
      <section className="card">
        {items.map((doctor) => (
          <div className="entry row" key={doctor.telegram_id}>
            <span>{displayName(doctor)} · {doctor.telegram_id}</span>
            {!doctor.is_admin && (
              <button className="danger" onClick={() => remove(doctor.telegram_id)}>Удалить</button>
            )}
          </div>
        ))}
        <button className="full secondary" onClick={() => load()}>Обновить список</button>
      </section>
    </>
  );
}

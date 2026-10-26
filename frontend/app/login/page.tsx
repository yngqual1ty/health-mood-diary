"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, setSessionToken } from "@/lib/api";
import { hasTelegramInitData } from "@/lib/telegram";
import type { Me } from "@/lib/types";

type TelegramLoginUser = {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
};

declare global {
  interface Window {
    onTelegramAuth?: (user: TelegramLoginUser) => void;
  }
}

export default function LoginPage() {
  const router = useRouter();
  const widgetRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (hasTelegramInitData()) {
      router.replace("/doctor/");
      return;
    }
    api<Me>("/api/me")
      .then((user) => {
        if (user.role === "doctor") router.replace("/doctor/");
      })
      .catch(() => undefined);
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    const host = widgetRef.current;

    async function mountWidget() {
      const response = await fetch("/api/auth/config");
      const data = await response.json().catch(() => ({}));
      const botUsername = typeof data.bot_username === "string" ? data.bot_username : "";
      if (!botUsername) {
        setStatus("Вход через Telegram не настроен. Укажите BOT_USERNAME.");
        return;
      }
      if (cancelled || !host) return;

      window.onTelegramAuth = async (user) => {
        setStatus("Проверка входа…");
        try {
          const authResponse = await fetch("/api/auth/telegram", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(user),
          });
          const payload = await authResponse.json().catch(() => ({}));
          if (!authResponse.ok) {
            const detail = typeof payload.detail === "string"
              ? payload.detail
              : "Не удалось войти.";
            setStatus(detail);
            return;
          }
          if (typeof payload.token === "string" && payload.token) {
            setSessionToken(payload.token);
          }
          router.replace("/doctor/");
        } catch {
          setStatus("Не удалось связаться с сервером. Попробуйте ещё раз.");
        }
      };

      host.replaceChildren();
      const script = document.createElement("script");
      script.src = "https://telegram.org/js/telegram-widget.js?22";
      script.async = true;
      script.setAttribute("data-telegram-login", botUsername);
      script.setAttribute("data-size", "large");
      script.setAttribute("data-radius", "8");
      script.setAttribute("data-onauth", "onTelegramAuth(user)");
      script.setAttribute("data-request-access", "write");
      host.appendChild(script);
      setReady(true);
    }

    mountWidget().catch(() => {
      setStatus("Не удалось загрузить вход через Telegram.");
    });

    return () => {
      cancelled = true;
      delete window.onTelegramAuth;
    };
  }, [router]);

  return (
    <main className="app login-page">
      <h1>Кабинет врача</h1>
      <section className="card">
        <p>
          Войдите через Telegram. Этот сайт — для врачей.
          Пациентам дневник доступен в боте.
        </p>
        <div className="login-widget" ref={widgetRef} />
        {!ready && !status && <p className="muted">Загрузка кнопки входа…</p>}
        <div className="status">{status}</div>
      </section>
    </main>
  );
}

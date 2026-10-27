"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { HelpTicker } from "@/components/HelpTicker";
import { api, logoutSession } from "@/lib/api";
import { hasTelegramInitData, initTelegram } from "@/lib/telegram";
import type { Me } from "@/lib/types";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [inTelegram, setInTelegram] = useState(false);
  const onLogin = pathname.startsWith("/login");

  useEffect(() => {
    if (onLogin) return;
    initTelegram();
    const telegram = hasTelegramInitData();
    setInTelegram(telegram);
    api<Me>("/api/me")
      .then((user) => {
        setMe(user);
        const onDoctor = pathname.startsWith("/doctor") || pathname.startsWith("/admin");
        if (user.role === "doctor" && !onDoctor) router.replace("/doctor/");
        if (user.role === "patient" && onDoctor) router.replace("/");
      })
      .catch((err) => {
        if (!telegram) {
          router.replace("/login/");
          return;
        }
        setError(err.message || "Не удалось проверить доступ. Откройте приложение через Telegram.");
      });
  }, [pathname, router, onLogin]);

  async function handleLogout() {
    await logoutSession();
    router.replace("/login/");
  }

  if (onLogin) {
    return <>{children}</>;
  }

  if (error) {
    return (
      <main className="app">
        <h1>Дневник настроения</h1>
        <div className="card">{error}</div>
      </main>
    );
  }

  if (!me) {
    return (
      <main className="app">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  const isDoctor = me.role === "doctor";
  const webLk = isDoctor && !inTelegram;
  const tabs = [
    { href: "/doctor/", label: "Пациенты", id: "doctor" },
    ...(me.is_admin ? [{ href: "/admin/", label: "Врачи", id: "admin" }] : []),
  ];
  const patientTabs = [
    { href: "/", label: "История", id: "history" },
    { href: "/mood/", label: "Настроение", id: "mood" },
    { href: "/notes/", label: "Заметки", id: "notes" },
  ];

  return (
    <>
      {webLk && (
        <header className="web-lk-bar">
          <span className="web-lk-title">Кабинет врача</span>
          <nav className="web-lk-nav">
            {tabs.map((tab) => {
              const active = pathname.startsWith(tab.href);
              return (
                <Link key={tab.id} href={tab.href} className={active ? "active" : ""}>
                  {tab.label}
                </Link>
              );
            })}
            <button type="button" className="secondary" onClick={handleLogout}>
              Выйти
            </button>
          </nav>
        </header>
      )}
      {!isDoctor && <HelpTicker />}
      <main className={webLk ? "app web-lk" : isDoctor ? "app has-nav" : "app has-ticker has-nav"}>
        {children}
      </main>
      {isDoctor && !webLk && (
        <nav className="nav" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}>
          {tabs.map((tab) => {
            const active = pathname.startsWith(tab.href);
            return (
              <Link key={tab.id} href={tab.href} className={active ? "active" : ""}>
                {tab.label}
              </Link>
            );
          })}
        </nav>
      )}
      {!isDoctor && (
        <nav className="nav" style={{ gridTemplateColumns: `repeat(${patientTabs.length}, minmax(0, 1fr))` }}>
          {patientTabs.map((tab) => (
            <Link key={tab.id} href={tab.href} className={pathname === tab.href ? "active" : ""}>
              {tab.label}
            </Link>
          ))}
        </nav>
      )}
    </>
  );
}

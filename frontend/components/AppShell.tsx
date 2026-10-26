"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { HelpTicker } from "@/components/HelpTicker";
import { api } from "@/lib/api";
import { initTelegram } from "@/lib/telegram";
import type { Me } from "@/lib/types";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    initTelegram();
    api<Me>("/api/me")
      .then((user) => {
        setMe(user);
        const onDoctor = pathname.startsWith("/doctor") || pathname.startsWith("/admin");
        if (user.role === "doctor" && !onDoctor) router.replace("/doctor/");
        if (user.role === "patient" && onDoctor) router.replace("/");
      })
      .catch((err) => setError(err.message || "Не удалось проверить доступ. Откройте приложение через Telegram."));
  }, [pathname, router]);

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
  const tabs = [
    { href: "/doctor/", label: "Пациенты", id: "doctor" },
    ...(me.is_admin ? [{ href: "/admin/", label: "Врачи", id: "admin" }] : []),
  ];

  return (
    <>
      {!isDoctor && <HelpTicker />}
      <main className={isDoctor ? "app has-nav" : "app has-ticker"}>{children}</main>
      {isDoctor && (
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
    </>
  );
}

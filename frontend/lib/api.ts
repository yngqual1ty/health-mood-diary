import { telegramApp } from "./telegram";

const SESSION_KEY = "session_token";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function sessionToken(): string {
  if (typeof window === "undefined") return "";
  return sessionStorage.getItem(SESSION_KEY) || "";
}

export function setSessionToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) sessionStorage.setItem(SESSION_KEY, token);
  else sessionStorage.removeItem(SESSION_KEY);
}

export function authHeaders(): HeadersInit {
  const initData = telegramApp()?.initData || "";
  if (initData) return { Authorization: `tma ${initData}` };
  const token = sessionToken();
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}

async function parseError(response: Response): Promise<ApiError> {
  const data = await response.json().catch(() => ({}));
  const detail = typeof data.detail === "string" ? data.detail : "Ошибка запроса";
  return new ApiError(detail, response.status);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Ошибка запроса";
    throw new ApiError(detail, response.status);
  }
  return data as T;
}

export async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      ...authHeaders(),
    },
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return response.blob();
}

export async function logoutSession(): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
  }).catch(() => undefined);
  setSessionToken(null);
}

export function displayName(user: {
  display_name?: string;
  first_name?: string;
  last_name?: string;
  username?: string;
}) {
  return (
    user.display_name?.trim()
    || [user.first_name, user.last_name].filter(Boolean).join(" ").trim()
    || (user.username ? `@${user.username}` : "Без имени")
  );
}

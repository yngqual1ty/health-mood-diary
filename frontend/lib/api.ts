import { telegramApp } from "./telegram";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const initData = telegramApp()?.initData || "";
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `tma ${initData}`,
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

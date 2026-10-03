import type { Role } from "../types/api";

export interface Session {
  apiKey: string;
  role: Role;
  keyName: string;
  /** Stage 9: bound customer identity (CUSTOMER keys only). */
  customerId?: string | null;
}

const SESSION_KEY = "prdt.auth";

export function getSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Session>;
    if (typeof parsed.apiKey !== "string" || parsed.apiKey.length === 0) {
      return null;
    }
    if (typeof parsed.role !== "string" || typeof parsed.keyName !== "string") {
      return null;
    }
    return {
      apiKey: parsed.apiKey,
      role: parsed.role as Role,
      keyName: parsed.keyName,
      customerId: typeof parsed.customerId === "string" ? parsed.customerId : null,
    };
  } catch {
    // Corrupted session payload — treat as no session.
    return null;
  }
}

export function setSession(session: Session): void {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  sessionStorage.removeItem(SESSION_KEY);
}

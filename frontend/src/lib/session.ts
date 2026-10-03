import type { Role } from "../types/api";

export interface Session {
  apiKey: string;
  role: Role;
  keyName: string;
  /** Stage 9: bound customer identity (CUSTOMER keys only). */
  customerId?: string | null;
}

const SESSION_KEY = "prdt.auth";

interface PersistedSession {
  role: Role;
  keyName: string;
  customerId?: string | null;
}

let memoryApiKey: string | null = null;

export function getSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedSession>;
    if (typeof parsed.role !== "string" || typeof parsed.keyName !== "string") {
      return null;
    }
    if (!memoryApiKey) {
      sessionStorage.removeItem(SESSION_KEY);
      return null;
    }
    return {
      apiKey: memoryApiKey,
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
  memoryApiKey = session.apiKey;
  sessionStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      role: session.role,
      keyName: session.keyName,
      customerId: session.customerId ?? null,
    } satisfies PersistedSession)
  );
}

export function clearSession(): void {
  memoryApiKey = null;
  sessionStorage.removeItem(SESSION_KEY);
}

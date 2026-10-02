import { get, getWithApiKey } from "./client";
import { meResponseSchema, type MeResponse } from "../types/api";

/** Validate the currently-stored API key (from session storage). */
export async function getMe(): Promise<MeResponse> {
  const data = await get<unknown>("/auth/me");
  return meResponseSchema.parse(data);
}

/**
 * Validate an explicitly provided API key (login flow — no session yet).
 * Throws ApiError on failure.
 */
export async function getMeWithKey(apiKey: string): Promise<MeResponse> {
  const data = await getWithApiKey<unknown>("/auth/me", apiKey);
  return meResponseSchema.parse(data);
}

// Customer profile API — the signed-in customer's own identity.
import { ApiError, get } from "./client";
import { customerProfileSchema, type CustomerProfile } from "../types/api";

/**
 * GET /customers/me — profile of the customer this key speaks for.
 * Returns null when the backend reports 404 (identity without a dataset
 * profile, e.g. test keys), mirroring the other optional reads.
 */
export async function getMyProfile(): Promise<CustomerProfile | null> {
  try {
    const data = await get<unknown>("/customers/me");
    return customerProfileSchema.parse(data);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

// Stage 12 — Support workspace API (customer-care view over the SAME data
// the rest of the app serves). All endpoints are STAFF-facing (SYSTEM/ADMIN/
// SUPPORT) and enforced server-side; the frontend RoleGate is convenience,
// not security. No fabricated data: missing fields arrive as null and stay
// null in the UI.
import { z } from "zod";
import { get, patch, post } from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CaseStatus =
  | "OPEN"
  | "IN_PROGRESS"
  | "WAITING_FOR_CUSTOMER"
  | "ESCALATED"
  | "RESOLVED"
  | "CLOSED";

export type CasePriority = "LOW" | "MEDIUM" | "HIGH" | "URGENT";

export interface SupportCaseNote {
  at: string;
  by: string;
  role: string;
  text: string;
}

export interface SupportCase {
  case_id: string;
  transaction_id: string;
  customer_id: string;
  subject: string;
  description: string | null;
  status: CaseStatus;
  priority: CasePriority;
  created_by: string;
  assignee: string | null;
  notes: SupportCaseNote[];
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface SupportCaseListResponse {
  cases: SupportCase[];
  total: number;
}

export interface CustomerSummary {
  customer_id: string;
  full_name: string;
  email: string;
  phone: string | null;
  status: string;
  segment: string | null;
  risk_profile: string | null;
  country: string | null;
}

export interface CustomerSearchResult extends CustomerSummary {
  transaction_count: number;
  failed_transaction_count: number;
  last_activity_at: string | null;
  open_case_count: number;
  /** False when the customer exists only as a transaction user_id (no
   * registry row) — identity fields are honest unknowns. */
  dataset_known: boolean;
}

export interface CustomerSearchResponse {
  query: string;
  results: CustomerSearchResult[];
  total: number;
}

export interface AccountOut {
  account_id: string;
  account_type: string;
  currency: string;
  balance: number;
  status: string;
  is_primary: boolean;
  last_activity_at: string | null;
}

export interface BehaviorSignalOut {
  signal_name: string;
  signal_value: number | null;
  unit: string | null;
  signal_level: string;
  window: string | null;
  computed_at: string | null;
}

export interface SupportTransactionSummary {
  transaction_id: string;
  customer_id: string;
  merchant_id: string;
  amount: number;
  currency: string;
  timestamp: string;
  current_state: string;
  failure_reason: string | null;
  risk_score: number | null;
  channel: string | null;
  country: string | null;
  direction: string | null;
  transaction_type: string | null;
}

export interface TransactionListResponse {
  transactions: SupportTransactionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CustomerAggregate {
  transaction_count: number;
  failed_count: number;
  recovered_count: number;
  last_activity_at: string | null;
}

export interface OpenCaseSummary {
  case_id: string;
  subject: string;
  status: CaseStatus;
  priority: CasePriority;
  transaction_id: string;
  created_at: string;
}

export interface CustomerProfileResponse {
  customer: CustomerSummary;
  accounts: AccountOut[];
  aggregate: CustomerAggregate;
  recent_transactions: SupportTransactionSummary[];
  behavior_signals: BehaviorSignalOut[];
  open_cases: OpenCaseSummary[];
  dataset_known: boolean;
}

export interface QueueCounts {
  open: number;
  in_progress: number;
  waiting_for_customer: number;
  escalated: number;
  resolved: number;
  closed: number;
}

export interface NeedsAttentionItem {
  transaction_id: string;
  customer_id: string;
  amount: number;
  currency: string;
  timestamp: string;
  failure_reason: string | null;
  recovery_status: string | null;
  blocked_reason: string | null;
  risk_level: string | null;
  anomaly_type: string | null;
  open_case_id: string | null;
}

export interface RecentActivityItem {
  transaction_id: string;
  customer_id: string;
  customer_name: string | null;
  amount: number;
  currency: string;
  timestamp: string;
  current_state: string;
  failure_reason: string | null;
}

export interface SupportOverviewResponse {
  cases_by_status: QueueCounts;
  needs_attention: NeedsAttentionItem[];
  recent_activity: RecentActivityItem[];
  generated_at: string;
}

export interface StreamTicketResponse {
  ticket: string;
  expires_in_seconds: number;
}

export interface SupportCaseCreateInput {
  transaction_id: string;
  subject: string;
  description?: string | null;
  priority?: CasePriority;
  assignee?: string | null;
}

export interface SupportCaseUpdateInput {
  status?: CaseStatus | null;
  priority?: CasePriority | null;
  assignee?: string | null;
  note?: string | null;
}

// ---------------------------------------------------------------------------
// Zod schemas — runtime validation, mirroring api/schemas/support.py.
// ---------------------------------------------------------------------------

const caseStatusSchema = z.enum([
  "OPEN",
  "IN_PROGRESS",
  "WAITING_FOR_CUSTOMER",
  "ESCALATED",
  "RESOLVED",
  "CLOSED",
]);

const casePrioritySchema = z.enum(["LOW", "MEDIUM", "HIGH", "URGENT"]);

const caseNoteSchema = z.object({
  at: z.string(),
  by: z.string(),
  role: z.string(),
  text: z.string(),
});

export const supportCaseSchema = z.object({
  case_id: z.string(),
  transaction_id: z.string(),
  customer_id: z.string(),
  subject: z.string(),
  description: z.string().nullable(),
  status: caseStatusSchema,
  priority: casePrioritySchema,
  created_by: z.string(),
  assignee: z.string().nullable(),
  notes: z.array(caseNoteSchema),
  created_at: z.string(),
  updated_at: z.string(),
  resolved_at: z.string().nullable(),
});

const supportCaseSchemaList = z.object({
  cases: z.array(supportCaseSchema),
  total: z.number(),
});

const customerSummarySchema = z.object({
  customer_id: z.string(),
  full_name: z.string(),
  email: z.string(),
  phone: z.string().nullable(),
  status: z.string(),
  segment: z.string().nullable(),
  risk_profile: z.string().nullable(),
  country: z.string().nullable(),
});

export const customerSearchResultSchema = customerSummarySchema.extend({
  transaction_count: z.number(),
  failed_transaction_count: z.number(),
  last_activity_at: z.string().nullable(),
  open_case_count: z.number(),
  dataset_known: z.boolean(),
});

export const customerSearchResponseSchema = z.object({
  query: z.string(),
  results: z.array(customerSearchResultSchema),
  total: z.number(),
});

const accountOutSchema = z.object({
  account_id: z.string(),
  account_type: z.string(),
  currency: z.string(),
  balance: z.number(),
  status: z.string(),
  is_primary: z.boolean(),
  last_activity_at: z.string().nullable(),
});

const behaviorSignalSchema = z.object({
  signal_name: z.string(),
  signal_value: z.number().nullable(),
  unit: z.string().nullable(),
  signal_level: z.string(),
  window: z.string().nullable(),
  computed_at: z.string().nullable(),
});

const supportTransactionSummarySchema = z.object({
  transaction_id: z.string(),
  customer_id: z.string(),
  merchant_id: z.string(),
  amount: z.number(),
  currency: z.string(),
  timestamp: z.string(),
  current_state: z.string(),
  failure_reason: z.string().nullable(),
  risk_score: z.number().nullable(),
  channel: z.string().nullable(),
  country: z.string().nullable(),
  direction: z.string().nullable(),
  transaction_type: z.string().nullable(),
});

export const transactionListResponseSchema = z.object({
  transactions: z.array(supportTransactionSummarySchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});

export const customerProfileResponseSchema = z.object({
  customer: customerSummarySchema,
  accounts: z.array(accountOutSchema),
  aggregate: z.object({
    transaction_count: z.number(),
    failed_count: z.number(),
    recovered_count: z.number(),
    last_activity_at: z.string().nullable(),
  }),
  recent_transactions: z.array(supportTransactionSummarySchema),
  behavior_signals: z.array(behaviorSignalSchema),
  open_cases: z.array(
    z.object({
      case_id: z.string(),
      subject: z.string(),
      status: caseStatusSchema,
      priority: casePrioritySchema,
      transaction_id: z.string(),
      created_at: z.string(),
    })
  ),
  dataset_known: z.boolean(),
});

export const supportOverviewResponseSchema = z.object({
  cases_by_status: z.object({
    open: z.number(),
    in_progress: z.number(),
    waiting_for_customer: z.number(),
    escalated: z.number(),
    resolved: z.number(),
    closed: z.number(),
  }),
  needs_attention: z.array(
    z.object({
      transaction_id: z.string(),
      customer_id: z.string(),
      amount: z.number(),
      currency: z.string(),
      timestamp: z.string(),
      failure_reason: z.string().nullable(),
      recovery_status: z.string().nullable(),
      blocked_reason: z.string().nullable(),
      risk_level: z.string().nullable(),
      anomaly_type: z.string().nullable(),
      open_case_id: z.string().nullable(),
    })
  ),
  recent_activity: z.array(
    z.object({
      transaction_id: z.string(),
      customer_id: z.string(),
      customer_name: z.string().nullable(),
      amount: z.number(),
      currency: z.string(),
      timestamp: z.string(),
      current_state: z.string(),
      failure_reason: z.string().nullable(),
    })
  ),
  generated_at: z.string(),
});

export const streamTicketResponseSchema = z.object({
  ticket: z.string(),
  expires_in_seconds: z.number(),
});

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /support/overview — queue counts + needs-attention + recent activity. */
export async function getSupportOverview(): Promise<SupportOverviewResponse> {
  const data = await get<unknown>("/support/overview");
  return supportOverviewResponseSchema.parse(data);
}

/** GET /support/customers/search?q= — id/name/email/phone/transaction-id. */
export async function searchSupportCustomers(
  query: string,
  limit = 20
): Promise<CustomerSearchResponse> {
  const data = await get<unknown>(
    `/support/customers/search?q=${encodeURIComponent(query)}&limit=${limit}`
  );
  return customerSearchResponseSchema.parse(data);
}

/** GET /support/customers/{id} — full profile; 404 when the customer exists nowhere. */
export async function getSupportCustomerProfile(customerId: string): Promise<CustomerProfileResponse> {
  const data = await get<unknown>(`/support/customers/${encodeURIComponent(customerId)}`);
  return customerProfileResponseSchema.parse(data);
}

export interface SupportTransactionQuery {
  q?: string;
  user_id?: string;
  state?: string;
  limit?: number;
  offset?: number;
}

/** GET /support/transactions — support-facing slice of the real ledger. */
export async function getSupportTransactions(query: SupportTransactionQuery = {}): Promise<TransactionListResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.user_id) params.set("user_id", query.user_id);
  if (query.state) params.set("state", query.state);
  params.set("limit", String(query.limit ?? 25));
  if (query.offset) params.set("offset", String(query.offset));
  const data = await get<unknown>(`/support/transactions?${params.toString()}`);
  return transactionListResponseSchema.parse(data);
}

export interface SupportCaseQuery {
  status?: CaseStatus;
  customer_id?: string;
  transaction_id?: string;
  limit?: number;
  offset?: number;
}

/** GET /support/cases — the ticket queue with filters. */
export async function getSupportCases(query: SupportCaseQuery = {}): Promise<SupportCaseListResponse> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.customer_id) params.set("customer_id", query.customer_id);
  if (query.transaction_id) params.set("transaction_id", query.transaction_id);
  params.set("limit", String(query.limit ?? 50));
  if (query.offset) params.set("offset", String(query.offset));
  const data = await get<unknown>(`/support/cases?${params.toString()}`);
  return supportCaseSchemaList.parse(data);
}

/** POST /support/cases — open a case; the customer is derived from the transaction. */
export async function createSupportCase(input: SupportCaseCreateInput): Promise<SupportCase> {
  const data = await post<unknown>("/support/cases", input);
  return supportCaseSchema.parse(data);
}

/** PATCH /support/cases/{id} — legal transitions/notes enforced by the backend. */
export async function updateSupportCase(
  caseId: string,
  input: SupportCaseUpdateInput
): Promise<SupportCase> {
  const data = await patch<unknown>(`/support/cases/${encodeURIComponent(caseId)}`, input);
  return supportCaseSchema.parse(data);
}

/** POST /support/stream-ticket — single-use SSE handshake ticket (EventSource
 * cannot send custom headers, so the key travels once via axios; the ticket
 * then authenticates the stream URL). */
export async function requestStreamTicket(): Promise<StreamTicketResponse> {
  const data = await post<unknown>("/support/stream-ticket", {});
  return streamTicketResponseSchema.parse(data);
}

/** Absolute base for EventSource (it cannot use the axios instance). */
export function supportStreamUrl(ticket: string): string {
  const base =
    import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
  return `${base}/support/stream?ticket=${encodeURIComponent(ticket)}`;
}

// ---------------------------------------------------------------------------
// Display helpers — human labels, never bare enum echoes.
// ---------------------------------------------------------------------------

const CASE_STATUS_LABELS: Record<CaseStatus, string> = {
  OPEN: "Open",
  IN_PROGRESS: "In progress",
  WAITING_FOR_CUSTOMER: "Waiting for customer",
  ESCALATED: "Escalated",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
};

export function humanizeCaseStatus(status: string): string {
  return CASE_STATUS_LABELS[status as CaseStatus] ?? status;
}

const CASE_PRIORITY_LABELS: Record<CasePriority, string> = {
  LOW: "Low",
  MEDIUM: "Medium",
  HIGH: "High",
  URGENT: "Urgent",
};

export function humanizeCasePriority(priority: string): string {
  return CASE_PRIORITY_LABELS[priority as CasePriority] ?? priority;
}

const STATE_LABELS: Record<string, string> = {
  INITIATED: "Initiated",
  PROCESSING: "Processing",
  SUCCESS: "Successful",
  FAILED: "Failed",
  STALLED: "Stalled",
  RISK_ASSESSED: "Risk assessed",
  RECOVERY_PENDING: "Awaiting recovery",
  LIMIT_RELEASED: "Limit released",
  MANUAL_REVIEW: "Manual review",
  RECOVERY_REJECTED: "Recovery rejected",
};

export function humanizeTxState(state: string): string {
  return STATE_LABELS[state] ?? state;
}

/** Mirror of the backend transition map (api/services/support_service.py) —
 * used ONLY to render which next-status buttons make sense. The backend
 * remains the single authority; an illegal pick still gets a clean 400. */
export const CASE_NEXT_STATUSES: Record<CaseStatus, CaseStatus[]> = {
  OPEN: ["IN_PROGRESS", "WAITING_FOR_CUSTOMER", "ESCALATED", "RESOLVED", "CLOSED"],
  IN_PROGRESS: ["WAITING_FOR_CUSTOMER", "ESCALATED", "RESOLVED", "CLOSED"],
  WAITING_FOR_CUSTOMER: ["IN_PROGRESS", "RESOLVED", "CLOSED"],
  ESCALATED: ["IN_PROGRESS", "RESOLVED", "CLOSED"],
  RESOLVED: ["OPEN", "CLOSED"],
  CLOSED: [],
};

export const OPEN_CASE_STATUSES: CaseStatus[] = [
  "OPEN",
  "IN_PROGRESS",
  "WAITING_FOR_CUSTOMER",
  "ESCALATED",
];

/** Support rows carry numeric amounts (the ledger contract uses strings). */
export function formatMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency }).format(amount);
  } catch {
    // Invalid currency code — fall back to plain formatting.
    return `${amount.toLocaleString()} ${currency}`;
  }
}

/** Same convention as the panel components: locale string, raw ISO on garbage. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

// Thin client for the AI Commerce Operator API.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// --- Access token -----------------------------------------------------------
//
// The API verifies a bearer token. Signing in with Google or with a code sent
// to an email address returns one (see the sign-in helpers at the bottom of
// this file); `python -m app.issue_token` still mints one by hand for whoever
// runs the server. It lives in localStorage, so it is per-browser and never
// leaves this device except as an Authorization header to our own API.
//
// localStorage is the honest limitation here: script running on this page can
// read it. The fix is an httpOnly cookie and a session endpoint, and until that
// exists a short SESSION_TOKEN_DAYS is what keeps a stolen token cheap.

const TOKEN_KEY = "aco.token";

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;               // private window, or storage disabled
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token.trim());
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing we can do, and nothing worth breaking the page over */
  }
}

/** Thrown when the API refuses the token, so a screen can ask for a new one. */
export class NotAuthenticated extends Error {
  constructor(message = "Sign in again: the access token is missing or expired.") {
    super(message);
    this.name = "NotAuthenticated";
  }
}

/** Every call goes through here, so no endpoint can forget the header. */
async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    // Any screen can hit this at any moment, because a token expires while
    // somebody is looking at a page rather than while they are signing in.
    // The session gate listens for this so that lands on the sign-in screen
    // instead of on an app that quietly shows nothing.
    if (typeof window !== "undefined") window.dispatchEvent(new Event("aco:unauthenticated"));
    throw new NotAuthenticated();
  }
  return res;
}

export interface ProductRequest {
  name: string;
  price: number;
  cogs: number;
  fba_fee: number;
  monthly_sales: number;
  referral_rate?: number;
  ppc_per_unit?: number;
  size_score?: number;
  dominant_brands?: number;
  median_reviews?: number;
  demand_score?: number;
  patent_ok?: boolean;
  cert_score?: number;
}

export interface WhatIf {
  break_even_price: number | null;
  price_for_floor: number | null;
  price_for_target: number | null;
  max_cogs_at_floor: number;
}

export interface Evaluation {
  name: string;
  economics: {
    referral_fee: number;
    fba_fee_total: number;
    profit_per_unit: number;
    margin: number;
    roi: number;
    monthly_profit: number;
  };
  score: number;
  subscores: Record<string, number>;
  verdict: "BUY" | "CAUTION" | "AVOID";
  reason: string;
  whatif: WhatIf;
  pros: string[];
  risks: string[];
  explanation: string | null;
  inputs?: ProductRequest | null;  // set by discovery, for "open in Product Hunter"
}

export interface EvaluateResult {
  results: Evaluation[];
  weights: Record<string, number>;
}

export async function evaluateProducts(
  products: ProductRequest[],
  explain = true,
): Promise<EvaluateResult> {
  const res = await apiFetch(`${API_BASE}/api/product-hunter/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products, explain }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data as EvaluateResult;
}

export interface HistoryItem {
  id: string;
  name: string;
  score: number;
  verdict: "BUY" | "CAUTION" | "AVOID";
  economics: Evaluation["economics"];
  explanation: string | null;
  created_at: string;
}

export async function getHistory(limit = 10): Promise<HistoryItem[]> {
  const res = await apiFetch(`${API_BASE}/api/product-hunter/history?limit=${limit}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.results as HistoryItem[];
}

// --- PPC Analyzer ---

export interface KeywordRequest {
  keyword: string;
  clicks: number;
  spend: number;
  sales: number;
  orders: number;
  impressions?: number;
  match_type?: string;
}

export interface KeywordFinding {
  keyword: string;
  match_type: string;
  acos: number | null;
  cpc: number;
  cvr: number;
  ctr: number;
  spend: number;
  sales: number;
  orders: number;
  action: "NEGATE" | "LOWER_BID" | "RAISE_BID" | "KEEP" | "GATHER_DATA";
  severity: "info" | "warning" | "critical";
  reason: string;
  suggested_bid: number | null;
  savings: number;
}

export interface PpcSummary {
  campaign: string;
  total_spend: number;
  total_sales: number;
  overall_acos: number | null;
  break_even_acos: number;
  target_acos: number;
  wasted_spend: number;
  potential_savings: number;
  action_counts: Record<string, number>;
}

export interface PpcResult {
  summary: PpcSummary;
  findings: KeywordFinding[];
  explanation: string | null;
}

export async function analyzePpc(payload: {
  name: string;
  break_even_acos: number;
  target_acos?: number | null;
  keywords: KeywordRequest[];
  explain?: boolean;
}): Promise<PpcResult> {
  const res = await apiFetch(`${API_BASE}/api/ppc/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ explain: true, ...payload }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as PpcResult;
}

export interface PpcHistoryItem {
  id: string;
  campaign: string;
  severity: "info" | "warning" | "critical";
  overall_acos: number | null;
  wasted_spend: number;
  potential_savings: number;
  created_at: string;
}

export async function getPpcHistory(limit = 10): Promise<PpcHistoryItem[]> {
  const res = await apiFetch(`${API_BASE}/api/ppc/history?limit=${limit}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.results as PpcHistoryItem[];
}

// --- Inventory Planner ---

export interface StockRequest {
  name: string;
  on_hand: number;
  avg_daily_sales: number;
  lead_time_days: number;
  inbound?: number;
  review_period_days?: number;
  safety_stock_days?: number | null;
  unit_cost?: number;
}

export interface InventoryFinding {
  name: string;
  daily_sales: number;
  days_of_cover: number | null;
  stockout_in_days: number | null;
  reorder_point: number;
  days_to_reorder: number | null;
  suggested_order_qty: number;
  stock_value: number;
  order_cost: number;
  status: "REORDER_NOW" | "REORDER_SOON" | "HEALTHY" | "OVERSTOCK" | "NO_SALES";
  severity: "info" | "warning" | "critical";
  reason: string;
}

export interface InventorySummary {
  total_skus: number;
  skus_at_risk: number;
  total_stock_value: number;
  total_reorder_cost: number;
  status_counts: Record<string, number>;
}

export interface InventoryResult {
  summary: InventorySummary;
  findings: InventoryFinding[];
  explanation: string | null;
}

export async function analyzeInventory(
  skus: StockRequest[],
  explain = true,
): Promise<InventoryResult> {
  const res = await apiFetch(`${API_BASE}/api/inventory/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ explain, skus }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as InventoryResult;
}

// --- Creative agent ---

export interface CreativeResult {
  listing: string;
  image_plan: string;
  concept_images: string[];
}

export async function generateCreative(payload: {
  name: string;
  price?: number | null;
  features?: string | null;
  images?: boolean;
}): Promise<CreativeResult> {
  const res = await apiFetch(`${API_BASE}/api/creative/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as CreativeResult;
}

// --- Daily Report ---

export interface ReportItem {
  module: "product_hunter" | "ppc" | "inventory";
  severity: "info" | "warning" | "critical";
  action: string;
  title: string;
  impact_usd: number;
  detail: string;
}

export interface DailyReport {
  items: ReportItem[];
  money_at_stake_usd: number;
  wasted_spend_usd: number;
  reorder_cost_usd: number;
  counts: Record<string, number>;
  briefing: string | null;
}

// --- Supplier Finder ---

export interface SupplierOffer {
  supplier: string;
  country: string;
  unit_price: number;
  moq: number;
  lead_time_days: number;
  rating: number;
  est_landed_cost: number;
  url: string | null;
}

export interface SupplierResult {
  source: string;
  note: string;
  query: string;
  suggested_cogs: number | null;
  offers: SupplierOffer[];
}

export async function searchSuppliers(payload: {
  query: string;
  limit?: number;
  max_moq?: number | null;
  max_lead_time?: number | null;
}): Promise<SupplierResult> {
  const res = await apiFetch(`${API_BASE}/api/suppliers/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as SupplierResult;
}

// --- Operator (3-agent launch plan) ---

export interface Payback {
  first_batch_units: number;
  upfront_investment: number;
  months_to_sell_batch: number | null;
  payback_months: number | null;
  break_even_units: number | null;
}

export interface LaunchPlan {
  niche: string;
  discovery_source: string;
  supplier_source: string;
  product: Evaluation;
  supplier: SupplierOffer | null;
  suggested_cogs: number | null;
  payback: Payback | null;
  listing_draft: string;
  steps: string[];
  note: string;
}

export async function buildLaunchPlan(niche: string, limit = 10): Promise<LaunchPlan> {
  const res = await apiFetch(`${API_BASE}/api/operator/launch-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ niche, limit }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as LaunchPlan;
}

// --- Autopilot (autonomous loop + approval queue) ---

export interface QueueItem {
  id: string;
  niche: string;
  status: "pending" | "approved" | "dismissed";
  product_name: string;
  verdict: "BUY" | "CAUTION" | "AVOID";
  score: number;
  margin: number;
  roi: number;
  competition: number;
  monthly_profit: number;
  suggested_cogs: number | null;
  supplier: string | null;
  upfront_investment: number | null;
  payback_months: number | null;
  break_even_units: number | null;
  listing_draft: string;
  steps: string[];
  created_at: string;
}

export interface MoneyCriteria {
  limit?: number;
  only_buy?: boolean;
  min_margin?: number | null;
  min_monthly_profit?: number | null;
  min_roi?: number | null;
  min_competition?: number | null;
  max_payback_months?: number | null;
}

export async function autopilotScan(
  niches: string[],
  criteria: MoneyCriteria = {},
): Promise<{ queued: number | null; skipped: number | null; job_id?: string; status?: string }> {
  const res = await apiFetch(`${API_BASE}/api/autopilot/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ niches, limit: 8, ...criteria }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export interface BackgroundJob {
  job_id: string;
  status: string;
  result: ({ queued?: number; skipped?: number } & Record<string, unknown>) | null;
}

export async function getBackgroundJob(jobId: string): Promise<BackgroundJob> {
  const res = await apiFetch(`${API_BASE}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function waitForBackgroundJob(jobId: string, timeoutMs = 120_000): Promise<BackgroundJob> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await getBackgroundJob(jobId);
    if (job.status === "finished") return job;
    if (job.status === "failed" || job.status === "stopped" || job.status === "canceled") {
      throw new Error("Background scan failed after retries.");
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  throw new Error("Background scan is still running. Check the queue again shortly.");
}

// --- Amazon SP-API connection + read-only sync ---

export interface AmazonConnection {
  connected: boolean;
  seller_id: string | null;
  marketplaces: Record<string, unknown>[];
}

export interface AmazonSyncStatus {
  status: string;
  sync_id: string | null;
  listings: number;
  inventory: number;
  started_at: string | null;
  completed_at: string | null;
}

export interface AmazonSalesDaily {
  date: string;
  ordered_sales: number;
  currency: string | null;
  units_ordered: number;
  order_items: number;
  page_views: number;
  sessions: number;
  buy_box_percentage: number | null;
}

export interface AmazonSalesSummary {
  marketplace_id: string;
  days: number;
  total_sales: number;
  total_units: number;
  total_order_items: number;
  currency: string | null;
  last_synced_at: string | null;
  results: AmazonSalesDaily[];
}

export interface RoiDashboard {
  marketplace_id: string | null;
  demo_data: boolean;
  period_days: number;
  revenue: number;
  previous_revenue: number;
  revenue_change_percentage: number | null;
  units: number;
  order_items: number;
  sessions: number;
  conversion_rate: number | null;
  average_order_value: number | null;
  currency: string | null;
  last_synced_at: string | null;
  freshness: "fresh" | "stale" | "outdated" | "no_data";
  amazon_fees: number | null;
  advertising_spend: number | null;
  landed_cogs: number | null;
  cogs_coverage_percentage: number | null;
  missing_cost_skus: string[];
  profit: number | null;
  margin: number | null;
  roi: number | null;
  missing_profit_inputs: string[];
  daily: { date: string; revenue: number; units: number; order_items: number; sessions: number }[];
}

export interface CommerceDashboard {
  period_days: number;
  demo_data: boolean;
  revenue: number;
  profit: number | null;
  orders: number;
  units: number;
  currency: string | null;
  channels: {
    channel_id: string; provider: string; display_name: string; status: string;
    revenue: number; profit: number | null; orders: number; currency: string;
    share_percentage: number;
  }[];
  daily: { date: string; revenue: number; orders: number; units: number }[];
  margin: number | null;
  last_synced_at: string | null;
  freshness: "fresh" | "stale" | "outdated" | "no_data";
}

export interface AccountStatus {
  plan: string; subscription_status: string; trial_ends_at: string | null;
  trial_days_remaining: number;
  entitlements: { connected_channels: number; monthly_ai_actions: number; autopilot: boolean };
  onboarding: { account_created: boolean; channel_connected: boolean; data_imported: boolean; first_ai_action: boolean };
  completion_percentage: number;
}

export async function getAccountStatus(): Promise<AccountStatus> {
  const res = await apiFetch(`${API_BASE}/api/account/status`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function createBillingCheckout(plan: "starter" | "operator" | "scale"): Promise<string> {
  const res = await apiFetch(`${API_BASE}/api/billing/checkout`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan }),
  });
  if (!res.ok) { const payload = await res.json().catch(() => null); throw new Error(payload?.detail ?? `API error ${res.status}`); }
  return (await res.json()).url as string;
}

export async function createBillingPortal(): Promise<string> {
  const res = await apiFetch(`${API_BASE}/api/billing/portal`, { method: "POST" });
  if (!res.ok) { const payload = await res.json().catch(() => null); throw new Error(payload?.detail ?? `API error ${res.status}`); }
  return (await res.json()).url as string;
}

export async function beginAmazonAuthorization(): Promise<string> {
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/authorize`, { method: "POST" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()).authorization_url as string;
}

export async function getAmazonConnection(): Promise<AmazonConnection> {
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/account`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function startAmazonSync(region = "EU"): Promise<BackgroundJob> {
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/sync?region=${region}`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function getAmazonSyncStatus(): Promise<AmazonSyncStatus> {
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/sync`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function startAmazonSalesReport(
  marketplaceId: string, days = 30, region = "EU",
): Promise<BackgroundJob> {
  const query = new URLSearchParams({ marketplace_id: marketplaceId, days: String(days), region });
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/reports/sales?${query}`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function startAmazonFinancesSync(
  marketplaceId: string, days = 30, region = "EU",
): Promise<BackgroundJob> {
  const query = new URLSearchParams({ marketplace_id: marketplaceId, days: String(days), region });
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/finances/sync?${query}`, {
    method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export interface AmazonSkuCost {
  sku: string;
  landed_cost: number;
  currency: string;
  updated_at?: string;
}

export async function saveAmazonSkuCosts(
  marketplaceId: string, costs: AmazonSkuCost[],
): Promise<{ marketplace_id: string; results: AmazonSkuCost[] }> {
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/costs`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ marketplace_id: marketplaceId, costs }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function getAmazonSalesSummary(
  marketplaceId: string, days = 30,
): Promise<AmazonSalesSummary> {
  const query = new URLSearchParams({ marketplace_id: marketplaceId, days: String(days) });
  const res = await apiFetch(`${API_BASE}/api/integrations/amazon/reports/sales?${query}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function getRoiDashboard(days = 30, marketplaceId?: string): Promise<RoiDashboard> {
  const query = new URLSearchParams({ days: String(days) });
  if (marketplaceId) query.set("marketplace_id", marketplaceId);
  const res = await apiFetch(`${API_BASE}/api/dashboard/roi?${query}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function getCommerceDashboard(days = 30): Promise<CommerceDashboard> {
  const res = await apiFetch(`${API_BASE}/api/dashboard/commerce?days=${days}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export interface ShopifyConnection {
  connected: boolean;
  shop: string | null;
  channel_id: string | null;
  notifications: "active" | "stale" | "pending";
  topics: string[];
  reason: string | null;
}

export async function getShopifyConnection(): Promise<ShopifyConnection> {
  const res = await apiFetch(`${API_BASE}/api/integrations/shopify/account`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function beginShopifyAuthorization(shop: string): Promise<string> {
  const query = new URLSearchParams({ shop });
  const res = await apiFetch(`${API_BASE}/api/integrations/shopify/authorize?${query}`, {
    method: "POST",
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `API error ${res.status}`);
  }
  return (await res.json()).authorization_url as string;
}

export async function retryShopifyNotifications(): Promise<ShopifyConnection> {
  const res = await apiFetch(`${API_BASE}/api/integrations/shopify/notifications/retry`, {
    method: "POST",
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `API error ${res.status}`);
  }
  return await res.json();
}

export async function startShopifySync(days = 30): Promise<BackgroundJob> {
  const res = await apiFetch(`${API_BASE}/api/integrations/shopify/sync?days=${days}`, {
    method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export interface WooCommerceConnection {
  connected: boolean;
  store_url: string | null;
  channel_id: string | null;
}

export async function getWooCommerceConnection(): Promise<WooCommerceConnection> {
  const res = await apiFetch(`${API_BASE}/api/integrations/woocommerce/account`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function beginWooCommerceAuthorization(storeUrl: string): Promise<string> {
  const query = new URLSearchParams({ store_url: storeUrl });
  const res = await apiFetch(`${API_BASE}/api/integrations/woocommerce/authorize?${query}`, {
    method: "POST",
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `API error ${res.status}`);
  }
  return (await res.json()).authorization_url as string;
}

export async function startWooCommerceSync(days = 30): Promise<BackgroundJob> {
  const res = await apiFetch(`${API_BASE}/api/integrations/woocommerce/sync?days=${days}`, {
    method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function seedDemoStore(): Promise<{ marketplace_id: string; days: number; skus: number; message: string }> {
  const res = await apiFetch(`${API_BASE}/api/demo/seed`, {
    method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function getQueue(
  status = "pending",
): Promise<{ results: QueueItem[]; total_monthly_profit: number }> {
  const res = await apiFetch(`${API_BASE}/api/autopilot/queue?status=${status}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function clearQueue(status?: string): Promise<number> {
  const url = status
    ? `${API_BASE}/api/autopilot/queue?status=${status}`
    : `${API_BASE}/api/autopilot/queue`;
  const res = await apiFetch(url, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()).deleted as number;
}

export interface Portfolio {
  count: number;
  total_upfront_investment: number;
  total_monthly_profit: number;
  blended_payback_months: number | null;
  items: QueueItem[];
}

export async function getPortfolio(): Promise<Portfolio> {
  const res = await apiFetch(`${API_BASE}/api/autopilot/portfolio`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

export async function decideQueueItem(id: string, action: "approve" | "dismiss"): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/autopilot/${id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
}

export async function getDailyReport(): Promise<DailyReport> {
  const res = await apiFetch(`${API_BASE}/api/report/daily`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as DailyReport;
}

// --- Product Discovery ---

export interface DiscoverResult {
  source: string;
  note: string;
  cached: boolean;
  results: Evaluation[];
  weights: Record<string, number>;
}

export interface KeepaStatus {
  enabled: boolean;
  tokens_left: number | null;
  refill_rate: number | null;
}

export async function getKeepaStatus(): Promise<KeepaStatus> {
  const res = await apiFetch(`${API_BASE}/api/discovery/keepa-status`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as KeepaStatus;
}

export interface DiscoveryHistoryItem {
  id: string;
  niche: string;
  results_count: number;
  top_name: string | null;
  top_score: number | null;
  created_at: string;
}

export async function getDiscoveryHistory(limit = 10): Promise<DiscoveryHistoryItem[]> {
  const res = await apiFetch(`${API_BASE}/api/discovery/history?limit=${limit}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.results as DiscoveryHistoryItem[];
}

export async function discoverProducts(
  niche: string,
  opts: { limit?: number; max_price?: number | null; min_monthly_sales?: number | null } = {},
): Promise<DiscoverResult> {
  const res = await apiFetch(`${API_BASE}/api/discovery/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ niche, limit: opts.limit ?? 10, ...opts }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as DiscoverResult;
}

// --- Operator payroll: what the operator did, and what it was worth ---

export interface ActionImpact {
  cost_avoided: number;
  revenue_gained: number;
  net: number;
  provisional: boolean;
  basis: string;
}

export interface OperatorAction {
  id: string;
  module: string;
  action_type: string;
  target: string;
  evidence_mode: "real" | "demo" | "unverified";
  source_type: string | null;
  source_id: string | null;
  status: "proposed" | "applied" | "measured" | "dismissed" | "reverted";
  applied_by: string | null;
  projected_impact: number | null;
  baseline: { days: number; spend: number; revenue: number } | null;
  outcome: { days: number; spend: number; revenue: number } | null;
  impact: ActionImpact | null;
  revert_to: Record<string, unknown> | null;
  measurement_days: number;
  currency: string;
  note: string | null;
  proposed_at: string;
  applied_at: string | null;
  measured_at: string | null;
}

export interface ProductCostEntry {
  product_id: string;
  product_title: string;
  variant_id: string | null;
  variant_title: string | null;
  units_sold: number;
  last_sold: string;
  sale_currency: string;
  /** null is unknown, never zero. The two are opposite claims. */
  unit_cost: number | null;
  cost_currency: string | null;
  effective_from: string | null;
  /** "shopify" | "manual" */
  source: string | null;
  /** "confirmed" (read from the shop) | "reported" (a person typed it) */
  verification: string | null;
  applies_to_every_variant: boolean;
  currency_matches: boolean;
  attributable_to_variant: boolean;
  waiting_measurements: string[];
}

export interface ProductCosts {
  entries: ProductCostEntry[];
  without_cost: number;
  blocking: number;
  currency: string;
}

export interface ProductCost {
  product_id: string;
  variant_id: string | null;
  variant_title: string | null;
  amount: number;
  purchase_amount: number | null;
  extra_amount: number | null;
  currency: string;
  effective_from: string;
  source: string;
  verification: string;
  entered_by: string | null;
  note: string | null;
  observed_at: string;
}

export async function getProductCosts(): Promise<ProductCosts> {
  return json<ProductCosts>(await apiFetch(`${API_BASE}/api/product-costs`));
}

export async function getProductCostHistory(productId?: string): Promise<ProductCost[]> {
  const query = productId ? `?product_id=${encodeURIComponent(productId)}` : "";
  const data = await json<{ costs: ProductCost[] }>(
    await apiFetch(`${API_BASE}/api/product-costs/history${query}`),
  );
  return data.costs;
}

export async function setProductCost(body: {
  product_id: string;
  variant_id?: string | null;
  variant_title?: string | null;
  purchase_amount: number;
  extra_amount?: number;
  currency: string;
  effective_from?: string;
  note?: string;
}): Promise<ProductCost> {
  return json<ProductCost>(await apiFetch(`${API_BASE}/api/product-costs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

export interface AwaitingMeasurement {
  action_id: string;
  action_type: string;
  target: string;
  applied_at: string;
  stock_before: number | null;
  verified_on_hand: number | null;
  window_first: string | null;
  window_last: string | null;
  measure_on: string | null;
  days_remaining: number | null;
  /** window_open | awaiting_data | ready | measured | not_measurable */
  state: string;
  /** collecting | complete | incomplete */
  data_state: string;
  reason: string;
}

export interface Payroll {
  period_days: number;
  settled_impact: number;
  provisional_impact: number;
  operator_cost: number;
  net: number;
  paid_for_itself: boolean;
  awaiting_measurement: number;
  counts: Record<string, number>;
  currency: string;
  evidence_mode: "real";
  excluded_unverified: number;
  excluded_demo: number;
  awaiting: AwaitingMeasurement[];
  explanation: string | null;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `API error ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function getPayroll(days = 30, explain = false): Promise<Payroll> {
  return json<Payroll>(
    await apiFetch(`${API_BASE}/api/dashboard/payroll?days=${days}&explain=${explain}`),
  );
}

export async function listActions(status?: string): Promise<OperatorAction[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const data = await json<{ actions: OperatorAction[] }>(
    await apiFetch(`${API_BASE}/api/actions${query}`),
  );
  return data.actions;
}

/** The six states the backend decides. Not recomputed here from dates or from
 *  Stripe's wording: two implementations of "may they" eventually disagree, and
 *  the browser's copy is always the generous one. */
export type EntitlementStatus =
  | "trialing" | "active" | "past_due" | "canceled" | "expired" | "not_configured";

export interface Entitlement {
  status: EntitlementStatus;
  access: boolean;
  period_ends_at: string | null;
  trial_days_remaining: number | null;
  action_required: boolean;
  action: "checkout" | "portal" | null;
  explanation: string;
  plan_name: string;
  price_per_month: number;
  currency: string;
  checkout_available: boolean;
  features: string[];
  support_email: string | null;
  support_response_time: string | null;
}

/** The single source of truth for what this customer may do. It changes only
 *  when Stripe has told the backend it has — never because somebody came back
 *  from a Checkout page. */
export async function getEntitlement(): Promise<Entitlement> {
  return json<Entitlement>(await apiFetch(`${API_BASE}/api/billing`));
}

/** The four states the backend reports. Not widened here: a status this file
 *  invents is a status no screen can be held to. */
export type OnboardingStatus =
  | "not_started" | "in_progress" | "complete" | "needs_attention";

/** What the backend may ask the screen to offer. Every one is idempotent. */
export type OnboardingAction =
  | "connect_shopify" | "retry_notifications" | "run_sync"
  | "run_analysis" | "open_proposals" | "open_help";

export interface OnboardingStep {
  key: string;
  title: string;
  status: OnboardingStatus;
  detail: string;
  action: OnboardingAction | null;
  action_label: string | null;
  help_url: string | null;
}

export interface Onboarding {
  steps: OnboardingStep[];
  complete: boolean;
  support_email: string | null;
  support_response_time: string | null;
  support_url: string | null;
}

/** Where this customer actually is. Every status is derived server-side from
 *  the database; nothing here recomputes one, because two implementations of
 *  "connected" is one more than can be kept honest. */
export async function getOnboarding(): Promise<Onboarding> {
  return json<Onboarding>(await apiFetch(`${API_BASE}/api/onboarding`));
}

export interface CommerceScan {
  days: number;
  observed_days: number;
  findings: number;
  opened: number;
  already_open: number;
}

/** Ask the Operator to read this store's measured days and open what it finds. */
export async function scanCommerce(days = 30): Promise<CommerceScan> {
  return json<CommerceScan>(
    await apiFetch(`${API_BASE}/api/commerce/scan?days=${days}`, { method: "POST" }),
  );
}

export interface CommerceConfirm {
  confirmed: boolean;
  on_hand_before: number;
  on_hand_now: number | null;
  reason: string;
}

/**
 * Tell the Operator the restock happened. It asks Shopify rather than taking
 * your word: if the shelf has not moved, nothing is recorded and the reason
 * comes back instead.
 */
export async function confirmCommerceAction(id: string): Promise<CommerceConfirm> {
  return json<CommerceConfirm>(
    await apiFetch(`${API_BASE}/api/commerce/actions/${id}/confirm`, { method: "POST" }),
  );
}

/** Price a confirmed action from the store's own days. No numbers are sent. */
export async function measureCommerceAction(id: string): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/commerce/actions/${id}/measure`, { method: "POST" }),
  );
}

export async function proposeAction(body: {
  module: string;
  action_type: string;
  target: string;
  projected_impact?: number | null;
  measurement_days?: number;
  currency?: string;
  note?: string | null;
}): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function markActionApplied(
  id: string,
  baseline: { days: number; spend: number; revenue: number },
  revertTo?: Record<string, unknown> | null,
): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/actions/${id}/applied`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseline, applied_by: "human", revert_to: revertTo ?? null }),
    }),
  );
}

export async function measureAction(
  id: string,
  outcome: { days: number; spend: number; revenue: number },
): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/actions/${id}/measure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome }),
    }),
  );
}

// --- Guardrails: what the operator may do unattended ---

export interface GuardrailPolicy {
  enabled: boolean;
  max_actions_per_day: number;
  max_change_pct: number;
  auto_apply_below: number;
  protected_spend_per_day: number;
  applied_today: number;
  remaining_today: number;
}

export async function getGuardrails(): Promise<GuardrailPolicy> {
  return json<GuardrailPolicy>(await apiFetch(`${API_BASE}/api/guardrails`));
}

export async function updateGuardrails(
  patch: Partial<Omit<GuardrailPolicy, "applied_today" | "remaining_today">>,
): Promise<GuardrailPolicy> {
  return json<GuardrailPolicy>(
    await apiFetch(`${API_BASE}/api/guardrails`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export async function revertAction(id: string, note?: string): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/actions/${id}/revert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note ?? null }),
    }),
  );
}

/** Decline a proposal. The reason is optional on purpose: demanding one is how
 *  a queue stays full instead of getting answered. */
export async function dismissAction(id: string, note?: string): Promise<OperatorAction> {
  return json<OperatorAction>(
    await apiFetch(`${API_BASE}/api/actions/${id}/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note ?? null }),
    }),
  );
}

// --- Privacy -----------------------------------------------------------------

export async function requestWorkspaceDeletion(confirmation: string): Promise<string> {
  const res = await apiFetch(`${API_BASE}/api/privacy/deletion-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail ?? `API error ${res.status}`);
  return data.request_id as string;
}

/**
 * Fetch the export and hand it to the browser as a file.
 *
 * A plain <a href> cannot carry the Authorization header, so linking straight
 * at the endpoint opens a tab that is simply refused. The bytes have to come
 * through the same authenticated client as everything else.
 */
export async function downloadWorkspaceExport(): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/privacy/export`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = "workspace-export.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

// --- Legal ------------------------------------------------------------------

export interface LegalDetails {
  entity: string | null;
  address: string | null;
  privacy_contact: string | null;
  representative: string | null;
  business_id: string | null;
  business_type: string | null;
  country: string | null;
  complete: boolean;
  missing: string[];
  subprocessors: { name: string; purpose: string; data: string }[];
  retention: { category: string; period: string }[];
}

/** Public by design: a privacy policy nobody can read without an account is not one. */
export async function getLegalDetails(): Promise<LegalDetails> {
  const res = await fetch(`${API_BASE}/api/legal`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return await res.json();
}

// --- Signing in -------------------------------------------------------------
//
// These four are the only calls that go out without a token, so they use a bare
// fetch rather than `apiFetch`: a 401 here means "that code is wrong", not
// "your session ended", and throwing NotAuthenticated would send the sign-in
// screen to the sign-in screen.

export interface AuthConfig {
  google_client_id: string | null;
  email_login: boolean;
  session_days: number;
}

export interface Session {
  token: string;
  email: string;
  role: string;
  tenant_id: string;
  expires_at: string;
}

export interface Principal {
  user_id: string;
  tenant_id: string;
  role: string;
  email: string;
}

/** Carries what the server said, so the screen can show it verbatim. */
export class SignInError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SignInError";
  }
}

async function publicCall<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new SignInError("Could not reach the server. Check that the API is running.");
  }
  const data = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (!res.ok) {
    throw new SignInError(
      typeof data?.detail === "string" ? data.detail : `Sign-in failed (${res.status}).`,
    );
  }
  return data as T;
}

/** What this deployment offers. Asked rather than baked in at build time. */
export function fetchAuthConfig(): Promise<AuthConfig> {
  return publicCall<AuthConfig>("/api/auth/config");
}

export function signInWithGoogle(credential: string): Promise<Session> {
  return publicCall<Session>("/api/auth/google", { credential });
}

export function requestLoginCode(email: string): Promise<{ sent: boolean }> {
  return publicCall<{ sent: boolean }>("/api/auth/email/request", { email });
}

export function verifyLoginCode(email: string, code: string): Promise<Session> {
  return publicCall<Session>("/api/auth/email/verify", { email, code });
}

/** Store the token every other call will send. */
export function startSession(session: Session): void {
  setToken(session.token);
}

export async function fetchMe(): Promise<Principal> {
  const res = await apiFetch(`${API_BASE}/api/auth/me`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as Principal;
}

/** One live way into the account. Never carries a token or part of one. */
export interface TokenSession {
  id: string;
  method: string;
  issued_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

/** End this session on the server, then locally.
 *
 * Clearing the browser was never signing out: the token stayed valid for the
 * rest of its life. The server call is what actually ends it, so it happens
 * first — but a failure must not strand somebody in a session they asked to
 * leave, so the local token goes either way and the caller is told what
 * happened.
 */
export async function endSession(): Promise<boolean> {
  let ended = false;
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/signout`, { method: "POST" });
    ended = res.ok;
  } catch {
    // NotAuthenticated means it was already over; anything else means the
    // server could not be reached and the session outlives this click.
    ended = false;
  } finally {
    setToken(null);
  }
  return ended;
}

export async function listSessions(): Promise<TokenSession[]> {
  const res = await apiFetch(`${API_BASE}/api/auth/sessions`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as TokenSession[];
}

/** End every session except this one — the answer to a lost laptop. */
export async function revokeOtherSessions(): Promise<number> {
  const res = await apiFetch(`${API_BASE}/api/auth/sessions/revoke-others`,
                             { method: "POST" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return ((await res.json()) as { ended: number }).ended;
}

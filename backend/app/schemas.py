"""Pydantic request/response models for the API. Defaults mirror ProductInput."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProductRequest(BaseModel):
    name: str
    price: float = Field(gt=0, description="Selling price, $")
    cogs: float = Field(gt=0, description="Landed cost per unit, $")
    fba_fee: float = Field(ge=0, description="FBA fulfillment fee, $")
    monthly_sales: float = Field(ge=0, description="Estimated units / month")
    referral_rate: float = Field(default=0.15, ge=0, le=1)
    ppc_per_unit: float = Field(default=0.0, ge=0)
    size_score: float = Field(default=1.0, ge=0, le=1)
    dominant_brands: int = Field(default=0, ge=0)
    median_reviews: int = Field(default=0, ge=0)
    demand_score: float = Field(default=1.0, ge=0, le=1)
    patent_ok: bool = True
    cert_score: float = Field(default=1.0, ge=0, le=1)


class EvaluateRequest(BaseModel):
    products: list[ProductRequest]
    explain: bool = Field(default=False, description="Generate an LLM narrative explanation")


class EconomicsOut(BaseModel):
    referral_fee: float
    fba_fee_total: float
    profit_per_unit: float
    margin: float
    roi: float
    monthly_profit: float


class WhatIfOut(BaseModel):
    break_even_price: float | None
    price_for_floor: float | None
    price_for_target: float | None
    max_cogs_at_floor: float


class EvaluationOut(BaseModel):
    name: str
    economics: EconomicsOut
    score: int
    subscores: dict[str, float]
    verdict: str
    reason: str
    whatif: WhatIfOut
    pros: list[str]
    risks: list[str]
    explanation: str | None = None
    inputs: ProductRequest | None = None  # raw inputs (set by discovery, for "open in Product Hunter")


class EvaluateResponse(BaseModel):
    results: list[EvaluationOut]
    weights: dict[str, int]  # criterion -> max points (for the score breakdown UI)


class EvaluationHistoryItem(BaseModel):
    id: str
    name: str
    score: int
    verdict: str
    economics: EconomicsOut
    explanation: str | None = None
    created_at: datetime


class EvaluationHistoryResponse(BaseModel):
    results: list[EvaluationHistoryItem]


# --- PPC Analyzer (module #2) ---

class KeywordRequest(BaseModel):
    keyword: str
    clicks: int = Field(ge=0)
    spend: float = Field(ge=0, description="Ad spend, $")
    sales: float = Field(ge=0, description="Ad-attributed sales, $")
    orders: int = Field(ge=0)
    impressions: int = Field(default=0, ge=0)
    match_type: str = Field(default="exact")


class PpcAnalyzeRequest(BaseModel):
    name: str = Field(default="Campaign")
    break_even_acos: float = Field(gt=0, le=1, description="Product profit margin = break-even ACOS")
    target_acos: float | None = Field(default=None, gt=0, le=1)
    explain: bool = Field(default=False)
    keywords: list[KeywordRequest]


class KeywordFindingOut(BaseModel):
    keyword: str
    match_type: str
    acos: float | None
    cpc: float
    cvr: float
    ctr: float
    spend: float
    sales: float
    orders: int
    action: str
    severity: str
    reason: str
    suggested_bid: float | None
    savings: float


class PpcSummaryOut(BaseModel):
    campaign: str
    total_spend: float
    total_sales: float
    overall_acos: float | None
    break_even_acos: float
    target_acos: float
    wasted_spend: float
    potential_savings: float
    action_counts: dict[str, int]


class PpcAnalyzeResponse(BaseModel):
    summary: PpcSummaryOut
    findings: list[KeywordFindingOut]
    explanation: str | None = None


class PpcHistoryItem(BaseModel):
    id: str
    campaign: str
    severity: str
    overall_acos: float | None
    wasted_spend: float
    potential_savings: float
    created_at: datetime


class PpcHistoryResponse(BaseModel):
    results: list[PpcHistoryItem]


# --- Inventory Planner (module #3) ---

class StockRequest(BaseModel):
    name: str
    on_hand: int = Field(ge=0)
    avg_daily_sales: float = Field(ge=0)
    lead_time_days: int = Field(gt=0)
    inbound: int = Field(default=0, ge=0)
    review_period_days: int = Field(default=30, gt=0)
    safety_stock_days: int | None = Field(default=None, ge=0)
    unit_cost: float = Field(default=0.0, ge=0)


class InventoryAnalyzeRequest(BaseModel):
    explain: bool = Field(default=False)
    skus: list[StockRequest]


class InventoryFindingOut(BaseModel):
    name: str
    daily_sales: float
    days_of_cover: float | None
    stockout_in_days: float | None
    reorder_point: int
    days_to_reorder: float | None
    suggested_order_qty: int
    stock_value: float
    order_cost: float
    status: str
    severity: str
    reason: str


class InventorySummaryOut(BaseModel):
    total_skus: int
    skus_at_risk: int
    total_stock_value: float
    total_reorder_cost: float
    status_counts: dict[str, int]


class InventoryAnalyzeResponse(BaseModel):
    summary: InventorySummaryOut
    findings: list[InventoryFindingOut]
    explanation: str | None = None


# --- Daily Report (cross-module digest) ---

class ReportItemOut(BaseModel):
    module: str
    severity: str
    action: str
    title: str
    impact_usd: float
    detail: str


class DailyReportResponse(BaseModel):
    items: list[ReportItemOut]
    money_at_stake_usd: float
    wasted_spend_usd: float
    reorder_cost_usd: float
    counts: dict[str, int]
    briefing: str | None = None


# --- Product Discovery (find what sells) ---

class DiscoverRequest(BaseModel):
    niche: str = Field(description="e.g. kitchen, pet, fitness, office, baby")
    limit: int = Field(default=10, gt=0, le=50)
    max_price: float | None = Field(default=None, gt=0, description="Only candidates at/below this price")
    min_monthly_sales: int | None = Field(default=None, ge=0, description="Only candidates with at least this many est. sales/mo")


class DiscoverResponse(BaseModel):
    source: str          # data source used (e.g. "sample")
    note: str            # transparency note about the data
    cached: bool = False  # True if served from cache (no API tokens spent)
    results: list[EvaluationOut]
    weights: dict[str, int]


class KeepaStatusResponse(BaseModel):
    enabled: bool = False
    tokens_left: int | None = None
    refill_rate: int | None = None


class DiscoveryHistoryItem(BaseModel):
    id: str
    niche: str
    results_count: int
    top_name: str | None = None
    top_score: int | None = None
    created_at: datetime


class DiscoveryHistoryResponse(BaseModel):
    results: list[DiscoveryHistoryItem]


# --- Supplier Finder (module #5) ---

class SupplierSearchRequest(BaseModel):
    query: str = Field(description="product to source, e.g. 'silicone baking molds'")
    limit: int = Field(default=10, gt=0, le=50)
    max_moq: int | None = Field(default=None, gt=0)
    max_lead_time: int | None = Field(default=None, gt=0)


class SupplierOfferOut(BaseModel):
    supplier: str
    country: str
    unit_price: float
    moq: int
    lead_time_days: int
    rating: float
    est_landed_cost: float
    url: str | None = None


class SupplierSearchResponse(BaseModel):
    source: str
    note: str
    query: str
    suggested_cogs: float | None
    offers: list[SupplierOfferOut]


# --- Operator (3-agent launch plan) ---

class LaunchPlanRequest(BaseModel):
    niche: str = Field(description="niche to build a launch plan for, e.g. 'kitchen'")
    limit: int = Field(default=10, gt=0, le=50)


class PaybackOut(BaseModel):
    first_batch_units: int
    upfront_investment: float
    months_to_sell_batch: float | None
    payback_months: float | None
    break_even_units: int | None = None


class LaunchPlanResponse(BaseModel):
    niche: str
    discovery_source: str
    supplier_source: str
    product: EvaluationOut          # re-scored with the sourced COGS
    supplier: SupplierOfferOut | None
    suggested_cogs: float | None
    payback: PaybackOut | None
    listing_draft: str
    steps: list[str]                # trace of what each agent did
    note: str


# --- Autopilot (autonomous loop + approval queue) ---

class AutopilotScanRequest(BaseModel):
    niches: list[str] = Field(min_length=1, description="niches to scan, one launch plan each")
    limit: int = Field(default=8, gt=0, le=20)
    # Money-focused gate — only queue plans that clear the bar.
    only_buy: bool = Field(default=True)
    min_margin: float | None = Field(default=0.25, ge=0, le=1)
    min_monthly_profit: float | None = Field(default=1000, ge=0)
    min_roi: float | None = Field(default=None, ge=0)
    min_competition: float | None = Field(default=None, ge=0, le=1, description="0-1 competition subscore; higher = less crowded")
    max_payback_months: float | None = Field(default=None, gt=0)


class AutopilotScanResponse(BaseModel):
    queued: int | None = None
    skipped: int | None = None
    job_id: str | None = None
    status: str | None = None


class BackgroundJobResponse(BaseModel):
    job_id: str
    status: str
    result: dict | None = None


class AmazonAuthorizationResponse(BaseModel):
    authorization_url: str


class AmazonConnectionResponse(BaseModel):
    connected: bool
    seller_id: str | None = None
    marketplaces: list[dict] = Field(default_factory=list)


class AmazonSyncStatusResponse(BaseModel):
    status: str
    sync_id: str | None = None
    listings: int = 0
    inventory: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AmazonSalesDailyOut(BaseModel):
    date: str
    ordered_sales: float
    currency: str | None = None
    units_ordered: int
    order_items: int
    page_views: int
    sessions: int
    buy_box_percentage: float | None = None


class AmazonSalesSummaryResponse(BaseModel):
    marketplace_id: str
    days: int
    total_sales: float
    total_units: int
    total_order_items: int
    currency: str | None = None
    last_synced_at: datetime | None = None
    results: list[AmazonSalesDailyOut] = Field(default_factory=list)


class RoiDailyOut(BaseModel):
    date: str
    revenue: float
    units: int
    order_items: int
    sessions: int


class RoiDashboardResponse(BaseModel):
    marketplace_id: str | None = None
    demo_data: bool = False
    period_days: int
    revenue: float
    previous_revenue: float
    revenue_change_percentage: float | None = None
    units: int
    order_items: int
    sessions: int
    conversion_rate: float | None = None
    average_order_value: float | None = None
    currency: str | None = None
    last_synced_at: datetime | None = None
    freshness: str
    amazon_fees: float | None = None
    advertising_spend: float | None = None
    landed_cogs: float | None = None
    cogs_coverage_percentage: float | None = None
    missing_cost_skus: list[str] = Field(default_factory=list)
    profit: float | None = None
    margin: float | None = None
    roi: float | None = None
    missing_profit_inputs: list[str] = Field(default_factory=list)
    daily: list[RoiDailyOut] = Field(default_factory=list)


class AmazonSkuCostInput(BaseModel):
    sku: str = Field(min_length=1, max_length=128)
    landed_cost: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class AmazonSkuCostsRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=32)
    costs: list[AmazonSkuCostInput] = Field(min_length=1, max_length=5000)


class AmazonSkuCostOut(AmazonSkuCostInput):
    updated_at: datetime


class AmazonSkuCostsResponse(BaseModel):
    marketplace_id: str
    results: list[AmazonSkuCostOut] = Field(default_factory=list)


class DemoSeedResponse(BaseModel):
    marketplace_id: str
    days: int
    skus: int
    message: str


class ChannelPerformanceOut(BaseModel):
    channel_id: str
    provider: str
    display_name: str
    status: str
    revenue: float
    profit: float | None = None
    orders: int
    currency: str
    share_percentage: float


class EntitlementResponse(BaseModel):
    """What this customer may do, and what to offer them next.

    Carries no Stripe customer id, subscription id or store id: the screen uses
    none of them, and a value the screen cannot use is a value that can only
    leak.
    """
    status: str          # trialing|active|past_due|canceled|expired|not_configured
    access: bool
    period_ends_at: datetime | None = None
    trial_days_remaining: int | None = None
    action_required: bool = False
    action: str | None = None      # checkout | portal | None
    explanation: str
    plan_name: str
    price_per_month: float
    currency: str
    checkout_available: bool
    features: list[str] = Field(default_factory=list)
    support_email: str | None = None
    support_response_time: str | None = None


class OnboardingStepOut(BaseModel):
    key: str
    title: str
    #: not_started | in_progress | complete | needs_attention. Never derived
    #: from a click: every one of these is read back out of the database.
    status: str
    detail: str
    action: str | None = None
    action_label: str | None = None
    help_url: str | None = None


class OnboardingResponse(BaseModel):
    steps: list[OnboardingStepOut] = Field(default_factory=list)
    complete: bool = False
    support_email: str | None = None
    support_response_time: str | None = None
    support_url: str | None = None


class CommerceScanResponse(BaseModel):
    """What one run of the commerce scan found and opened."""
    days: int                 # window requested
    observed_days: int        # calendar days the store actually has data for
    findings: int
    opened: int
    already_open: int         # findings whose action was still awaiting a decision


class CommerceConfirmResponse(BaseModel):
    """The store's answer to "I did it", not the seller's."""
    confirmed: bool
    on_hand_before: int
    on_hand_now: int | None      # None: Shopify does not count this product
    reason: str


class CommerceDailyOut(BaseModel):
    date: str
    revenue: float
    orders: int
    units: int


class CommerceDashboardResponse(BaseModel):
    period_days: int
    demo_data: bool = False
    revenue: float
    profit: float | None = None
    # Product margin is useful before fees and advertising are known, but it is
    # deliberately separate from net profit so the UI cannot confuse the two.
    landed_cogs: float | None = None
    gross_profit: float | None = None
    gross_margin: float | None = None
    cogs_complete: bool = False
    missing_cost_variants: list[str] = Field(default_factory=list)
    orders: int
    units: int
    currency: str | None = None
    channels: list[ChannelPerformanceOut] = Field(default_factory=list)
    # The day-by-day series behind `revenue`. Without it the only shape a chart
    # could draw was the Amazon one, so a Shopify store with real money showed
    # an empty chart beside a real total — the screen contradicting itself.
    daily: list[CommerceDailyOut] = Field(default_factory=list)
    # `margin` is a fraction, and null whenever costs are incomplete: an unknown
    # margin must leave the profit line absent rather than draw a flattering one.
    margin: float | None = None
    last_synced_at: datetime | None = None
    freshness: str = "no_data"


class ShopifyAuthorizationResponse(BaseModel):
    authorization_url: str


class ShopifyConnectionResponse(BaseModel):
    connected: bool
    shop: str | None = None
    channel_id: str | None = None
    # Order notifications. "active" once Shopify accepted the subscriptions and
    # they point where this server listens; "stale" when they point somewhere else
    # (a restarted tunnel); "pending" when there are none; "failing_signature"
    # when deliveries are arriving and being refused, which is what a rotated
    # SHOPIFY_CLIENT_SECRET looks like from here. reason says why.
    notifications: str = "pending"
    topics: list[str] = Field(default_factory=list)
    reason: str | None = None


class ShopifyPilotResponse(BaseModel):
    """Public aggregate only — never reveals another merchant or their data."""
    maximum_stores: int
    enrolled_stores: int
    remaining_stores: int
    available: bool
    trial_days: int


class WooCommerceAuthorizationResponse(BaseModel):
    authorization_url: str


class WooCommerceConnectionResponse(BaseModel):
    connected: bool
    store_url: str | None = None
    channel_id: str | None = None


class AccountStatusResponse(BaseModel):
    plan: str
    subscription_status: str
    trial_ends_at: datetime | None = None
    trial_days_remaining: int
    entitlements: dict[str, int | bool]
    onboarding: dict[str, bool]
    completion_percentage: int


class BillingLinkRequest(BaseModel):
    plan: str = Field(pattern="^(starter|operator|scale)$")


class BillingLinkResponse(BaseModel):
    url: str


class DeletionRequest(BaseModel):
    confirmation: str


class PrivacyRequestResponse(BaseModel):
    request_id: str
    status: str


class QueueItem(BaseModel):
    id: str
    niche: str
    status: str                     # pending | approved | dismissed
    product_name: str
    verdict: str
    score: int
    margin: float
    roi: float
    competition: float
    monthly_profit: float
    suggested_cogs: float | None
    supplier: str | None
    upfront_investment: float | None
    payback_months: float | None
    break_even_units: int | None
    listing_draft: str
    steps: list[str]
    created_at: datetime


class QueueResponse(BaseModel):
    results: list[QueueItem]
    total_monthly_profit: float = 0.0


class PortfolioResponse(BaseModel):
    count: int
    total_upfront_investment: float
    total_monthly_profit: float
    blended_payback_months: float | None
    items: list[QueueItem]


# --- Creative agent (listing copy + image plan + concept images) ---

class CreativeRequest(BaseModel):
    name: str = Field(min_length=1)
    price: float | None = None
    features: str | None = None
    images: bool = Field(default=True, description="also generate concept images")


class CreativeResponse(BaseModel):
    listing: str
    image_plan: str
    concept_images: list[str]   # data URLs (may be empty if no image key / generation off)


class DecisionRequest(BaseModel):
    action: str                     # approve | dismiss


class DecisionResponse(BaseModel):
    status: str


# --- Operator action ledger (the payroll) -----------------------------------

class MetricWindowIn(BaseModel):
    days: int = Field(gt=0, le=365, description="Length of the observation window")
    spend: float = Field(default=0.0, ge=0, description="Money spent over the window")
    revenue: float = Field(default=0.0, ge=0, description="Money earned over the window")


class ActionCreateRequest(BaseModel):
    module: str = Field(max_length=32, description="ppc | inventory | product_hunter | listing")
    action_type: str = Field(max_length=48, description="e.g. NEGATE_KEYWORD, LOWER_BID, REORDER")
    target: str = Field(max_length=255, description="Keyword, SKU or ASIN acted on")
    projected_impact: float | None = Field(default=None, description="What the engine predicted")
    recommendation_id: str | None = None
    measurement_days: int = Field(default=14, ge=3, le=90)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    note: str | None = None


class ActionAppliedRequest(BaseModel):
    baseline: MetricWindowIn
    # No applied_by here on purpose. This endpoint is reached by a person
    # pressing a button, so the server decides who acted; a client that could
    # declare itself "human" would choose its own guardrail limits.
    change_pct: float | None = Field(
        default=None, ge=-1, le=10,
        description="Relative size of the change, e.g. -0.15 for a 15% bid cut")
    revert_to: dict | None = Field(default=None, description="Previous value, for undo")


class ActionMeasureRequest(BaseModel):
    outcome: MetricWindowIn


class ImpactOut(BaseModel):
    cost_avoided: float
    revenue_gained: float
    net: float
    provisional: bool
    basis: str


class RestockImpactOut(BaseModel):
    """What a witnessed restock is measured to have earned.

    `net` is the margin on the units the old shelf could not have covered — not
    their revenue. It is the only field `build_payroll` adds up, and putting
    revenue in it would be the whole sale price wearing a profit's name.

    The revenue and the cost are both reported beside it so the number can be
    argued with rather than taken on faith.
    """
    attributable_units: int
    attributable_margin: float
    attributable_revenue: float
    attributable_cost: float
    observed_units: int
    observed_revenue: float
    stock_before: int
    days: int
    currency: str
    provisional: bool
    net: float
    basis: str
    evidence_reason: str = ""


class ProductCostWrite(BaseModel):
    """What a seller says a unit costs them.

    Split the way they think about it — what they paid the supplier, and what it
    cost to get it onto the shelf — because a single box labelled "cost" invites
    a guess, and two labelled boxes invite two numbers somebody actually knows.
    """
    product_id: str = Field(min_length=1, max_length=128)
    variant_id: str | None = Field(default=None, max_length=128)
    variant_title: str | None = Field(default=None, max_length=255)
    purchase_amount: float = Field(ge=0, description="Paid to the supplier, per unit")
    extra_amount: float | None = Field(
        default=0, ge=0, description="Everything else, per unit: freight, duty, packing")
    currency: str = Field(min_length=3, max_length=3)
    #: When this became true. A cost entered today for a purchase made in March
    #: prices March's sales, and a window already measured is never restated.
    effective_from: date | None = None
    note: str | None = Field(default=None, max_length=500)


class ProductCostOut(BaseModel):
    product_id: str
    variant_id: str | None = None
    variant_title: str | None = None
    amount: float
    purchase_amount: float | None = None
    extra_amount: float | None = None
    currency: str
    effective_from: date
    source: str            # shopify | manual
    verification: str      # confirmed | reported
    entered_by: str | None = None
    note: str | None = None
    observed_at: datetime


class ProductCostHistoryOut(BaseModel):
    costs: list[ProductCostOut]


class ProductCostEntry(BaseModel):
    """One variant that has sold, and whether it can be priced.

    `unit_cost` of None is unknown, never zero. The two are opposite claims, and
    only one of them can be measured.
    """
    product_id: str
    product_title: str
    variant_id: str | None = None
    variant_title: str | None = None
    units_sold: int
    last_sold: date
    sale_currency: str
    unit_cost: float | None = None
    cost_currency: str | None = None
    effective_from: date | None = None
    source: str | None = None
    verification: str | None = None
    applies_to_every_variant: bool = False
    currency_matches: bool = True
    attributable_to_variant: bool = True
    waiting_measurements: list[str] = []


class ProductCostsResponse(BaseModel):
    entries: list[ProductCostEntry]
    without_cost: int
    blocking: int
    currency: str


class ActionOut(BaseModel):
    id: str
    module: str
    action_type: str
    target: str
    evidence_mode: Literal["real", "demo", "unverified"]
    source_type: str | None = None
    source_id: str | None = None
    status: str
    applied_by: str | None = None
    projected_impact: float | None = None
    baseline: dict | None = None
    outcome: dict | None = None
    impact: ImpactOut | RestockImpactOut | None = None
    revert_to: dict | None = None
    measurement_days: int
    currency: str
    note: str | None = None
    proposed_at: datetime
    applied_at: datetime | None = None
    measured_at: datetime | None = None


class ActionListResponse(BaseModel):
    actions: list[ActionOut]


class AwaitingMeasurementOut(BaseModel):
    """One applied action and exactly how far it is from being priced.

    The screen used to say only how many were "waiting to be priced", and it
    could not even say that: the count was taken after the list had been
    filtered down to proven money, so an action awaiting its first measurement —
    which is by definition not proven yet — was invisible. Somebody confirmed a
    restock and watched it vanish.
    """
    action_id: str
    action_type: str
    target: str
    applied_at: datetime
    stock_before: int | None = None
    verified_on_hand: int | None = None
    window_first: date | None = None
    window_last: date | None = None
    measure_on: date | None = None
    days_remaining: int | None = None
    state: str
    data_state: str
    reason: str


class PayrollResponse(BaseModel):
    period_days: int
    settled_impact: float
    provisional_impact: float
    operator_cost: float
    net: float
    paid_for_itself: bool
    awaiting_measurement: int
    counts: dict[str, int]
    currency: str
    evidence_mode: Literal["real"] = "real"
    excluded_unverified: int = 0
    excluded_demo: int = 0
    awaiting: list[AwaitingMeasurementOut] = []
    explanation: str | None = None


# --- Guardrails: the limits on unattended action ----------------------------

class GuardrailPolicyOut(BaseModel):
    enabled: bool
    max_actions_per_day: int
    max_change_pct: float
    auto_apply_below: float
    protected_spend_per_day: float
    applied_today: int = 0
    remaining_today: int = 0


class GuardrailPolicyUpdate(BaseModel):
    enabled: bool | None = None
    max_actions_per_day: int | None = Field(default=None, ge=0, le=1000)
    max_change_pct: float | None = Field(default=None, gt=0, le=1)
    auto_apply_below: float | None = Field(default=None, ge=0)
    protected_spend_per_day: float | None = Field(default=None, ge=0)


class GuardrailDecisionOut(BaseModel):
    verdict: str
    reason: str
    limits_hit: list[str] = Field(default_factory=list)
    allowed: bool


class ActionRevertRequest(BaseModel):
    note: str | None = None


class ActionDismissRequest(BaseModel):
    """Declining a proposal, and optionally saying why.

    The reason is worth asking for and never worth demanding: a proposal
    declined in silence still has to be declinable, or people leave the queue
    full instead of answering it.
    """
    note: str | None = Field(default=None, max_length=500)


class AlertConditionOut(BaseModel):
    name: str
    severity: str
    detail: str
    action: str
    count: int = 0
    context: dict = Field(default_factory=dict)


class AlertsResponse(BaseModel):
    worst: str | None = None
    conditions: list[AlertConditionOut] = Field(default_factory=list)


class SubprocessorOut(BaseModel):
    name: str
    purpose: str
    data: str


class RetentionOut(BaseModel):
    category: str
    period: str


class LegalDetailsResponse(BaseModel):
    entity: str | None = None
    address: str | None = None
    privacy_contact: str | None = None
    representative: str | None = None
    business_id: str | None = None
    business_type: str | None = None
    country: str | None = None
    complete: bool = False
    missing: list[str] = Field(default_factory=list)
    subprocessors: list[SubprocessorOut] = Field(default_factory=list)
    retention: list[RetentionOut] = Field(default_factory=list)


# --- Signing in ------------------------------------------------------------
#
# Two doors, one shape of answer. Whichever way somebody proved who they are,
# what comes back is the bearer token every other endpoint already expects.


class GoogleSignInRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=8192,
                            description="The ID token returned by Sign in with Google")


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class EmailCodeVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=4, max_length=12)


class SessionResponse(BaseModel):
    token: str
    email: str
    role: str
    tenant_id: str
    expires_at: datetime


class AuthConfigResponse(BaseModel):
    """What the sign-in screen may offer, decided by the server that knows."""
    google_client_id: str | None = None
    email_login: bool = False
    session_days: int = 7


class PrincipalResponse(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: str


class TokenSessionResponse(BaseModel):
    """One live way into the account, as the owner should see it.

    No token, no fragment of one: this list exists so somebody can recognise a
    session they do not want and end it, which needs a date and a device story,
    not a credential.
    """
    id: str
    method: str
    issued_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = False


class SessionsEndedResponse(BaseModel):
    """How many sessions were actually ended, counted rather than assumed."""
    ended: int

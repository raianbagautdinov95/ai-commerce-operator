"""The measurement cycle against real PostgreSQL, as the application role.

SQLite proves the arithmetic and nothing else. Row-level security does not exist
there, and two sessions racing for the same row is a thing only a real database
can be asked about — so the two claims that matter most are made here:

  * one tenant's measurement can never be computed from another's sales;
  * two schedulers firing at the same instant cannot both take the same day.

They are skipped, loudly, when no PostgreSQL is reachable. A skipped test must
never read as a passed one.

    docker compose exec api python -m pytest tests/test_measurement_rls_postgres.py -q
"""
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

PRODUCT = "gid://shopify/Product/1"


def _dsn() -> str | None:
    """The application role's DSN, not the owner's: RLS is what is being tested,
    and a superuser or the owner without FORCE would sail past it."""
    for name in ("RLS_TEST_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name, "")
        if value and not value.startswith("sqlite"):
            return value.replace("postgresql+psycopg://", "postgresql://")
    return None


def _connect(autocommit=False):
    dsn = _dsn()
    if not dsn:
        pytest.skip("No PostgreSQL reachable — this proves NOTHING about RLS or "
                    "concurrency. Run inside the api container.")
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        pytest.skip("psycopg is not installed in this environment")
    try:
        return psycopg.connect(dsn, autocommit=autocommit)
    except Exception as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"Could not reach PostgreSQL: {type(exc).__name__}")


@pytest.fixture
def conn():
    connection = _connect()
    yield connection
    connection.rollback()
    connection.close()


def _declare(cur, tenant: str | None) -> None:
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant or "",))


@pytest.fixture
def two_shops(conn):
    """Two tenants, each with a shop, an applied restock and a fortnight of
    sales of the same product. Rolled back afterwards."""
    cur = conn.cursor()
    applied = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)
    made = {}
    _declare(cur, None)
    for label, units in (("a", 1), ("b", 10)):
        store = str(uuid.uuid4())
        user = str(uuid.uuid4())
        channel = str(uuid.uuid4())
        action = str(uuid.uuid4())
        cur.execute("INSERT INTO users (id, email, created_at) VALUES (%s, %s, now())",
                    (user, f"{label}-{store[:8]}@rls.test"))
        cur.execute("INSERT INTO stores (id, user_id, marketplace, created_at) "
                    "VALUES (%s, %s, 'US', now())", (store, user))
        _declare(cur, store)
        cur.execute(
            "INSERT INTO channel_connections "
            "(id, store_id, provider, external_account_id, display_name, status,"
            " currency, settings, connected_at, synced_at) "
            "VALUES (%s, %s, 'shopify', %s, %s, 'connected', 'USD', %s, now(), now())",
            (channel, store, f"{label}-shop.myshopify.com", label,
             json.dumps({"synced_from": (applied - dt.timedelta(days=1)).date().isoformat()})))
        cur.execute(
            "INSERT INTO operator_actions "
            "(id, store_id, module, action_type, target, evidence_mode, status,"
            " applied_by, baseline, measurement_days, currency, proposed_at, applied_at) "
            "VALUES (%s, %s, 'commerce', 'RESTOCK_PRODUCT', 'Snowboard', 'unverified',"
            " 'applied', 'human', %s, 14, 'USD', now(), %s)",
            (action, store,
             json.dumps({"on_hand": 4, "product_id": PRODUCT, "verified_on_hand": 30,
                         "days": 10, "revenue": 400.0}),
             applied))
        for offset in range(1, 15):
            cur.execute(
                "INSERT INTO product_daily_metrics "
                "(id, store_id, channel_id, external_product_id, metric_date, units,"
                " revenue, currency, source, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'USD', 'shopify_graphql', now())",
                (str(uuid.uuid4()), store, channel, PRODUCT,
                 (applied + dt.timedelta(days=offset)).date(), units, units * 60.0))
        made[label] = {"store": store, "channel": channel, "action": action}
    yield cur, made
    conn.rollback()


# --- isolation --------------------------------------------------------------

def test_a_measurement_reads_only_its_own_tenants_days(two_shops):
    """Both shops sold the same product id over the same fortnight. Reading the
    other one's rows would price a restock from somebody else's sales."""
    cur, made = two_shops
    _declare(cur, made["a"]["store"])
    cur.execute("SELECT COALESCE(SUM(units), 0), COUNT(*) FROM product_daily_metrics "
                "WHERE external_product_id = %s", (PRODUCT,))
    units, rows = cur.fetchone()
    assert rows == 14, "another tenant's product days were visible"
    assert units == 14, "another tenant's units were counted"


def test_the_query_the_job_runs_finds_only_its_own_actions(two_shops):
    """The scheduler walks tenants and declares each one. This is the query it
    runs once declared, asked of the database rather than of the ORM."""
    cur, made = two_shops
    _declare(cur, made["a"]["store"])
    cur.execute("SELECT id FROM operator_actions WHERE status = 'applied' "
                "AND measured_at IS NULL")
    found = [str(row[0]) for row in cur.fetchall()]
    assert found == [made["a"]["action"]]


def test_a_run_with_no_tenant_declared_cannot_write_into_a_tenant(two_shops):
    """A batch job that forgets to declare must fail, not write somewhere
    arbitrary. The WITH CHECK policy is what makes forgetting loud."""
    import psycopg

    cur, made = two_shops
    _declare(cur, None)
    with pytest.raises(psycopg.errors.Error):
        cur.execute(
            "INSERT INTO product_daily_metrics "
            "(id, store_id, channel_id, external_product_id, metric_date, units,"
            " revenue, currency, source, synced_at) "
            "VALUES (%s, %s, %s, 'x', CURRENT_DATE, 1, 1.0, 'USD', 'test', now())",
            (str(uuid.uuid4()), made["a"]["store"], made["a"]["channel"]))


def test_one_tenant_cannot_mark_anothers_action_measured(two_shops):
    """The update the measurement performs, aimed at the wrong tenant."""
    cur, made = two_shops
    _declare(cur, made["a"]["store"])
    cur.execute("UPDATE operator_actions SET measured_at = now(), status = 'measured' "
                "WHERE id = %s", (made["b"]["action"],))
    assert cur.rowcount == 0, "a tenant priced another tenant's action"


def test_a_tenant_cannot_read_what_another_pays_for_its_stock(two_shops):
    """The single most sensitive number in a shop, and the one a competitor
    would most like to have."""
    cur, made = two_shops
    for label, amount in (("a", "60.0000"), ("b", "12.0000")):
        _declare(cur, made[label]["store"])
        cur.execute(
            "INSERT INTO product_costs "
            "(id, store_id, external_product_id, external_variant_id, amount,"
            " currency, effective_from, source, verification, observed_at, created_at) "
            "VALUES (%s, %s, %s, '', %s, 'USD', CURRENT_DATE, 'manual', 'reported',"
            " now(), now())",
            (str(uuid.uuid4()), made[label]["store"], PRODUCT, amount))

    _declare(cur, made["a"]["store"])
    cur.execute("SELECT amount FROM product_costs")
    assert [str(row[0]) for row in cur.fetchall()] == ["60.0000"]


def test_a_cost_cannot_be_written_into_another_tenant(two_shops):
    import psycopg

    cur, made = two_shops
    _declare(cur, made["a"]["store"])
    with pytest.raises(psycopg.errors.Error):
        cur.execute(
            "INSERT INTO product_costs "
            "(id, store_id, external_product_id, external_variant_id, amount,"
            " currency, effective_from, source, verification, observed_at, created_at) "
            "VALUES (%s, %s, %s, '', 1.0, 'USD', CURRENT_DATE, 'manual', 'reported',"
            " now(), now())",
            (str(uuid.uuid4()), made["b"]["store"], PRODUCT))


def test_variant_sales_are_invisible_across_tenants(two_shops):
    """Both shops sell the same variant id. Reading the other's rows would
    price one shop's restock from another shop's units."""
    cur, made = two_shops
    for label, units in (("a", 2), ("b", 20)):
        _declare(cur, made[label]["store"])
        cur.execute(
            "INSERT INTO variant_daily_metrics "
            "(id, store_id, channel_id, external_product_id, external_variant_id,"
            " metric_date, units, revenue, refunded_units, refunded_revenue,"
            " currency, source, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, %s, 100.00, 0, 0, 'USD',"
            " 'shopify_graphql', now())",
            (str(uuid.uuid4()), made[label]["store"], made[label]["channel"],
             PRODUCT, "gid://shopify/ProductVariant/10", units))

    _declare(cur, made["a"]["store"])
    cur.execute("SELECT COALESCE(SUM(units), 0) FROM variant_daily_metrics")
    assert cur.fetchone()[0] == 2


def test_erasure_removes_a_tenants_costs_and_leaves_the_others(two_shops):
    """A deletion request has to take the working data with it — and only
    theirs."""
    cur, made = two_shops
    for label in ("a", "b"):
        _declare(cur, made[label]["store"])
        cur.execute(
            "INSERT INTO product_costs "
            "(id, store_id, external_product_id, external_variant_id, amount,"
            " currency, effective_from, source, verification, observed_at, created_at) "
            "VALUES (%s, %s, %s, '', 5.0, 'USD', CURRENT_DATE, 'manual', 'reported',"
            " now(), now())",
            (str(uuid.uuid4()), made[label]["store"], PRODUCT))

    _declare(cur, made["a"]["store"])
    cur.execute("DELETE FROM product_costs")
    cur.execute("SELECT COUNT(*) FROM product_costs")
    assert cur.fetchone()[0] == 0

    _declare(cur, made["b"]["store"])
    cur.execute("SELECT COUNT(*) FROM product_costs")
    assert cur.fetchone()[0] == 1, "one tenant's erasure took another's data"


# --- concurrency ------------------------------------------------------------

def test_a_day_already_claimed_blocks_the_other_scheduler_before_any_commit():
    """Two containers, two connections, one day.

    The second insert is stopped while the first transaction is still open — it
    waits on the unique index and gives up at the lock timeout. That is the part
    worth proving: a lock that only takes effect once the first run commits is
    not a lock, because the window where both are working is exactly the window
    where both would send, read and price the same things twice.
    """
    import psycopg

    first = _connect(autocommit=False)
    second = _connect(autocommit=False)
    run_key = f"race-{uuid.uuid4().hex[:8]}"
    try:
        a, b = first.cursor(), second.cursor()
        a.execute("INSERT INTO scheduler_runs (id, job, run_key, status, started_at) "
                  "VALUES (%s, 'measurement', %s, 'running', now())",
                  (str(uuid.uuid4()), run_key))

        b.execute("SET LOCAL lock_timeout = '2s'")
        with pytest.raises(psycopg.errors.Error) as blocked:
            b.execute("INSERT INTO scheduler_runs (id, job, run_key, status, started_at) "
                      "VALUES (%s, 'measurement', %s, 'running', now())",
                      (str(uuid.uuid4()), run_key))
        # It was held off rather than allowed through, whether the database
        # answers with a timeout or an outright conflict.
        assert isinstance(blocked.value, (psycopg.errors.LockNotAvailable,
                                          psycopg.errors.QueryCanceled,
                                          psycopg.errors.UniqueViolation))
    finally:
        first.rollback(); first.close()
        second.rollback(); second.close()


def test_a_second_claim_after_a_committed_one_is_refused():
    import psycopg

    conn = _connect(autocommit=True)
    run_key = f"claim-{uuid.uuid4().hex[:8]}"
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO scheduler_runs (id, job, run_key, status, started_at) "
                    "VALUES (%s, 'shopify_sync', %s, 'running', now())",
                    (str(uuid.uuid4()), run_key))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("INSERT INTO scheduler_runs (id, job, run_key, status, started_at) "
                        "VALUES (%s, 'shopify_sync', %s, 'running', now())",
                        (str(uuid.uuid4()), run_key))
        cur.execute("DELETE FROM scheduler_runs WHERE run_key = %s", (run_key,))
    finally:
        conn.close()


def test_each_job_claims_its_own_day_independently():
    """Three stages, one date. A shared key would let a working sync hide a
    measurement that never ran."""
    conn = _connect(autocommit=True)
    run_key = f"stages-{uuid.uuid4().hex[:8]}"
    try:
        cur = conn.cursor()
        for job in ("trial_warnings", "shopify_sync", "measurement"):
            cur.execute("INSERT INTO scheduler_runs (id, job, run_key, status, started_at) "
                        "VALUES (%s, %s, %s, 'running', now())",
                        (str(uuid.uuid4()), job, run_key))
        cur.execute("SELECT COUNT(*) FROM scheduler_runs WHERE run_key = %s", (run_key,))
        assert cur.fetchone()[0] == 3
        cur.execute("DELETE FROM scheduler_runs WHERE run_key = %s", (run_key,))
    finally:
        conn.close()

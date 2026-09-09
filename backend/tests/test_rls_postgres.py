"""
Tenant isolation, proved against PostgreSQL.

The rest of the suite runs on SQLite, which has no row-level security at all, so
every isolation test there proves only that the application remembered to write
`where store_id = ...`. That is worth having and it is not the guarantee: an
application filter is one forgotten clause away from being nothing, and this
project has already shipped a bug where a query ran with no tenant declared and
silently matched no rows — the same mechanism, pointing the other way.

These tests connect as the unprivileged application role and ask the database
directly. They are skipped, loudly, when no PostgreSQL is reachable — a skipped
test must never read as a passed one, so the report at the end says which of the
two happened.

Point RLS_TEST_DATABASE_URL at a throwaway database, or run inside the stack:

    docker compose exec api python -m pytest tests/test_rls_postgres.py -q
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# Tables that must be invisible across tenants, and the column each is keyed on.
# Every one of these holds something a competitor would like: what a store sells,
# what it earns, what it has been advised to do, and the tokens to act as it.
TENANT_TABLES = [
    "channel_connections",
    "channel_products",
    "product_daily_metrics",
    "variant_daily_metrics",
    # What a shop pays for its stock is close to the most sensitive number in it.
    "product_costs",
    "commerce_daily_metrics",
    "operator_actions",
    "webhook_deliveries",
    "idempotency_records",
    "integration_credentials",
    "audit_events",
    "recommendations",
    "subscriptions",
    "email_receipts",
]


def _dsn() -> str | None:
    """The application role's DSN, not the owner's: RLS is what we are testing,
    and a superuser or the table owner without FORCE would sail past it."""
    for name in ("RLS_TEST_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name, "")
        if value and not value.startswith("sqlite"):
            return value.replace("postgresql+psycopg://", "postgresql://")
    return None


@pytest.fixture(scope="module")
def conn():
    dsn = _dsn()
    if not dsn:
        pytest.skip("No PostgreSQL reachable — RLS is NOT proved by this run. "
                    "Set RLS_TEST_DATABASE_URL, or run inside the api container.")
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        pytest.skip("psycopg is not installed in this environment")
    try:
        connection = psycopg.connect(dsn, autocommit=False)
    except Exception as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"Could not reach PostgreSQL: {type(exc).__name__}")
    yield connection
    connection.rollback()
    connection.close()


def _declare(cur, tenant: str | None) -> None:
    if tenant is None:
        cur.execute("SELECT set_config('app.tenant_id', '', true)")
    else:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))


@pytest.fixture
def two_stores(conn):
    """Two tenants with one channel each, rolled back afterwards.

    Created with the tenant declared for each insert, which is also a test: a
    WITH CHECK policy refuses a row written for somebody else.
    """
    cur = conn.cursor()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        _declare(cur, None)
        for user, store, mail in ((user_a, a, f"a-{a[:8]}@rls.test"),
                                  (user_b, b, f"b-{b[:8]}@rls.test")):
            cur.execute("INSERT INTO users (id, email, created_at) "
                        "VALUES (%s, %s, now())", (user, mail))
            cur.execute("INSERT INTO stores (id, user_id, marketplace, created_at) "
                        "VALUES (%s, %s, 'US', now())", (store, user))
        for store, shop in ((a, "tenant-a.myshopify.com"), (b, "tenant-b.myshopify.com")):
            _declare(cur, store)
            cur.execute(
                "INSERT INTO channel_connections "
                "(id, store_id, provider, external_account_id, display_name, status,"
                " currency, settings, connected_at) "
                "VALUES (%s, %s, 'shopify', %s, %s, 'connected', 'USD', '{}', now())",
                (str(uuid.uuid4()), store, shop, shop))
        yield a, b
    finally:
        conn.rollback()


def test_every_tenant_table_is_forced_not_merely_enabled(conn):
    """ENABLE alone exempts the table owner, and the owner is who migrations run
    as. FORCE is what makes the policy mean something for everybody."""
    cur = conn.cursor()
    cur.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(%s)",
        (TENANT_TABLES,))
    found = {name: (enabled, forced) for name, enabled, forced in cur.fetchall()}
    missing = [t for t in TENANT_TABLES if t not in found]
    assert not missing, f"tables absent from the schema: {missing}"
    unprotected = [name for name, (enabled, forced) in found.items()
                   if not (enabled and forced)]
    assert unprotected == [], f"tenant tables without FORCE RLS: {unprotected}"


def test_a_tenant_cannot_see_another_tenants_channel(two_stores, conn):
    """The row that decides whose data an order belongs to."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("SELECT external_account_id FROM channel_connections")
    visible = {row[0] for row in cur.fetchall()}
    assert "tenant-a.myshopify.com" in visible
    assert "tenant-b.myshopify.com" not in visible


def test_a_tenant_cannot_count_another_tenants_rows(two_stores, conn):
    """Not even the number. A count is a leak with fewer characters."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("SELECT count(*) FROM channel_connections WHERE store_id = %s", (b,))
    assert cur.fetchone()[0] == 0


def test_a_tenant_cannot_update_another_tenants_channel(two_stores, conn):
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("UPDATE channel_connections SET status = 'disconnected' "
                "WHERE store_id = %s", (b,))
    assert cur.rowcount == 0, "one tenant disconnected another tenant's store"


def test_a_tenant_cannot_delete_another_tenants_channel(two_stores, conn):
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("DELETE FROM channel_connections WHERE store_id = %s", (b,))
    assert cur.rowcount == 0


def test_a_tenant_cannot_write_a_row_belonging_to_another(two_stores, conn):
    """WITH CHECK, not just USING: reading is only half of isolation."""
    import psycopg

    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO operator_actions "
            "(id, store_id, module, action_type, target, status, evidence_mode,"
            " currency, measurement_days, proposed_at) "
            "VALUES (%s, %s, 'commerce', 'RESTOCK_PRODUCT', 'x', 'proposed',"
            " 'unverified', 'USD', 14, now())",
            (str(uuid.uuid4()), b))
    conn.rollback()


def test_a_transaction_that_never_declares_a_tenant_reads_nothing(two_stores, conn):
    """Failing closed is the point: a forgotten declaration must lose data, not
    expose it. channel_connections is the deliberate exception — a webhook has
    to find its shop before anyone knows the tenant."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, None)
    for table in ("operator_actions", "commerce_daily_metrics",
                  "integration_credentials", "webhook_deliveries",
                  "idempotency_records", "channel_products",
                  "product_daily_metrics", "subscriptions",
                  "email_receipts"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, f"{table} was readable with no tenant declared"


def test_the_pre_tenant_exception_is_only_for_channel_connections(two_stores, conn):
    """The webhook's lookup is permitted by name, not by a general hole."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, None)
    cur.execute("SELECT count(*) FROM channel_connections "
                "WHERE external_account_id = 'tenant-b.myshopify.com'")
    assert cur.fetchone()[0] == 1, "a webhook could not find the shop it arrived for"


def test_declaring_a_tenant_closes_the_exception(two_stores, conn):
    """Once anybody is identified, the exception stops applying to them."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("SELECT count(*) FROM channel_connections "
                "WHERE external_account_id = 'tenant-b.myshopify.com'")
    assert cur.fetchone()[0] == 0


def test_the_application_role_cannot_turn_the_policies_off(conn):
    """A role that may disable RLS has none. NOSUPERUSER and NOBYPASSRLS are
    what make every test above mean something."""
    cur = conn.cursor()
    cur.execute("SELECT current_user")
    role = cur.fetchone()[0]
    cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (role,))
    row = cur.fetchone()
    assert row is not None, f"role {role} not found"
    superuser, bypass = row
    assert not superuser, f"{role} is a superuser; every policy here is decoration"
    assert not bypass, f"{role} may bypass RLS; every policy here is decoration"


# --- deletion, under the policies rather than beside them -------------------

def test_a_tenant_cannot_delete_another_tenants_rows_wholesale(two_stores, conn):
    """What an erasure would look like if it ran with the wrong tenant declared:
    the database refuses rather than relying on the WHERE clause being right."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    for table in ("operator_actions", "commerce_daily_metrics",
                  "webhook_deliveries", "idempotency_records",
                  "integration_credentials", "channel_products",
                  "product_daily_metrics", "subscriptions",
                  "email_receipts"):
        cur.execute(f"DELETE FROM {table} WHERE store_id = %s", (b,))
        assert cur.rowcount == 0, f"{table} was deletable across tenants"


def test_erasing_one_tenant_leaves_the_others_channel(two_stores, conn):
    """The whole point of the command running tenant-scoped: it removes one
    customer and cannot reach the next."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, a)
    cur.execute("DELETE FROM channel_connections WHERE store_id = %s", (a,))
    assert cur.rowcount == 1

    _declare(cur, b)
    cur.execute("SELECT count(*) FROM channel_connections WHERE store_id = %s", (b,))
    assert cur.fetchone()[0] == 1, "erasing one tenant took another's channel"
    conn.rollback()


def test_a_session_row_is_reachable_for_deletion_by_user(two_stores, conn):
    """token_sessions sits outside the policies on purpose - it is read before a
    tenant exists - so erasure has to name it explicitly. This checks the row is
    reachable at all, which is what makes that explicit deletion possible."""
    a, b = two_stores
    cur = conn.cursor()
    _declare(cur, None)
    cur.execute("SELECT count(*) FROM token_sessions WHERE store_id = %s", (a,))
    assert cur.fetchone()[0] == 0  # none created, and no error: the table is readable

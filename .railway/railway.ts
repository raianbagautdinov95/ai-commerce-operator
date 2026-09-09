import {
  defineRailway,
  github,
  group,
  postgres,
  preserve,
  project,
  redis,
  service,
} from "railway/iac";

const repository = "raianbagautdinov95/ai-commerce-operator";

export default defineRailway(() => {
  const database = postgres("postgres");
  const cache = redis("redis");

  // Secret values are installed directly in Railway and preserved by IaC.
  // DATABASE_URL must use the restricted aco_app role; only
  // MIGRATION_DATABASE_URL below receives Railway's owner connection.
  const backendEnvironment = {
    APP_ENV: "production",
    DATABASE_URL: preserve(),
    REDIS_URL: cache.env.REDIS_URL,
    QUEUE_ENABLED: "true",
    AUTH_ENABLED: "true",
    CREDENTIAL_ENCRYPTION_KEYS: preserve(),
    CREDENTIAL_ACTIVE_KEY_VERSION: preserve(),
    SHOPIFY_CLIENT_ID: preserve(),
    SHOPIFY_CLIENT_SECRET: preserve(),
    SHOPIFY_API_VERSION: "2026-01",
    SHOPIFY_WEBHOOK_URI:
      "https://api.aicommerceoperator.com/api/webhooks/shopify",
    RESEND_API_KEY: preserve(),
    LOGIN_EMAIL_FROM: preserve(),
    PUBLIC_APP_URL: "https://app.aicommerceoperator.com",
    SUPPORT_EMAIL: preserve(),
    SENTRY_DSN: preserve(),
    SENTRY_TRACES_SAMPLE_RATE: "0.1",
  };

  // Keep the already-created Railway service instead of deleting/recreating it.
  // Its visible name is the project name; functionally this is the API.
  const api = service("ai-commerce-operator", {
    source: github(repository, { branch: "main", rootDirectory: "backend" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
      watchPatterns: ["backend/**"],
    },
    deploy: {
      preDeployCommand: ["alembic -c alembic.ini upgrade head"],
      healthcheckPath: "/health/ready",
      healthcheckTimeout: 120,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      ...backendEnvironment,
      // Pinned so the custom domain's target port cannot drift away from what
      // the container listens on. Railway may inject a PORT of its own, and the
      // Dockerfile obeys whatever it is given — so the day the platform picks a
      // different number, the domain keeps pointing at the old one and the edge
      // answers 404 with nothing wrong in any log. Same value as EXPOSE.
      PORT: "8000",
      MIGRATION_DATABASE_URL: database.env.DATABASE_URL,
      APP_DB_PASSWORD: preserve(),
      APP_DB_ROLE: "aco_app",
      JWT_SECRET: preserve(),
      CORS_ALLOWED_ORIGINS: "https://app.aicommerceoperator.com",
      SHOPIFY_REDIRECT_URI:
        "https://api.aicommerceoperator.com/api/integrations/shopify/callback",
      SHOPIFY_SCOPES: "read_products,read_orders,read_inventory",
      LEGAL_ENTITY: preserve(),
      LEGAL_ADDRESS: preserve(),
      PRIVACY_CONTACT: preserve(),
      SUPPORT_RESPONSE_TIME: preserve(),

      // Billing. Listed here even though none of it is set yet, because this
      // file is the whole set: a variable that exists in Railway and not here
      // is one an apply can remove, and the day it removes these is the day
      // Checkout starts answering 503 with nothing in the diff to explain it.
      //
      // The secrets stay in Railway — preserve() means "this is set by hand,
      // leave it alone". The return URLs are not secrets and are derived from
      // the domain like every other address, so they are written down: Stripe
      // sends a customer to them right after taking their money, and a
      // hostname that changed overnight lands them on nothing.
      STRIPE_SECRET_KEY: preserve(),
      STRIPE_WEBHOOK_SECRET: preserve(),
      STRIPE_PRICE_OPERATOR: preserve(),
      STRIPE_API_VERSION: preserve(),
      STRIPE_SUCCESS_URL: "https://app.aicommerceoperator.com/billing",
      STRIPE_CANCEL_URL: "https://app.aicommerceoperator.com/pricing",
      STRIPE_PORTAL_RETURN_URL: "https://app.aicommerceoperator.com/billing",
    },
  });

  const worker = service("worker", {
    source: github(repository, { branch: "main", rootDirectory: "backend" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
      watchPatterns: ["backend/**"],
    },
    deploy: {
      startCommand: "python -m app.worker",
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: backendEnvironment,
  });

  const scheduler = service("scheduler", {
    source: github(repository, { branch: "main", rootDirectory: "backend" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
      watchPatterns: ["backend/**"],
    },
    deploy: {
      startCommand: "python -m app.daily",
      cronSchedule: "0 7 * * *",
      restartPolicyType: "NEVER",
    },
    env: {
      ...backendEnvironment,
      TRIAL_WARNING_DAYS: "3",
      SCHEDULER_STALE_AFTER_HOURS: "36",
      MEASUREMENT_STALE_AFTER_HOURS: "48",
      DAILY_SYNC_DAYS: "7",
    },
  });

  const frontend = service("frontend", {
    source: github(repository, { branch: "main", rootDirectory: "frontend" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
      watchPatterns: ["frontend/**"],
    },
    deploy: {
      healthcheckPath: "/",
      healthcheckTimeout: 60,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      // Pinned for the same reason as the API's: the domain's target port has
      // to be a number somebody can look up, not one the platform chooses.
      PORT: "3000",
      NEXT_PUBLIC_API_BASE: "https://api.aicommerceoperator.com",
      // The address above is compiled into the JavaScript a browser downloads,
      // so a wrong one ships and no restart corrects it. It has shipped wrong
      // twice here: once as a domain with no DNS record, once as a developer's
      // own localhost left in a shell variable.
      //
      // frontend/Dockerfile refuses http, localhost and throwaway tunnels when
      // this is "true" — and it was set only in docker-compose.production.yml,
      // which is not the build that reaches anybody. The one build that ships
      // to real browsers was the one without the guard.
      REQUIRE_PUBLIC_API_BASE: "true",
      NEXT_PUBLIC_SUPPORT_EMAIL: preserve(),
      NEXT_PUBLIC_SUPPORT_RESPONSE_TIME: preserve(),
    },
  });

  const backend = group("Backend", [database, cache, api, worker, scheduler]);

  return project("ai-commerce-operator", {
    resources: [backend, frontend],
  });
});

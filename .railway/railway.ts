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
      NEXT_PUBLIC_API_BASE: "https://api.aicommerceoperator.com",
      NEXT_PUBLIC_SUPPORT_EMAIL: preserve(),
      NEXT_PUBLIC_SUPPORT_RESPONSE_TIME: preserve(),
    },
  });

  const backend = group("Backend", [database, cache, api, worker, scheduler]);

  return project("ai-commerce-operator", {
    resources: [backend, frontend],
  });
});

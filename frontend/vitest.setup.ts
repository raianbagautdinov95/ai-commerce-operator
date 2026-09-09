import "@testing-library/jest-dom/vitest";

// Compiled in by the real build (see frontend/Dockerfile), so the tests set it
// the same way. It is the fallback the support line falls back *to* when the
// API call that would have carried it is the thing that failed.
process.env.NEXT_PUBLIC_SUPPORT_EMAIL = "help@example.test";
process.env.NEXT_PUBLIC_SUPPORT_RESPONSE_TIME = "same business day";

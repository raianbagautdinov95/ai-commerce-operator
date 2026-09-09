import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The onboarding screen is the one place a new customer decides whether this
// product is honest, so it is the one screen with tests. jsdom rather than a
// browser: what is being checked is what the component renders from a given
// API response, and every one of those responses is a shape the backend
// actually returns.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["app/**/*.test.tsx", "lib/**/*.test.ts"],
  },
});

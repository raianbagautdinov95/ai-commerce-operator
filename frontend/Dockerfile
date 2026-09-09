FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
# The support address is served by the API too, but it is needed most when the
# API cannot be reached, so it is also compiled in.
ARG NEXT_PUBLIC_SUPPORT_EMAIL
ENV NEXT_PUBLIC_SUPPORT_EMAIL=$NEXT_PUBLIC_SUPPORT_EMAIL
ARG NEXT_PUBLIC_SUPPORT_RESPONSE_TIME
ENV NEXT_PUBLIC_SUPPORT_RESPONSE_TIME=$NEXT_PUBLIC_SUPPORT_RESPONSE_TIME

# The browser's API address is compiled into the bundle, so a wrong one is not a
# setting anybody can correct later: it ships. It has already shipped twice
# here — once as a domain with no DNS record, once as a developer's own
# localhost:8001 left in a shell variable — and both times the symptom was
# "Failed to fetch" on every screen while the API answered every request put to
# it directly. Nothing in the running container hints at the cause.
#
# So a production build is made to fail here instead. Local builds pass this
# unchanged, because REQUIRE_PUBLIC_API_BASE is only set by the production
# compose file.
RUN if [ -z "$NEXT_PUBLIC_API_BASE" ] || [ "$NEXT_PUBLIC_API_BASE" = "UNSET" ]; then \
      echo "REFUSED: nobody chose the API address this bundle will call." >&2; \
      echo "  local:      add -f docker-compose.local.yml" >&2; \
      echo "  production: add -f docker-compose.production.yml" >&2; \
      echo "  either:     set FRONTEND_API_BASE explicitly" >&2; \
      exit 1; \
    fi

ARG REQUIRE_PUBLIC_API_BASE=false
RUN if [ "$REQUIRE_PUBLIC_API_BASE" = "true" ]; then \
      case "$NEXT_PUBLIC_API_BASE" in \
        https://*) ;; \
        *) echo "REFUSED: a production frontend needs an https:// API address, got '$NEXT_PUBLIC_API_BASE'" >&2; exit 1 ;; \
      esac; \
      case "$NEXT_PUBLIC_API_BASE" in \
        *localhost*|*127.0.0.1*|*trycloudflare.com*|*ngrok*) \
          echo "REFUSED: '$NEXT_PUBLIC_API_BASE' is a development address; it would ship to real browsers" >&2; exit 1 ;; \
      esac; \
      echo "API address accepted for a production build: $NEXT_PUBLIC_API_BASE"; \
    fi

RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next ./.next
COPY --from=build --chown=app:app /app/package.json /app/package-lock.json ./
COPY --from=dependencies --chown=app:app /app/node_modules ./node_modules
USER app
ENV PORT=3000
EXPOSE 3000
# next directly rather than through npm, so `exec` actually reaches the server
# and it receives SIGTERM instead of npm swallowing it.
CMD ["sh", "-c", "exec node_modules/.bin/next start -p ${PORT:-3000}"]

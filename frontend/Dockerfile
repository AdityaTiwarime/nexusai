# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# nginx as a non-root user (uid 101), listening on 8080. Stable nginx line, plus the
# latest Alpine security fixes (the base image can lag behind them).
FROM nginxinc/nginx-unprivileged:1.28-alpine
USER root
RUN apk upgrade --no-cache
USER 101
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

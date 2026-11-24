ARG NODE_IMAGE=node:22.14.0-alpine
ARG NGINX_IMAGE=nginx:1.28.0-alpine
FROM ${NODE_IMAGE} AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${NGINX_IMAGE} AS web-base
COPY deployment/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY deployment/nginx/proxy.inc /etc/nginx/conf.d/proxy.inc
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/gateway-health || exit 1

FROM web-base AS web-prebuilt
COPY frontend/dist /usr/share/nginx/html

FROM web-base AS web
COPY --from=frontend /frontend/dist /usr/share/nginx/html

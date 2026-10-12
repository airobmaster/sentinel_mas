# Investigator workbench: built with Node, served by nginx, which also proxies /api to the API service
# (same origin for the browser: no CORS). Context: ui/workbench.
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=api:8000 DNS_RESOLVER=127.0.0.11 NGINX_ENVSUBST_FILTER=^(API_UPSTREAM|DNS_RESOLVER)$
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# Builds the live-mode dashboard and serves it with nginx, which also proxies
# /api and /ws to the API container (see nginx.conf). Used by docker-compose.yml.
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

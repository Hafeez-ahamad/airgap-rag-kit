# syntax=docker/dockerfile:1
# Builds the UI and packages it with the nginx reverse proxy.
# TODO(verify): pin by digest in Phase 4.

FROM node:24.22.1-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.30.6-alpine AS proxy
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 8443

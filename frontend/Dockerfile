# ----------------------------------------
# Stage 1: React build
# ----------------------------------------
FROM node:24-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ----------------------------------------
# Stage 2: Nginx runtime
# ----------------------------------------
FROM nginx:stable-alpine
COPY frontend/nginx.conf \
 /etc/nginx/conf.d/default.conf
COPY --from=build \
 /app/dist \
 /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

# V-Watch Frontend Dockerfile
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci --silent
COPY . .

# In Docker, the frontend is served by nginx which proxies /api → backend:8000
# Use relative paths so the browser calls the same host (nginx) and nginx
# forwards the request internally — no CORS issues, works on any port.
ARG VITE_API_URL=/api/v1
ARG VITE_WS_URL=ws://localhost/api/v1
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_WS_URL=$VITE_WS_URL

RUN npm run build

# Production stage with Nginx
FROM nginx:alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=10s CMD wget -q -O - http://localhost/health || exit 1
CMD ["nginx", "-g", "daemon off;"]

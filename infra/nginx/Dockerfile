# Única porta exposta ao host. Placeholder estático até a fatia do frontend (#8),
# quando vira multi-stage com o build da SPA React+Vite.
FROM nginx:1.27-alpine

COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/nginx/index.html /usr/share/nginx/html/index.html

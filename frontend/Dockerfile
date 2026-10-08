ARG BASE_IMAGE=node:22.13.1-bookworm-slim
FROM ${BASE_IMAGE}

ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
COPY --chown=node:node scripts ./scripts
RUN npm ci
COPY --chown=node:node . .
ARG NEXT_PUBLIC_BOOKDISTILL_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_BOOKDISTILL_API_URL=${NEXT_PUBLIC_BOOKDISTILL_API_URL} \
    NODE_ENV=production \
    SITES_RUNTIME_ROOT=/tmp/sites-runtime
RUN npm run build && chown -R node:node /app/dist
USER node
EXPOSE 3000
CMD ["node", "--import", "./scripts/sites-env.mjs", "./node_modules/wrangler/bin/wrangler.js", "dev", "--config", "dist/server/wrangler.json", "--local", "--ip", "0.0.0.0", "--port", "3000", "--inspector-port", "0"]

FROM node:22-bookworm-slim

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
ENV NPM_CONFIG_PACKAGE_LOCK=false
RUN npm install

WORKDIR /workspace/apps/web
COPY apps/web/index.html ./index.html
COPY apps/web/vite.config.ts ./vite.config.ts
COPY apps/web/tsconfig.json ./tsconfig.json
COPY apps/web/public ./public
COPY apps/web/src ./src

EXPOSE 5173

CMD ["npm", "run", "dev"]

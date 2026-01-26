FROM node:22-bookworm-slim

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/api/package.json ./apps/api/package.json
ENV NPM_CONFIG_PACKAGE_LOCK=false
RUN npm install

WORKDIR /workspace/apps/api
COPY apps/api/tsconfig.json ./tsconfig.json
COPY apps/api/src ./src

ENV PORT=3000
EXPOSE 3000

CMD ["npm", "run", "dev"]

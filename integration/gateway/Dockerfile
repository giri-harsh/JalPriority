FROM node:20-alpine AS build
WORKDIR /repo
COPY member-a/dashboard/package.json member-a/dashboard/package-lock.json member-a/dashboard/
RUN cd member-a/dashboard && npm ci
COPY member-b/dashboard/package.json member-b/dashboard/package-lock.json member-b/dashboard/
RUN cd member-b/dashboard && npm ci
COPY member-a/dashboard member-a/dashboard
COPY member-b/dashboard member-b/dashboard
COPY contracts contracts
ENV VITE_B_USE_MOCK_A_SCREENS=false \
    VITE_B_A_SCREENS_PATH=../../member-a/dashboard/src/screens/index.ts
RUN cd member-b/dashboard && npx vite build

FROM nginx:1.27-alpine
COPY integration/gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/member-b/dashboard/dist /usr/share/nginx/html

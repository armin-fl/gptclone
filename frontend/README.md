## Frontend (Next.js App Router)

This frontend provides a ChatGPT-style development UI:

- Sidebar with previous conversations.
- New chat flow.
- Message history per conversation.
- Phone-number OTP sign-in with JWT-backed chat requests.
- Message composer that calls Django DRF endpoints.

### Version check

- Current project version: `next@16.2.4` (App Router).

### Run

```bash
npm run dev
```

Open `http://localhost:3000`, `http://127.0.0.1:3000`, or `http://46.100.12.235:3000`.
Set `PUBLIC_DEV_HOST` before starting the frontend and backend if the public IP changes.

When the dev Nginx service is running, use `http://127.0.0.1` or `http://46.100.12.235`.
Nginx disables caching and proxies HMR/websocket traffic back to this dev server.

### shadcn

A `components.json` is included and the UI uses shadcn-style component structure under `components/ui`.

If you want the official shadcn CLI workflow, run these yourself:

```bash
npx shadcn@latest init
npx shadcn@latest add button input textarea scroll-area
```

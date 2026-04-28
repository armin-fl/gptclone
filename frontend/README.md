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

Open `http://localhost:3000`.

### shadcn

A `components.json` is included and the UI uses shadcn-style component structure under `components/ui`.

If you want the official shadcn CLI workflow, run these yourself:

```bash
npx shadcn@latest init
npx shadcn@latest add button input textarea scroll-area
```

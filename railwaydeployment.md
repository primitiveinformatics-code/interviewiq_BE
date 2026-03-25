# InterviewIQ Backend — Railway Deployment Guide

This document covers deploying the backend service to Railway after the code has been pushed to GitHub (`primitiveinformatics-code/interviewiq_BE`).

---

## Prerequisites

- Code pushed to GitHub (already done)
- Railway account with an existing project
- Postgres and Redis services already created in Railway
- Node.js installed (for Railway CLI)
- Python installed locally (to generate secrets)

---

## Part 1 — Install & Connect Railway CLI

### Install

```bash
# Option A — npm (recommended)
npm install -g @railway/cli

# Option B — Scoop (Windows)
scoop install railway

# Verify installation
railway --version
```

### Login & Link to your project

```bash
# Opens browser to authenticate
railway login

# Link the CLI to your Railway project and backend service
railway link
# → Select your project
# → Select the 'backend' service
```

---

## Part 2 — Create the Backend Service in Railway Dashboard

1. Go to your Railway project dashboard
2. Click **New Service → GitHub Repo**
3. Authorize Railway to access `primitiveinformatics-code/interviewiq_BE`
4. Select the repo — Railway auto-detects `Dockerfile.backend` via `railway.toml`
5. In service **Settings**, rename the service to **`backend`**

---

## Part 3 — Generate Secrets Locally

Run these commands in your terminal and save the output values — you'll use them in Part 4.

```bash
# Generate SECRET_KEY
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"

# Generate JWT_SECRET
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"

# Generate ENCRYPTION_SALT
python -c "import os,base64; print('ENCRYPTION_SALT=' + base64.b64encode(os.urandom(16)).decode())"
```

---

## Part 4 — Set Environment Variables via Railway CLI

Run each block after completing `railway link` in Part 1.

### App Settings

```bash
railway variables --service backend set APP_ENV=production

# Paste the generated values from Part 3:
railway variables --service backend set SECRET_KEY=<paste-generated-value>
railway variables --service backend set JWT_SECRET=<paste-generated-value>
railway variables --service backend set ENCRYPTION_SALT=<paste-generated-value>
railway variables --service backend set JWT_ALGORITHM=HS256
```

### Database & Redis

```bash
railway variables --service backend set DATABASE_URL='${{Postgres.DATABASE_URL}}'
railway variables --service backend set SYNC_DATABASE_URL='${{Postgres.DATABASE_URL}}'
railway variables --service backend set REDIS_URL='${{Redis.REDIS_URL}}'
```

> **Important — DATABASE_URL driver fix**
>
> Railway's reference variable resolves to `postgresql://...` but the async app requires `postgresql+asyncpg://...`.
> After setting the variable, go to:
> **Railway Dashboard → backend service → Variables → DATABASE_URL**
> and manually change the prefix from `postgresql://` to `postgresql+asyncpg://`.
>
> `SYNC_DATABASE_URL` and `REDIS_URL` do **not** need any changes.

### LLM & Embeddings

```bash
railway variables --service backend set OPENROUTER_API_KEY=<your-sk-or-v1-key>
railway variables --service backend set COHERE_API_KEY=<your-cohere-api-key>
railway variables --service backend set EMBEDDINGS_MODEL=cohere/embed-english-v3.0
railway variables --service backend set INTERVIEWER_MODEL='openrouter/nvidia/nemotron-3-nano-30b-a3b:free'
railway variables --service backend set EVALUATOR_MODEL='openrouter/nvidia/nemotron-3-nano-30b-a3b:free'
railway variables --service backend set PARSER_MODEL='openrouter/nvidia/nemotron-3-nano-30b-a3b:free'
railway variables --service backend set FOLLOWUP_MODEL='openrouter/nvidia/nemotron-3-nano-30b-a3b:free'
railway variables --service backend set REPORT_MODEL='openrouter/nvidia/nemotron-3-nano-30b-a3b:free'
```

> **API Keys:**
> - OpenRouter free key: [openrouter.ai/keys](https://openrouter.ai/keys) (no credit card needed)
> - Cohere free trial key: [cohere.com/api-keys](https://cohere.com/api-keys)

### Admin & Payments

```bash
railway variables --service backend set ADMIN_EMAILS=nipin88832@gmail.com
railway variables --service backend set RAZORPAY_CURRENCY=INR
# Leave Razorpay keys empty for now — billing endpoints will be inactive
```

### CORS & Service URLs

Set these with placeholder values for now. Update once the frontend is deployed (see Part 7).

```bash
railway variables --service backend set ALLOWED_ORIGINS=https://placeholder.up.railway.app
railway variables --service backend set FRONTEND_URL=https://placeholder.up.railway.app
railway variables --service backend set BACKEND_URL=https://placeholder.up.railway.app
railway variables --service backend set LANGCHAIN_TRACING_V2=false
```

### Complete Variable Reference Table

| Variable | Value / Notes |
|---|---|
| `APP_ENV` | `production` |
| `SECRET_KEY` | Generated — `secrets.token_urlsafe(64)` |
| `JWT_SECRET` | Generated — `secrets.token_urlsafe(64)` |
| `ENCRYPTION_SALT` | Generated — `base64.b64encode(os.urandom(16))` |
| `JWT_ALGORITHM` | `HS256` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` → change prefix to `postgresql+asyncpg://` |
| `SYNC_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (keep as `postgresql://`) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `OPENROUTER_API_KEY` | Your `sk-or-v1-...` key |
| `COHERE_API_KEY` | Your Cohere key |
| `EMBEDDINGS_MODEL` | `cohere/embed-english-v3.0` |
| `INTERVIEWER_MODEL` | `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` |
| `EVALUATOR_MODEL` | `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` |
| `PARSER_MODEL` | `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` |
| `FOLLOWUP_MODEL` | `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` |
| `REPORT_MODEL` | `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` |
| `ADMIN_EMAILS` | `nipin88832@gmail.com` |
| `RAZORPAY_CURRENCY` | `INR` |
| `ALLOWED_ORIGINS` | Frontend Railway URL (update after FE deploy) |
| `FRONTEND_URL` | Frontend Railway URL (update after FE deploy) |
| `BACKEND_URL` | Backend Railway URL (visible after first deploy) |
| `LANGCHAIN_TRACING_V2` | `false` (set `true` only if you have a LangSmith key) |

---

## Part 5 — Deploy

```bash
# Trigger a deploy from the linked service
railway up --service backend --detach
```

Railway will:
1. Pull the code from GitHub
2. Build the Docker image using `Dockerfile.backend`
3. Start uvicorn (4 workers)
4. App lifespan runs `create_all()` — creates all DB tables with `Vector(1024)` dimensions

---

## Part 6 — Verify the Deployment

```bash
# Watch live deployment logs
railway logs --service backend

# Get your public backend URL
railway domain --service backend
```

Once the URL is available, test the health endpoint:

```bash
curl https://<your-backend-url>.up.railway.app/health
# Expected response: {"status": "ok"}
```

You can also test user registration:

```bash
curl -X POST https://<your-backend-url>.up.railway.app/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "TestPass123"}'
```

---

## Part 7 — One-Time Alembic Initialisation

After the first successful deploy, run this once to initialise Alembic's migration tracking on the fresh database:

```bash
railway run --service backend alembic stamp head
```

This prevents Alembic from trying to re-run old migrations on a database that was already set up by `create_all()`.

---

## Part 8 — Update URLs After Frontend Deployment

Once the frontend service is deployed and you have its Railway URL, update the three URL variables:

```bash
railway variables --service backend set ALLOWED_ORIGINS=https://<frontend-url>.up.railway.app
railway variables --service backend set FRONTEND_URL=https://<frontend-url>.up.railway.app
railway variables --service backend set BACKEND_URL=https://<backend-url>.up.railway.app
```

A redeploy will trigger automatically after setting variables.

---

## Part 9 — OAuth Setup (When Ready)

When adding Google or GitHub OAuth later:

**Google:**
1. Go to [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Add Authorised redirect URI: `https://<backend-url>.up.railway.app/auth/callback/google`
3. Set variables:
```bash
railway variables --service backend set GOOGLE_CLIENT_ID=<your-client-id>
railway variables --service backend set GOOGLE_CLIENT_SECRET=<your-client-secret>
```

**GitHub:**
1. Go to [github.com/settings/developers](https://github.com/settings/developers) → New OAuth App
2. Set Authorization callback URL: `https://<backend-url>.up.railway.app/auth/callback/github`
3. Set variables:
```bash
railway variables --service backend set GITHUB_CLIENT_ID=<your-client-id>
railway variables --service backend set GITHUB_CLIENT_SECRET=<your-client-secret>
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Build fails — `Dockerfile.backend not found` | Ensure `railway.toml` is committed with `dockerfilePath = "Dockerfile.backend"` |
| App crashes on start — `SECRET_KEY` error | Ensure `SECRET_KEY` is set and not the default placeholder |
| DB connection refused | Check `DATABASE_URL` prefix is `postgresql+asyncpg://` not `postgresql://` |
| Embeddings fail | Verify `COHERE_API_KEY` is set and `EMBEDDINGS_MODEL=cohere/embed-english-v3.0` |
| CORS errors from frontend | Update `ALLOWED_ORIGINS` with the exact frontend URL |
| Health check timeout | Check Railway logs — app may be failing to connect to Postgres or Redis |
| `alembic stamp head` fails | Ensure the service is running and the DB is reachable |

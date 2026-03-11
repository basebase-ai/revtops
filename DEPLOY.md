# Deploying Basebase to Railway

This guide walks you through deploying the Basebase application to Railway.

## Prerequisites

1. A [Railway account](https://railway.app)
2. Your code pushed to a GitHub repository
3. Environment variables ready (see below)

## Architecture

The deployment consists of two services:
- **Backend** (FastAPI) - `/backend` directory
- **Frontend** (Vite + Nginx) - `/frontend` directory

## Step 1: Create a New Project on Railway

1. Go to [railway.app](https://railway.app) and create a new project
2. Choose "Empty Project"

## Step 2: Deploy the Backend

1. Click "New Service" → "GitHub Repo"
2. Select your repository
3. In the service settings:
   - **Root Directory**: `backend`
   - Railway will auto-detect the `railway.toml` configuration

4. Add the following environment variables in the service settings:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Your Supabase PostgreSQL URL (use pooler URL with `?pgbouncer=true`) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `NANGO_SECRET_KEY` | Your Nango secret key |
| `NANGO_PUBLIC_KEY` | Your Nango public key |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Your Supabase service role key |
| `FRONTEND_URL` | (Set after frontend deploys) Frontend URL for CORS |

5. Click "Deploy"

## Step 3: Deploy the Frontend

1. Click "New Service" → "GitHub Repo" (same repo)
2. In the service settings:
   - **Root Directory**: `frontend`
   - Railway will auto-detect the `railway.toml` configuration

3. Add the following environment variables (as build args):

| Variable | Description |
|----------|-------------|
| `VITE_API_URL` | Backend URL from Step 2 (e.g., `https://backend-xxx.railway.app`) |
| `VITE_SUPABASE_URL` | Your Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Your Supabase anonymous key |
| `VITE_NANGO_PUBLIC_KEY` | Your Nango public key |

4. Click "Deploy"

## Step 4: Update CORS

After both services are deployed:

1. Go to your **Backend** service settings
2. Add/update the `FRONTEND_URL` variable with your frontend's Railway URL
3. Redeploy the backend

## Step 5: Configure Custom Domains (Optional)

1. Go to each service's settings
2. Click "Generate Domain" or add a custom domain
3. Update environment variables if using custom domains

## Environment Variable Reference

### Backend (.env example)

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db?pgbouncer=true

# AI
ANTHROPIC_API_KEY=sk-ant-...

# Nango
NANGO_SECRET_KEY=...
NANGO_PUBLIC_KEY=...

# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...

# CORS (set after frontend deploys)
FRONTEND_URL=https://frontend-xxx.railway.app
```

### Frontend (build args)

```env
VITE_API_URL=https://backend-xxx.railway.app
VITE_SUPABASE_URL=https://xxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...
VITE_NANGO_PUBLIC_KEY=...
```

## Nango

OAuth redirect stays as `api.nango.dev/oauth/callback`; no change needed when you change app/API domain. Ensure **VITE_NANGO_PUBLIC_KEY** is set when building the frontend (required for the Connect UI popup); if it’s missing, connecting GitHub or other Nango integrations can fail.

### Slack: Add-to-Slack (Basebase bot) and Nango Connect

To support both (1) **Connect** (Nango OAuth) and (2) **Add Basebase to Slack** (other workspaces installing the bot), use a single Slack OAuth callback on your backend:

1. **Slack app** (api.slack.com → Your App → OAuth & Permissions): set **Redirect URL** to:
   ```
   https://your-backend-domain/api/auth/slack/oauth-callback
   ```
2. **Nango** (Slack integration): set the integration’s **callback URL** to the same backend URL above (so Nango sends users to Slack with this `redirect_uri`).
3. **Backend env**: set `BACKEND_PUBLIC_URL=https://your-backend-domain`, `SLACK_CLIENT_ID`, and `SLACK_CLIENT_SECRET` (same app as Nango).
4. Run migration `078_slack_bot_installs` so the `slack_bot_installs` table exists.

## Updating Supabase Auth

**Required after changing app domain (e.g. to app.basebase.com):** If users are sent to the old domain after Google/OAuth sign-in, Supabase is still using the previous Site URL.

1. Go to **Supabase Dashboard** → **Authentication** → **URL Configuration**
2. Set:
   - **Site URL**: your app origin, e.g. `https://app.basebase.com`
   - **Redirect URLs**: add `https://app.basebase.com/**` (or at least `https://app.basebase.com/auth/callback`)
3. Save. New OAuth sign-ins will redirect to this domain.

## Troubleshooting

### CORS Errors
- Ensure `FRONTEND_URL` is set correctly on the backend
- Check that the URL doesn't have a trailing slash

### Nango after domain change (e.g. old-domain.com → basebase.com)
- Redirect URLs in Nango stay as **api.nango.dev/oauth/callback**; the domain change doesn’t affect that.
- **Backend env (production):** Set `FRONTEND_URL=https://app.basebase.com` (and `BACKEND_PUBLIC_URL=https://api.basebase.com` if used). The backend passes `redirect_url` to Nango so users land on your app after OAuth; that URL is built from `FRONTEND_URL`.
- **Supabase:** Update **Site URL** and **Redirect URLs** to `https://app.basebase.com` (see “Updating Supabase Auth” above).

### GitHub / Nango integrations fail to connect
- Ensure **VITE_NANGO_PUBLIC_KEY** is set when building the frontend (build arg / env). If it’s missing, the Connect UI can fail (console may show “VITE_NANGO_PUBLIC_KEY is not set”). Nango OAuth callback stays as api.nango.dev; no change needed when changing app domain.

### OAuth redirects to wrong domain (e.g. old-domain.com instead of app.basebase.com)
- Supabase uses **Site URL** as the default post-login redirect. Update it: **Supabase** → **Authentication** → **URL Configuration** → set **Site URL** to your app origin (e.g. `https://app.basebase.com`) and add that origin to **Redirect URLs** (`https://app.basebase.com/**`).

### WebSocket Connection Issues
- Ensure `VITE_API_URL` is set correctly (should be the backend URL without `/api`)
- Railway supports WebSockets by default

### Build Failures
- Check that all build args (VITE_*) are set in Railway's service variables
- Ensure the root directory is set correctly for each service

## Local Development vs Production

| Feature | Local | Production |
|---------|-------|------------|
| API URL | Proxied via Vite | Direct to backend |
| WebSocket | `ws://localhost:5173` | `wss://backend.railway.app` |
| Database | Local PostgreSQL | Supabase |

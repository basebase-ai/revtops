# Deploying Revtops to Railway

This guide walks you through deploying the Revtops application to Railway.

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

## Updating Nango Callback URLs

After deployment, update your Nango integration callbacks:

1. Go to Nango dashboard → Integrations
2. For each integration (HubSpot, Slack, etc.), update the callback URL to:
   ```
   https://your-frontend-domain.railway.app
   ```

## Updating Supabase Auth

1. Go to Supabase → Authentication → URL Configuration
2. Add your frontend Railway URL to:
   - Site URL: `https://your-frontend-domain.railway.app`
   - Redirect URLs: `https://your-frontend-domain.railway.app/**`

## Troubleshooting

### CORS Errors
- Ensure `FRONTEND_URL` is set correctly on the backend
- Check that the URL doesn't have a trailing slash

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

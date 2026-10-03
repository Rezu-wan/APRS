# Free Deployment Guide

Complete setup for **100% free** hosting using best-in-class platforms.

---

## Architecture

```
Frontend (Vercel/Netlify) → Backend (Railway/Render) → PostgreSQL (Neon/Supabase)
   FREE, always-on              FREE trial/hobby          FREE 500MB
```

---

## Step 1: Deploy PostgreSQL Database (Choose One)

### Option A: Neon (Recommended)

1. Go to https://neon.tech
2. Sign up with GitHub
3. Create new project: `aprs-db`
4. Copy connection string (format: `postgresql://...`)
5. Free tier: 500 MB storage, always-on

### Option B: Supabase

1. Go to https://supabase.com
2. Sign up with GitHub
3. New project: `aprs-production`
4. Settings → Database → Connection string (copy the URI mode)
5. Free tier: 500 MB storage, 2 concurrent connections

**Save your DATABASE_URL** — you'll need it for backend deployment.

---

## Step 2: Deploy Backend (Choose One)

### Option A: Railway (Recommended - $5 trial credit)

1. **Sign up**: https://railway.app (use GitHub)

2. **Create new project**: 
   - Click "New Project"
   - Select "Deploy from GitHub repo"
   - Connect your repository

3. **Configure service**:
   - Railway auto-detects Python
   - Click on the service → Settings
   - Start Command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`

4. **Add environment variables**:
   ```
   ENVIRONMENT=production
   DATABASE_URL=<your-neon-or-supabase-url>
   LOG_LEVEL=INFO
   PYTHON_VERSION=3.12.0
   RECOVERY_MIN_SAFE_PROBABILITY=0.90
   RECOVERY_MAX_AMOUNT=1500
   RECOVERY_MAX_PREVIOUS_FAILURES=3
   MODEL_DIR=models
   AI_PROVIDER=mock
   AI_TIMEOUT_SECONDS=12
   ```

5. **Generate API keys** (run locally):
   ```bash
   python -c "import secrets; print('API_KEY_SYSTEM=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_ADMIN=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_SUPPORT=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_CUSTOMER=' + secrets.token_urlsafe(32))"
   ```
   Add all 4 to Railway environment variables.

6. **Set CORS** (add after frontend is deployed):
   ```
   CORS_ORIGINS=https://your-frontend.vercel.app
   ```

7. **Deploy**: Railway deploys automatically on push

8. **Your backend URL**: `https://your-service.up.railway.app`

---

### Option B: Render (100% free, no trial needed)

1. **Sign up**: https://render.com (use GitHub)

2. **Create Web Service**:
   - Dashboard → New → Web Service
   - Connect your repository
   - Name: `aprs-backend`
   - Environment: Python 3
   - Build Command: `pip install -r requirements.txt && alembic upgrade head`
   - Start Command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`

3. **Add environment variables** (same as Railway above)

4. **Deploy**: Takes ~5-10 minutes

5. **Your backend URL**: `https://aprs-backend.onrender.com`

---

## Step 3: Deploy Frontend (Choose One)

### Option A: Vercel (Recommended)

1. **Sign up**: https://vercel.com (use GitHub)

2. **Import project**:
   - New Project → Import your GitHub repo
   - Framework Preset: Vite
   - Root Directory: `frontend`
   - Build Command: `npm run build`
   - Output Directory: `dist`

3. **Add environment variable**:
   - Settings → Environment Variables
   - Key: `VITE_API_BASE_URL`
   - Value: `https://your-backend-url.up.railway.app/api/v1`
   - (Replace with your Railway or Render backend URL)

4. **Deploy**: Vercel deploys automatically

5. **Your frontend URL**: `https://your-project.vercel.app`

---

### Option B: Netlify

1. **Sign up**: https://netlify.com (use GitHub)

2. **Import project**:
   - Add new site → Import from Git
   - Select your repository
   - Base directory: `frontend`
   - Build command: `npm run build`
   - Publish directory: `frontend/dist`

3. **Add environment variable**:
   - Site settings → Environment variables
   - Key: `VITE_API_BASE_URL`
   - Value: `https://your-backend-url/api/v1`

4. **Deploy**: Automatic

5. **Your frontend URL**: `https://your-project.netlify.app`

---

## Step 4: Update CORS

Go back to your **backend platform** (Railway or Render):

1. Add/update environment variable:
   ```
   CORS_ORIGINS=https://your-actual-frontend.vercel.app
   ```
   (Replace with your actual Vercel/Netlify URL)

2. Redeploy backend (Railway/Render will auto-redeploy on env change)

---

## Step 5: Seed Demo Data (Optional)

After all services are deployed:

1. **Railway**: 
   - Open your service → Shell tab
   - Run: `python -m scripts.seed_demo`

2. **Render**:
   - Service → Shell
   - Run: `python -m scripts.seed_demo`

---

## Quick Deploy Checklist

- [ ] Deploy PostgreSQL (Neon or Supabase) → Copy DATABASE_URL
- [ ] Generate 4 secure API keys locally
- [ ] Deploy backend (Railway or Render) with all environment variables
- [ ] Deploy frontend (Vercel or Netlify) with VITE_API_BASE_URL
- [ ] Update backend CORS_ORIGINS with frontend URL
- [ ] Test health endpoint: `curl https://your-backend/health`
- [ ] Login with admin key and verify dashboard loads
- [ ] (Optional) Seed demo data from backend shell

---

## Platform Limits (Free Tier)

| Platform | Limits | Sleep? | Upgrade |
|----------|--------|--------|---------|
| **Vercel** | Unlimited bandwidth | Never | $20/mo |
| **Netlify** | 100 GB bandwidth/mo | Never | $19/mo |
| **Railway** | $5 trial credit (~500 hrs) | After credit | $5/mo |
| **Render** | 750 hrs/mo, 100 GB/mo | After 15 min idle | $7/mo |
| **Neon** | 500 MB storage | Never | $19/mo |
| **Supabase** | 500 MB storage, 2 connections | Never | $25/mo |

---

## Recommended Stack (Best Free Experience)

```
Frontend:  Vercel          (never sleeps, global CDN)
Backend:   Railway         ($5 trial, fast, no sleep for months)
Database:  Neon            (always-on, 500 MB free forever)
```

**Total cost**: $0 for first few months (Railway trial), then $5/month

---

## Live URLs

After deployment, update README.md with your live URLs:

```markdown
## Live Demo

- **Frontend**: https://aprs.vercel.app
- **Backend API**: https://aprs-backend.up.railway.app
- **API Documentation**: https://aprs-backend.up.railway.app/docs

### Demo Login
Use the admin API key from deployment environment variables.
```

---

## Troubleshooting

### Backend health check fails
```bash
# Check logs on Railway/Render dashboard
# Verify DATABASE_URL is correct
# Ensure migrations ran: alembic upgrade head
```

### Frontend can't connect to backend
```bash
# Verify VITE_API_BASE_URL in Vercel/Netlify
# Check CORS_ORIGINS in backend includes frontend URL
# Test backend directly: curl https://backend-url/health
```

### Database connection errors
```bash
# Neon/Supabase: Check connection string format
# Must be: postgresql://user:pass@host/dbname
# For Supabase, use "Connection String" in URI mode, not session mode
```

---

## Need Help?

1. Check platform status: 
   - https://www.railway.app/status
   - https://www.renderstatuspage.com
   - https://www.vercel-status.com

2. View logs in each platform's dashboard

3. Test each component independently:
   - Database: `psql <DATABASE_URL>`
   - Backend: `curl https://backend/health`
   - Frontend: Visit URL in browser, check Network tab

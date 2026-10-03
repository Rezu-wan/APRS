# APRS Deployment Guide

Complete step-by-step guide to deploy APRS (AI Powered Recovery System) to production.

---

## 🚀 Option 1: Deploy to Render (Recommended - Free Tier)

Render provides free hosting for both backend and frontend with PostgreSQL included.

### Step 1: Prerequisites
- GitHub account (you already have this ✓)
- Render account (sign up at https://render.com with your GitHub)
- Your repository is public or connected to Render

### Step 2: Push render.yaml (Already Created)
The `render.yaml` file is ready in your repository. This tells Render how to deploy your app.

### Step 3: Deploy on Render

1. **Go to Render Dashboard**
   - Visit https://dashboard.render.com
   - Click "New +" → "Blueprint"

2. **Connect Repository**
   - Select your GitHub repository: `payment-recovery-digital-twin`
   - Branch: `main`
   - Render will detect `render.yaml` automatically

3. **Configure Environment Variables**
   
   Click on the backend service and set these:
   
   ```env
   CORS_ORIGINS=https://aprs-frontend.onrender.com
   OPENAI_API_KEY=<your-openai-key-if-using>
   ```
   
   Click on the frontend service and set:
   
   ```env
   VITE_API_BASE_URL=https://aprs-backend.onrender.com/api/v1
   ```

4. **Generate Secure API Keys**
   
   Run locally to generate keys:
   ```bash
   python -c "import secrets; print('API_KEY_SYSTEM=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_ADMIN=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_SUPPORT=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('API_KEY_CUSTOMER=' + secrets.token_urlsafe(32))"
   ```
   
   Add these to Render environment variables (delete the auto-generated ones)

5. **Deploy**
   - Click "Apply" to start deployment
   - Wait 5-10 minutes for both services to build
   - Render will run migrations automatically

6. **Seed Demo Data (Optional)**
   
   After deployment, run from Render Shell:
   ```bash
   python -m scripts.seed_demo
   ```

### Your Live URLs
- **Frontend**: `https://aprs-frontend.onrender.com`
- **Backend API**: `https://aprs-backend.onrender.com`
- **API Docs**: `https://aprs-backend.onrender.com/docs`

---

## 🚀 Option 2: Deploy to Railway

Railway provides simple deployment with automatic HTTPS and databases.

### Step 1: Install Railway CLI
```bash
npm install -g @railway/cli
railway login
```

### Step 2: Initialize Project
```bash
railway init
railway link
```

### Step 3: Add PostgreSQL
```bash
railway add --database postgresql
```

### Step 4: Deploy Backend
```bash
# Set environment variables
railway variables set ENVIRONMENT=production
railway variables set CORS_ORIGINS=https://your-frontend-url.railway.app

# Deploy
railway up
```

### Step 5: Deploy Frontend
Create a new service for frontend, then:
```bash
cd frontend
railway up
```

---

## 🚀 Option 3: Deploy to Vercel (Frontend) + Render (Backend)

Best for performance - Vercel CDN for frontend, Render for backend.

### Backend on Render
Follow Option 1 above for backend only.

### Frontend on Vercel

1. **Install Vercel CLI**
   ```bash
   npm install -g vercel
   ```

2. **Deploy Frontend**
   ```bash
   cd frontend
   vercel
   ```

3. **Set Environment Variable**
   - Go to Vercel Dashboard → Project Settings → Environment Variables
   - Add: `VITE_API_BASE_URL=https://aprs-backend.onrender.com/api/v1`
   - Redeploy: `vercel --prod`

---

## 🚀 Option 4: Deploy to DigitalOcean App Platform

### Step 1: Create App
1. Go to https://cloud.digitalocean.com/apps
2. Click "Create App"
3. Connect your GitHub repository

### Step 2: Configure Components

**Backend Service:**
- Type: Web Service
- Build Command: `pip install -r requirements.txt && alembic upgrade head`
- Run Command: `uvicorn api.main:app --host 0.0.0.0 --port 8080`
- Port: 8080

**Frontend Service:**
- Type: Static Site
- Build Command: `cd frontend && npm install && npm run build`
- Output Directory: `frontend/dist`

**Database:**
- Add PostgreSQL Dev Database (free tier)

### Step 3: Set Environment Variables
Same as Render option above.

---

## 🚀 Option 5: Self-Hosted with Docker

If you have your own VPS (DigitalOcean Droplet, AWS EC2, etc.):

### Step 1: Install Docker
```bash
# On Ubuntu/Debian
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo apt install docker-compose
```

### Step 2: Clone and Configure
```bash
git clone https://github.com/Rezu-wan/payment-recovery-digital-twin.git
cd payment-recovery-digital-twin
cp .env.example .env
nano .env  # Edit with production values
```

### Step 3: Deploy with Docker Compose
```bash
docker-compose up -d
```

### Step 4: Set Up Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location /api {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location / {
        proxy_pass http://localhost:5173;
        proxy_set_header Host $host;
    }
}
```

### Step 5: Get SSL Certificate
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 📋 Pre-Deployment Checklist

Before deploying to production:

- [ ] Generate secure API keys (not dev keys)
- [ ] Set `ENVIRONMENT=production` in environment variables
- [ ] Configure `CORS_ORIGINS` with your frontend URL
- [ ] Update `VITE_API_BASE_URL` in frontend to backend URL
- [ ] Use PostgreSQL (not SQLite) for production
- [ ] Set `OPENAI_API_KEY` if using OpenAI explanations
- [ ] Test health endpoint after deployment
- [ ] Run database migrations
- [ ] Seed demo data (optional)
- [ ] Test login with generated API keys
- [ ] Verify customer ownership scoping (403 for foreign transactions)

---

## 🔒 Security Considerations

1. **API Keys**: Never commit production keys to git
2. **CORS**: Only allow your frontend domain
3. **HTTPS**: Always use HTTPS in production (Render/Railway/Vercel provide this automatically)
4. **Database**: Use connection pooling for production PostgreSQL
5. **Rate Limiting**: Current implementation is per-process; consider Redis for multi-worker setups
6. **Environment**: Set `ENVIRONMENT=production` to enforce security checks

---

## 🧪 Post-Deployment Testing

After deployment, verify:

1. **Health Check**
   ```bash
   curl https://your-backend-url.onrender.com/health
   # Expected: {"status":"healthy","database":"connected","ml_models":"loaded"}
   ```

2. **Login Test**
   - Visit your frontend URL
   - Login with admin key
   - Navigate to dashboard
   - Verify data loads

3. **Demo Scenarios**
   - Login as admin
   - Go to `/demo`
   - Click "Reset Demo"
   - Prepare S1, process recovery
   - Verify AUTO_RECOVERED status

4. **Customer Access**
   - Login with customer key
   - Verify can only see own transactions (403 for others)

---

## 🆘 Troubleshooting

### Backend won't start
- Check logs: `railway logs` or Render dashboard
- Verify DATABASE_URL is set correctly
- Ensure migrations ran: `alembic upgrade head`
- Check Python version is 3.12

### Frontend can't connect to backend
- Verify `VITE_API_BASE_URL` is correct
- Check CORS_ORIGINS includes frontend domain
- Test backend health endpoint directly

### Database connection errors
- Verify DATABASE_URL format: `postgresql+psycopg://user:password@host:port/dbname`
- Check database service is running
- Ensure migrations completed

### ML models not loaded
- Check `models/` directory exists in deployment
- Verify models are committed to git (they should be)
- Check logs for model loading errors

---

## 💰 Cost Estimate

### Free Tier Options
- **Render**: Free for both backend + frontend + PostgreSQL (with limitations)
- **Railway**: $5/month credit included (about $5/month for small apps)
- **Vercel + Render**: Vercel free for frontend, Render free for backend

### Paid Options (Production Ready)
- **Render**: $7/month (backend) + $7/month (database) = $14/month
- **Railway**: ~$10/month for backend + database
- **DigitalOcean**: $12/month (App Platform) + $15/month (managed database) = $27/month
- **Self-Hosted VPS**: $6/month (Droplet) + your time

---

## 📊 Monitoring

After deployment, monitor:

1. **Metrics Endpoint** (ADMIN only)
   ```bash
   curl https://your-backend-url/api/v1/metrics \
     -H "X-API-Key: your-admin-key"
   ```

2. **Audit Log**
   ```bash
   curl https://your-backend-url/api/v1/audit \
     -H "X-API-Key: your-admin-key"
   ```

3. **Platform Logs**
   - Render: Dashboard → Service → Logs
   - Railway: `railway logs`
   - Vercel: Dashboard → Deployments → Logs

---

## 🎯 Recommended: Deploy to Render (Easiest)

For the fastest deployment:

1. Push `render.yaml` to GitHub (already done ✓)
2. Sign up at https://render.com
3. New Blueprint → Connect your repo
4. Set 2 environment variables (CORS_ORIGINS, VITE_API_BASE_URL)
5. Generate and add secure API keys
6. Click "Apply"
7. Wait 10 minutes
8. Your app is live! 🎉

**Total time**: ~15 minutes  
**Cost**: Free (with sleep after 15 min inactivity)  
**Upgrade**: $7/month for always-on

---

Need help? Check logs first, then verify environment variables match the checklist above.
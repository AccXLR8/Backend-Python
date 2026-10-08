Yes. Now that we know the working setup, I’d make the **new-VM deployment reproducible** rather than repeating the trial-and-error steps.

The architecture would be:

```text
                         ┌─────────────────────────┐
                         │       Vercel            │
                         │  Next.js Frontend       │
                         └────────────┬────────────┘
                                      │ HTTPS
                                      ▼
                         ┌─────────────────────────┐
                         │   Cloudflare Tunnel     │
                         │   cloudflared           │
                         └────────────┬────────────┘
                                      │ localhost:8080
                                      ▼
┌─────────────────────────────────────────────────────────────┐
│                         NEW VM                              │
│                                                             │
│  Docker network: chainreaction                              │
│                                                             │
│  ┌─────────────────┐   ┌─────────────────┐                  │
│  │ FastAPI          │   │ PostgreSQL 15   │                  │
│  │ chainreaction-app│   │                 │                  │
│  │ :8080            │   │ :5432           │                  │
│  └────────┬────────┘   └────────┬────────┘                  │
│           │                     │                            │
│           └──────────┬──────────┘                            │
│                      │                                       │
│                ┌─────▼─────┐                                 │
│                │   Redis   │                                 │
│                │   :6379   │                                 │
│                └───────────┘                                 │
└─────────────────────────────────────────────────────────────┘
```

The important thing is that **Postgres and Redis do not need to be publicly exposed**. Only FastAPI needs to be reachable through the tunnel.

## 1. Create the new VM

I would use Ubuntu 22.04/24.04 LTS with at least:

- 2 vCPU
- 4 GB RAM
- 30+ GB disk

SSH into it:

```bash
ssh <user>@<NEW_VM_IP>
```

Then:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git curl ca-certificates
```

---

# 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Add your user to Docker:

```bash
sudo usermod -aG docker $USER
```

Then log out and SSH back in.

Verify:

```bash
docker --version
docker ps
```

---

# 3. Clone the backend

```bash
cd ~
git clone https://github.com/AccXLR8/chainreaction-backend.git
cd ~/chainreaction-backend
```

Pull the latest:

```bash
git checkout main
git pull origin main
```

---

# 4. Create the Docker network

This is important because your containers communicate using their Docker names.

```bash
docker network create chainreaction
```

You only do this once.

Verify:

```bash
docker network ls
```

You should see:

```text
chainreaction
```

---

# 5. Start PostgreSQL

Use a persistent Docker volume so destroying/recreating the container doesn't destroy the database.

```bash
docker volume create chainreaction-postgres-data
```

Then:

```bash
docker run -d \
  --name chainreaction-postgres \
  --restart unless-stopped \
  --network chainreaction \
  -e POSTGRES_USER=chainreaction \
  -e POSTGRES_PASSWORD='<YOUR_DB_PASSWORD>' \
  -e POSTGRES_DB=chainreaction \
  -v chainreaction-postgres-data:/var/lib/postgresql/data \
  postgres:15
```

Notice that I am **not** publishing port 5432 publicly.

That's intentional.

The backend can reach Postgres internally as:

```text
chainreaction-postgres:5432
```

Check:

```bash
docker ps
```

Then:

```bash
docker logs chainreaction-postgres --tail 50
```

---

# 6. Start Redis

Create persistent storage:

```bash
docker volume create chainreaction-redis-data
```

Run Redis:

```bash
docker run -d \
  --name chainreaction-redis \
  --restart unless-stopped \
  --network chainreaction \
  -v chainreaction-redis-data:/data \
  redis:7 \
  redis-server --appendonly yes
```

Check:

```bash
docker ps
```

---

# 7. Build the backend

From:

```bash
cd ~/chainreaction-backend
```

Build:

```bash
docker build -t chain-reaction-backend .
```

For a completely clean build:

```bash
docker build --no-cache -t chain-reaction-backend .
```

The Dockerfile installs the ChainReaction Py Engine automatically (cloning the Backend-Python repository during the build).

This is one of the reasons I would **keep your existing Dockerfile** rather than manually installing the engine plus Python dependencies on the VM.

---

# 8. Start FastAPI

Use your production secrets here.

```bash
docker run -d \
  --name chainreaction-app \
  --restart unless-stopped \
  --network chainreaction \
  -p 8080:8080 \
  -e APP_ENV=production \
  -e DEBUG=false \
  -e LOG_LEVEL=INFO \
  -e SECRET_KEY='<STRONG_RANDOM_SECRET>' \
  -e ACCESS_TOKEN_EXPIRE_MINUTES=60 \
  -e DATABASE_URL='postgresql+asyncpg://chainreaction:<YOUR_DB_PASSWORD>@chainreaction-postgres:5432/chainreaction' \
  -e REDIS_URL='redis://chainreaction-redis:6379/0' \
  -e AZURE_STORAGE_CONNECTION_STRING='<YOUR_AZURE_CONNECTION_STRING>' \
  -e AZURE_BLOB_CONTAINER=replays \
  -e ENGINE_MODULE=chainreaction_py \
  -e GAME_BOARD_WIDTH=8 \
  -e GAME_BOARD_HEIGHT=8 \
  -e GAME_CLOCK_SECONDS=300 \
  -e WEBSOCKET_HEARTBEAT_SECONDS=15 \
  -e RATE_LIMIT_MOVES_PER_MINUTE=60 \
  -e MATCHMAKING_QUEUE_TIMEOUT_SECONDS=30 \
  chain-reaction-backend
```

**Don't copy the old `SECRET_KEY=hellothere` or placeholder Azure credentials into a new VM.** Generate real secrets.

---

# 9. Verify FastAPI

First:

```bash
docker ps
```

You want:

```text
chainreaction-app
chainreaction-postgres
chainreaction-redis
```

Then:

```bash
docker logs --tail 100 chainreaction-app
```

Test:

```bash
curl http://localhost:8080/api/health/ready
```

Expected:

```json
{"status":"ok","database":"ready","redis":"ready"}
```

---

# 10. Verify CORS BEFORE TOUCHING Vercel

This is the test that caught our problem earlier.

Run:

```bash
curl -i \
  -H "Origin: https://chain-reaction-frontend-ebon.vercel.app" \
  http://localhost:8080/api/health/ready
```

You need:

```text
access-control-allow-origin: https://chain-reaction-frontend-ebon.vercel.app
```

If that header isn't there, **stop here** and fix the backend before deploying the frontend.

---

# 11. Install Cloudflare Tunnel

On the new VM:

```bash
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
```

Install:

```bash
sudo dpkg -i cloudflared.deb
```

Verify:

```bash
cloudflared --version
```

---

# 12. For your current setup, use a Quick Tunnel

You can run:

```bash
cloudflared tunnel --url http://localhost:8080
```

It will give you something like:

```text
https://something-random.trycloudflare.com
```

Then test:

```bash
curl -i \
  -H "Origin: https://chain-reaction-frontend-ebon.vercel.app" \
  https://something-random.trycloudflare.com/api/health/ready
```

Again, you want:

```text
access-control-allow-origin: https://chain-reaction-frontend-ebon.vercel.app
```

---

# 13. Update Vercel

In Vercel, set:

```text
NEXT_PUBLIC_API_URL=https://something-random.trycloudflare.com
NEXT_PUBLIC_WS_URL=wss://something-random.trycloudflare.com
```

Then redeploy the frontend.

The important distinction is:

```text
HTTP:
https://something-random.trycloudflare.com

WebSocket:
wss://something-random.trycloudflare.com
```

Do **not** use:

```text
http://<VM-IP>:8080
```

from the Vercel frontend.

---

# 14. Test the complete system

First:

```bash
curl https://something-random.trycloudflare.com/api/health/ready
```

Then:

```bash
curl -i \
  -H "Origin: https://chain-reaction-frontend-ebon.vercel.app" \
  https://something-random.trycloudflare.com/api/health/ready
```

Then the auth preflight:

```bash
curl -i -X OPTIONS \
  -H "Origin: https://chain-reaction-frontend-ebon.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  https://something-random.trycloudflare.com/api/auth/guest
```

Finally open:

```text
https://chain-reaction-frontend-ebon.vercel.app
```

---

# The one thing I'd change for a real deployment

Your current setup uses a **Quick Tunnel**:

```text
trycloudflare.com
```

That's excellent for testing, but I wouldn't use it as the permanent production architecture.

A better production setup is:

```text
Vercel
   │
   │ HTTPS
   ▼
api.yourdomain.com
   │
   ▼
Cloudflare Tunnel
   │
   ▼
localhost:8080
   │
   ▼
FastAPI
```

Then your Vercel variables never need to change when the VM is replaced:

```text
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
NEXT_PUBLIC_WS_URL=wss://api.yourdomain.com
```

You can destroy the VM, create another one, reconnect the Cloudflare Tunnel, and **the frontend doesn't care**.

---

## Even better: automate the VM setup

Once this works, I'd put the remaining deployment configuration into the backend repo:

```text
chainreaction-backend/
├── Dockerfile
├── app/
├── scripts/
│   ├── deploy.sh
│   └── backup-db.sh
└── .env.example
```

Then a brand-new VM becomes roughly:

```bash
git clone https://github.com/AccXLR8/chainreaction-backend.git
cd chainreaction-backend

docker network create chainreaction

# start postgres
# start redis
# build backend
# start backend
# start cloudflared
```

And ideally we go one step further and create a **`docker-compose.yml`** so the whole backend stack becomes:

```bash
docker compose up -d --build
```

instead of manually maintaining three separate `docker run` commands.

**That's what I would recommend for ChainReaction.** It would make moving to another VM dramatically easier and eliminate most of the manual deployment mistakes we encountered here.
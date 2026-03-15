# Deployment Guide

GBot runs as Docker containers via Docker Compose.

---

## Prerequisites

- Docker Engine 20+
- Docker Compose v2
- A domain (optional, for SSL)

---

## Quick Deploy

```bash
# 1. Clone and configure
git clone https://github.com/omrylcn/gbot.git
cd gbot
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your settings

# 2. Create .env
cat > .env << 'EOF'
OPENROUTER_API_KEY=sk-or-v1-...
JWT_SECRET_KEY=your-32-char-random-secret
WAHA_API_KEY=your-waha-key
EOF

# 3. Build and start
docker compose up -d --build

# 4. Create owner user
docker compose exec gbot gbot user add owner --name "Your Name" --password "yourpassword"
```

---

## Services

```yaml
services:
  gbot:           # Core API server (port 8000)
  dashboard:      # Admin web UI (port 3001) — optional
  waha:           # WhatsApp gateway (port 3000) — optional
```

| Service | Port | Image | Description |
|---------|------|-------|-------------|
| `gbot` | 127.0.0.1:8000 | Custom (Dockerfile) | FastAPI + LangGraph agent |
| `dashboard` | 127.0.0.1:3001 | Custom (dashboard/Dockerfile) | React + nginx, proxies `/api/` to gbot |
| `waha` | 127.0.0.1:3000 | devlikeapro/waha | WhatsApp Web bridge |

All ports bind to `127.0.0.1` by default — not exposed to the internet.

---

## Volumes & Data

| Path | Container Path | Description |
|------|---------------|-------------|
| `./data/` | `/app/data/` | SQLite database (`gbot.db`) |
| `./workspace/` | `/app/workspace/` | Agent workspace files |
| `./config/` | `/app/config/` | Config files (read-only mount) |
| `waha_sessions` | `/app/.sessions/` | WAHA session data (named volume) |

**Important:** `data/` contains the database. Back it up regularly.

---

## Reverse Proxy (Caddy)

For production with SSL:

```
# /etc/caddy/Caddyfile
gbot.yourdomain.com {
    # API
    handle /api/* {
        reverse_proxy localhost:8000
    }

    # Dashboard
    handle {
        reverse_proxy localhost:3001
    }
}
```

Or with nginx:

```nginx
server {
    listen 443 ssl;
    server_name gbot.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/gbot.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gbot.yourdomain.com/privkey.pem;

    # Dashboard
    location / {
        proxy_pass http://127.0.0.1:3001;
    }

    # API (direct, bypassing dashboard nginx)
    location /api/ {
        rewrite ^/api/(.*) /$1 break;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## Backup

### Database Backup

```bash
# Copy from container
docker exec gbot sqlite3 /app/data/gbot.db ".backup /tmp/backup.db"
docker cp gbot:/tmp/backup.db ./gbot_backup_$(date +%Y%m%d).db

# Or directly from host volume
cp data/gbot.db backups/gbot_$(date +%Y%m%d).db
```

### Automated Backup (cron)

```bash
# /etc/cron.d/gbot-backup
0 3 * * * root docker exec gbot sqlite3 /app/data/gbot.db ".backup /tmp/backup.db" && docker cp gbot:/tmp/backup.db /backups/gbot_$(date +\%Y\%m\%d).db && find /backups -name "gbot_*.db" -mtime +7 -delete
```

### Restore

```bash
docker compose down
cp gbot_backup_20260315.db data/gbot.db
docker compose up -d
```

---

## Update

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose up -d --build

# Check logs
docker compose logs -f gbot
```

For zero-downtime (if running behind reverse proxy):

```bash
docker compose build gbot
docker compose up -d --no-deps gbot
```

---

## Monitoring

### Health Check

```bash
curl http://localhost:8000/health
# {"status":"ok","agent_ready":true,"version":"1.14.0"}
```

Docker Compose has a built-in healthcheck (30s interval).

### Logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f gbot --since 5m

# WhatsApp specific
docker logs gbot --since 5m 2>&1 | grep -i whatsapp
docker logs waha --since 5m 2>&1 | grep -v health
```

### Database Size

```bash
docker exec gbot du -h /app/data/gbot.db
```

### WAL Checkpoint

```bash
docker exec gbot sqlite3 /app/data/gbot.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Container won't start | Check `docker compose logs gbot` — usually config error |
| Auth errors (401) | Verify `JWT_SECRET_KEY` is set in `.env` |
| Telegram not responding | Check webhook: `gbot user list` → verify telegram link |
| WhatsApp not responding | Check WAHA session status: `curl localhost:3000/api/sessions/default -H "X-Api-Key: ..."` |
| Database locked | WAL checkpoint: `sqlite3 gbot.db "PRAGMA wal_checkpoint(TRUNCATE);"` |
| High memory usage | Check DB size, consider `VACUUM` |
| Dashboard can't reach API | Verify both containers on same Docker network |

---

## Environment Variables (Docker)

Set in `docker-compose.yml` or `.env`:

```yaml
environment:
  - TZ=Europe/Istanbul    # Timezone for cron jobs
```

All `GBOT_*` env vars are passed through `env_file: .env` in compose.

# Upgrading from the pre-split single-container stack

This takes a host running the old stack (one `backend` container, one `db`,
`bluenviron/mediamtx:latest`, plain nginx) to the current one: separate
surveillance and parking services with their own databases, an mTLS mesh,
sealed camera credentials, cookie sessions and the audit log.

Plan for about 30 minutes of downtime. Run everything from the repository
root on the host. Commands assume the single-host `docker-compose.yml`; for
split hosts use `deploy/compose/docker-compose.surveillance.yml` and
`deploy/compose/docker-compose.parking.yml` (`-f ...`) on each machine.

## Before you start: existing live-host MediaMTX advisory

The old stack ran `bluenviron/mediamtx:latest` with the **public placeholder
passwords** from the repository (`mtx_api_change_me`, `mtx_publish_change_me`)
and anonymous read. Anyone who read the public repository could publish fake
streams to that server, control it through its API, and watch every camera.

Check the running host **now**, before upgrading:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u mtx_api:mtx_api_change_me http://127.0.0.1:9997/v3/paths/list
```

- `200`: the placeholders are live. Treat the cameras as exposed: rotate the
  camera passwords themselves if MediaMTX ever held them in source URLs,
  firewall 9997/8554/8889 to localhost right away, and complete this upgrade
  (it generates new MediaMTX passwords and removes anonymous read).
- `401`: the placeholders were changed. Still upgrade: the old image also
  allowed anonymous read.
- Connection refused: the API is not on this host or port; check
  `docker compose ps` for where MediaMTX runs.

## Steps

### 1. Back up the databases

```bash
docker compose exec -T db pg_dump -U surveillance -Fc surveillance > backup-surveillance-$(date +%F).dump
```

Keep it off the host. Also copy `.env` and any `deploy/pki/out/` that already exists.

### 2. Get the new code

```bash
git pull
```

### 3. Create the PKI and keys

```bash
deploy/pki.sh init
```

This creates the private CA, an mTLS certificate and a service-token key per
service, the user-token signing key (`surveillance/auth.key`), and a
**camera-secrets key for each service, even when a PKI already exists**.
Back up `deploy/pki/out/` now: losing `camera-secrets.key` loses every
stored camera URL (`SECURITY.md`).

For split hosts, set each machine's SANs first, e.g.
`SURVEILLANCE_SANS="DNS:surv.lan,IP:10.0.0.5" PARKING_SANS="DNS:parking.lan" deploy/pki.sh init`,
then copy `deploy/pki/out/<svc>/` to `/run/secrets/argus/<svc>/` on that
machine (directory 700, files 600).

### 4. Generate the new secrets

```bash
scripts/generate-secrets.sh
```

It keeps existing values and adds what the new stack needs:
`PARKING_POSTGRES_PASSWORD`, `MEDIAMTX_READ_PASSWORD`, `MEDIAMTX_VIEWER_PASSWORD`
and `MEDIAMTX_VIEWER_BASIC`. **Change `MEDIAMTX_API_PASSWORD` and
`MEDIAMTX_PUBLISH_PASSWORD` if they still hold the placeholders**, then run
the script again (it re-derives `MEDIAMTX_VIEWER_BASIC`). Set
`PUBLIC_SERVER_NAME` in `.env` and `CORS_ORIGINS=["https://<that name>"]` in
`services/surveillance/.env` and `services/parking/.env`, with `DEBUG=false`.

### 5. Stop the old stack and start the databases

```bash
docker compose down            # the old containers; volumes are kept
docker compose up -d surveillance-db parking-db
```

`surveillance-db` reuses the old `postgres_data` volume, so the existing data
is there.

### 6. Create the parking schema

```bash
docker compose run --rm --no-deps parking alembic upgrade head
```

### 7. Copy the parking data out of the surveillance database

```bash
docker compose run --rm --no-deps parking python scripts/migrate_parking_data.py \
  --source "postgresql://surveillance:${POSTGRES_PASSWORD}@surveillance-db:5432/surveillance" \
  --target "postgresql://parking:${PARKING_POSTGRES_PASSWORD}@parking-db:5432/parking" \
  --target-camera-key /run/secrets/argus/parking/camera-secrets.key
```

(Load `.env` into the shell first, e.g. `set -a; . ./.env; set +a`.) The old
stack stored camera URLs in plaintext, so no `--source-camera-key` is
needed; `--target-camera-key` seals parking's camera URLs immediately. The
copy is idempotent; re-running it is safe. Nothing is deleted from the source.

### 8. Upgrade the surveillance schema

```bash
docker compose run --rm --no-deps -e ARGUS_PARKING_DATA_MIGRATED=1 surveillance alembic upgrade head
```

`ARGUS_PARKING_DATA_MIGRATED=1` confirms step 7 ran: only then does migration
`20260924_0006` drop the old parking tables from the surveillance database
(it refuses to drop populated tables otherwise). This also creates the
session, login-limiter and audit tables.

### 9. Start everything

```bash
docker compose up -d
docker compose ps        # all healthy?
```

At startup each service seals any plaintext camera URL left in its database.

### 10. Create the first administrator (only if there are no users yet)

```bash
docker compose exec surveillance python -m app.cli.create_admin --username admin
```

Existing users keep their passwords.

### 11. Public TLS

```bash
scripts/setup-tls.sh <your-domain> <your-email>
```

## What users notice

- **Everyone is signed out once.** Dashboard sessions moved from a token in
  `localStorage` to httpOnly cookies; the old token is discarded on first load.
- Operators no longer see admin-only controls (camera and zone editing, arm
  mode, grants, bay checkout, the audit log).
- ParkBot commands now show the proposed action and run only after **Confirm**.

## Verify

```bash
# 1. The placeholder MediaMTX credentials are gone (expect 401):
curl -s -o /dev/null -w '%{http_code}\n' -u mtx_api:mtx_api_change_me http://127.0.0.1:9997/v3/paths/list
# 2. No anonymous playback (expect 401 or 403):
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<your-domain>/stream/cam1/whep
# 3. Internal API not public (expect 404):
curl -s -o /dev/null -w '%{http_code}\n' https://<your-domain>/internal/v1/health
```

Then sign in as an admin, open **Audit log** and check that your sign-in is
listed.

## Rolling back

Stop the new stack, restore `backup-surveillance-<date>.dump` into a fresh
`postgres_data` volume (`pg_restore -U surveillance -d surveillance --clean`),
check out the previous commit and start it. Rotate the MediaMTX passwords in
the old `.env` before doing so: the placeholders must not come back.

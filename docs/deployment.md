# Deployment

Phase 1 deploys only the **public** market-data collector; nothing holds exchange credentials.

```bash
POSTGRES_PASSWORD=<choose-one> docker compose up -d --build
docker compose logs -f collector
docker compose run --rm collector status
```

Services: `db` (PostgreSQL 16, bound to 127.0.0.1), `migrate` (one-shot `db upgrade`),
`collector` (`fundpilot collect`, restarts unless stopped).

Notes

* The Dockerfile and compose file have **not been build-tested** in the development
  environment (no Docker daemon there); CI builds the image on every push.
* A single small VM is sufficient; no Kubernetes or paid managed services are required.
* Back up the `pgdata` volume (`pg_dump`) before running `prune --confirm` or migrations.
* Deploying any service with exchange credentials (Phase 7) requires a separate review and
  explicit approval; see `docs/security.md`.

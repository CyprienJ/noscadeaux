# Operations and deployment

[Project overview](../../README.md)

Production is described by [docker-compose.yml](../../docker-compose.yml),
[Dockerfile](../../Dockerfile), and [nginx.conf](../../nginx.conf).
An external HTTPS proxy connects to the Compose Nginx service through a shared
Docker network, typically managed by Nginx Proxy Manager.

## Services

| Service | Role |
| --- | --- |
| `db` | PostgreSQL 16 with a health check and persistent database volume |
| `web` | Application image: migrations, static collection, then three Gunicorn workers |
| `scheduler` | Same application image; periodic email and cleanup commands |
| `nginx` | Static/media serving and proxying application requests to `web:8000` |

Nginx exposes port 80 to its Docker networks; Compose does not publish a host port.
The external `proxy` network must already exist under the configured name.

The web startup sequence is `migrate`, `collectstatic --clear`, then Gunicorn.
The scheduler waits 30 seconds before its loop; it depends on database health,
not on completion of the web service's migrations. Consider this ordering when
deploying schema changes to a populated database.

## Build and release path

[ci-cd.yml](../../.github/workflows/ci-cd.yml) runs PR checks for version increment,
Python lint/format, release notes, Django tests, and JavaScript tests. Pushes to
`main` build and publish the application image in GHCR. Repository branch
protection must separately require the desired checks.

[deploy.yml](../../.github/workflows/deploy.yml) is manually triggered. It copies
Compose and Nginx configuration to `~/app`, writes the deployment `.env` from
GitHub secrets/variables, checks the proxy network, pulls images, starts services,
and prunes unused images.

The build workflow injects application version and commit revision. The deployment
uses the mutable `latest` tag; record the running revision/image when diagnosing
a release or preparing rollback.

## Deployment inputs

Provide database password, Django secret, and any enabled integration credentials.
The manual workflow additionally needs SSH host/user/private key and GHCR access.
See [configuration](configuration.md) for exact names, defaults, and which settings
Compose forwards to each service.

`PUBLIC_BASE_URL` affects some generated links but does not automatically change
`ALLOWED_HOSTS`, CSRF trusted origins, or every email URL. Review those settings
and the actual proxy/request host when using a different domain.

## Data and recovery

Persistent data includes the PostgreSQL volume and uploaded media. Collected
static assets can be regenerated from the image. Back up database and media
together when a consistent recovery point is needed.

Applying migrations is part of web startup. Rehearse data-sensitive changes on a
recent database copy before the deployment window. Migration reversibility does
not necessarily mean that historical data transformations can be undone.

For the onboarding rollout and managed-identity repair, follow
[the dedicated migration guide](onboarding-migration.md).

## Routine inspection

From the deployment directory:

```bash
docker compose ps
docker compose logs --tail=100 web scheduler nginx
```

Check the scheduler separately from HTTP availability: a healthy website can still
have stopped reminders or cleanup. Use the [maintenance guide](maintenance.md)
for command semantics and common failure paths.

## Deeper references

- [Configuration](configuration.md): environment and fixed Django settings.
- [Maintenance](maintenance.md): scheduled commands, retention, diagnosis.
- [Onboarding migration](onboarding-migration.md): audit, rehearsal, rollback constraints.

# Changelog

## [Unreleased] — feat/vault-sync-pipeline (PR #51)

### Added

- **Vault-Sync-Pipeline** (`HBE-757`): `POST /api/lena/vault/write` endpoint — Lena can write
  Markdown files into Sven's Obsidian vault mirror (`/opt/vault-mirror`), committing and pushing
  via git.  Supported modes: `create`, `append`, `overwrite`.
- **Path-whitelist enforcement**: writes restricted to `05 Daily Notes/`, `09 Lena Inbox/`,
  `01 Inbox/`; `04 Ressourcen/Personen/` is append-only (no overwrite).
- **Path-traversal guard** (`_vault_resolve`): `../` sequences rejected with HTTP 400.
- **Audit log**: every write attempt (OK or rejected) logged to `/var/log/vault-audit.log`.
- **`GET /api/lena/vault/read`**: read Markdown files from the mirror (whitelist-gated).
- **Scheduled pull** (`_vault_pull_from_origin`): background job pulls `origin/master` every 2 min
  using `merge --ff-only` to preserve local commits that have not been pushed yet (`HBE-766`).
- `scripts/setup-vault-mirror.sh`: one-time Hetzner VPS setup — clones `vault-memory`, writes
  `~/.netrc`, registers cron job and logrotate config.  **Requires explicit ops sign-off** before
  execution (see ops-confirmation prompt added in `HBE-767`).
- `scripts/vault-pull-cron.sh`: cron wrapper for scheduled vault pulls.

### Changed

- **`Dockerfile`**: added `git` to `apt-get install` so the API container can run git operations.
- **`docker-compose.yml`**: added bind-mount `/opt/vault-mirror:/opt/vault-mirror` on the `api`
  service so the vault mirror on the host is accessible inside the container.
- **`.env.example`**: added `VAULT_MIRROR_PATH`, `GITHUB_BOT_TOKEN` placeholders.

### Fixed

- **`HBE-766`**: replaced `git reset --hard` with `git merge --ff-only FETCH_HEAD` in the pull
  scheduler — prevents wiping local commits that have not yet been pushed (e.g. after a temporary
  network failure).
- **Code-Reviewer findings** (`HBE-758` / commit `b1ccf38`): trusted-proxy header hardening,
  `hmac.compare_digest` for constant-time key comparison, removed debug endpoint.

### Tests

- `tests/test_vault_whitelist.py`: path-whitelist unit tests, endpoint-level HTTP tests
  (`create` / `append` / `overwrite` happy-paths, 403 forbidden, 400 path-traversal, 409
  conflict-on-create), and pull-scheduler regression tests (`merge --ff-only` invariant).

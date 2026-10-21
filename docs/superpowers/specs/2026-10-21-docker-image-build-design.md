# Docker image build: from main only (design)

Date: 2026-10-21. Extends the Docker/compose work (Dockerfile, `docker-compose.yml`,
`docker-compose.cpu.yml`, README "Docker" sections; branch `claude/docker-migration-compose-a48190`).

## Rules (owner decisions)

1. **The Kestrel image is built from `main`, never from a working tree or worktree.** The
   Dockerfile ends in `COPY . .`, so building in a checkout bakes in uncommitted edits and
   feature-branch files. The image must correspond to a committed ref.
2. **Docker artifacts are named `kestrel`**, not `good-badminton`: image tags, container name.
   The repository name is unchanged.

## Design

- `scripts/docker_build.py` is the only supported way to build. It fetches `origin/main`,
  exports the ref with `git archive` into a temp dir (the build context), and runs
  `docker build`. `--cpu` selects the CPU flavour; `--ref` builds a tag or commit instead;
  `--no-fetch` skips the fetch.
- Tags: `kestrel:gpu` / `kestrel:cpu` (what compose runs) and `kestrel:<flavor>-<short sha>`
  (traceability and rollback).
- Flavour build args (`ACCEL`, `BASE_IMAGE`) live in the script. Compose files have **no
  `build:` section**, so `docker compose up --build` cannot build the checkout. They only
  reference `image: kestrel:<flavor>`; the container is named `kestrel`.
- Flow: `python scripts/docker_build.py [--cpu]`, then `docker compose [-f ... cpu.yml] up -d`.
  Compose fails until the image exists (nothing to pull or build); that is intended.

## Consequences

- A change reaches the image only after it is merged to `main`.
- Old `good-badminton:*` images/containers are not cleaned up automatically
  (`docker rm -f good-badminton`, `docker rmi good-badminton:gpu good-badminton:cpu`).

## Not done

- No CI workflow building on push to `main` / publishing to a registry (GHCR). Candidate follow-up.
- `docker_build.py` verified only as far as `--help` and the `git archive` export; no full
  `docker build` run with the script yet.

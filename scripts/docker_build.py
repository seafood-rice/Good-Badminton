"""Build the Kestrel Docker image from a committed ref (default: origin/main).

The image is never built from a working tree or worktree: this exports the ref with
`git archive` into a temp dir and uses that as the build context, so uncommitted edits
and feature-branch files cannot leak in. Tags: kestrel:<flavor> (what compose
runs) and kestrel:<flavor>-<short sha>.

    python scripts/docker_build.py              # GPU image from origin/main
    python scripts/docker_build.py --cpu        # CPU image
    python scripts/docker_build.py --ref v1.2   # a tag/commit instead of main
"""
import argparse
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

FLAVORS = {
    "gpu": {"BASE_IMAGE": "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04"},
    "cpu": {"BASE_IMAGE": "ubuntu:24.04"},
}


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--cpu", action="store_true", help="build the CPU flavour (default: GPU)")
    p.add_argument("--ref", default="origin/main", help="git ref to build (default: origin/main)")
    p.add_argument("--no-fetch", action="store_true", help="do not `git fetch origin` first")
    args = p.parse_args()
    flavor = "cpu" if args.cpu else "gpu"

    if not args.no_fetch and args.ref.startswith("origin/"):
        subprocess.run(["git", "fetch", "origin", args.ref.split("/", 1)[1]], check=True)
    sha = git("rev-parse", "--short=10", f"{args.ref}^{{commit}}").decode().strip()

    with tempfile.TemporaryDirectory(prefix="gb-docker-") as tmp:
        archive = Path(tmp) / "src.tar"
        archive.write_bytes(git("archive", "--format=tar", args.ref))
        ctx = Path(tmp) / "context"
        with tarfile.open(archive) as tf:
            tf.extractall(ctx)
        cmd = ["docker", "build", "--build-arg", f"ACCEL={flavor}"]
        for k, v in FLAVORS[flavor].items():
            cmd += ["--build-arg", f"{k}={v}"]
        cmd += ["-t", f"kestrel:{flavor}", "-t", f"kestrel:{flavor}-{sha}", str(ctx)]
        print(f"Building {args.ref} @ {sha} ({flavor})")
        return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())

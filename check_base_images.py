#!/usr/bin/env python3
"""Report container base images that are newer than the tags in docker-compose.yml.

Dependabot's ``docker`` ecosystem only parses Dockerfiles and Kubernetes
manifests, so the images pinned in ``docker-compose.yml`` (PostgreSQL and
ClamAV) are invisible to it and would otherwise drift. This script closes that
gap: it reads the tags from ``docker-compose.yml``, asks Docker Hub which
releases exist, and reports anything worth bumping.

Exit codes:
  0  every configured tag still tracks the newest release
  1  at least one tag can be bumped (the weekly CI job goes red on purpose)
  2  the check itself could not run (network error, unreadable compose file)

Usage:
  python3 check_base_images.py                  # summary table
  python3 check_base_images.py --verbose        # plus raw comparison details
  python3 check_base_images.py --compose path   # check another compose file
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_COMPOSE = "docker-compose.yml"
HUB_TAGS_URL = (
    "https://hub.docker.com/v2/repositories/{repo}/tags"
    "?page_size=100&ordering=last_updated"
)
USER_AGENT = "daygle-mail-archiver-base-image-check"

# Majors that have been reviewed and deliberately deferred. The value is the
# highest major covered by the decision: a newer major re-enables the warning.
# Use this to keep the weekly job meaningful when you are knowingly staying on
# an older line (for example while a database migration is scheduled).
DEFERRED_MAJORS: dict[str, int] = {
    # PostgreSQL 18 and 19 exist, but moving off 17 is a pg_dump/pg_restore (or
    # pg_upgrade) migration rather than a tag change, so the jump is recorded as
    # reviewed-and-deferred instead of failing every weekly run. Remove this
    # entry when the archive is migrated; a major above 19 reports normally.
    "postgres": 19,
}

IMAGE_RE = re.compile(r"^\s*image:\s*([^\s#]+)")
SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


class CheckError(RuntimeError):
    """The check could not be completed."""


def parse_compose_images(path: str) -> list[tuple[str, str]]:
    """Return (service, image) pairs for every ``image:`` line in the file."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise CheckError(f"cannot read {path}: {exc}") from exc

    images: list[tuple[str, str]] = []
    service = "?"
    for line in lines:
        service_match = SERVICE_RE.match(line)
        if service_match:
            service = service_match.group(1)
            continue
        image_match = IMAGE_RE.match(line)
        if image_match:
            images.append((service, image_match.group(1)))
    if not images:
        raise CheckError(f"no 'image:' entries found in {path}")
    return images


def fetch_tags(repo: str, attempts: int = 3) -> list[dict]:
    """Return the most recently updated tags published for a Docker Hub repo."""
    url = HUB_TAGS_URL.format(repo=repo)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)["results"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, KeyError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise CheckError(f"cannot read Docker Hub tags for {repo}: {last_error}")


def parse_version(tag: str) -> tuple[int, ...] | None:
    match = VERSION_RE.match(tag)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def split_image(image: str) -> tuple[str, str]:
    """Split ``repo:tag`` and expand the implicit Docker Hub library prefix."""
    repo, _, tag = image.partition(":")
    tag = tag or "latest"
    hub_repo = repo if "/" in repo else f"library/{repo}"
    return hub_repo, tag


def analyse(service: str, image: str, tags: list[dict]) -> dict:
    hub_repo, tag = split_image(image)
    configured = parse_version(tag)

    releases = []
    for entry in tags:
        version = parse_version(entry["name"])
        if version:
            releases.append((version, entry))
    if not releases:
        return {"service": service, "image": image, "status": "UNKNOWN",
                "detail": "Docker Hub lists no version-style tags"}

    newest_version, newest_entry = max(releases, key=lambda item: item[0])
    newest = newest_entry["name"]

    if configured is None:
        # Floating tag such as `latest`: compare digests so silent drift shows up.
        configured_entry = next((e for e in tags if e["name"] == tag), None)
        if configured_entry is None:
            return {"service": service, "image": image, "status": "UNKNOWN",
                    "detail": f"tag '{tag}' is not among the 100 most recently "
                              "updated tags on Docker Hub"}
        configured_digest = configured_entry.get("digest")
        newest_digest = newest_entry.get("digest")
        if configured_digest and configured_digest == newest_digest:
            return {"service": service, "image": image, "status": "OK",
                    "detail": f"'{tag}' currently matches the newest release {newest}",
                    "newest": newest}
        return {"service": service, "image": image, "status": "UPDATE",
                "detail": f"'{tag}' does not match the newest release {newest} "
                          f"({(configured_digest or 'no digest')[:19]} vs "
                          f"{(newest_digest or 'no digest')[:19]}); pin the version tag "
                          "or confirm the floating tag is intentional",
                "newest": newest}

    major = configured[0]
    in_line = max((v for v, _ in releases if v[0] == major), default=None)
    if in_line is None:
        behind_line = True
    elif configured == in_line:
        behind_line = False
    elif len(configured) < len(in_line) and in_line[:len(configured)] == configured:
        behind_line = False  # moving tag such as postgres:17 that follows 17.x
    else:
        behind_line = configured < in_line

    newer_major = newest_version[0] > major
    deferred_through = DEFERRED_MAJORS.get(hub_repo.split("/")[-1])
    deferred = newer_major and deferred_through is not None and \
        newest_version[0] <= deferred_through

    notes = []
    if in_line is not None:
        notes.append(f"line {major} tracks {'.'.join(map(str, in_line))}")
    if newer_major:
        notes.append(f"major {'.'.join(map(str, newest_version))} available"
                     + (" (deferred)" if deferred else ""))
    if deferred:
        status = "DEFERRED"
    elif behind_line or newer_major:
        status = "UPDATE"
    else:
        status = "OK"

    return {"service": service, "image": image, "status": status,
            "detail": "; ".join(notes) or f"matches the newest release {newest}",
            "newest": newest}


def render(findings: list[dict], verbose: bool) -> None:
    headers = ("SERVICE", "IMAGE TAG", "STATUS", "DETAIL")
    rows = [(f["service"], f["image"], f["status"], f["detail"]) for f in findings]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(4)]
    pattern = "  ".join(f"{{:<{width}}}" for width in widths)
    print(pattern.format(*headers))
    print(pattern.format(*("-" * width for width in widths)))
    for row in rows:
        print(pattern.format(*row))
    if verbose:
        print()
        for finding in findings:
            print(f"{finding['image']}: newest release {finding.get('newest', 'unknown')}")


def write_step_summary(findings: list[dict]) -> None:
    """Publish the table to the GitHub Actions run summary when available."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## Container base image check", "",
             "| Service | Image | Status | Detail |",
             "| --- | --- | --- | --- |"]
    lines += [f"| `{f['service']}` | `{f['image']}` | {f['status']} | {f['detail']} |"
              for f in findings]
    lines += ["", "Bump the tag in `docker-compose.yml`, then run "
                  "`docker compose pull && docker compose up -d`.", ""]
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare docker-compose.yml image tags with Docker Hub releases.")
    parser.add_argument("--compose", default=DEFAULT_COMPOSE,
                        help=f"compose file to inspect (default: {DEFAULT_COMPOSE})")
    parser.add_argument("--verbose", action="store_true",
                        help="also print the newest release found per image")
    args = parser.parse_args(argv)

    try:
        findings = [analyse(service, image, fetch_tags(split_image(image)[0]))
                    for service, image in parse_compose_images(args.compose)]
    except CheckError as exc:
        print(f"check_base_images: {exc}", file=sys.stderr)
        return 2

    render(findings, args.verbose)
    write_step_summary(findings)

    updates = [f for f in findings if f["status"] in ("UPDATE", "UNKNOWN")]
    if updates:
        print()
        print("Action needed:")
        for finding in updates:
            print(f"  - {finding['service']} ({finding['image']}): {finding['detail']}")
        print()
        print("Bump the tag in docker-compose.yml, then run "
              "`docker compose pull && docker compose up -d`.")
        print("See docs/configuration.md#container-image-updates for the procedure.")
        return 1

    print()
    print("All configured image tags track the newest available releases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

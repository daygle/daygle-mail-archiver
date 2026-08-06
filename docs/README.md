# Project documentation

These guides supplement the in-application Help page and the project README. They describe behavior that is important during deployment and administration.

- [Configuration and deployment](configuration.md)
- [Roles and permissions](roles-and-permissions.md)
- [Quarantine, ClamAV, and integrity verification](quarantine-and-integrity.md)

- [Translation workflow](translations.md)

The GitHub Wiki may contain provider-specific walkthroughs and screenshots. Keep the local guides accurate for the checked-in code and schema; update them when configuration keys, permissions, or data-handling behavior changes.

## Dependency maintenance

Runtime Python dependencies are pinned in `api/requirements.txt` and `worker/requirements.txt`; shared package versions must remain synchronized. Development tools are pinned in `requirements-dev.txt`. The CI static-check job installs that file, runs Ruff, and runs `pip-audit` against the API runtime requirements.

The application ships self-hosted, pinned frontend assets: Bootstrap 5.3.8, Chart.js 4.5.1, GridStack 13.1.2, and Font Awesome Free 7.3.1. These exact bundles, source maps, webfonts, and license files are tracked in Git and are not fetched at runtime. Font Awesome is served with relative paths to the local `/static/vendor/webfonts` directory. Upgrade bundles as complete distributions and run the vendor asset tests plus dashboard regression checks after future updates.

Container images currently use the supported Python 3.12 Bookworm line, PostgreSQL 17, and ClamAV's `latest` tag. Refresh image tags as part of deployment maintenance and consider digest pinning for reproducible production builds. Python package audit results do not cover Debian packages or image-layer vulnerabilities, so scan built images with the deployment team's container scanner.

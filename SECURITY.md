# Security Policy

## Supported versions

Only the current `main` branch is supported while the project is pre-1.0.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability involving credentials, local transcripts, audio, or a way to expose the Codex runtime. Use GitHub's private vulnerability reporting for this repository if it is enabled, or contact the maintainer privately through the email listed on their GitHub profile.

Include a minimal reproduction, impact, affected revision, and any relevant configuration without attaching secrets or personal conversation data. We will acknowledge a report within seven days and work with you on disclosure timing.

## Security boundary

The project must never copy the operator's Codex OAuth credentials into its database, HTTP responses, WebSocket messages, logs, Docker images, or browser bundle. The app-server is local-only. Reports showing a bypass of either rule are high priority.

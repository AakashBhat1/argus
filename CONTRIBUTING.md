# Contributing to Argus

Thanks for helping improve Argus. Keep changes focused, explain their operational impact, and avoid committing camera credentials, model weights, tokens, production URLs, or recorded surveillance data.

## Before you start

- Search existing issues before opening a new one.
- Use the bug or feature template so maintainers have enough context to respond.
- For a substantial behavior or architecture change, open an issue before investing in an implementation.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Development setup

Follow the [README setup instructions](README.md#getting-started). Copy the example environment files and keep real values in ignored local files.

Create a branch from `main` and use a descriptive name such as `fix/websocket-reconnect` or `feat/camera-filtering`.

## Validation

Run the checks relevant to your change.

Backend tests, from the repository root:

```powershell
python -m pytest backend\yolo_classifier\tests
```

Frontend lint, from `frontend/`:

```powershell
npm run lint
```

If a change affects camera ingest, inference, WebSockets, or WebRTC playback, describe the manual stream scenario you tested. Tests must not depend on private streams or committed credentials.

## Pull requests

In the pull request:

- Explain the problem and the chosen solution.
- Link the related issue when one exists.
- List automated and manual validation performed.
- Call out configuration, migration, deployment, security, or privacy effects.
- Include screenshots only when they clarify a user-interface change, and remove sensitive camera content first.

Keep unrelated refactors out of the same pull request. Update public documentation when commands, configuration, compatibility, or behavior changes.

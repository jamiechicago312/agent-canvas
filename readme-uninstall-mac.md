# readme-uninstall-mac

Use this if you tried all 3 install paths from the main README and want a clean reset on macOS.

Run in Terminal.

## 1) Undo the global npm install

```sh
npm uninstall -g @openhands/agent-canvas
```

## 2) Undo the Docker sandbox install

```sh
docker rm -f $(docker ps -aq --filter ancestor=ghcr.io/openhands/agent-canvas) 2>/dev/null || true
docker image rm $(docker image ls ghcr.io/openhands/agent-canvas --format '{{.Repository}}:{{.Tag}}') 2>/dev/null || true
```

## 3) Undo the source install

If you cloned the repo just for this, delete the clone:

```sh
rm -rf <path-to-your-agent-canvas-clone>
```

If you want to keep the clone but remove local install artifacts:

```sh
cd <path-to-your-agent-canvas-clone>
rm -rf node_modules
rm -f .env
```

## 4) Remove shared local state

```sh
rm -rf ~/.openhands
uv cache clean
```

## 5) Optional: clear browser-local app state

Clear site data for:

- `http://localhost:8000`
- `http://localhost:3001`

## Notes

- This does **not** delete your own project folders under `PROJECTS_PATH` unless you remove them yourself.
- If you installed Node, Docker Desktop, `uv`, or Git only for this test, uninstall them with the same tool you used to install them.

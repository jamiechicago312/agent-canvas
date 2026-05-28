# readme-uninstall-windows

Use this if you tried all 3 install paths from the main README and want a clean reset on Windows.

Run in PowerShell.

## 1) Undo the global npm install

```powershell
npm uninstall -g @openhands/agent-canvas
```

## 2) Undo the Docker sandbox install

```powershell
$containers = docker ps -aq --filter ancestor=ghcr.io/openhands/agent-canvas
if ($containers) { docker rm -f $containers }

$images = docker image ls ghcr.io/openhands/agent-canvas --format "{{.Repository}}:{{.Tag}}"
if ($images) { docker image rm $images }
```

## 3) Undo the source install

If you cloned the repo just for this, delete the clone:

```powershell
Remove-Item -Recurse -Force <path-to-your-agent-canvas-clone>
```

If you want to keep the clone but remove local install artifacts:

```powershell
cd <path-to-your-agent-canvas-clone>
Remove-Item -Recurse -Force .\node_modules -ErrorAction SilentlyContinue
Remove-Item -Force .\.env -ErrorAction SilentlyContinue
```

## 4) Remove shared local state

```powershell
Remove-Item -Recurse -Force "$HOME\.openhands" -ErrorAction SilentlyContinue
uv cache clean
```

## 5) Optional: clear browser-local app state

Clear site data for:

- `http://localhost:8000`
- `http://localhost:3001`

## Notes

- This does **not** delete your own project folders under `PROJECTS_PATH` unless you remove them yourself.
- If you installed Node, Docker Desktop, `uv`, or Git only for this test, uninstall them with the same tool you used to install them.

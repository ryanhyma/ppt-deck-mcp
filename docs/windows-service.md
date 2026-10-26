# Windows Server deployment: venv + NSSM

Production target: Windows Server, **no containers**, LibreOffice installed locally (or portable),
LibreChat native. Scripts: [`deploy/windows/install-service.ps1`](../deploy/windows/install-service.ps1),
[`uninstall-service.ps1`](../deploy/windows/uninstall-service.ps1),
env template [`ppt-mcp.env.example`](../deploy/windows/ppt-mcp.env.example).

## What the installer does, and why each part matters

| Step | Why |
|---|---|
| venv at `<root>\.venv` | The system Python is PEP-668 managed on many boxes; a venv also isolates upgrades. `python -u` in the service keeps stdout unbuffered — without it, log files stay empty while the service is running. |
| `nssm install … "<venv>\Scripts\python.exe" -u server.py --transport http` | Point NSSM at `python.exe`, **not** at `server.py`. Registering a `.py` gives the classic `1053: the service did not respond` with no log line. |
| `AppDirectory` | Services start in `C:\Windows\System32`; relative paths silently fail. |
| `AppStdout`/`AppStderr` + `AppRotateFiles`/`AppRotateBytes`/`AppTimestampLog` | No console, so unredirected output is lost. Rotation keeps a Protected B disk from filling. |
| `AppExit Default Restart`, `AppRestartDelay`, `AppThrottle` | Survive a crash without a restart storm. |
| `AppStopMethodConsole 15000` | Give LibreOffice time to exit before NSSM force-kills the tree. |
| `ObjectName` (service account) | Least privilege, "Log on as a service", no interactive logon. For a **gMSA**, use `sc.exe config PPTDeckMCP obj= "CONTOSO\svc-ppt-mcp$"` — NSSM's `ObjectName` needs a password and cannot set one. |
| `icacls` on `data\` and `logs\` | Nothing else on the box should read decks or previews. Inheritance is removed first. |
| Inbound firewall limited to the LibreChat host | The MCP port must never be reachable from a workstation. The browser reaches **images**, which are a different problem (see below). |
| Outbound firewall block on `soffice.exe` | A malicious `.pptx` must not be able to make LibreOffice fetch a URL (SSRF) or call home. |

## Session 0 and headless LibreOffice

A service runs in Session 0 with no desktop. Two consequences:

1. LibreOffice must stay headless (`--headless --invisible --nolockcheck --norestore`), which the render
   bridge already passes. Never add `--writer`/`--impress` style GUI flags.
2. `pyuno` does **not** come from the venv. It ships with LibreOffice's own interpreter
   (`…\program\python.exe`). The installer detects it, verifies `import uno`, and sets
   `PPT_UNO_PYTHON`. If it is missing, previews fall back to the cold PDF chain: correct, ~3× slower.

Check it explicitly after install:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -c "import uno; print('pyuno ok')"
```

## Known gap: the Job Object (fix before Protected B sign-off)

The warm renderer keeps one `soffice.exe` alive under a bridge process. On Linux the parent puts both in
one process group and reaps the group (`os.killpg`) when the bridge dies. **Windows has neither
`start_new_session` nor `killpg`**, so today:

- a normal service stop is fine — NSSM terminates the whole tree;
- a **crashed or killed bridge** can leave one headless `soffice.exe` behind until the next preview
  spawns a fresh bridge (which reaps nothing on Windows).

The correct fix is a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` created for the bridge, so
Windows itself guarantees the child dies with its parent. Tracked as a code item; the acceptance test
below is what proves it.

## Acceptance tests on the Windows box

Run these in order and record the result; each one maps to a failure we already hit on Linux.

1. **Cold start** — `Restart-Service PPTDeckMCP`; within ~10 s `invoke-webrequest http://127.0.0.1:8931/apps/deck_studio/app.js` returns 200.
2. **First preview** — build a 5-slide deck, `preview_deck` it. Expect `render.renderer = libreoffice-warm`; PNGs are `1600×900`.
3. **Cache** — preview again: `renderer = cache`, and the call returns in well under a second.
4. **One-slide edit** — restyle one slide, preview: `misses: 1`, `hits: 4`.
5. **Kill LibreOffice** — `Stop-Process -Name soffice.bin -Force` mid-preview. Next preview must succeed (bridge restarts, new soffice) with no manual intervention.
6. **Kill the bridge** — `Stop-Process -Id (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -like '*render_bridge*').ProcessId`. Next preview must succeed **and** Task Manager must show exactly **one** `soffice.bin`. Two means the Job Object gap is still open.
7. **Stop the service** — no `soffice.bin`, no `python.exe` left; `%TEMP%` has no `lo-warm-*` profile older than an hour after the next start.
8. **Reboot** — service returns automatically, cache still warm (it lives in `data\previews\.cache`).
9. **Browser path** — with LibreChat pointed at the service, the Deck Studio pane loads slide images. This is the test that catches a wrong `PPT_PUBLIC_BASE` (container-only hostnames fail here, not in step 2).
10. **Token** — with `PPT_AUTH_MODE=entra`, a request without `Authorization` gets `401` with a `WWW-Authenticate` header, and a token minted for Microsoft Graph gets `401 invalid_token`.

## Operating notes

- **Logs:** `logs\ppt-mcp.out.log` / `.err.log` (rotated at 10 MB). Ship them to the SIEM; LibreChat's
  container logs are not enough for AU-2/AU-9.
- **Updates:** LibreOffice is the highest-risk dependency (it parses untrusted decks). Patch monthly and
  keep the portable copy out of user-writable paths; a portable LibreOffice under `C:\Tools` must be
  writable only by Administrators and the service account.
- **Backups / retention:** `data\uploads\decks` and `data\previews` are working copies of user content.
  Give them a retention period and a disposal procedure (MP-6); nothing in the app deletes them except
  the cache pruner.
- **Sizing:** one `soffice.bin` ≈ 320 MB RSS; the service ≈ 100 MB. Preview throughput is serialised by
  design (one LibreOffice), which is the queue's job, not a bug.
- **Do not** put the venv or `data` on a network share that LibreChat users can write to.

# SVM V11.2 + LocalAI gateway

This workspace contains a separate working copy of the public SVM V11.2 bot.
It preserves the existing VPS, node, payment, IPAM, and WebSSH code; it does not
write to or push changes to the upstream GitHub repository.

## Install and configure

```bash
cp .env.example .env
# Set DISCORD_TOKEN and a real MAIN_ADMIN_ID before starting the bot.
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

For a VPS deployment, `sudo ./install.sh` installs/upgrades the systemd service
under `/opt/svm` (or `SVM_DIR` if set). The installer retains the existing `.env`,
backs up the current code and database, and validates the configured paths.

## LocalAI

Run LocalAI separately; this project does not download models or start a second
AI server. Configure the OpenAI-compatible endpoint and model in `.env`:

```dotenv
AI_ENABLED=true
AI_AGENT_ENABLED=true
AI_PROVIDER=localai
LOCALAI_BASE_URL=http://127.0.0.1:8080
LOCALAI_API_KEY=
LOCALAI_MODEL=your-installed-model-id
```

Use `python ai_agent.py models` to list actual model IDs, then set
`LOCALAI_MODEL`. Check readiness with `python ai_agent.py doctor`.

Discord commands:

- `!askai <message>`; aliases: `!aiask`, `!ask`, `!ai`
- `!askai status` and `!askai models`
- `!askai start|stop|restart` (admin-only; affects AI request handling, not SVM)
- `!askai new|clear|history|session` (history is isolated by guild and user)
- `!askai show my VPS` (reads only VPS records owned by the requesting Discord user)
- `!askai nodes` and `!askai resources` (admin-only existing read-only views)
- `!aiusage` (admin-only persisted usage totals)

When `AI_MEMORY=true`, conversation content is stored in the bot's SQLite
database and isolated by guild and user; `!askai clear` erases the current
session. Set `AI_MEMORY=false` to disable saved conversation history. Keep the
database and its backups private. Old sessions are removed after 90 days by
default; usage records are retained for 365 days. Set `AI_SESSION_RETENTION_DAYS`
and `AI_USAGE_RETENTION_DAYS` to change those periods.

Current AI scope is text chat and the explicit read-only routes above. The model
has no shell, file, payment, IPAM, or VPS-control tools. It cannot run generated
code or perform infrastructure changes. Streaming, image/vision, sandbox
execution, broad tool calling, and live status-channel publishing are not
implemented; the bot reports only capabilities it can verify.
If older configuration sets streaming, tool calling, file/code tools, vision, or
image-generation flags to `true`, AI status lists them as requested but
unsupported; those flags do not activate hidden behavior.

## WebSSH security

WebSSH defaults to disabled and binds to loopback when enabled. To allow a
connection, set `WEBSSH_ENABLED=true` and list exact target IP addresses in
`WEBSSH_ALLOWED_HOSTS`. The SSH host key must already be trusted in the service
user's `known_hosts`; new host keys are not accepted automatically. Keep any
remote exposure behind an authenticated HTTPS reverse proxy. Do not expose the
Flask development server directly to the Internet.

The optional post-provisioning `HOST_MOTD` command is disabled unless
`HOST_MOTD_ENABLED=true` is set explicitly.

## Installer controls

`install.sh` supports `status`, `doctor`, `ai-install`, `ai-start`, `ai-stop`,
`ai-restart`, `ai-status`, `ai-models`, and `ai-doctor`. AI start/stop controls
only gate new AI requests; they do not stop Discord, VPS, nodes, billing, or
monitoring. LocalAI itself remains a separately managed service.

Never commit `.env`, database files, logs, or backups.
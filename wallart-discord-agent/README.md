# WallArt Discord Agent (No-Install Hosting)
Run the agent on Replit/Render without installing Python locally.

## Files
- agent_importer.py
- blueprint.yaml
- .env.example
- requirements.txt

## Replit
1) Create Repl -> Python -> upload all files (or drag&drop this zip).
2) Add Secrets/Environment:
   - DISCORD_TOKEN, GUILD_ID, AGENT_SOURCE_URL, AGENT_POLL_SEC=900
3) Click Run; watch logs for "Ready as ...".
4) In Discord: /dryrun -> /sync.

## Render
Build: `pip install -r requirements.txt`
Start: `python agent_importer.py`
Add env vars in the dashboard.

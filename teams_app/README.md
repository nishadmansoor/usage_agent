# Teams app package

This folder is the **Teams app** that puts the bot into Microsoft Teams. Zipping
`manifest.json` + the two icons produces the installable package.

```
teams_app/
├── manifest.json   # app + bot definition
├── color.png       # 192x192 icon (required)
└── outline.png     # 32x32 transparent icon (required)
```

## Before you zip
1. **Create an Azure Bot resource** (Azure Portal → "Azure Bot"). Choose an app
   type (single-tenant is typical for internal), generate a **client secret**, and
   note the **Microsoft App ID**, **secret**, and **tenant ID**.
2. In `manifest.json`, replace **`REPLACE_WITH_AZURE_BOT_APP_ID`** (the `botId`)
   with that App ID. (The `id` field at the top is the *app* GUID — keep it unique;
   the placeholder there is fine to reuse for a single internal app, or regenerate.)
3. Make sure `color.png` (192×192) and `outline.png` (32×32, transparent) are
   present in this folder. Any brand icons work; swap in EisnerAmper artwork when
   you have it.

## Zip it
From this folder — zip the **contents**, not the folder itself (manifest.json must
be at the root of the zip):
```powershell
Compress-Archive -Path manifest.json,color.png,outline.png -DestinationPath usage_agent_teams.zip -Force
```

## Host the bot (pick one)
The bot must be reachable at `https://<host>/api/messages`, and its process must
have the same `MicrosoftAppId`/`MicrosoftAppPassword`/tenant set (BotConfig reads
them from env):
- **Pilot:** run `python -m bot` locally (on VPN so it reaches the data) and expose
  it with a dev tunnel — `devtunnel host -p 3978 --allow-anonymous` — then set the
  Azure Bot's **Messaging endpoint** to `https://<tunnel>/api/messages`.
- **Production:** deploy to Azure App Service / Container Apps, VNet-integrated (to
  reach the private Foundry/Power BI/Fabric endpoints) with a managed identity;
  set the messaging endpoint to the app's public URL + `/api/messages`.

## Enable Teams + install
1. On the Azure Bot resource: **Channels → add Microsoft Teams**.
2. In Teams: **Apps → Manage your apps → Upload a custom app** → upload
   `usage_agent_teams.zip`. (If that option is missing, your Teams admin must allow
   custom-app upload, or publish the app via the Teams admin center.)
3. Add the bot to a channel (@mention it) or open it as a personal chat.

## Test
DM the bot or @mention it: *"how much did we spend last week?"* — it should reply
in chat (plain text), identical to the CLI for the same question.

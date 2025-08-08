# HomeGPT
An addon for Home Assistant giving you a personal ai that can monitor, control and guard your home
### Quick start

1. Create a new private add‑on repo (or add this under your existing add‑on repo root) with the folder `homegpt/` and the files above.
2. In Home Assistant: **Settings → Add‑ons → Add-on Store → ⋮ → Repositories →** add your repo URL.
3. Install **HomeGPT** add‑on.
4. Open **Configuration** for the add‑on and set:
   - `openai_api_key`
   - `mode`: `passive` to start, then try `active`
   - `control_allowlist`: list of entity_ids you explicitly allow HomeGPT to control
   - `dry_run`: keep `true` at first to audit actions via persistent notifications
5. **Start** the add‑on. You’ll see daily summaries at the configured `summarize_time` and real‑time action proposals/notifications.

### Safety rails

- **Allow‑list only**: HomeGPT can only act on explicitly allow‑listed `entity_id`s.
- **Rate limits**: cap actions/hour to avoid runaway loops.
- **Dry‑run** mode by default: inspect the plan before enabling writes.
- **Notifications**: every planned/executed action is announced via persistent notifications.

### Extending

- Add an **ingress UI** (FastAPI/Starlette) to display recent events, actions, and tweak settings.
- Add a **feedback loop**: thumbs‑up/down on actions to fine‑tune prompts.
- Implement **scoped triggers**: only wake the model on specific domains or significant state deltas.
- Store short‑term context in `/data/` (the add‑on’s writable volume) to persist across restarts.

### Notes for Vetle/SMARTi

- Swap the OpenAI client to your preferred model; wire in your existing subscription gate if needed.
- For presence/price‑aware behavior, include Nord Pool data and person/device_tracker entities in the prompt, then let the model propose time‑shifts for loads.

# Meowant — Home Assistant integration

Tracks a Meowant self-cleaning litter box in Home Assistant via Meowant's cloud API.

Not affiliated with Meowant. See the [repo root README](../../README.md) and [`API.md`](../../API.md) for the protocol reference and disclaimer.

## Installing

### Manual

Copy this entire `custom_components/meowant/` folder into your Home Assistant config directory, so you end up with:

```
<HA config dir>/custom_components/meowant/
```

**Restart Home Assistant**. Custom integrations are only discovered at startup.

### HACS (custom repository)

1. HACS → Integrations → ⋮ menu → **Custom repositories**
2. Add this repo's URL, category **Integration**
3. Install, then restart Home Assistant

## Setting it up

Settings → Devices & Services → **Add Integration** → search "Meowant". You'll be asked for:

- **Email** / **Password**: your AIR PET app login
- **Country**: the country you chose when signing up in the app (defaults to United States). The server looks accounts up by country, so the wrong one shows up as an invalid login.

## What you get

Nothing yet. Entities will be added once the API is mapped out.

The integration ships its own icon and logo in `brand/`. Home Assistant 2026.3+ serves these locally on the Integrations and Devices pages. HACS's downloads panel doesn't read this inline icon yet and shows "icon not available" ([hacs/integration#5223](https://github.com/hacs/integration/issues/5223)).

## Troubleshooting

If your password changes, the integration will prompt for **reauthentication** in the HA notifications area.

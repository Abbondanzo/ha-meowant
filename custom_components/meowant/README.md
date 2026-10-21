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

Everything is read-only for now: the integration never changes the box's settings or runs it.

**Per litter box** (one device, with model, serial and firmware):

| Entity | What it shows |
|---|---|
| Waste bin level / Waste bin uses | Fill % and uses since emptied (capacity and alert threshold as attributes) |
| Waste bin needs emptying | On once uses reach the box's alert threshold |
| Deodorant remaining / Deodorant expired | Days left on the deodorant block |
| Last cat visit | When; which cat, weight and time in the box as attributes |
| Last visit weight / Last visit duration | Numbers you can graph |
| Last clean | Latest scheduled clean or clean after a visit |
| Online | Whether the box is connected |
| Activity (event) | Fires `cat_visit`, `scheduled_clean`, `auto_clean` or `other` for each new record, for automations |
| Last device event, Auto-clean delay | Diagnostic |

**Per cat** (one device each, model = breed): Weight, Visits today, Time in box today. The "today" counters come straight from the app's cat profile and haven't been checked against a full day yet.

The box can only identify a cat by weight once that's set up in the app. Visits it can't match show no cat.

Polling runs every 60 seconds: about 8 small requests per box per minute.

A cat-visit automation example:

```yaml
trigger:
  - platform: state
    entity_id: event.smart_cat_litter_box_activity
condition:
  - condition: state
    entity_id: event.smart_cat_litter_box_activity
    attribute: event_type
    state: cat_visit
action:
  - action: notify.notify
    data:
      message: >
        {{ state_attr('event.smart_cat_litter_box_activity', 'cat') or 'A cat' }} used the box
        ({{ state_attr('event.smart_cat_litter_box_activity', 'weight_kg') }} kg).
```

The integration ships its own icon and logo in `brand/`. Home Assistant 2026.3+ serves these locally on the Integrations and Devices pages. HACS's downloads panel doesn't read this inline icon yet and shows "icon not available" ([hacs/integration#5223](https://github.com/hacs/integration/issues/5223)).

## Troubleshooting

If your password changes, the integration will prompt for **reauthentication** in the HA notifications area.

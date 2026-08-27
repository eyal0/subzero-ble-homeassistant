# Sub-Zero Refrigerator (BLE)

[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/eyal0/subzero-ble-homeassistant?style=for-the-badge)](https://github.com/eyal0/subzero-ble-homeassistant/releases)
[![GitHub issues](https://img.shields.io/github/issues/eyal0/subzero-ble-homeassistant?style=for-the-badge)](https://github.com/eyal0/subzero-ble-homeassistant/issues)

Home Assistant custom integration for Sub-Zero Group refrigerators over Bluetooth Low Energy. It talks to the appliance locally — no Sub-Zero cloud account is required.

Discovery looks for BLE advertisements whose local name starts with `SZG` (Sub-Zero Group). After pairing, the integration keeps a persistent GATT connection, polls about every 10 seconds, and applies live notifications (door changes, setpoint updates) when the encrypted data channel is available.

This project is not affiliated with or endorsed by Sub-Zero Group, Inc.

## Requirements

- Home Assistant with a Bluetooth adapter on the **same host** as Home Assistant (`bluetooth_adapters`)
- A Sub-Zero Group appliance that advertises as `SZG…` with Bluetooth enabled
- **Linux / BlueZ** for pairing (Home Assistant OS, Supervised, or Container on Linux). The passkey agent is BlueZ-specific.

The appliance allows **one BLE connection at a time**. Close the official Sub-Zero Group Owner app (and any ESPHome client) before using this integration, or the link will drop.

## Installation

### HACS (recommended)

This is a **custom** HACS repository (not in the default HACS store yet). You add it once, then install and update it from HACS like any other integration.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eyal0&repository=subzero-ble-homeassistant&category=integration)

Or add the repository by hand:

1. [Install HACS](https://hacs.xyz/docs/use/) if you do not already have it.
2. HACS → **Integrations** → three-dot menu → **Custom repositories**.
3. **Repository:** `https://github.com/eyal0/subzero-ble-homeassistant`
4. **Type:** Integration
5. Search for **Sub-Zero Refrigerator BLE**, download it, then **restart Home Assistant**.

After that, add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=subzero_ble)

**Settings** → **Devices & services** → **Add integration** → **Sub-Zero Refrigerator (BLE)**. Home Assistant may also prompt you when it sees an `SZG*` advertisement.

HACS uses the GitHub release tag as the installed version. Prefer a tagged release over `main` unless you are testing unreleased changes.

The integration version is stored only in `custom_components/subzero_ble/manifest.json` (`const.VERSION` reads it). Create a GitHub release with:

```bash
./scripts/release.sh patch    # or minor / major / 0.22.0
```

That bumps the manifest, commits, tags `vX.Y.Z`, and pushes to `eyal0`. The tag push creates the GitHub release. Add `--dry-run` to print the git commands without changing anything. **Actions → Release** does the same from the GitHub UI.

### Manual

Copy `custom_components/subzero_ble` into `<config>/custom_components/subzero_ble` and restart Home Assistant.

## Setup

1. Add the integration (HACS my-link above, or **Settings** → **Devices & services** → **Add integration**).
2. Select the appliance.
3. Enter the **6-digit PIN**, or leave it blank for diagnostic-only mode.

### Diagnostic-only (no PIN)

Without a PIN the integration uses the unauthenticated diagnostic channel. Refrigerator and freezer **door** sensors work. Temperatures, filters, ice-maker flags, and writes stay unavailable.

### Full access (PIN + pairing)

The same 6-digit code is used for BLE bonding (the passkey shown on the appliance) and for the protocol `unlock_channel` command.

1. Copy the PIN from the **Sub-Zero Group Owner** app, or watch the appliance display during pairing.
2. Enter that PIN during setup, or later under **Configure**.
3. The integration bonds with BlueZ (Keyboard Only I/O), reconnects so the GATT link is encrypted, then unlocks the control and data channels.

**Start pairing** asks the appliance to show the PIN on its display. That write uses the encrypted control channel, so a PIN must already be saved in **Configure**. It cannot bootstrap the first PIN by itself.

If the official app re-pairs the appliance, the PIN often rotates. Status **302** in the logs means enter the new PIN under **Configure**.

## Entities

Not every model reports every field. Missing keys stay `unknown`.

### Controls

| Entity | Description |
| --- | --- |
| Refrigerator Temperature | Fridge **setpoint** (°F), writable after pairing. Valid range is 34–42°F. |
| Freezer Temperature | Freezer **setpoint** (°F), writable after pairing. Valid range is −5–5°F. |
| Ice Maker | Normal, Max Ice, Night Ice, or Off. Writable after pairing. |
| Mode | Normal, High Usage, Short Vacation, Long Vacation, or Sabbath. Writable after pairing. |
| Air Purifier | On/off (`air_filter_on`). Writable after pairing. |
| Night Mode | On/off (`night_mode` as `1`/`0`). Different from Night Ice. Writable after pairing. |
| Humidity Control | Normal or Enhanced (`humidity_control` as `1`/`2`). Writable after pairing. |
| Start pairing | Shows the PIN on the appliance display (needs a configured PIN and a bonded link). |

These temperature entities are **setpoints**, not measured cavity temperatures. The appliance firmware does not expose live fridge/freezer temps over BLE. Values on the wire are Fahrenheit integers. Changing °C/°F on the appliance display is a local preference and does not change the BLE numbers.

### Sensors

| Entity | Description |
| --- | --- |
| Connection Status | Diagnostic: Not in range, Disconnected, Diagnostic only, Connected, Paired, or Invalid PIN |
| Water Filter Life Remaining | Percent remaining |
| Air Filter Life Remaining | Percent remaining |
| Appliance Model | Diagnostic |
| Appliance Name | Diagnostic |
| Appliance Serial | Diagnostic |
| Appliance Type | Diagnostic |
| Build Info | Diagnostic; state is `desc`, other keys are attributes |
| Door Ajar Alarm Timeout | Minutes (diagnostic) |
| Max Ice Start Time / End Time | Diagnostic text |
| Service | Diagnostic (JSON if the appliance sends a dict) |
| Active Faults | Diagnostic text of `active_faults` (empty when none) |
| Notifications | Diagnostic count of the `notifs` log; each entry is `notif_<seq>` |
| Uptime | Seconds since last power cycle (diagnostic) |
| Firmware Version | e.g. `fw 2.27 / api 5.5` (diagnostic) |

### Binary sensors

| Entity | Description |
| --- | --- |
| Refrigerator Door | Open / closed |
| Freezer Door | Open / closed |
| Ice Maker | Diagnostic; disabled by default (use the Ice Maker select) |
| Max Ice Mode | Diagnostic; disabled by default (use the Ice Maker select) |
| Night Ice Mode | Diagnostic; disabled by default (use the Ice Maker select) |
| Sabbath Mode | Diagnostic; disabled by default (use the Mode select) |
| Service Mode | Diagnostic |
| Service Required | Problem-class diagnostic |
| Active Faults | Problem-class diagnostic; on when `active_faults` is non-empty |
| Notifications | Diagnostic; on when the `notifs` event log is non-empty |
| Pairing Window | Diagnostic; on when the appliance BLE pairing window is open |

## Supported devices

Tested against a **Sub-Zero DEU2450C** (advertised as `SZG DEU2450C`, firmware 2.27 / API 5.5).

Any Sub-Zero Group appliance that advertises `SZG*` may be discovered. This integration currently exposes **refrigerator/freezer** entities. Wolf, Cove, wine-only, and other types share the same BLE protocol but will not get matching entities yet.

The protocol and many other fridge models are documented in [JonGilmore/esphome-subzero-ble](https://github.com/JonGilmore/esphome-subzero-ble).

## Known limitations

- **One BLE client.** Phone app, ESPHome, and this integration cannot share the connection.
- **Pairing is BlueZ-only.** Home Assistant Core on macOS or Windows will not run the passkey agent used here.
- **Setpoint writes** need a PIN, a bonded link, and the encrypted control channel. The appliance may acknowledge a `set` and still not apply it on some models.
- Occasional GATT drops during a poll are common (idle timeout or another client). The integration reconnects on the next update.

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| No devices found | Enable Bluetooth on the appliance, keep it in range of the HA adapter, and disconnect the phone app. |
| Doors work, nothing else | Add the 6-digit PIN under **Configure**. |
| `status 302` / invalid PIN | The PIN rotated. Copy it from the app or the display and save it again. |
| Disconnect during poll | Usually a single-slot or idle drop. If it repeats, make sure nothing else is connected. |
| Start pairing fails | Save the PIN first. `display_pin` only works after bonding. |
| Writes do nothing | Confirm pairing succeeded (filters/temps populate). Check logs for D5 errors. |

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.subzero_ble: debug
```

`custom_components.subzero_ble.ble` logs GATT TX/RX. Unlock payloads that contain the PIN are redacted.

After changing Python files in a manual install, do a **full Home Assistant restart** (reload is not enough).

## Development

The integration imports Home Assistant packages that are not installed in a normal editor Python. Current Home Assistant also needs **Python 3.14**, so `pip install homeassistant` on 3.12 will fail.

To make the language server resolve those imports, point it at a local clone of [home-assistant/core](https://github.com/home-assistant/core):

```bash
cp pyrightconfig.json.example pyrightconfig.json
```

Then edit `extraPaths` so it is the directory that contains the `homeassistant` package (the Core repo root). `pyrightconfig.json` is gitignored because that path is machine-specific.

## Brand icon (HACS and Home Assistant)

HACS and Home Assistant both look for brand images next to the integration code, not in a separate brands repo.

Put PNG files here:

```text
custom_components/subzero_ble/brand/
  icon.png          # 256×256, square, required
  icon@2x.png       # 512×512, optional but preferred
  dark_icon.png     # optional, for HA dark theme
  logo.png          # optional landscape wordmark for the config page
```

Specs from the [Home Assistant brands guide](https://github.com/home-assistant/brands):

- PNG, square **1:1**, transparent background preferred
- Trim empty padding
- Do **not** use the Home Assistant logo (that makes a custom integration look official)
- Do **not** paste Sub-Zero’s trademarked logo unless you have permission; an original fridge + BLE mark is the safe option

After the files are in a release and the integration is installed, Home Assistant 2026.3+ serves them from `custom_components/subzero_ble/brand/` on **Settings → Devices & services**. Older HACS versions may still show a placeholder in the HACS download list even when HA itself shows the icon; that is a HACS lookup issue, not a missing file.

To make an icon: export a 256×256 PNG (and 512×512 `@2x`) from Figma, Inkscape, or [RedKetchup Image Resizer](https://redketchup.io/image-resizer). If you want one generated in-repo, say so.

## HACS default store (optional)

Anyone can install this today as a custom repository. Listing it in the default HACS store is a separate PR to [hacs/default](https://github.com/hacs/default) (`integration` file, alphabetical).

hassfest and the HACS Action run on every push, pull request, nightly, and via **Actions → Validate → Run workflow**. Both must pass **without ignored checks**. After a green run, push a `vX.Y.Z` tag (or use **Actions → Release**) so a GitHub **release** is created, then open the `hacs/default` PR.

Also required on the GitHub repo itself: a **description**, **topics**, and issues enabled. The repo uses the MIT license (`LICENSE`). Brand assets are already at `custom_components/subzero_ble/brand/icon.png`.

See [Include default repositories](https://hacs.xyz/docs/publish/include/). Reviews there often take a long time; custom-repository install does not wait on that.

## Credits

BLE protocol details come from [JonGilmore/esphome-subzero-ble](https://github.com/JonGilmore/esphome-subzero-ble) ([protocol reference](https://github.com/JonGilmore/esphome-subzero-ble/blob/main/docs/ble-protocol.adoc)), reverse-engineered from the official app and live appliance traffic.

## License

[MIT](LICENSE). Use at your own risk. Not affiliated with Sub-Zero Group, Inc.

# osu! Tablet Area Calculator

A small Windows-friendly Tkinter desktop app for osu! tablet area calculations.

## Run on Windows

1. Install Python from <https://www.python.org/downloads/windows/> if you do not already have it.
2. Open PowerShell in this folder.
3. Run:

```powershell
py .\app.py
```

If `py` is not recognized, try:

```powershell
python .\app.py
```

The older launcher still works too:

```powershell
py .\osu_tablet_area_calc.py
```

## Features

- Loads OpenTabletDriver `settings.json` from common Windows paths:
  - `%LOCALAPPDATA%\OpenTabletDriver\settings.json`
  - `%APPDATA%\OpenTabletDriver\settings.json`
- Lets you browse for a custom OTD JSON config.
- Remembers the last selected OTD config path.
- Detects configured tablet/device profiles and shows them in a **Tablet profile** dropdown.
- Automatically loads a single profile, but asks you to choose when multiple profiles exist.
- Warns when a profile does not include a clear tablet/model name or may need manual verification.
- Applies calculated settings back to the selected OTD profile only.
- Creates a timestamped backup before writing, such as `settings.json.backup-YYYYMMDD-HHMMSS`.
- Can restore timestamped OTD backups after creating a new emergency backup.
- Optionally applies live through a user-selected `OpenTabletDriver.Console.exe`.
- Opens the current OTD config folder from the app.
- Keeps all loaded fields editable.
- Supports target width and target height.
- Can lock target width/height to the aspect ratio.
- Keeps the same center point when requested.
- Includes a `-20%` to `+20%` size adjustment slider in `0.1%` steps.
  The slider resizes proportionally from the current area, so `0%` exactly matches the current width and height and preserves the current aspect ratio without using the rounded aspect-ratio field.
  The slider percentage is a width/height scale. The output **Area change** is the resulting tablet area change, so `+20%` width/height becomes `+44%` area.
- Shows a compact tablet area visualizer for the current and calculated areas.
- Recalculates live as fields change.
- Rounds displayed output to 5 decimal places.
- Includes copy buttons for each output and a full result copy button.

## Build an EXE

Install PyInstaller:

```powershell
py -m pip install pyinstaller
```

Build:

```powershell
py -m PyInstaller --onefile --windowed app.py
```

The EXE will be created in `dist`.

## Applying to OTD

Use **Apply to OTD** after loading `settings.json`, selecting a tablet profile, and checking the calculated output. The app asks for confirmation, creates a backup, then updates only the selected profile's tablet area fields.

After saving, use OpenTabletDriver's reload/apply settings option if the change does not appear immediately.

For live apply, browse to your local `OpenTabletDriver.Console.exe` and enable **Apply live through OTD Console**. After saving `settings.json`, the app runs:

```powershell
OpenTabletDriver.Console.exe loadsettings "<settings.json path>"
```

After `loadsettings` succeeds, the app re-reads `settings.json` and refreshes the UI from the selected profile. The console path is remembered. If the console is missing or fails, the settings file remains saved and the manual OTD reload/apply workflow is still available. Console output containing `Unrecognized command`, `Unrecognized argument`, `error`, or `failed` is treated as a failed live apply.

Enable **Debug live apply** to include:

- Requested GUI values.
- The exact settings file path.
- OTD Console command output.

## Restoring OTD Backups

After loading an OTD `settings.json`, the **OTD Backups** section lists matching backups beside it:

```text
settings.json.backup-*
```

Use **Refresh backups** to rescan the folder. Select a backup and choose **Restore selected backup** to restore it.

The selected backup also shows a read-only preview for the currently selected tablet profile, including the backup creation time plus width, height, X, and Y values. This preview does not change the active app fields until you restore the backup.

Restore safety behavior:

- The app asks for confirmation before restoring.
- Existing backups are never deleted.
- Before replacing `settings.json`, the app creates a new emergency backup of the current file.
- If an existing backup already has the same settings values as the current `settings.json`, the app reuses that backup instead of creating a duplicate emergency backup.
- The selected backup is copied over `settings.json` with a safe temporary-file replace.
- After restore, the app re-reads `settings.json` and refreshes the tablet profile dropdown/current area fields.
- If **Apply live through OTD Console** is enabled and a valid `OpenTabletDriver.Console.exe` is selected, the app runs:

```powershell
OpenTabletDriver.Console.exe loadsettings "<settings.json path>"
```

If live reload is unavailable, the restored file remains in place and you can use OpenTabletDriver's reload/apply settings option manually.

## Area Preview

The **Area Preview** panel draws:

- Tablet boundary.
- Current area.
- New calculated area.
- Center markers for both areas.

The preview uses OpenTabletDriver's area model: `X` and `Y` are the center of the active area in millimeters. The drawn area is calculated as `left = X - width / 2`, `top = Y - height / 2`, `right = X + width / 2`, and `bottom = Y + height / 2`. When **Keep same center point** is enabled, the current and new center markers should overlap and the output `X/Y` stay unchanged.

When a resized area would exceed a detected or known tablet boundary, the app clamps the output center like OpenTabletDriver so the active area fits inside the tablet. For unknown tablets using a virtual boundary, clamping is not applied.

For supported tablet models, the preview uses a fixed max active area as the tablet boundary. Known models are loaded from `tablet_areas.json` in the same folder as the app. If full tablet boundary dimensions are detected directly from the loaded profile, those detected dimensions are used instead. If the tablet model is unknown and full dimensions cannot be detected, the preview labels the boundary as **virtual boundary**. The virtual boundary is a stable comparison viewport, not the tablet's physical maximum.

Known tablets use OTD-style center clamping: the output center is clamped so the active area fits inside the tablet's max boundary. Unknown tablets using a virtual boundary are not clamped.

## Tablet Max Areas (tablet_areas.json)

`tablet_areas.json` in the project root stores the maximum active area for known tablet models. The app loads it at startup and matches the selected OTD profile name against it (case-insensitive substring match).

Default contents:

```json
{
  "Wacom CTL-470": { "width_mm": 147.2, "height_mm": 92.0 },
  "Wacom CTL-472": { "width_mm": 152.0, "height_mm": 95.0 },
  "Wacom CTL-672": { "width_mm": 216.0, "height_mm": 135.0 }
}
```

To add a tablet model, append a new entry using the display name and its max active area in millimeters:

```json
"Wacom CTL-6100WL": { "width_mm": 224.0, "height_mm": 148.0 }
```

The display name is also used as the match key (case-insensitive). Any OTD profile name that contains the display name as a substring will match. For example, `"Wacom CTL-470"` matches an OTD profile named `"Wacom CTL-470"` or `"wacom ctl-470"`.

**Known tablet** (matched from `tablet_areas.json`):
- Uses the configured `width_mm` / `height_mm` as the fixed tablet boundary in the visualizer.
- Applies OTD-style center clamping so the active area stays inside the boundary.
- Labels the boundary with the display name and dimensions.

**Unknown tablet** (no match):
- Uses a virtual boundary sized around the current area.
- Does not apply center clamping.
- Labels the boundary as **virtual boundary**.

If `tablet_areas.json` is missing or contains invalid JSON, the app still launches. A warning is printed to stderr and an empty tablet map is used, treating all tablets as unknown.

## Generating tablet_areas.json from OTD Configurations

`scripts/generate_tablet_areas_from_otd.py` builds or updates `tablet_areas.json` from a local copy of the OpenTabletDriver Configurations folder. Normal app use does not require GitHub or any internet connection — this script is an optional offline tool only.

### Get the OTD Configurations folder

Download or shallow-clone the OpenTabletDriver repository (branch `0.6.x`):

```powershell
git clone --branch 0.6.x --depth 1 https://github.com/OpenTabletDriver/OpenTabletDriver.git
```

The Configurations folder is at:

```
OpenTabletDriver\OpenTabletDriver.Configurations\Configurations
```

### Run the generator

```powershell
py scripts\generate_tablet_areas_from_otd.py "C:\path\to\OpenTabletDriver.Configurations\Configurations"
```

The script recursively scans `*.json` files in the given folder and extracts:

- `Name` — tablet display name, used as the `tablet_areas.json` key and the match key in the app
- `Specifications.Digitizer.Width` — max active width in **millimeters** (no conversion)
- `Specifications.Digitizer.Height` — max active height in **millimeters** (no conversion)

Entries are written to `tablet_areas.json` in the project root with a `"source": "opentabletdriver-config"` field so future runs can distinguish generated entries from manually edited ones.

### Merge behavior

| Existing entry | OTD dimensions | Result |
|---|---|---|
| Previously generated (`source` tag) | any | Replaced with latest OTD data |
| Manual (no `source`) | Same as OTD | Source tag added; dimensions unchanged |
| Manual (no `source`) | Different from OTD | Preserved unchanged |
| Not present | — | Added as new entry |

Use `--overwrite-manual` to replace manual entries even when their dimensions differ from OTD data.

The script prints a summary:

```
OTD folder: C:\...\Configurations
Output:     C:\...\tablet_areas.json
Scanned:    87 JSON file(s)
Valid:      87 entries extracted from OTD configs
Added:      84 new
Updated:    3 refreshed or source-tagged
Preserved:  0 manual entries kept (dimensions differ from OTD)
Skipped:    0 file(s) — no usable digitizer dimensions
Total:      87 entries written to tablet_areas.json
```

### After running

Restart the app. Tablet profiles whose OTD profile name contains a `tablet_areas.json` key (case-insensitive substring match) will use the fixed OTD boundary in the visualizer and apply OTD-style center clamping. Unknown tablets continue to use a virtual boundary.

## Included Example

Click **Load example** to fill:

- Current area: `80 x 60`
- Current center: `100.66665 / 36.17692`
- Target width: `84`
- Aspect ratio: `1.3333`

With the center kept, the output center remains `100.66665 / 36.17692`. OpenTabletDriver stores `X/Y` as center coordinates, so changing width or height does not require shifting `X/Y` to preserve the same center point.

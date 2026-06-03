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
- Includes a `-20%` to `+20%` size adjustment slider in `0.5%` steps.
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

If full tablet boundary dimensions are available from the loaded profile, or the selected profile matches a known Wacom model, the preview uses that real tablet size and labels it in the canvas. Known models include CTL-470, CTL-472, and CTL-672.

If full dimensions cannot be detected or matched, the preview labels the boundary as **virtual boundary** and uses a fallback tablet space starting at `0,0` that is large enough to contain both the current and new areas. Manual values still work without loading an OTD config, but the preview is OTD-accurate only when a real tablet boundary is detected or known.

## Included Example

Click **Load example** to fill:

- Current area: `80 x 60`
- Current center: `100.66665 / 36.17692`
- Target width: `84`
- Aspect ratio: `1.3333`

With the center kept, the output center remains `100.66665 / 36.17692`. OpenTabletDriver stores `X/Y` as center coordinates, so changing width or height does not require shifting `X/Y` to preserve the same center point.

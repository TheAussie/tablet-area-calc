import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from datetime import datetime
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "OsuTabletAreaCalc"
DISPLAY_PRECISION = 5
SLIDER_MAX_PERCENT = 20
VIRTUAL_BOUNDARY_PADDING_SCALE = 0.2
def _tablet_areas_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "tablet_areas.json"
    return Path(__file__).parent / "tablet_areas.json"


def _fmt_dim(v):
    s = f"{v:g}"
    return s if "." in s else s + ".0"


def load_tablet_areas():
    path = _tablet_areas_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for display_name, entry in raw.items():
            w = float(entry["width_mm"])
            h = float(entry["height_mm"])
            label = f"{display_name} {_fmt_dim(w)} x {_fmt_dim(h)}mm"
            result[display_name.lower()] = (w, h, label)
        return result
    except FileNotFoundError:
        print(f"[tablet_areas] {path} not found; using empty tablet map.", file=sys.stderr)
        return {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"[tablet_areas] Could not load {path}: {exc}", file=sys.stderr)
        return {}


KNOWN_TABLET_MAX_AREAS_MM = load_tablet_areas()


def parse_float(value, field_name):
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid number.") from exc


def parse_float_or_none(value):
    text = value.strip()
    if not text:
        return None
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return None


def format_number(value):
    return f"{value:.{DISPLAY_PRECISION}f}"


def format_command(command):
    return " ".join(quote_command_part(part) for part in command)


def format_command_result(command, result):
    return f"> {format_command(command)}\n{result['output'] or '(no output)'}"


def quote_command_part(part):
    text = str(part)
    if any(char.isspace() for char in text) or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def output_contains_error(output):
    error_markers = (
        "Unrecognized command",
        "Unrecognized argument",
        "error",
        "failed",
    )
    return any(marker.lower() in output.lower() for marker in error_markers)


def format_live_apply_debug(requested_area, settings_path):
    return (
        "Live apply debug\n"
        "Requested GUI values:\n"
        f"  width={format_number(requested_area['width'])}, "
        f"height={format_number(requested_area['height'])}, "
        f"x={format_number(requested_area['x'])}, "
        f"y={format_number(requested_area['y'])}\n"
        f"Settings file: {settings_path}"
    )


def format_profile_area(profile):
    area = profile["area"]
    return (
        f"{profile['name']}: "
        f"{format_number(area['width'])} x {format_number(area['height'])} "
        f"@ center X {format_number(area['offset_x'])}, Y {format_number(area['offset_y'])}"
    )


def format_backup_created_at(backup_path):
    match = re.search(r"\.backup-(\d{8})-(\d{6})", backup_path.name)
    if match:
        created_at = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        return created_at.strftime("%Y-%m-%d %H:%M:%S")

    modified_at = datetime.fromtimestamp(backup_path.stat().st_mtime)
    return modified_at.strftime("%Y-%m-%d %H:%M:%S")


def concise_console_output(output):
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""

    command_mismatch = any(
        marker.lower() in output.lower()
        for marker in ("Unrecognized command", "Unrecognized argument")
    )
    if not command_mismatch:
        return "\n".join(lines)

    stop_markers = ("Usage:", "Description:", "Commands:", "Options:")
    trimmed = []
    for line in lines:
        if any(line.strip().startswith(marker) for marker in stop_markers):
            break
        trimmed.append(line)

    return "\n".join(trimmed[:8]) if trimmed else "\n".join(lines[:4])


def get_app_config_path():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        config_dir = Path(local_app_data) / APP_NAME
    else:
        config_dir = Path.home() / f".{APP_NAME}"
    return config_dir / "config.json"


def get_common_otd_paths():
    paths = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    app_data = os.environ.get("APPDATA")

    if local_app_data:
        paths.append(Path(local_app_data) / "OpenTabletDriver" / "settings.json")
    if app_data:
        paths.append(Path(app_data) / "OpenTabletDriver" / "settings.json")

    return paths


def first_existing_otd_path():
    for path in get_common_otd_paths():
        if path.is_file():
            return path
    return None


def extract_tablet_profiles(data):
    """Collect tablet profiles without silently choosing between them."""
    profiles = []
    seen = set()

    def add_profile(name, area, source, warning=""):
        normalized = normalize_area(area)
        signature = (
            source,
            normalized["width"],
            normalized["height"],
            normalized["offset_x"],
            normalized["offset_y"],
        )
        if signature in seen:
            return
        seen.add(signature)

        profile_number = len(profiles) + 1
        label_name = name or f"Tablet profile {profile_number}"
        if not name:
            warning = warning or "This profile did not include a tablet/model name; verify it matches your device."
        label = (
            f"{label_name} - "
            f"{format_number(normalized['width'])} x {format_number(normalized['height'])}"
        )
        profiles.append(
            {
                "label": label,
                "name": label_name,
                "area": normalized,
                "raw_area": area,
                "source": source,
                "warning": warning,
            }
        )

    def walk(value, path="settings", inherited_name=""):
        if isinstance(value, dict):
            profile_name = profile_name_from(value) or inherited_name

            absolute_settings = value.get("AbsoluteModeSettings")
            if isinstance(absolute_settings, dict):
                tablet_area = absolute_settings.get("Tablet")
                if isinstance(tablet_area, dict) and is_area_dict(tablet_area):
                    add_profile(
                        profile_name,
                        tablet_area,
                        f"{path}.AbsoluteModeSettings.Tablet",
                    )

            for key, child in value.items():
                if key == "Tablet" and isinstance(child, dict) and is_area_dict(child):
                    warning = (
                        "Loaded from a Tablet area block, but no surrounding profile name "
                        "was confirmed."
                    )
                    add_profile(profile_name, child, f"{path}.Tablet", warning)
                elif key != "AbsoluteModeSettings":
                    child_name = profile_name
                    if not child_name and isinstance(child, dict):
                        child_name = profile_name_from_key(key)
                    walk(child, f"{path}.{key}", child_name)
        elif isinstance(value, list):
            for index, child in enumerate(value, start=1):
                walk(child, f"{path}[{index}]", inherited_name)

    walk(data)
    if not profiles:
        raise ValueError("Could not find any tablet profiles with area fields in this OTD file.")

    make_profile_labels_unique(profiles)
    return profiles


def make_profile_labels_unique(profiles):
    counts = {}
    for profile in profiles:
        label = profile["label"]
        counts[label] = counts.get(label, 0) + 1
        if counts[label] > 1:
            profile["label"] = f"{label} ({counts[label]})"


def profile_name_from(value):
    for key in ("Name", "ProfileName", "TabletName", "DeviceName", "DisplayName"):
        name = value.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()

    tablet = value.get("Tablet")
    if isinstance(tablet, str) and tablet.strip():
        return tablet.strip()

    device = value.get("Device")
    if isinstance(device, str) and device.strip():
        return device.strip()
    if isinstance(device, dict):
        for key in ("Name", "Tablet", "Model", "DeviceName"):
            name = device.get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()

    return ""


def profile_name_from_key(key):
    generic_keys = {
        "profiles",
        "settings",
        "devices",
        "tabletsettings",
        "tabletsettingslist",
        "bindings",
        "filters",
    }
    if key.lower() in generic_keys:
        return ""
    return str(key).strip()


def is_area_dict(value):
    width = get_first_key(value, ("Width", "width"))
    height = get_first_key(value, ("Height", "height"))
    x_value = get_first_key(value, ("X", "x", "XOffset", "xOffset", "xoffset"))
    y_value = get_first_key(value, ("Y", "y", "YOffset", "yOffset", "yoffset"))
    return all(is_number(item) for item in (width, height, x_value, y_value))


def normalize_area(value):
    return {
        "width": float(get_first_key(value, ("Width", "width"))),
        "height": float(get_first_key(value, ("Height", "height"))),
        "offset_x": float(get_first_key(value, ("X", "x", "XOffset", "xOffset", "xoffset"))),
        "offset_y": float(get_first_key(value, ("Y", "y", "YOffset", "yOffset", "yoffset"))),
    }


def get_first_key(value, keys):
    for key in keys:
        if key in value:
            return value[key]
    return None


def first_existing_key(value, keys):
    for key in keys:
        if key in value:
            return key
    return None


def is_number(value):
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


class TabletAreaCalculator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("osu! Tablet Area Calculator")
        self.resizable(False, False)
        self.configure(bg="#101216")

        self.config_path = get_app_config_path()
        self.inputs = {}
        self.results = {}
        self.last_valid = {}
        self.updating = False
        self.calculation_valid = False
        self.visualizer_canvas = None
        self.otd_data = None
        self.loaded_config_path = None
        self.collapsible_sections = {}

        self.otd_path = tk.StringVar()
        self.console_path = tk.StringVar()
        self.profile_var = tk.StringVar()
        self.backup_var = tk.StringVar()
        self.backup_preview_text = tk.StringVar(value="Select a backup to preview its tablet area.")
        self.loaded_path = tk.StringVar(value="No OTD config loaded.")
        self.tablet_profiles = []
        self.backup_paths = {}
        self.apply_live = tk.BooleanVar(value=False)
        self.debug_live_apply = tk.BooleanVar(value=False)
        self.create_backup = tk.BooleanVar(value=True)
        self.live_apply_running = False
        self.keep_center = tk.BooleanVar(value=True)
        self.lock_aspect_ratio = tk.BooleanVar(value=True)
        self.percent_adjustment = tk.DoubleVar(value=0)
        self.percent_text = tk.StringVar(value="+0.0%")
        self.no_change_text = tk.StringVar(value="")
        self.bounds_warning_text = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="Ready.")

        self.load_saved_config_path()
        self.apply_dark_theme()
        self.build_daily_ui()
        self.bind("<Return>", lambda _event: self.calculate())

        if not self.otd_path.get():
            detected_path = first_existing_otd_path()
            if detected_path:
                self.otd_path.set(str(detected_path))

    def apply_dark_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        bg = "#101216"
        panel = "#181b21"
        field = "#232832"
        text = "#edf1f7"
        muted = "#aab2c0"
        accent = "#67d0ff"
        border = "#303744"

        style.configure(".", background=bg, foreground=text, font=("Segoe UI", 9))
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("Muted.TLabel", background=bg, foreground=muted)
        style.configure("Status.TLabel", background=bg, foreground=accent)
        style.configure("Badge.TLabel", background=bg, foreground="#9ee493", font=("Segoe UI", 9, "bold"))
        style.configure("Warning.TLabel", background=bg, foreground="#ffb86b")
        style.configure("Section.TLabel", background=bg, foreground=muted, font=("Segoe UI", 8, "bold"))
        style.configure(
            "TLabelframe",
            background=panel,
            foreground=text,
            bordercolor=border,
            relief="solid",
        )
        style.configure("TLabelframe.Label", background=panel, foreground=text)
        style.configure(
            "TEntry",
            fieldbackground=field,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map("TEntry", fieldbackground=[("readonly", "#1b1f27")])
        style.configure(
            "TButton",
            background="#27303d",
            foreground=text,
            bordercolor=border,
            padding=(9, 5),
        )
        style.configure(
            "Primary.TButton",
            background="#2f6076",
            foreground=text,
            bordercolor="#67d0ff",
            padding=(10, 5),
        )
        style.configure("Action.TButton", padding=(12, 5))
        style.map(
            "TButton",
            background=[("active", "#344154"), ("pressed", "#1f2631")],
            foreground=[("disabled", "#6d7480")],
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#36748f"), ("pressed", "#244b5d"), ("disabled", "#27303d")],
            foreground=[("disabled", "#6d7480")],
        )
        style.configure("TCheckbutton", background=panel, foreground=text)
        style.map("TCheckbutton", background=[("active", panel)])
        style.configure(
            "TCombobox",
            fieldbackground=field,
            background=field,
            foreground=text,
            arrowcolor=text,
            bordercolor=border,
            selectbackground=field,
            selectforeground=text,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", field), ("disabled", "#1b1f27")],
            foreground=[("readonly", text), ("disabled", "#6d7480")],
            selectbackground=[("readonly", field)],
            selectforeground=[("readonly", text)],
        )
        self.option_add("*TCombobox*Listbox.background", field)
        self.option_add("*TCombobox*Listbox.foreground", text)
        self.option_add("*TCombobox*Listbox.selectBackground", "#344154")
        self.option_add("*TCombobox*Listbox.selectForeground", text)
        style.configure("Horizontal.TScale", background=panel, troughcolor="#242a34")

    def build_daily_ui(self):
        main = ttk.Frame(self, padding=14)
        main.grid(row=0, column=0, sticky="nsew")

        otd_frame = ttk.LabelFrame(main, text="OpenTabletDriver profile", padding=10)
        otd_frame.grid(row=0, column=0, sticky="ew")
        otd_frame.columnconfigure(2, weight=1)

        ttk.Button(otd_frame, text="Load from OTD", command=self.load_from_otd).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Label(otd_frame, text="Tablet profile").grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.profile_combo = ttk.Combobox(
            otd_frame,
            textvariable=self.profile_var,
            state="readonly",
            width=40,
        )
        self.profile_combo.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)
        self.apply_button = ttk.Button(
            otd_frame,
            text="Apply to OTD",
            command=self.apply_to_otd,
            state="disabled",
            style="Primary.TButton",
        )
        self.apply_button.grid(row=0, column=3, sticky="ew")
        ttk.Checkbutton(
            otd_frame,
            text="Create backup before writing settings",
            variable=self.create_backup,
        ).grid(row=1, column=3, sticky="e", pady=(4, 0))
        ttk.Label(otd_frame, textvariable=self.loaded_path, style="Muted.TLabel").grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Label(main, text="Advanced", style="Section.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(10, 0),
        )

        config_frame = self.add_collapsible_section(main, "OTD config path", 2)
        config_frame.columnconfigure(1, weight=1)
        ttk.Label(config_frame, text="Path").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(config_frame, textvariable=self.otd_path, width=46).grid(
            row=0,
            column=1,
            sticky="ew",
        )
        ttk.Button(config_frame, text="Browse", command=self.browse_otd_config).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        ttk.Button(config_frame, text="Load", command=self.load_selected_otd_config).grid(
            row=0,
            column=3,
            padx=(8, 0),
        )
        ttk.Button(
            config_frame,
            text="Open OTD config folder",
            command=self.open_otd_config_folder,
        ).grid(row=1, column=1, columnspan=3, sticky="e", pady=(8, 0))

        backup_frame = self.add_collapsible_section(main, "Backups & restore", 3)
        backup_frame.columnconfigure(1, weight=1)
        ttk.Label(backup_frame, text="Backup").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.backup_combo = ttk.Combobox(
            backup_frame,
            textvariable=self.backup_var,
            state="readonly",
            width=46,
        )
        self.backup_combo.grid(row=0, column=1, sticky="ew")
        self.backup_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_backup_selected())
        ttk.Button(backup_frame, text="Refresh backups", command=self.refresh_backups).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        self.restore_button = ttk.Button(
            backup_frame,
            text="Restore selected backup",
            command=self.restore_selected_backup,
            state="disabled",
        )
        self.restore_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(
            backup_frame,
            textvariable=self.backup_preview_text,
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        console_frame = self.add_collapsible_section(main, "Live apply & debug", 4)
        console_frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            console_frame,
            text="Apply live through OTD Console",
            variable=self.apply_live,
            command=self.update_apply_button_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            console_frame,
            text="Debug live apply",
            variable=self.debug_live_apply,
        ).grid(row=0, column=2, sticky="e")
        ttk.Label(console_frame, text="Console exe").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        ttk.Entry(console_frame, textvariable=self.console_path, width=46).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(console_frame, text="Browse", command=self.browse_console_exe).grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=(8, 0),
        )

        input_frame = ttk.LabelFrame(main, text="Current Area", padding=10)
        input_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        input_frame.columnconfigure(1, weight=1)
        input_frame.columnconfigure(3, weight=1)
        self.add_entry(input_frame, "Current width", "old_width", 0, 0)
        self.add_entry(input_frame, "Current height", "old_height", 0, 2)
        self.add_entry(input_frame, "Current center X", "old_offset_x", 1, 0)
        self.add_entry(input_frame, "Current center Y", "old_offset_y", 1, 2)

        presets_frame = ttk.LabelFrame(main, text="Offset Presets", padding=8)
        presets_frame.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(presets_frame, text="Move area to:", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        for _col, (_label, _preset) in enumerate(
            [
                ("Left", "left"),
                ("Center X", "center_x"),
                ("Right", "right"),
                ("Top", "top"),
                ("Center Y", "center_y"),
                ("Bottom", "bottom"),
                ("Center", "center"),
            ],
            start=1,
        ):
            ttk.Button(
                presets_frame,
                text=_label,
                command=lambda p=_preset: self.apply_offset_preset(p),
            ).grid(row=0, column=_col, padx=(0, 4))

        target_frame = ttk.LabelFrame(main, text="Target Area", padding=10)
        target_frame.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        target_frame.columnconfigure(1, weight=1)
        target_frame.columnconfigure(3, weight=1)
        self.add_entry(target_frame, "Target width", "target_width", 0, 0)
        self.add_entry(target_frame, "Target height", "target_height", 0, 2)
        self.add_entry(target_frame, "Aspect ratio", "aspect_ratio", 1, 0)
        self.inputs["aspect_ratio"].insert(0, "1.3333")
        ttk.Checkbutton(
            target_frame,
            text="Lock aspect ratio",
            variable=self.lock_aspect_ratio,
            command=self.on_lock_changed,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0))

        slider_frame = ttk.Frame(target_frame, style="Card.TFrame")
        slider_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        slider_frame.columnconfigure(1, weight=1)
        ttk.Label(slider_frame, text="Size adjustment").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            slider_frame,
            from_=-20,
            to=20,
            orient="horizontal",
            variable=self.percent_adjustment,
            command=self.on_slider_change,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 10))
        ttk.Label(slider_frame, textvariable=self.percent_text, width=7).grid(row=0, column=2, sticky="e")
        ttk.Checkbutton(
            target_frame,
            text="Keep same center point",
            variable=self.keep_center,
            command=self.calculate,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = ttk.Frame(main)
        actions.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Calculate", command=self.calculate, style="Action.TButton").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(actions, text="Clear", command=self.clear, style="Action.TButton").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )
        ttk.Button(actions, text="Load example", command=self.load_example, style="Action.TButton").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(8, 0),
        )

        result_frame = ttk.LabelFrame(main, text="Output", padding=10)
        result_frame.grid(row=9, column=0, sticky="ew")
        result_frame.columnconfigure(1, weight=1)
        result_frame.columnconfigure(3, weight=1)
        self.add_result(result_frame, "New width", "new_width", 0, 0)
        self.add_result(result_frame, "New height", "new_height", 0, 2)
        self.add_result(result_frame, "New center X", "new_offset_x", 1, 0)
        self.add_result(result_frame, "New center Y", "new_offset_y", 1, 2)
        self.add_result(result_frame, "Area change", "area_change", 2, 0)
        ttk.Label(result_frame, textvariable=self.no_change_text, style="Badge.TLabel").grid(
            row=2,
            column=2,
            sticky="w",
            padx=(12, 0),
            pady=3,
        )
        ttk.Button(result_frame, text="Copy result", command=self.copy_all_results).grid(
            row=2,
            column=3,
            sticky="ew",
            padx=(12, 0),
            pady=3,
        )

        visualizer_frame = ttk.LabelFrame(main, text="Area Preview", padding=10)
        visualizer_frame.grid(row=10, column=0, sticky="ew", pady=(10, 0))
        visualizer_frame.columnconfigure(0, weight=1)
        self.visualizer_canvas = tk.Canvas(
            visualizer_frame,
            width=620,
            height=200,
            bg="#101216",
            highlightthickness=1,
            highlightbackground="#303744",
        )
        self.visualizer_canvas.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            visualizer_frame,
            textvariable=self.bounds_warning_text,
            style="Warning.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        ttk.Label(main, textvariable=self.status_text, style="Status.TLabel").grid(
            row=11,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        for key, entry in self.inputs.items():
            entry.bind("<KeyRelease>", lambda _event, name=key: self.on_input_changed(name))
            entry.bind("<FocusOut>", lambda _event, name=key: self.on_input_changed(name))
        self.redraw_visualizer()

    def add_collapsible_section(self, parent, title, row):
        wrapper = ttk.Frame(parent)
        wrapper.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        wrapper.columnconfigure(0, weight=1)

        expanded = tk.BooleanVar(value=False)
        button_text = tk.StringVar(value=f"+ {title}")
        button = ttk.Button(
            wrapper,
            textvariable=button_text,
            command=lambda: self.toggle_collapsible_section(title),
        )
        button.grid(row=0, column=0, sticky="ew")

        content = ttk.LabelFrame(wrapper, text=title, padding=10)
        content.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        content.grid_remove()
        self.collapsible_sections[title] = {
            "expanded": expanded,
            "content": content,
            "button_text": button_text,
        }
        return content

    def toggle_collapsible_section(self, title):
        section = self.collapsible_sections[title]
        expanded = not section["expanded"].get()
        section["expanded"].set(expanded)
        if expanded:
            section["content"].grid()
            section["button_text"].set(f"- {title}")
        else:
            section["content"].grid_remove()
            section["button_text"].set(f"+ {title}")

    def build_ui(self):
        main = ttk.Frame(self, padding=14)
        main.grid(row=0, column=0, sticky="nsew")

        otd_frame = ttk.LabelFrame(main, text="OpenTabletDriver Config", padding=10)
        otd_frame.grid(row=0, column=0, sticky="ew")
        otd_frame.columnconfigure(1, weight=1)

        ttk.Label(otd_frame, text="Path").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(otd_frame, textvariable=self.otd_path, width=46).grid(
            row=0,
            column=1,
            sticky="ew",
        )
        ttk.Button(otd_frame, text="Browse", command=self.browse_otd_config).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        ttk.Button(otd_frame, text="Load", command=self.load_selected_otd_config).grid(
            row=0,
            column=3,
            padx=(8, 0),
        )
        ttk.Button(otd_frame, text="Load from OTD", command=self.load_from_otd).grid(
            row=1,
            column=0,
            columnspan=1,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Label(otd_frame, text="Tablet profile").grid(
            row=1,
            column=1,
            sticky="e",
            padx=(8, 8),
            pady=(8, 0),
        )
        self.profile_combo = ttk.Combobox(
            otd_frame,
            textvariable=self.profile_var,
            state="readonly",
            width=36,
        )
        self.profile_combo.grid(
            row=1,
            column=2,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)

        otd_actions = ttk.Frame(otd_frame, style="Card.TFrame")
        otd_actions.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.apply_button = ttk.Button(
            otd_actions,
            text="Apply to OTD",
            command=self.apply_to_otd,
            state="disabled",
        )
        self.apply_button.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            otd_actions,
            text="Open OTD config folder",
            command=self.open_otd_config_folder,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(otd_frame, textvariable=self.loaded_path, style="Muted.TLabel").grid(
            row=3,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        backup_frame = ttk.LabelFrame(main, text="OTD Backups", padding=10)
        backup_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        backup_frame.columnconfigure(1, weight=1)

        ttk.Label(backup_frame, text="Backup").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.backup_combo = ttk.Combobox(
            backup_frame,
            textvariable=self.backup_var,
            state="readonly",
            width=46,
        )
        self.backup_combo.grid(row=0, column=1, sticky="ew")
        self.backup_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_backup_selected())
        ttk.Button(backup_frame, text="Refresh backups", command=self.refresh_backups).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        self.restore_button = ttk.Button(
            backup_frame,
            text="Restore selected backup",
            command=self.restore_selected_backup,
            state="disabled",
        )
        self.restore_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(
            backup_frame,
            textvariable=self.backup_preview_text,
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        console_frame = ttk.LabelFrame(main, text="Optional Live Apply", padding=10)
        console_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        console_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            console_frame,
            text="Apply live through OTD Console",
            variable=self.apply_live,
            command=self.update_apply_button_state,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            console_frame,
            text="Debug live apply",
            variable=self.debug_live_apply,
        ).grid(row=0, column=2, sticky="e")
        ttk.Label(console_frame, text="Console exe").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        ttk.Entry(console_frame, textvariable=self.console_path, width=46).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(console_frame, text="Browse", command=self.browse_console_exe).grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=(8, 0),
        )

        input_frame = ttk.LabelFrame(main, text="Current Area", padding=10)
        input_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        input_frame.columnconfigure(1, weight=1)
        input_frame.columnconfigure(3, weight=1)

        self.add_entry(input_frame, "Current width", "old_width", 0, 0)
        self.add_entry(input_frame, "Current height", "old_height", 0, 2)
        self.add_entry(input_frame, "Current center X", "old_offset_x", 1, 0)
        self.add_entry(input_frame, "Current center Y", "old_offset_y", 1, 2)

        target_frame = ttk.LabelFrame(main, text="Target Area", padding=10)
        target_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        target_frame.columnconfigure(1, weight=1)
        target_frame.columnconfigure(3, weight=1)

        self.add_entry(target_frame, "Target width", "target_width", 0, 0)
        self.add_entry(target_frame, "Target height", "target_height", 0, 2)
        self.add_entry(target_frame, "Aspect ratio", "aspect_ratio", 1, 0)
        self.inputs["aspect_ratio"].insert(0, "1.3333")

        ttk.Checkbutton(
            target_frame,
            text="Lock aspect ratio",
            variable=self.lock_aspect_ratio,
            command=self.on_lock_changed,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0))

        slider_frame = ttk.Frame(target_frame, style="Card.TFrame")
        slider_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        slider_frame.columnconfigure(1, weight=1)

        ttk.Label(slider_frame, text="Size adjustment").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            slider_frame,
            from_=-20,
            to=20,
            orient="horizontal",
            variable=self.percent_adjustment,
            command=self.on_slider_change,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 10))
        ttk.Label(slider_frame, textvariable=self.percent_text, width=7).grid(
            row=0,
            column=2,
            sticky="e",
        )

        ttk.Checkbutton(
            target_frame,
            text="Keep same center point",
            variable=self.keep_center,
            command=self.calculate,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = ttk.Frame(main)
        actions.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Calculate", command=self.calculate).grid(row=0, column=0)
        ttk.Button(actions, text="Clear", command=self.clear).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Load example", command=self.load_example).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )

        result_frame = ttk.LabelFrame(main, text="Output", padding=10)
        result_frame.grid(row=6, column=0, sticky="ew")
        result_frame.columnconfigure(1, weight=1)
        result_frame.columnconfigure(4, weight=1)

        self.add_result(result_frame, "New width", "new_width", 0, 0)
        self.add_result(result_frame, "New height", "new_height", 0, 3)
        self.add_result(result_frame, "New center X", "new_offset_x", 1, 0)
        self.add_result(result_frame, "New center Y", "new_offset_y", 1, 3)
        self.add_result(result_frame, "Area change", "area_change", 2, 0)
        ttk.Button(result_frame, text="Copy result", command=self.copy_all_results).grid(
            row=2,
            column=3,
            columnspan=3,
            sticky="ew",
            padx=(12, 0),
            pady=3,
        )

        visualizer_frame = ttk.LabelFrame(main, text="Area Preview", padding=10)
        visualizer_frame.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        visualizer_frame.columnconfigure(0, weight=1)
        self.visualizer_canvas = tk.Canvas(
            visualizer_frame,
            width=620,
            height=180,
            bg="#101216",
            highlightthickness=1,
            highlightbackground="#303744",
        )
        self.visualizer_canvas.grid(row=0, column=0, sticky="ew")

        example = (
            "Example: 80 x 60, center 100.66665 / 36.17692, "
            "target width 84, ratio 1.3333."
        )
        ttk.Label(main, text=example, style="Muted.TLabel", wraplength=620).grid(
            row=8,
            column=0,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Label(main, textvariable=self.status_text, style="Status.TLabel").grid(
            row=9,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        for key, entry in self.inputs.items():
            entry.bind("<KeyRelease>", lambda _event, name=key: self.on_input_changed(name))
            entry.bind("<FocusOut>", lambda _event, name=key: self.on_input_changed(name))
        self.redraw_visualizer()

    def add_entry(self, parent, label, key, row, column):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=3)
        entry = ttk.Entry(parent, width=18)
        entry.grid(row=row, column=column + 1, sticky="ew", pady=3)
        self.inputs[key] = entry

    def add_result(self, parent, label, key, row, column):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=3)
        value = tk.StringVar(value="")
        output = ttk.Entry(parent, textvariable=value, width=18, state="readonly")
        output.grid(row=row, column=column + 1, sticky="ew", pady=3)
        self.results[key] = value

    def on_input_changed(self, key):
        if self.updating:
            return

        try:
            if key in ("old_width", "old_height") and self.percent_adjustment.get() != 0:
                self.update_target_area_from_slider()
            elif key == "target_width" and self.lock_aspect_ratio.get():
                self.update_target_height_from_width()
            elif key == "target_height" and self.lock_aspect_ratio.get():
                self.update_target_width_from_height()
            elif key == "aspect_ratio" and self.lock_aspect_ratio.get():
                self.update_target_height_from_width()
            self.calculate()
        except ValueError as exc:
            self.status_text.set(str(exc))
            self.redraw_visualizer()

    def on_lock_changed(self):
        if self.lock_aspect_ratio.get():
            try:
                if self.percent_adjustment.get() != 0:
                    self.update_target_area_from_slider()
                else:
                    self.update_target_height_from_width()
            except ValueError as exc:
                self.status_text.set(str(exc))
        self.calculate()

    def update_target_height_from_width(self):
        width = parse_float(self.inputs["target_width"].get(), "Target width")
        ratio = parse_float(self.inputs["aspect_ratio"].get(), "Aspect ratio")
        if ratio <= 0:
            raise ValueError("Aspect ratio must be greater than zero.")
        self.set_entry("target_height", format_number(width / ratio))

    def update_target_width_from_height(self):
        height = parse_float(self.inputs["target_height"].get(), "Target height")
        ratio = parse_float(self.inputs["aspect_ratio"].get(), "Aspect ratio")
        if ratio <= 0:
            raise ValueError("Aspect ratio must be greater than zero.")
        self.set_entry("target_width", format_number(height * ratio))

    def on_slider_change(self, _value):
        percent = round(self.percent_adjustment.get() * 10) / 10
        self.percent_adjustment.set(percent)
        self.percent_text.set(f"{percent:+.1f}%")

        try:
            self.update_target_area_from_slider()
            self.calculate()
        except ValueError as exc:
            self.status_text.set(str(exc))

    def update_target_area_from_slider(self):
        percent = self.percent_adjustment.get()
        old_width = parse_float(self.inputs["old_width"].get(), "Current width")
        old_height = parse_float(self.inputs["old_height"].get(), "Current height")
        if old_width <= 0 or old_height <= 0:
            raise ValueError("Current width and height must be greater than zero.")

        scale = 1 + percent / 100
        new_width = old_width * scale
        new_height = old_height * scale
        self.set_entry("target_width", format_number(new_width))
        self.set_entry("target_height", format_number(new_height))

    def calculate(self):
        try:
            old_width = parse_float(self.inputs["old_width"].get(), "Current width")
            old_height = parse_float(self.inputs["old_height"].get(), "Current height")
            old_offset_x = parse_float(self.inputs["old_offset_x"].get(), "Current center X")
            old_offset_y = parse_float(self.inputs["old_offset_y"].get(), "Current center Y")
            target_width = parse_float(self.inputs["target_width"].get(), "Target width")
            target_height = parse_float(self.inputs["target_height"].get(), "Target height")

            if old_width <= 0 or old_height <= 0:
                raise ValueError("Current width and height must be greater than zero.")
            if target_width <= 0 or target_height <= 0:
                raise ValueError("Target width and height must be greater than zero.")

            if self.keep_center.get():
                new_offset_x = old_offset_x
                new_offset_y = old_offset_y
            else:
                new_offset_x = old_offset_x
                new_offset_y = old_offset_y
            new_offset_x, new_offset_y, center_was_clamped = self.clamp_center_for_selected_tablet(
                new_offset_x,
                new_offset_y,
                target_width,
                target_height,
            )

            old_area = old_width * old_height
            new_area = target_width * target_height
            area_change = ((new_area - old_area) / old_area) * 100

            self.results["new_width"].set(format_number(target_width))
            self.results["new_height"].set(format_number(target_height))
            self.results["new_offset_x"].set(format_number(new_offset_x))
            self.results["new_offset_y"].set(format_number(new_offset_y))
            self.results["area_change"].set(f"{format_number(area_change)}%")
            self.no_change_text.set("No change" if abs(area_change) < 0.000005 else "")
            self.calculation_valid = True
            if center_was_clamped:
                self.status_text.set("Center adjusted to fit tablet bounds.")
            else:
                self.status_text.set("Calculated.")
        except ValueError as exc:
            self.calculation_valid = False
            self.no_change_text.set("")
            self.status_text.set(str(exc))
        self.redraw_visualizer()
        self.update_apply_button_state()

    def apply_offset_preset(self, preset):
        try:
            area_width = parse_float(self.inputs["old_width"].get(), "Current width")
            area_height = parse_float(self.inputs["old_height"].get(), "Current height")
        except ValueError as exc:
            self.status_text.set(f"offset-preset: need valid area dimensions — {exc}")
            return

        tablet_area = self.get_selected_tablet_full_area_mm()
        if not tablet_area:
            self.status_text.set("offset-preset: no tablet dimensions known — load an OTD profile first.")
            return

        tablet_w = tablet_area["width"]
        tablet_h = tablet_area["height"]
        current_cx = parse_float_or_none(self.inputs["old_offset_x"].get())
        current_cy = parse_float_or_none(self.inputs["old_offset_y"].get())
        if current_cx is None:
            current_cx = tablet_w / 2
        if current_cy is None:
            current_cy = tablet_h / 2

        if preset == "left":
            new_cx = area_width / 2
            new_cy = current_cy
        elif preset == "right":
            new_cx = tablet_w - area_width / 2
            new_cy = current_cy
        elif preset == "top":
            new_cx = current_cx
            new_cy = area_height / 2
        elif preset == "bottom":
            new_cx = current_cx
            new_cy = tablet_h - area_height / 2
        elif preset == "center_x":
            new_cx = tablet_w / 2
            new_cy = current_cy
        elif preset == "center_y":
            new_cx = current_cx
            new_cy = tablet_h / 2
        elif preset == "center":
            new_cx = tablet_w / 2
            new_cy = tablet_h / 2
        else:
            return

        min_cx = area_width / 2
        max_cx = tablet_w - area_width / 2
        min_cy = area_height / 2
        max_cy = tablet_h - area_height / 2

        clamped = False
        if min_cx > max_cx:
            new_cx = tablet_w / 2
            clamped = True
        else:
            clamped_cx = min(max(new_cx, min_cx), max_cx)
            if abs(clamped_cx - new_cx) > 1e-7:
                clamped = True
            new_cx = clamped_cx

        if min_cy > max_cy:
            new_cy = tablet_h / 2
            clamped = True
        else:
            clamped_cy = min(max(new_cy, min_cy), max_cy)
            if abs(clamped_cy - new_cy) > 1e-7:
                clamped = True
            new_cy = clamped_cy

        self.set_entry("old_offset_x", format_number(new_cx))
        self.set_entry("old_offset_y", format_number(new_cy))

        if clamped:
            print(f"offset-preset: {preset} clamped")
            self.status_text.set(f"offset-preset: {preset} (clamped to tablet bounds)")
        else:
            print(f"offset-preset: {preset}")
        self.calculate()

    def clamp_center_for_selected_tablet(self, center_x, center_y, width, height):
        tablet_area = self.get_selected_tablet_full_area_mm()
        if not tablet_area or tablet_area.get("source") not in ("detected_from_otd", "known_model"):
            return center_x, center_y, False

        return self.clamp_center_to_tablet_bounds(
            center_x,
            center_y,
            width,
            height,
            tablet_area["width"],
            tablet_area["height"],
        )

    @staticmethod
    def clamp_center_to_tablet_bounds(center_x, center_y, width, height, tablet_width, tablet_height):
        min_center_x = width / 2
        max_center_x = tablet_width - (width / 2)
        min_center_y = height / 2
        max_center_y = tablet_height - (height / 2)

        if min_center_x > max_center_x:
            clamped_x = tablet_width / 2
        else:
            clamped_x = min(max(center_x, min_center_x), max_center_x)

        if min_center_y > max_center_y:
            clamped_y = tablet_height / 2
        else:
            clamped_y = min(max(center_y, min_center_y), max_center_y)

        was_clamped = abs(clamped_x - center_x) > 0.0000001 or abs(clamped_y - center_y) > 0.0000001
        return clamped_x, clamped_y, was_clamped

    def get_visualizer_rects(self):
        old_width = parse_float_or_none(self.inputs["old_width"].get())
        old_height = parse_float_or_none(self.inputs["old_height"].get())
        old_offset_x = parse_float_or_none(self.inputs["old_offset_x"].get())
        old_offset_y = parse_float_or_none(self.inputs["old_offset_y"].get())
        new_width = parse_float_or_none(self.results["new_width"].get())
        new_height = parse_float_or_none(self.results["new_height"].get())
        new_offset_x = parse_float_or_none(self.results["new_offset_x"].get())
        new_offset_y = parse_float_or_none(self.results["new_offset_y"].get())

        values = [
            old_width,
            old_height,
            old_offset_x,
            old_offset_y,
            new_width,
            new_height,
            new_offset_x,
            new_offset_y,
        ]
        if any(value is None for value in values):
            return None
        if old_width <= 0 or old_height <= 0 or new_width <= 0 or new_height <= 0:
            return None

        current_rect = (
            old_offset_x - (old_width / 2),
            old_offset_y - (old_height / 2),
            old_offset_x + (old_width / 2),
            old_offset_y + (old_height / 2),
        )
        new_rect = (
            new_offset_x - (new_width / 2),
            new_offset_y - (new_height / 2),
            new_offset_x + (new_width / 2),
            new_offset_y + (new_height / 2),
        )

        return {
            "current": current_rect,
            "new": new_rect,
            "boundary": self.get_visualizer_boundary(current_rect, new_rect),
            "boundary_label": self.get_visualizer_boundary_label(),
        }

    def get_out_of_bounds_areas(self, rects, tolerance=0.0001):
        tablet_area = self.get_selected_tablet_full_area_mm()
        if not tablet_area or tablet_area.get("source") not in ("detected_from_otd", "known_model"):
            return []

        boundary = rects["boundary"]
        out_of_bounds = []
        for label, rect in (("Current area", rects["current"]), ("New area", rects["new"])):
            if (
                rect[0] < boundary[0] - tolerance
                or rect[1] < boundary[1] - tolerance
                or rect[2] > boundary[2] + tolerance
                or rect[3] > boundary[3] + tolerance
            ):
                out_of_bounds.append(label)
        return out_of_bounds

    def format_bounds_warning(self, out_of_bounds):
        if not out_of_bounds:
            return ""
        if len(out_of_bounds) == 1:
            return f"Warning: {out_of_bounds[0].lower()} extends beyond the tablet boundary."
        return "Warning: current and new areas extend beyond the tablet boundary."

    def get_visualizer_boundary(self, current_rect, new_rect):
        tablet_area = self.get_selected_tablet_full_area_mm()
        if tablet_area:
            return (0, 0, tablet_area["width"], tablet_area["height"])

        return self.get_virtual_visualizer_boundary(current_rect)

    def get_virtual_visualizer_boundary(self, current_rect):
        left, top, right, bottom = current_rect
        width = right - left
        height = bottom - top
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        max_scale = 1 + (SLIDER_MAX_PERCENT / 100)
        viewport_width = width * max_scale * (1 + VIRTUAL_BOUNDARY_PADDING_SCALE * 2)
        viewport_height = height * max_scale * (1 + VIRTUAL_BOUNDARY_PADDING_SCALE * 2)

        return (
            center_x - (viewport_width / 2),
            center_y - (viewport_height / 2),
            center_x + (viewport_width / 2),
            center_y + (viewport_height / 2),
        )

    def get_visualizer_boundary_label(self):
        tablet_area = self.get_selected_tablet_full_area_mm()
        if tablet_area:
            return f"tablet boundary: {tablet_area['label']}"
        return "virtual boundary"

    def get_visualizer_viewport(self, rects):
        boundary = rects["boundary"]
        min_x = min(boundary[0], rects["current"][0], rects["new"][0])
        min_y = min(boundary[1], rects["current"][1], rects["new"][1])
        max_x = max(boundary[2], rects["current"][2], rects["new"][2])
        max_y = max(boundary[3], rects["current"][3], rects["new"][3])

        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return boundary

        margin = max(width, height) * 0.04
        return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)

    def get_selected_tablet_full_area_mm(self):
        profile = self.selected_profile()
        if not profile:
            return None

        raw_area = profile.get("raw_area", {})
        width = get_first_key(raw_area, ("TabletWidth", "FullWidth", "MaxWidth", "BoundaryWidth"))
        height = get_first_key(raw_area, ("TabletHeight", "FullHeight", "MaxHeight", "BoundaryHeight"))
        if is_number(width) and is_number(height):
            width = float(width)
            height = float(height)
            if width > 0 and height > 0:
                return {
                    "width": width,
                    "height": height,
                    "source": "detected_from_otd",
                    "label": f"detected {format_number(width).rstrip('0').rstrip('.')}x{format_number(height).rstrip('0').rstrip('.')}mm",
                }

        model_name = profile.get("name", "").lower()
        for model_key, (width, height, label) in KNOWN_TABLET_MAX_AREAS_MM.items():
            if model_key in model_name:
                return {
                    "width": width,
                    "height": height,
                    "source": "known_model",
                    "label": label,
                }

        return None

    def redraw_visualizer(self):
        canvas = self.visualizer_canvas
        if canvas is None:
            return

        canvas.delete("all")
        canvas_width = int(canvas["width"])
        canvas_height = int(canvas["height"])
        bg = "#f4f4f4"
        boundary_color = "#b8b8b8"
        current_color = "#1d93d1"
        new_color = "#f7c948"
        warning_color = "#d97706"
        area_fill = "#78b8e6"
        label_color = "#111827"
        center_color = "#111827"
        canvas.configure(bg=bg)

        self.draw_visualizer_legend(canvas, current_color, new_color, boundary_color)

        rects = self.get_visualizer_rects()
        if rects is None:
            self.bounds_warning_text.set("")
            canvas.create_text(
                canvas_width / 2,
                canvas_height / 2,
                text="Enter valid area values to preview.",
                fill="#aab2c0",
                font=("Segoe UI", 9),
            )
            return

        drawing_top = 30
        padding = 14
        boundary = rects["boundary"]
        viewport = self.get_visualizer_viewport(rects)
        scale_data = self.get_visualizer_scale(viewport, canvas_width, canvas_height, padding, drawing_top)
        if scale_data is None:
            return

        boundary_canvas = self.map_rect_to_canvas(boundary, scale_data)
        current_canvas = self.map_rect_to_canvas(rects["current"], scale_data)
        new_canvas = self.map_rect_to_canvas(rects["new"], scale_data)
        out_of_bounds = self.get_out_of_bounds_areas(rects)
        self.bounds_warning_text.set(self.format_bounds_warning(out_of_bounds))

        self.draw_origin_marker(canvas, scale_data, boundary_color)
        canvas.create_rectangle(*boundary_canvas, outline=boundary_color, width=2, dash=(4, 3))
        current_outline = warning_color if "Current area" in out_of_bounds else current_color
        new_outline = warning_color if "New area" in out_of_bounds else new_color
        current_options = {"outline": current_outline, "fill": area_fill, "width": 2}
        new_options = {"outline": new_outline, "width": 2}
        if "Current area" in out_of_bounds:
            current_options["dash"] = (6, 3)
        if "New area" in out_of_bounds:
            new_options["dash"] = (6, 3)
        canvas.create_rectangle(*current_canvas, **current_options)
        canvas.create_rectangle(*new_canvas, **new_options)
        self.draw_center_marker(canvas, rects["current"], scale_data, current_color, center_color)
        self.draw_center_marker(canvas, rects["new"], scale_data, new_color, center_color)
        self.draw_area_labels(canvas, rects["new"], scale_data, label_color)
        self.draw_boundary_label(canvas, rects["boundary_label"], canvas_width, drawing_top, padding, label_color)
        if out_of_bounds:
            canvas.create_text(
                padding,
                canvas_height - padding,
                text="out of bounds",
                fill=warning_color,
                anchor="sw",
                font=("Segoe UI", 8, "bold"),
            )

        if self.keep_center.get():
            center_status = "Center preserved \u2713" if self.area_centers_overlap(rects) else "Centers differ"
            canvas.create_text(
                canvas_width - padding,
                canvas_height - padding,
                text=center_status,
                fill="#334155",
                anchor="se",
                font=("Segoe UI", 8),
            )

        canvas.create_rectangle(0, 0, canvas_width, canvas_height, outline="#303744")

    def draw_visualizer_legend(self, canvas, current_color, new_color, boundary_color):
        y = 14
        items = [
            ("tablet boundary", boundary_color, True),
            ("current area", current_color, False),
            ("new area", new_color, False),
        ]
        x = 12
        for label, color, dashed in items:
            if dashed:
                canvas.create_rectangle(x, y - 5, x + 18, y + 5, outline=color, dash=(3, 2))
            else:
                canvas.create_rectangle(x, y - 5, x + 18, y + 5, outline=color, width=2)
            canvas.create_text(x + 24, y, text=label, fill="#111827", anchor="w", font=("Segoe UI", 8))
            x += 130

    def area_centers_overlap(self, rects, tolerance=0.0001):
        current_x1, current_y1, current_x2, current_y2 = rects["current"]
        new_x1, new_y1, new_x2, new_y2 = rects["new"]
        current_center = ((current_x1 + current_x2) / 2, (current_y1 + current_y2) / 2)
        new_center = ((new_x1 + new_x2) / 2, (new_y1 + new_y2) / 2)
        return (
            abs(current_center[0] - new_center[0]) <= tolerance
            and abs(current_center[1] - new_center[1]) <= tolerance
        )

    def get_visualizer_scale(self, boundary, canvas_width, canvas_height, padding, drawing_top):
        min_x, min_y, max_x, max_y = boundary
        boundary_width = max_x - min_x
        boundary_height = max_y - min_y
        available_width = canvas_width - (padding * 2)
        available_height = canvas_height - drawing_top - padding
        if boundary_width <= 0 or boundary_height <= 0 or available_width <= 0 or available_height <= 0:
            return None

        scale = min(available_width / boundary_width, available_height / boundary_height)
        drawn_width = boundary_width * scale
        drawn_height = boundary_height * scale

        return {
            "min_x": min_x,
            "min_y": min_y,
            "scale": scale,
            "origin_x": padding + ((available_width - drawn_width) / 2),
            "origin_y": drawing_top + ((available_height - drawn_height) / 2),
        }

    def map_rect_to_canvas(self, rect, scale_data):
        x1, y1, x2, y2 = rect
        return (
            self.map_x_to_canvas(x1, scale_data),
            self.map_y_to_canvas(y1, scale_data),
            self.map_x_to_canvas(x2, scale_data),
            self.map_y_to_canvas(y2, scale_data),
        )

    def map_x_to_canvas(self, x, scale_data):
        return scale_data["origin_x"] + ((x - scale_data["min_x"]) * scale_data["scale"])

    def map_y_to_canvas(self, y, scale_data):
        return scale_data["origin_y"] + ((y - scale_data["min_y"]) * scale_data["scale"])

    def draw_center_marker(self, canvas, rect, scale_data, outline_color, fill_color):
        x1, y1, x2, y2 = rect
        center_x = self.map_x_to_canvas((x1 + x2) / 2, scale_data)
        center_y = self.map_y_to_canvas((y1 + y2) / 2, scale_data)
        size = 4
        canvas.create_line(center_x - size, center_y, center_x + size, center_y, fill=fill_color, width=1)
        canvas.create_line(center_x, center_y - size, center_x, center_y + size, fill=fill_color, width=1)
        canvas.create_oval(
            center_x - size,
            center_y - size,
            center_x + size,
            center_y + size,
            outline=outline_color,
        )

    def draw_origin_marker(self, canvas, scale_data, color):
        origin_x = self.map_x_to_canvas(0, scale_data)
        origin_y = self.map_y_to_canvas(0, scale_data)
        canvas.create_text(
            origin_x + 4,
            origin_y + 4,
            text="0,0",
            fill=color,
            anchor="nw",
            font=("Segoe UI", 7),
        )

    def draw_boundary_label(self, canvas, label, canvas_width, drawing_top, padding, label_color):
        canvas.create_text(
            canvas_width - padding,
            drawing_top - 8,
            text=label,
            fill=label_color,
            anchor="ne",
            font=("Segoe UI", 8),
        )

    def draw_area_labels(self, canvas, rect, scale_data, label_color):
        x1, y1, x2, y2 = rect
        canvas_rect = self.map_rect_to_canvas(rect, scale_data)
        canvas_x1, canvas_y1, canvas_x2, canvas_y2 = canvas_rect
        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            return

        center_x = (canvas_x1 + canvas_x2) / 2
        center_y = (canvas_y1 + canvas_y2) / 2
        ratio = width / height
        font = ("Segoe UI", 8)

        canvas.create_text(
            center_x,
            canvas_y1 + 10,
            text=f"{format_number(width).rstrip('0').rstrip('.')}mm",
            fill=label_color,
            font=font,
        )
        canvas.create_text(
            canvas_x1 + 10,
            center_y,
            text=f"{format_number(height).rstrip('0').rstrip('.')}mm",
            fill=label_color,
            font=font,
            angle=90,
        )
        canvas.create_text(
            center_x,
            center_y + 18,
            text=format_number(ratio).rstrip("0").rstrip("."),
            fill=label_color,
            font=font,
        )

    def update_apply_button_state(self):
        if not hasattr(self, "apply_button"):
            return

        can_apply = (
            self.loaded_config_path is not None
            and self.selected_profile() is not None
            and self.calculation_valid
            and not self.live_apply_running
        )
        self.apply_button.configure(state="normal" if can_apply else "disabled")
        self.update_restore_button_state()

    def update_restore_button_state(self):
        if not hasattr(self, "restore_button"):
            return

        selected_backup = self.backup_paths.get(self.backup_var.get())
        can_restore = (
            self.loaded_config_path is not None
            and self.loaded_config_path.is_file()
            and selected_backup is not None
            and selected_backup.is_file()
        )
        self.restore_button.configure(state="normal" if can_restore else "disabled")

    def on_backup_selected(self):
        self.update_backup_preview()
        self.update_restore_button_state()

    def browse_console_exe(self):
        initial_dir = None
        current_path = self.console_path.get().strip()
        if current_path:
            initial_dir = str(Path(current_path).expanduser().parent)

        filename = filedialog.askopenfilename(
            title="Select OpenTabletDriver.Console.exe",
            initialdir=initial_dir,
            filetypes=(("Executable files", "*.exe"), ("All files", "*.*")),
        )
        if filename:
            path = Path(filename)
            if self.is_trusted_console_path(path):
                self.console_path.set(str(path))
                self.save_app_config()
                self.status_text.set("OTD Console path selected.")
            else:
                self.status_text.set("Console executable missing or not named OpenTabletDriver.Console.exe.")

    def browse_otd_config(self):
        initial_dir = None
        current_path = self.otd_path.get().strip()
        if current_path:
            initial_dir = str(Path(current_path).expanduser().parent)

        filename = filedialog.askopenfilename(
            title="Select OpenTabletDriver settings.json",
            initialdir=initial_dir,
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if filename:
            self.otd_path.set(filename)
            self.load_otd_config(Path(filename))

    def load_from_otd(self):
        selected = self.otd_path.get().strip()
        if selected and Path(selected).is_file():
            self.load_otd_config(Path(selected))
            return

        detected_path = first_existing_otd_path()
        if detected_path:
            self.otd_path.set(str(detected_path))
            self.load_otd_config(detected_path)
            return

        common_paths = "\n".join(str(path) for path in get_common_otd_paths())
        self.status_text.set(f"No OTD settings.json found. Checked: {common_paths}")

    def load_selected_otd_config(self):
        selected = self.otd_path.get().strip()
        if selected:
            self.load_otd_config(Path(selected))
        else:
            self.load_from_otd()

    def load_otd_config(self, path):
        self.otd_data = None
        self.loaded_config_path = None
        self.tablet_profiles = []
        self.profile_var.set("")
        if hasattr(self, "profile_combo"):
            self.profile_combo["values"] = []
        self.update_apply_button_state()

        try:
            if not path.is_file():
                raise ValueError("That OTD config file does not exist.")
            with path.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            profiles = extract_tablet_profiles(data)
        except json.JSONDecodeError as exc:
            self.status_text.set(f"Could not read JSON from OTD config: {exc}")
            return
        except (OSError, ValueError) as exc:
            self.status_text.set(f"Could not load OTD config: {exc}")
            self.refresh_backups()
            return

        self.otd_data = data
        self.loaded_config_path = path
        self.tablet_profiles = profiles
        self.profile_combo["values"] = [profile["label"] for profile in profiles]
        self.otd_path.set(str(path))
        self.loaded_path.set(f"Loaded: {path}")
        self.save_app_config()
        self.refresh_backups()

        if len(profiles) == 1:
            self.profile_var.set(profiles[0]["label"])
            self.apply_tablet_profile(profiles[0])
            return

        self.profile_var.set("")
        self.status_text.set(
            f"Found {len(profiles)} tablet profiles. Select the matching Tablet profile."
        )
        self.update_apply_button_state()

    def refresh_backups(self):
        self.backup_paths = {}
        self.backup_var.set("")

        config_path = self.loaded_config_path
        if config_path is None:
            path_text = self.otd_path.get().strip()
            if path_text:
                config_path = Path(path_text)

        backups = []
        if config_path and config_path.parent.is_dir():
            backups = sorted(
                config_path.parent.glob(f"{config_path.name}.backup-*"),
                key=lambda backup: backup.stat().st_mtime,
                reverse=True,
            )

        for backup in backups:
            label = backup.name
            if label in self.backup_paths:
                label = str(backup)
            self.backup_paths[label] = backup

        if hasattr(self, "backup_combo"):
            self.backup_combo["values"] = list(self.backup_paths.keys())

        if backups:
            newest_label = next(iter(self.backup_paths))
            self.backup_var.set(newest_label)
            self.status_text.set(f"Found {len(backups)} OTD backup(s).")
        elif config_path:
            self.status_text.set("No OTD backups found beside the selected settings.json.")

        self.update_backup_preview()
        self.update_restore_button_state()

    def update_backup_preview(self):
        if not hasattr(self, "backup_preview_text"):
            return

        backup_path = self.backup_paths.get(self.backup_var.get())
        if not backup_path:
            self.backup_preview_text.set("Select a backup to preview its tablet area.")
            return
        if not backup_path.is_file():
            self.backup_preview_text.set("Selected backup file is missing.")
            return

        try:
            with backup_path.open("r", encoding="utf-8") as backup_file:
                data = json.load(backup_file)
            profiles = extract_tablet_profiles(data)
        except json.JSONDecodeError as exc:
            self.backup_preview_text.set(f"Could not preview backup JSON: {exc}")
            return
        except (OSError, ValueError) as exc:
            self.backup_preview_text.set(f"Could not preview backup: {exc}")
            return

        selected_profile = self.selected_profile()
        if not selected_profile:
            self.backup_preview_text.set(
                f"Backup area preview: {backup_path.name}\n"
                f"Created: {format_backup_created_at(backup_path)}\n"
                "Select a tablet profile to preview its backup area."
            )
            return

        matching_profile = self.find_matching_backup_profile(profiles, selected_profile)
        if not matching_profile:
            self.backup_preview_text.set(
                f"Backup area preview: {backup_path.name}\n"
                f"Created: {format_backup_created_at(backup_path)}\n"
                f"No matching profile found for {selected_profile['name']}."
            )
            return

        self.backup_preview_text.set(
            f"Backup area preview: {backup_path.name}\n"
            f"Created: {format_backup_created_at(backup_path)}\n"
            f"{format_profile_area(matching_profile)}"
        )

    def find_matching_backup_profile(self, backup_profiles, selected_profile):
        for profile in backup_profiles:
            if profile["name"] == selected_profile["name"]:
                return profile
        if len(backup_profiles) == 1:
            return backup_profiles[0]
        return None

    def restore_selected_backup(self):
        config_path = self.loaded_config_path
        backup_path = self.backup_paths.get(self.backup_var.get())

        if not config_path or not config_path.is_file():
            self.status_text.set("Load a valid settings.json before restoring a backup.")
            self.update_restore_button_state()
            return
        if not backup_path or not backup_path.is_file():
            self.status_text.set("Select a valid backup to restore.")
            self.update_restore_button_state()
            return

        existing_current_backup = self.find_existing_backup_for_current_settings(config_path)
        if existing_current_backup:
            confirmation_detail = (
                "A backup with the current settings values already exists:\n\n"
                f"{existing_current_backup.name}\n\n"
                "No duplicate emergency backup will be created. Continue?"
            )
        else:
            confirmation_detail = (
                "An emergency backup of the current settings.json will be created first. Continue?"
            )

        if not messagebox.askyesno(
            "Restore OTD backup",
            "This will replace your current settings.json with:\n\n"
            f"{backup_path.name}\n\n"
            f"{confirmation_detail}",
            parent=self,
        ):
            return

        try:
            safety_backup, safety_backup_created = self.restore_backup_file(
                config_path,
                backup_path,
                existing_current_backup,
            )
            self.refresh_loaded_settings_after_live_apply()
            self.refresh_backups()
        except (OSError, ValueError) as exc:
            self.status_text.set(f"Could not restore OTD backup: {exc}")
            messagebox.showerror("Restore failed", f"Could not restore OTD backup:\n\n{exc}", parent=self)
            return

        if self.apply_live.get() and self.is_trusted_console_path(Path(self.console_path.get().strip())):
            self.status_text.set("Backup restored. Reloading through OTD Console...")
            self.apply_live_through_console(safety_backup, "Backup restored")
            return

        safety_message = (
            f"Emergency backup created:\n{safety_backup}"
            if safety_backup_created
            else f"Existing matching backup reused:\n{safety_backup}"
        )

        if self.apply_live.get():
            self.status_text.set("Backup restored. Console executable missing, so live reload was skipped.")
            messagebox.showwarning(
                "Backup restored",
                "Backup restored, but live reload was skipped because OpenTabletDriver.Console.exe "
                "is missing or was not selected.\n\n"
                f"{safety_message}",
                parent=self,
            )
            return

        self.status_text.set(f"Backup restored. Safety backup: {safety_backup}")
        messagebox.showinfo(
            "Backup restored",
            "Backup restored into settings.json.\n\n"
            f"{safety_message}",
            parent=self,
        )

    @staticmethod
    def restore_backup_file(config_path, backup_path, existing_current_backup=None):
        if backup_path.parent != config_path.parent:
            raise ValueError("Selected backup is not beside the loaded settings.json.")
        if not backup_path.name.startswith(f"{config_path.name}.backup-"):
            raise ValueError("Selected file does not match the expected backup naming pattern.")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety_backup = existing_current_backup
        safety_backup_created = False
        temp_path = config_path.with_name(f"{config_path.name}.restore-tmp-{timestamp}")

        if safety_backup is None:
            safety_backup = config_path.with_name(f"{config_path.name}.backup-{timestamp}-emergency")
            shutil.copy2(config_path, safety_backup)
            safety_backup_created = True

        try:
            shutil.copyfile(backup_path, temp_path)
            os.replace(temp_path, config_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return safety_backup, safety_backup_created

    @staticmethod
    def find_existing_backup_for_current_settings(config_path):
        for backup in config_path.parent.glob(f"{config_path.name}.backup-*"):
            if backup.is_file() and TabletAreaCalculator.settings_files_have_same_values(config_path, backup):
                return backup
        return None

    @staticmethod
    def settings_files_have_same_values(first_path, second_path):
        try:
            with first_path.open("r", encoding="utf-8") as first_file:
                first_data = json.load(first_file)
            with second_path.open("r", encoding="utf-8") as second_file:
                second_data = json.load(second_file)
            return first_data == second_data
        except (OSError, json.JSONDecodeError):
            try:
                return first_path.read_bytes() == second_path.read_bytes()
            except OSError:
                return False

    def on_profile_selected(self, _event=None):
        profile = self.selected_profile()
        if profile:
            self.apply_tablet_profile(profile)
        self.update_backup_preview()

    def selected_profile(self):
        selected_label = self.profile_var.get()
        for profile in self.tablet_profiles:
            if profile["label"] == selected_label:
                return profile
        return None

    def apply_tablet_profile(self, profile):
        area = profile["area"]

        self.set_entry("old_width", format_number(area["width"]))
        self.set_entry("old_height", format_number(area["height"]))
        self.set_entry("old_offset_x", format_number(area["offset_x"]))
        self.set_entry("old_offset_y", format_number(area["offset_y"]))

        if self.percent_adjustment.get() != 0:
            self.update_target_area_from_slider()
        else:
            self.set_entry("target_width", format_number(area["width"]))
            self.set_entry("target_height", format_number(area["height"]))

        self.calculate()
        if profile["warning"]:
            self.status_text.set(f"Warning: {profile['warning']}")
        else:
            self.status_text.set(f"Loaded tablet profile: {profile['name']}")
        self.update_apply_button_state()

    def apply_to_otd(self):
        profile = self.selected_profile()
        if not self.loaded_config_path or not profile or not self.calculation_valid:
            self.status_text.set("Load a config, select a tablet profile, and calculate valid values first.")
            self.update_apply_button_state()
            return

        do_backup = self.create_backup.get()
        backup_note = "A timestamped backup will be created first." if do_backup else "No backup will be created (disabled by you)."
        if not messagebox.askyesno(
            "Apply to OpenTabletDriver",
            f"This will modify your OTD settings.json.\n{backup_note}\nContinue?",
            parent=self,
        ):
            return

        try:
            new_values = {
                "width": parse_float(self.results["new_width"].get(), "New width"),
                "height": parse_float(self.results["new_height"].get(), "New height"),
                "offset_x": parse_float(self.results["new_offset_x"].get(), "New center X"),
                "offset_y": parse_float(self.results["new_offset_y"].get(), "New center Y"),
            }
            backup_path = self.write_selected_profile_to_otd(profile, new_values, create_backup=do_backup)
        except (OSError, ValueError) as exc:
            self.status_text.set(f"Could not save OTD settings: {exc}")
            messagebox.showerror("Save failed", f"Could not save OTD settings:\n\n{exc}", parent=self)
            return

        if not do_backup:
            print("Backup skipped by user setting.")

        profile["area"] = new_values
        self.loaded_path.set(f"Loaded: {self.loaded_config_path}")
        if self.apply_live.get():
            self.apply_live_through_console(backup_path, "Settings saved")
            return

        self.status_text.set(
            "Settings saved. Use OpenTabletDriver's reload/apply settings option "
            "if the change does not appear immediately."
        )
        backup_detail = f"Backup created:\n{backup_path}" if backup_path else "Backup skipped by user setting."
        messagebox.showinfo(
            "Settings saved",
            "Settings saved. Use OpenTabletDriver's reload/apply settings option "
            f"if the change does not appear immediately.\n\n{backup_detail}",
            parent=self,
        )

    def apply_live_through_console(self, backup_path, action_label="Settings saved"):
        console_path = Path(self.console_path.get().strip())
        if not self.is_trusted_console_path(console_path):
            self.status_text.set("Console executable missing")
            backup_detail = f"Backup created:\n{backup_path}" if backup_path else "Backup skipped by user setting."
            messagebox.showwarning(
                "Console executable missing",
                f"{action_label}, but live apply could not run because "
                "OpenTabletDriver.Console.exe is missing or was not selected.\n\n"
                f"{backup_detail}",
                parent=self,
            )
            return

        self.live_apply_running = True
        self.update_apply_button_state()
        self.status_text.set(f"{action_label}. Applying through OTD Console...")

        profile = self.selected_profile()
        apply_context = {
            "tablet_name": profile["name"] if profile else "",
            "width": self.results["new_width"].get(),
            "height": self.results["new_height"].get(),
            "offset_x": self.results["new_offset_x"].get(),
            "offset_y": self.results["new_offset_y"].get(),
            "debug": self.debug_live_apply.get(),
        }
        thread = threading.Thread(
            target=self.run_console_apply,
            args=(console_path, self.loaded_config_path, backup_path, apply_context),
            daemon=True,
        )
        thread.start()

    def run_console_apply(self, console_path, settings_path, backup_path, apply_context):
        try:
            requested_area = {
                "width": float(apply_context["width"]),
                "height": float(apply_context["height"]),
                "x": float(apply_context["offset_x"]),
                "y": float(apply_context["offset_y"]),
            }
            debug_enabled = apply_context["debug"]
        except Exception as exc:
            self.after(0, self.finish_console_apply, False, str(exc), backup_path)
            return

        outputs = []

        if debug_enabled:
            outputs.append(format_live_apply_debug(requested_area, settings_path))

        loadsettings_command = [str(console_path), "loadsettings", str(settings_path)]
        loadsettings_result = self.run_console_command(loadsettings_command)
        outputs.append(format_command_result(loadsettings_command, loadsettings_result))

        if loadsettings_result["missing"]:
            self.after(0, self.finish_console_apply, False, "Console executable missing", backup_path)
            return

        if loadsettings_result["success"]:
            self.after(0, self.finish_console_apply, True, "\n\n".join(outputs), backup_path)
            return

        self.after(0, self.finish_console_apply, False, "\n\n".join(outputs), backup_path)

    def run_console_command(self, command):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError:
            return {"success": False, "missing": True, "output": "Console executable missing"}
        except subprocess.TimeoutExpired as exc:
            return {"success": False, "missing": False, "output": f"Console command timed out: {exc}"}
        except OSError as exc:
            return {"success": False, "missing": False, "output": str(exc)}

        output = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        output = concise_console_output(output)
        has_error_output = output_contains_error(output)
        success = result.returncode == 0 and not has_error_output
        if result.returncode != 0 and not output:
            output = f"Exit code {result.returncode}"
        return {"success": success, "missing": False, "output": output}

    def finish_console_apply(self, succeeded, output, backup_path):
        self.live_apply_running = False
        self.update_apply_button_state()

        backup_detail = f"Safety backup:\n{backup_path}" if backup_path else "Backup skipped by user setting."
        if succeeded:
            message = "Settings reloaded through OTD Console."
            detail = output or "OTD Console reported success."
            self.refresh_loaded_settings_after_live_apply()
            status_backup = f"Backup: {backup_path}" if backup_path else "Backup skipped by user setting."
            self.status_text.set(f"{message} {status_backup}")
            messagebox.showinfo(
                "Settings reloaded",
                f"{message}\n\n{backup_detail}\n\n{detail}",
                parent=self,
            )
        else:
            message = "Failed to apply"
            if output == "Console executable missing":
                message = output
            self.status_text.set(f"{message}: {output}")
            messagebox.showwarning(
                message,
                "Settings were saved, but live apply did not complete.\n\n"
                f"{backup_detail}\n\n"
                f"Details:\n{output}",
                parent=self,
            )

    def refresh_loaded_settings_after_live_apply(self):
        selected_label = self.profile_var.get()
        selected_name = self.selected_profile()["name"] if self.selected_profile() else ""
        config_path = self.loaded_config_path
        if not config_path:
            return

        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            profiles = extract_tablet_profiles(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self.status_text.set(f"Settings reloaded, but could not refresh UI: {exc}")
            return

        self.otd_data = data
        self.tablet_profiles = profiles
        self.profile_combo["values"] = [profile["label"] for profile in profiles]

        matching_profile = None
        for profile in profiles:
            if profile["label"] == selected_label or (selected_name and profile["name"] == selected_name):
                matching_profile = profile
                break
        if matching_profile is None and len(profiles) == 1:
            matching_profile = profiles[0]

        if matching_profile:
            self.profile_var.set(matching_profile["label"])
            self.apply_tablet_profile(matching_profile)

    def is_trusted_console_path(self, path):
        try:
            resolved = path.expanduser().resolve()
        except (OSError, RuntimeError):
            return False

        return (
            resolved.is_file()
            and resolved.suffix.lower() == ".exe"
            and resolved.name.lower() == "opentabletdriver.console.exe"
        )

    def write_selected_profile_to_otd(self, profile, new_values, create_backup=True):
        config_path = self.loaded_config_path
        if self.otd_data is None:
            raise ValueError("No OTD settings are loaded.")
        if not config_path or not config_path.is_file():
            raise ValueError("The loaded OTD settings file no longer exists.")

        raw_area = profile.get("raw_area")
        if not isinstance(raw_area, dict):
            raise ValueError("Selected tablet profile cannot be safely updated.")

        area_updates = {
            ("Width", "width"): new_values["width"],
            ("Height", "height"): new_values["height"],
            ("X", "x", "XOffset", "xOffset", "xoffset"): new_values["offset_x"],
            ("Y", "y", "YOffset", "yOffset", "yoffset"): new_values["offset_y"],
        }

        for keys, value in area_updates.items():
            key = first_existing_key(raw_area, keys)
            if key is None:
                raise ValueError("Selected tablet profile is missing expected area fields.")
            raw_area[key] = value

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = None
        if create_backup:
            backup_path = config_path.with_name(f"{config_path.name}.backup-{timestamp}")
            shutil.copy2(config_path, backup_path)

        temp_path = config_path.with_name(f"{config_path.name}.tmp-{timestamp}")
        try:
            with temp_path.open("w", encoding="utf-8") as config_file:
                json.dump(self.otd_data, config_file, indent=2)
                config_file.write("\n")
            os.replace(temp_path, config_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return backup_path

    def open_otd_config_folder(self):
        path_text = self.otd_path.get().strip()
        config_path = self.loaded_config_path or (Path(path_text) if path_text else None)
        if not config_path:
            self.status_text.set("No OTD config path is selected.")
            return

        folder = config_path.parent
        if not folder.is_dir():
            self.status_text.set("The OTD config folder does not exist.")
            return

        try:
            os.startfile(folder)
        except OSError as exc:
            self.status_text.set(f"Could not open OTD config folder: {exc}")

    def load_saved_config_path(self):
        try:
            if not self.config_path.is_file():
                return
            with self.config_path.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            path = data.get("otd_config_path", "")
            if path:
                self.otd_path.set(path)
            console_path = data.get("console_path", "")
            if console_path:
                self.console_path.set(console_path)
        except (OSError, json.JSONDecodeError):
            return

    def save_app_config(self):
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8") as config_file:
                json.dump(
                    {
                        "otd_config_path": self.otd_path.get(),
                        "console_path": self.console_path.get(),
                    },
                    config_file,
                    indent=2,
                )
        except OSError:
            self.status_text.set("Could not save remembered app paths.")

    def set_entry(self, key, value):
        self.updating = True
        self.inputs[key].delete(0, tk.END)
        self.inputs[key].insert(0, value)
        self.updating = False

    def copy_result(self, result):
        value = result.get()
        if not value:
            self.status_text.set("Nothing to copy yet.")
            return

        self.clipboard_clear()
        self.clipboard_append(value)
        self.status_text.set(f"Copied {value}")

    def copy_all_results(self):
        values = {
            "New width": self.results["new_width"].get(),
            "New height": self.results["new_height"].get(),
            "New center X": self.results["new_offset_x"].get(),
            "New center Y": self.results["new_offset_y"].get(),
            "Area change": self.results["area_change"].get(),
        }
        if not all(values.values()):
            self.status_text.set("Calculate first, then copy the result.")
            return

        text = "\n".join(f"{label}: {value}" for label, value in values.items())
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_text.set("Copied full result.")

    def clear(self):
        for key, entry in self.inputs.items():
            entry.delete(0, tk.END)
            if key == "aspect_ratio":
                entry.insert(0, "1.3333")
        for result in self.results.values():
            result.set("")
        self.no_change_text.set("")
        self.bounds_warning_text.set("")
        self.keep_center.set(True)
        self.lock_aspect_ratio.set(True)
        self.percent_adjustment.set(0)
        self.percent_text.set("+0.0%")
        self.profile_var.set("")
        self.status_text.set("Ready.")
        self.redraw_visualizer()

    def load_example(self):
        example_values = {
            "old_width": "80",
            "old_height": "60",
            "old_offset_x": "100.66665",
            "old_offset_y": "36.17692",
            "target_width": "84",
            "aspect_ratio": "1.3333",
        }
        for key, value in example_values.items():
            self.set_entry(key, value)
        self.keep_center.set(True)
        self.lock_aspect_ratio.set(True)
        self.percent_adjustment.set(5)
        self.percent_text.set("+5.0%")
        self.update_target_height_from_width()
        self.calculate()


if __name__ == "__main__":
    app = TabletAreaCalculator()
    app.mainloop()

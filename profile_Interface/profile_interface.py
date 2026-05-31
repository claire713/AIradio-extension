
"""
File: profile_interface.py
This script was generated using AI


A small Tkinter desktop GUI for creating and managing user profiles that are
keyed to micro:bit devices by their MAC address. All profiles are stored
together in a single JSON file (users.json) under a top-level "users" key.

Main features:
  • Add a micro:bit by entering its MAC address. The address is validated
    against the standard XX:XX:XX:XX:XX:XX format and normalised to uppercase
    with colon separators before being stored.
  • Browse all registered devices in a scrollable list, with per-device
    "Remove" buttons and a highlight showing the currently selected device.
  • Edit a profile for the selected device: a name plus two "interests" and
    two "reminders". Fields stay disabled until a device is selected.
  • "Load Defaults" fills the form with placeholder example values.
  • "Save Profile" validates that a name is present, then writes the profile
    back to users.json and confirms with a timestamped status message.

Data layout (users.json):
  {
    "users": {
      "A4:C3:F0:85:AC:01": {
        "name": "...", "interest1": "...", "interest2": "...",
        "reminder1": "...", "reminder2": "..."
      },
      ...
    }
  }

The JSON file lives at:
  <script_dir>/ai-radio/channels/audio/ai_seg/users.json

Run directly (python this_file.py) to launch the GUI.


"""




import tkinter as tk
from tkinter import messagebox, simpledialog
import json
import os
import re
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
JSON_DIR  = BASE_DIR/"ai-radio"/"channels"/"audio"/"ai_seg"
JSON_PATH = JSON_DIR/"users.json"



DEFAULT_VALUES = {
    "name":      "Default User",
    "interest1": "Technology",
    "interest2": "Music",
    "reminder1": "Check emails at 9am",
    "reminder2": "Team meeting at 3pm",
}

# ── Colours ────────────────────────────────────────────────────────────────────
BG        = "#ffffff"
PANEL     = "#f4f8fb"
DARK_BLUE = "#1a3a5c"
TEAL      = "#3dbfb0"
TEAL_LT   = "#e6f7f5"
BLACK     = "#111111"
MUTED     = "#6a8a9a"
BORDER    = "#c5d8e4"
ENTRY_BG  = "#f9fcff"

FONT_HEAD  = ("Arial", 14, "bold")
FONT_SUB   = ("Arial",  9)
FONT_SEC   = ("Arial",  9, "bold")
FONT_LABEL = ("Arial", 10)
FONT_ENTRY = ("Arial", 10)
FONT_BTN   = ("Arial",  9, "bold")

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")

def valid_mac(s): return bool(MAC_RE.match(s.strip()))
def norm_mac(s):  return s.strip().upper().replace("-", ":")

def load_data():
    """Load the unified users.json file. Returns dict with 'users' key."""
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH) as f:
            try:
                data = json.load(f)
                if "users" not in data:
                    data = {"users": data}
                return data
            except:
                pass
    return {"users": {}}

def save_data(data):
    """Save the unified users.json file."""
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)

def get_mac_list():
    """Return list of all MAC addresses from the unified file."""
    return list(load_data().get("users", {}).keys())

def get_user(mac):
    """Return user profile dict for a given MAC, or empty dict."""
    return load_data().get("users", {}).get(mac, {})

def save_user(mac, profile):
    """Save a user profile under the given MAC address."""
    data = load_data()
    data["users"][mac] = profile
    save_data(data)

def remove_user(mac):
    """Remove a user entry by MAC address."""
    data = load_data()
    data["users"].pop(mac, None)
    save_data(data)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("User Profile Manager")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.selected_mac  = tk.StringVar()
        self._active_btn   = None
        self.fields        = {}
        self.entry_widgets = []
        self.mac_buttons   = []
        self._build()
        self._center()

    def _center(self):
        self.update_idletasks()
        w  = self.winfo_reqwidth()
        h  = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self):
        tk.Frame(self, bg=DARK_BLUE, height=5).pack(fill="x")

        # ── Header ──
        hdr = tk.Frame(self, bg=BG, pady=14)
        hdr.pack(fill="x", padx=24)
        tk.Label(hdr, text="User Profile Manager",
                 bg=BG, fg=BLACK, font=FONT_HEAD).pack(anchor="w")
        tk.Label(hdr, text="Select a micro:bit device, fill in the profile, then save.",
                 bg=BG, fg=MUTED, font=FONT_SUB).pack(anchor="w")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Device section ──
        dev_outer = tk.Frame(self, bg=BG, pady=10)
        dev_outer.pack(fill="x", padx=24)

        top = tk.Frame(dev_outer, bg=BG)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="STEP 1 — Select Device (MAC Address)",
                 bg=BG, fg=DARK_BLUE, font=FONT_SEC).pack(side="left")
        tk.Button(top, text="+ Add micro:bit", font=FONT_BTN,
                  bg=DARK_BLUE, fg="white",
                  activebackground=TEAL, activeforeground="white",
                  relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                  command=self._add_device).pack(side="right")

        list_frame = tk.Frame(dev_outer, bg=PANEL,
                              highlightthickness=1,
                              highlightbackground=BORDER)
        list_frame.pack(fill="x")

        self.canvas = tk.Canvas(list_frame, bg=PANEL,
                                highlightthickness=0, height=110)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.dev_frame = tk.Frame(self.canvas, bg=PANEL)
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.dev_frame, anchor="nw")

        self.dev_frame.bind("<Configure>", self._on_dev_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._refresh_devices()

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Form section ──
        form_outer = tk.Frame(self, bg=BG, pady=10)
        form_outer.pack(fill="x", padx=24)

        tk.Label(form_outer, text="STEP 2 — Enter Profile Details",
                 bg=BG, fg=DARK_BLUE, font=FONT_SEC).pack(anchor="w", pady=(0, 8))

        fields = [
            ("name",      "Name"),
            ("interest1", "Interest 1"),
            ("interest2", "Interest 2"),
            ("reminder1", "Reminder 1"),
            ("reminder2", "Reminder 2"),
        ]
        for key, label in fields:
            row = tk.Frame(form_outer, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, width=12, anchor="w",
                     bg=BG, fg=BLACK, font=FONT_LABEL).pack(side="left")
            var = tk.StringVar()
            ent = tk.Entry(row, textvariable=var,
                           bg=ENTRY_BG, fg=BLACK, insertbackground=TEAL,
                           disabledbackground=PANEL, disabledforeground=MUTED,
                           relief="solid", bd=1, font=FONT_ENTRY,
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=TEAL, state="disabled")
            ent.pack(side="left", fill="x", expand=True, ipady=6, padx=(8, 0))
            self.fields[key] = var
            self.entry_widgets.append(ent)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Footer ──
        foot = tk.Frame(self, bg=PANEL, pady=12)
        foot.pack(fill="x", side="bottom")

        tk.Canvas(self, bg=BG, height=5, highlightthickness=0).pack(fill="x", side="bottom")
        bar = tk.Frame(self, height=5)
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=TEAL,      width=290, height=5).pack(side="left")
        tk.Frame(bar, bg=DARK_BLUE, height=5).pack(side="left", fill="x", expand=True)

        self.status_lbl = tk.Label(foot, text="Select a device to begin.",
                                    bg=PANEL, fg=MUTED, font=FONT_SUB)
        self.status_lbl.pack(fill="x", padx=18, pady=(0, 6))

        btn_row = tk.Frame(foot, bg=PANEL)
        btn_row.pack(fill="x", padx=18)

        self.default_btn = tk.Button(btn_row, text="Load Defaults",
                                      font=FONT_BTN, bg=PANEL, fg=DARK_BLUE,
                                      activebackground=TEAL_LT,
                                      activeforeground=DARK_BLUE,
                                      relief="solid", bd=1, padx=10, pady=5,
                                      cursor="hand2", state="disabled",
                                      command=self._load_defaults)
        self.default_btn.pack(side="left", padx=(0, 8))

        self.save_btn = tk.Button(btn_row, text="Save Profile",
                                   font=FONT_BTN, bg=DARK_BLUE, fg="white",
                                   activebackground=TEAL, activeforeground="white",
                                   relief="flat", bd=0, padx=14, pady=6,
                                   cursor="hand2", state="disabled",
                                   command=self._save)
        self.save_btn.pack(side="left")

    # ── Scrollable list helpers ────────────────────────────────────────────────
    def _on_dev_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _refresh_devices(self):
        for w in self.dev_frame.winfo_children():
            w.destroy()
        self.mac_buttons.clear()

        macs = get_mac_list()

        if not macs:
            tk.Label(self.dev_frame,
                     text="No devices yet. Click '+ Add micro:bit' above.",
                     bg=PANEL, fg=MUTED, font=FONT_SUB, pady=10).pack()
            return

        for mac in macs:
            row = tk.Frame(self.dev_frame, bg=PANEL)
            row.pack(fill="x", padx=8, pady=3)

            b = tk.Button(row, text=mac, font=FONT_BTN,
                          bg=PANEL, fg=BLACK, anchor="w",
                          activebackground=TEAL_LT, activeforeground=DARK_BLUE,
                          relief="flat", bd=0, padx=10, pady=6,
                          cursor="hand2", width=28,
                          command=lambda m=mac: self._select(m))
            b.pack(side="left", fill="x", expand=True)
            b.bind("<Enter>", lambda e, btn=b: btn.config(bg=TEAL_LT, fg=DARK_BLUE)
                   if btn != self._active_btn else None)
            b.bind("<Leave>", lambda e, btn=b: btn.config(
                bg=TEAL if btn == self._active_btn else PANEL,
                fg="white" if btn == self._active_btn else BLACK))

            tk.Button(row, text="Remove", font=("Arial", 8),
                      bg=PANEL, fg=MUTED,
                      activebackground="#fde8e8", activeforeground="#cc0000",
                      relief="flat", bd=0, padx=6, pady=6, cursor="hand2",
                      command=lambda m=mac: self._remove(m)).pack(side="right")

            self.mac_buttons.append((mac, b))

    # ── Logic ──────────────────────────────────────────────────────────────────
    def _select(self, mac):
        self.selected_mac.set(mac)
        for m, b in self.mac_buttons:
            b.config(bg=PANEL, fg=BLACK)
        for m, b in self.mac_buttons:
            if m == mac:
                b.config(bg=TEAL, fg="white")
                self._active_btn = b
                break

        stored = get_user(mac)
        for i, key in enumerate(["name", "interest1", "interest2",
                                   "reminder1", "reminder2"]):
            self.entry_widgets[i].config(state="normal")
            self.fields[key].set(stored.get(key, ""))

        self.save_btn.config(state="normal")
        self.default_btn.config(state="normal")
        self.status_lbl.config(
            text=f"Device: {mac}  |  {'Profile loaded.' if stored else 'New profile.'}",
            fg=DARK_BLUE)

    def _add_device(self):
        raw = simpledialog.askstring(
            "Add micro:bit",
            "Enter the micro:bit MAC address:\n(e.g. A4:C3:F0:85:AC:01)",
            parent=self)
        if not raw:
            return
        mac = norm_mac(raw)
        if not valid_mac(mac):
            messagebox.showerror("Invalid MAC Address",
                                 f"'{raw}' is not a valid MAC address.\n"
                                 "Use format: XX:XX:XX:XX:XX:XX")
            return
        if mac in get_mac_list():
            messagebox.showinfo("Already Added", f"{mac} is already in the list.")
            return
        # Add the MAC with an empty profile so it appears in the list
        save_user(mac, {})
        self._refresh_devices()
        self.status_lbl.config(text=f"Added: {mac}", fg=TEAL)

    def _remove(self, mac):
        if not messagebox.askyesno("Remove Device",
                                    f"Remove {mac} and their saved profile?"):
            return
        remove_user(mac)
        if self.selected_mac.get() == mac:
            self.selected_mac.set("")
            self._active_btn = None
            for e in self.entry_widgets:
                e.config(state="disabled")
            self.save_btn.config(state="disabled")
            self.default_btn.config(state="disabled")
            self.status_lbl.config(text="Select a device to begin.", fg=MUTED)
        self._refresh_devices()

    def _load_defaults(self):
        if not messagebox.askyesno("Load Defaults",
                                    "Overwrite current fields with default values?"):
            return
        for key, val in DEFAULT_VALUES.items():
            self.fields[key].set(val)
        self.status_lbl.config(text="Defaults loaded. Press Save to apply.", fg=TEAL)

    def _save(self):
        mac = self.selected_mac.get()
        if not mac:
            messagebox.showwarning("No Device", "Please select a device first.")
            return
        name = self.fields["name"].get().strip()
        if not name:
            messagebox.showwarning("Missing Field", "Name cannot be empty.")
            return
        profile = {
            "name":      name,
            "interest1": self.fields["interest1"].get().strip(),
            "interest2": self.fields["interest2"].get().strip(),
            "reminder1": self.fields["reminder1"].get().strip(),
            "reminder2": self.fields["reminder2"].get().strip(),
        }
        save_user(mac, profile)
        ts = datetime.now().strftime("%H:%M:%S")
        self.status_lbl.config(text=f"Saved at {ts}.", fg=DARK_BLUE)
        messagebox.showinfo("Saved",
                            f"Profile for '{name}' saved.\n\nFile: {JSON_PATH}")


if __name__ == "__main__":
    App().mainloop()


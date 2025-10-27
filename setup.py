import json
import os

print("=== ETS2 Twitch Mod Bot Setup ===")
token = input(
    "Enter your Twitch OAuth token (get it from https://twitchtokengenerator.com/): "
).strip()
channel = input("Enter your Twitch channel name: ").strip()

default_mod_path = os.path.expanduser("~/Documents/Euro Truck Simulator 2/mod")
default_profile_path = os.path.expanduser("~/Documents/Euro Truck Simulator 2/profiles")
default_steam_path = (
    "C:/Program Files (x86)/Steam/steamapps/common/Euro Truck Simulator 2"
)

mod_path = (
    input(f"Enter ETS2 mod folder path [{default_mod_path}]: ").strip()
    or default_mod_path
)
profile_path = (
    input(f"Enter ETS2 profiles folder path [{default_profile_path}]: ").strip()
    or default_profile_path
)
steam_path = (
    input(f"Enter ETS2 Steam install path [{default_steam_path}]: ").strip()
    or default_steam_path
)

config = {
    "twitch_token": token,
    "twitch_channel": channel,
    "ets2_mod_path": mod_path,
    "ets2_profile_path": profile_path,
    "ets2_steam_path": steam_path,
}

with open("config.json", "w") as cfg:
    json.dump(config, cfg, indent=4)

print("✅ Setup complete! Run 'python bot.py' to start the bot.")

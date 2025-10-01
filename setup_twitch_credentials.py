#!/usr/bin/env python3
"""
Setup script to help configure Twitch API credentials for the ETS2 Twitch Mod Bot.
"""

import json
import os

CONFIG_FILE = "config.json"

def main():
    print("🔧 ETS2 Twitch Mod Bot - Credentials Setup")
    print("=" * 50)
    
    # Load existing config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        print("❌ No config.json found. Please run setup.py first!")
        return
    
    print("\n📋 You need to get Twitch API credentials:")
    print("1. Go to: https://dev.twitch.tv/console/apps")
    print("2. Click 'Register Your Application'")
    print("3. Fill in:")
    print("   - Name: Your bot name (e.g., 'ETS2ModBot')")
    print("   - OAuth Redirect URLs: http://localhost")
    print("   - Category: Chat Bot")
    print("4. Click 'Create'")
    print("5. Click 'Manage' on your new application")
    print("6. Copy the Client ID and generate a Client Secret")
    print("\n🔑 You also need your bot's User ID:")
    print("7. Go to: https://www.streamweasels.com/twitch-tools/username-to-user-id-converter/")
    print("8. Enter your bot's username and get the User ID")
    
    print("\n" + "=" * 50)
    
    # Get credentials from user
    client_id = input("Enter your Twitch Client ID: ").strip()
    if not client_id:
        print("❌ Client ID is required!")
        return
    
    client_secret = input("Enter your Twitch Client Secret: ").strip()
    if not client_secret:
        print("❌ Client Secret is required!")
        return
    
    bot_id = input("Enter your bot's User ID: ").strip()
    if not bot_id:
        print("❌ Bot User ID is required!")
        return
    
    # Update config
    config["twitch_client_id"] = client_id
    config["twitch_client_secret"] = client_secret
    config["twitch_bot_id"] = bot_id
    
    # Save updated config
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    print("\n✅ Credentials saved to config.json!")
    print("🚀 You can now run your bot with: python bot.py")

if __name__ == "__main__":
    main()
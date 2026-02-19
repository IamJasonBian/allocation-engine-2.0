"""
Interactive setup script to configure Robinhood credentials
"""

import os


def setup_credentials():
    """Interactively set up Robinhood credentials"""
    print("Robinhood Credentials Setup")
    print("=" * 60)
    print("\nThis script will help you configure your Robinhood credentials.")
    print("Your credentials will be stored in .env file (never committed to git).\n")

    # Read existing .env if it exists
    env_path = '.env'
    env_lines = {}

    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_lines[key] = value

    # Automated account (primary)
    print("AUTOMATED TRADING ACCOUNT")
    print("-" * 60)
    auto_email = input(f"Enter automated account email [{env_lines.get('RH_AUTO_EMAIL', '')}]: ").strip()
    auto_password = input("Enter automated account password [hidden]: ").strip()

    if auto_email:
        env_lines['RH_AUTO_EMAIL'] = auto_email
    if auto_password:
        env_lines['RH_AUTO_PASSWORD'] = auto_password

    # Main account (optional)
    print("\nMAIN ACCOUNT (Optional - press Enter to skip)")
    print("-" * 60)
    main_email = input(f"Enter main account email [{env_lines.get('RH_MAIN_EMAIL', '')}]: ").strip()
    main_password = input("Enter main account password [hidden]: ").strip()

    if main_email:
        env_lines['RH_MAIN_EMAIL'] = main_email
    if main_password:
        env_lines['RH_MAIN_PASSWORD'] = main_password

    # Active account
    print("\n CONFIGURATION")
    print("-" * 60)
    active = input("Which account to use by default? (automated/main) [automated]: ").strip() or 'automated'
    env_lines['RH_ACTIVE_ACCOUNT'] = active

    # Write to .env
    with open(env_path, 'w') as f:
        f.write("# Robinhood Account Credentials\n")
        f.write("# IMPORTANT: Never commit this file to git!\n\n")

        f.write("# Main Account\n")
        f.write(f"RH_MAIN_EMAIL={env_lines.get('RH_MAIN_EMAIL', 'your_main_email@example.com')}\n")
        f.write(f"RH_MAIN_PASSWORD={env_lines.get('RH_MAIN_PASSWORD', 'your_main_password')}\n\n")

        f.write("# Automated Trading Account\n")
        f.write(f"RH_AUTO_EMAIL={env_lines.get('RH_AUTO_EMAIL', 'your_automated_email@example.com')}\n")
        f.write(f"RH_AUTO_PASSWORD={env_lines.get('RH_AUTO_PASSWORD', 'your_automated_password')}\n\n")

        f.write("# Active account selection (main or automated)\n")
        f.write(f"RH_ACTIVE_ACCOUNT={env_lines.get('RH_ACTIVE_ACCOUNT', 'automated')}\n")

    # Set restrictive permissions
    os.chmod(env_path, 0o600)

    print("\n[OK] Credentials saved to .env file")
    print("   File permissions set to 600 (owner read/write only)")
    print("\n[LOCKED] Security reminders:")
    print("   • .env file is in .gitignore (won't be committed)")
    print("   • Enable 2FA on your Robinhood accounts")
    print("   • Keep your .env file secure and never share it")
    print("\nNext steps:")
    print("   1. Test connection: python rh_auth.py")
    print("   2. Run trading bot: python trading_bot.py")
    print("=" * 60)


if __name__ == "__main__":
    setup_credentials()

#!/usr/bin/env python3
"""
Generate OWASP-recommended scrypt password hashes for Beardslee St. Research
authorized dashboard users (isamalfuqaha@gmail.com and samuelnoginsky@gmail.com).

Usage:
    python3 set_passwords.py
"""

import getpass
from security import hash_password


def main() -> None:
    print("Beardslee St. Research — Credential Hash Generator (scrypt)")
    print("Paste the generated lines directly into your server .env file.\n")

    users = [
        ("isamalfuqaha@gmail.com", "AUTH_HASH_ISAM"),
        ("samuelnoginsky@gmail.com", "AUTH_HASH_SAMUEL"),
    ]

    output_lines = []
    for email, env_var in users:
        while True:
            pw1 = getpass.getpass(f"Enter password for {email} (min 12 chars recommended): ")
            if len(pw1) < 8:
                print("  Password must be at least 8 characters.")
                continue
            pw2 = getpass.getpass(f"Confirm password for {email}: ")
            if pw1 != pw2:
                print("  Passwords do not match. Try again.")
                continue
            hashed = hash_password(pw1)
            output_lines.append(f'{env_var}="{hashed}"')
            print(f"  Generated scrypt hash for {email}.\n")
            break

    print("--- Copy into .env ---")
    for line in output_lines:
        print(line)
    print("----------------------")


if __name__ == "__main__":
    main()

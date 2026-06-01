#!/usr/bin/env python3
"""
FRCS VMS POS System - Production Ready
Fiji Revenue & Customs Service VAT Monitoring System

This is a fresh-install system. On first run with no users,
you will be redirected to the Setup Wizard to create your
first Branch + Administrator.
"""

import sys
import os

# Force UTF-8 on Windows console to avoid emoji / special char errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        os.system("chcp 65001 > nul")  # fallback


def main():
    print("\n" + "=" * 72)
    print("   FRCS VMS POS + INVENTORY + SALES + FISCAL SYSTEM")
    print("         Fiji Revenue & Customs Service - Ready")
    print("=" * 72)

    print(">>> Starting application initialization...")

    # Try to create the app with good error reporting
    try:
        print(">>> Importing create_app from app package...")
        from app import create_app
        print(">>> Calling create_app() (this is where migrations & schema upgrades happen)...")
        app = create_app()
        print(">>> create_app() completed successfully.")
    except Exception as e:
        print("\n" + "!" * 72)
        print("   FATAL ERROR DURING APPLICATION STARTUP")
        print("!" * 72)
        print(f"\nError: {e}\n")
        print("Common solutions:")
        print("  1. Delete the database and start fresh:")
        print("       Remove-Item instance\\frcs_vms_pos.db -Force")
        print("  2. Install/update dependencies:")
        print("       pip install -r requirements.txt")
        print("  3. Run PowerShell as Administrator")
        print("  4. Check that you are using the correct Python (not Microsoft Store stub)")
        print("\nFull error traceback:")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)

    print("\n  Access the system at:  http://127.0.0.1:5000")
    print("\n  First time use:")
    print("    → The system will automatically open the Setup Wizard")
    print("    → Create your first Branch (TIN + VMS UID required for FRCS)")
    print("    → Create your Administrator account")
    print("\n  To enable demo data (optional):")
    print("    set FRCS_VMS_SEED_DEMO=true   (then run again)")
    print("\n  Features:")
    print("    • Full POS with real FRCS VMS fiscal invoices")
    print("    • Inventory + Departments + Excel Import")
    print("    • Purchase Orders & Goods Receipts (VEP/VIP)")
    print("    • Debtors + Advanced Manual Invoice Entry")
    print("    • Stock Movement History (full audit trail)")
    print("    • Special Pricing, Reports, Certificates, Backup/Restore")
    print("=" * 72 + "\n")

    # Run the server (production-like settings)
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    except Exception as e:
        print(f"\nServer error: {e}")
        input("Press Enter to exit...")


if __name__ == '__main__':
    main()

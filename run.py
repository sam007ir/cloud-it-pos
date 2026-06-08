#!/usr/bin/env python3
"""
Cloud IT POS System - Production Ready
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
    print("   Cloud IT POS + INVENTORY + SALES + FISCAL SYSTEM")
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
        print("  1. Delete the database you were using and start fresh (use the exact name):")
        print("       Remove-Item -Force instance\\frcs_vms_pos.db   # or yourchosen.db")
        print("       Then run start.bat again, or visit /choose-db to pick/create another.")
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
    print("\n  First time use / Fresh Install:")
    print("    → The system will automatically open the Setup Wizard if no users exist")
    print("    → Create your first Branch (TIN + VMS UID required for FRCS)")
    print("    → Create your Administrator account")
    print("\n  DATABASE SELECTION (NEW):")
    print("    → On first load (or from Login page) you will see /choose-db")
    print("    → Lists ALL .db files found in the instance/ folder")
    print("    → Click 'Use this database' or use 'Create a brand new database' (e.g. mycompany)")
    print("    → Each DB is 100% separate: different users, inventory, sales, rentals, everything.")
    print("    → Default is frcs_vms_pos.db (or set FRCS_DB_NAME env var)")
    print("\n  IMPORTANT - If it 'already has a database' and you want a true FRESH INSTALL:")
    print("    1. Stop the server completely (close window or Ctrl+C)")
    print("    2. In PowerShell (from this folder) run EXACTLY for the DB you want to reset:")
    print("         Remove-Item -Force instance\\frcs_vms_pos.db")
    print("         (or instance\\your_other_db.db )")
    print("    3. Double-click start.bat again  OR  visit http://127.0.0.1:5000/choose-db to create a fresh named DB")
    print("    → This deletes the previous data for that specific database and forces the Setup Wizard for it.")
    print("\n  To enable demo data (optional, NOT for real use):")
    print("    set FRCS_VMS_SEED_DEMO=true   (then run again)")
    print("\n  Features:")
    print("    • Full POS with real FRCS VMS fiscal invoices")
    print("    • Inventory + Departments + Excel Import")
    print("    • Purchase Orders & Goods Receipts (VEP/VIP)")
    print("    • Invoice Entry (Supplier Invoice) — stock-in per supplier invoice + pack conversion (no VMS)")
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

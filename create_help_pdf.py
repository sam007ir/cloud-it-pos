#!/usr/bin/env python3
"""
Cloud IT POS - How to Begin (Getting Started Guide) PDF Generator
Comprehensive beginner guide including:
- Installation & First Run
- Initial Setup
- Core POS / Inventory workflows
- Rental Management (Properties with Residential VEP / Commercial VIP tax treatment)
- Maintenance, Leases, Reports, Receipt Designer, etc.
"""

from fpdf import FPDF
import os

class HowToBeginPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(10, 77, 104)
        self.cell(0, 6, "Cloud IT POS - How to Begin Guide", ln=True, align="C")
        self.set_draw_color(10, 77, 104)
        self.line(10, 12, 200, 12)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(128)
        self.cell(0, 10, f"Page {self.page_no()} | Cloud IT POS - Complete Fresh Install Guide", align="C")


def create_how_to_begin_guide():
    pdf = HowToBeginPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=14)

    # Title Page
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(10, 77, 104)
    pdf.cell(0, 14, "Cloud IT POS", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(70)
    pdf.cell(0, 7, "How to Begin - Getting Started Guide", ln=True, align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 6, "Production-Ready POS + Inventory + Rental Management System", ln=True, align="C")
    pdf.ln(8)

    # Important Note
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(16, 185, 129)
    pdf.cell(0, 6, "IMPORTANT: Fresh Install Only - No Sample Data", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 5,
        "This system is designed for real business use. There is NO automatic sample data.\n"
        "You must create everything yourself through the Setup Wizard and Settings.\n"
        "This gives you a clean, production starting point.")

    pdf.ln(5)

    # 1. Installation
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(10, 77, 104)
    pdf.cell(0, 7, "1. Installation (Windows)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 4.8,
        "1. Extract or clone the cloud-it-pos folder.\n"
        "2. Double-click install.bat\n"
        "   - It will create a venv, install dependencies, and create the 'instance' folder.\n"
        "   - A desktop shortcut 'Cloud IT POS' will be created (optional).\n"
        "3. Double-click start.bat to run the application.\n\n"
        "The app will start on http://127.0.0.1:5000\n"
        "If port 5000 is busy, edit run.py and change the port.")

    pdf.ln(3)

    # 2. First Run - Setup Wizard
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "2. First Run - Setup Wizard (Mandatory)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "On the very first launch (or when no users exist):\n"
        "1. You will be automatically redirected to /setup\n"
        "2. Fill in your first Branch details:\n"
        "   - Branch Name, Address, TIN (mandatory for VMS), VMS UID, Phone\n"
        "3. Create your Administrator account (username + strong password)\n"
        "4. Click 'Complete Setup'\n\n"
        "After setup you will be taken to the login page.\n"
        "Log in with the admin credentials you just created.")

    pdf.ln(3)

    # Reset to Fresh Install
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(220, 38, 38)
    pdf.cell(0, 7, "Reset to Completely Fresh Install (if it already has a database)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 4.8,
        "If the app 'already has a database' from previous runs or testing and you want a true fresh start:\n\n"
        "1. Completely stop the server (close the black console window).\n"
        "2. Open PowerShell in the cloud-it-pos folder and run for the specific DB file:\n"
        "     Remove-Item -Force instance\\frcs_vms_pos.db\n"
        "     (or instance\\yourcompany.db etc.)\n"
        "3. Run start.bat again, or after launch go to the Database Selector (/choose-db) and use 'Create a brand new database'.\n\n"
        "The system will detect no users (in the chosen/created DB) and redirect you to the Setup Wizard with a brand new, empty database.\n"
        "This is the correct and recommended way to achieve a 'fresh install' for one particular database while leaving others untouched.")

    pdf.ln(3)

    # New: Multiple Databases
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(10, 77, 104)
    pdf.cell(0, 7, "2b. Multiple Databases - Choose or Create New (NEW)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 4.8,
        "Cloud IT POS now supports running several completely separate databases (e.g. different companies, branches, or test vs live).\n\n"
        "How it works:\n"
        "1. On first visit, or from the Login page, click the link 'Switch / Create Database' (or go to /choose-db).\n"
        "2. You will see a list of all .db files present in the instance/ folder.\n"
        "   - Size and last-modified date are shown for each.\n"
        "   - The default 'frcs_vms_pos.db' is highlighted.\n"
        "3. Click 'Use this database' to switch to it (session remembers your choice).\n"
        "4. Or use the 'Create a brand new database' form:\n"
        "   - Type a simple name like 'mycompany' or 'branch_suva_2026'\n"
        "   - Click Create & Use\n"
        "   - It creates an empty .db file, initializes the full schema + minimal required seeds (tax + payments), sets it active, and sends you to login/setup for that DB.\n\n"
        "Important:\n"
        "   - Everything (users, products, sales, tenants, leases, certificates, receipt layouts...) is per-database.\n"
        "   - To reset just one: while stopped, `Remove-Item -Force instance\\thatname.db` then recreate via the selector.\n"
        "   - You can backup/ copy .db files safely when the app is not running.\n"
        "   - The top navigation bar (once logged in) always shows the current DB name + click to switch.\n"
        "   - Default database name can be overridden with env var FRCS_DB_NAME=other.db")

    pdf.ln(3)

    # 3. Essential Initial Configuration
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "3. Essential Configuration (Do This Immediately)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Go to Settings (top right, admin only).\n\n"
        "A. Tax Rates (Critical for correct calculations)\n"
        "   - Create at least one active Tax Rate (default G 12.5% is seeded)\n"
        "   - You can create multiple rates (Zero-rated, Exempt, etc.)\n"
        "   - Every product and rental property can be assigned its own rate.\n\n"
        "B. Payment Methods\n"
        "   - Create the methods you accept: CASH, CARD, ON_ACCOUNT, etc.\n\n"
        "C. Receipt Design (Optional but recommended)\n"
        "   - Set Company Name, Header/Footer text\n"
        "   - Upload logo (recommended for professional receipts)\n"
        "   - Click 'Open Visual Drag & Drop Receipt Designer' (Composer 2.5)\n"
        "     to customize exactly which fields appear on receipts.")

    pdf.ln(3)

    # 4. Adding Your First Products
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "4. Adding Products (Inventory)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Go to Inventory in the top navigation.\n\n"
        "Click 'Add Product':\n"
        "   - Barcode (highly recommended - used for scanning in POS)\n"
        "   - Name, SKU (optional - auto-generated if left blank)\n"
        "   - Selling Price (this is the VIP price the customer pays)\n"
        "   - Cost Price\n"
        "   - Assign Tax Rate (this controls VEP/VIP calculation)\n"
        "   - Department (highly recommended)\n"
        "   - Special Pricing Group (if you want bundle/quantity deals)\n\n"
        "Tip: Use 'Bulk Update' or Excel Import for many products at once.")

    pdf.ln(3)

    # 5. Using the POS
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "5. Using the POS Terminal", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Click 'Open POS Terminal' from the Dashboard or main menu.\n\n"
        "1. Select your Branch (if you have multiple)\n"
        "2. Scan a product barcode or type part of the name and press Enter\n"
        "3. Adjust quantities if needed\n"
        "4. (Optional) Select a Debtor for on-account sales\n"
        "5. Choose Payment Method\n"
        "6. Click 'Complete Sale'\n\n"
        "After the sale you can:\n"
        "   - Download the official FRCS PDF receipt\n"
        "   - View the sale details\n\n"
        "Special features:\n"
        "   - Special pricing (bundles) is automatically applied when conditions are met\n"
        "   - Branch-specific pricing is used when configured\n"
        "   - Full tax breakdown per line (using the product's assigned tax rate)")

    pdf.ln(3)

    # 6. Rental Management - How to Begin
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(16, 185, 129)
    pdf.cell(0, 7, "6. Rental Management (New Major Feature)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 4.8,
        "Go to the 'Rental' dropdown in the top navigation.\n\n"
        "Step 1: Create Properties\n"
        "   - Go to Rental > Properties\n"
        "   - Add Property:\n"
        "     - Give it a unique Barcode (this is how you will 'scan' it in Rent Collection)\n"
        "     - Set Monthly Rent\n"
        "     - IMPORTANT: Choose Property Type:\n"
        "         * Residential = VEP (price is ex-tax)\n"
        "         * Commercial = VIP (price is inc-tax)\n"
        "     - Optionally assign a specific Tax Rate\n"
        "     - Optionally assign a current Tenant\n\n"
        "Step 2: Create Tenants\n"
        "   - Go to Rental > Tenants\n"
        "   - Add tenant details (name, phone, etc.)\n"
        "   - Outstanding balance will be managed automatically\n\n"
        "Step 3: Collect Rent (POS-style)\n"
        "   - Go to Rental > Rent Collection (Scan & Pay)\n"
        "   - This screen works almost exactly like the main POS\n"
        "   - Scan (or type) the Property Barcode\n"
        "   - It will show the tenant and suggested amount (adjusted for VEP/VIP)\n"
        "   - Add as 'Charge (Due Rent)' or 'Record Payment'\n"
        "   - Use Amount Received + Change calculation\n"
        "   - Complete the transaction\n\n"
        "   Tenant balances update automatically.\n"
        "   Charges and Payments are recorded with full tax breakdown based on property type.")

    pdf.ln(3)

    # 7. Maintenance & Leases
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "7. Maintenance & Leases (Rental Add-ons)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Maintenance / Work Orders:\n"
        "   - Go to Rental > Maintenance / Work Orders\n"
        "   - Log issues reported against a property\n"
        "   - Track status (Open -> In Progress -> Completed)\n"
        "   - Record actual costs\n\n"
        "Leases:\n"
        "   - Go to Rental > Leases\n"
        "   - Record formal lease agreements with start/end dates and terms\n"
        "   - Click 'Generate Monthly Charges' to automatically create rent charges\n"
        "     for all active leases (great for recurring billing)")

    pdf.ln(3)

    # 8. Reports & Daily Sales
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "8. Reports & Daily Sales Summary", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Daily Sales Summary (very useful):\n"
        "   - Shows sales grouped by date + payment method\n"
        "   - Clearly shows Credit Notes / Returns (negative) and Net Sales\n\n"
        "General Reports:\n"
        "   - Sales by payment method, department, etc.\n"
        "   - Export to PDF or Excel\n\n"
        "Access is controlled by permissions (can_view_reports, can_view_daily_sales_summary).")

    pdf.ln(3)

    # 9. Users & Permissions
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "9. Users & Permissions (Granular Access Control)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Only Administrators can manage users.\n\n"
        "In Settings > Users you can:\n"
        "   - Create new users with role (admin / manager / staff)\n"
        "   - Assign very specific permissions such as:\n"
        "     can_issue_credit_notes, can_view_daily_sales_summary,\n"
        "     can_manage_debtors, can_do_stock_adjustment, etc.\n\n"
        "The navigation automatically hides options the user does not have permission for.")

    pdf.ln(3)

    # 10. VMS / Fiscal (Fiji Specific)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "10. VMS Certificates & Fiscalization", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Go to 'VMS Certificates'.\n\n"
        "For each branch you can:\n"
        "   - Set Simulation mode (default - no real FRCS call)\n"
        "   - Set Real VSDC mode + enter the vsdc_base_url and receipt_sequence\n"
        "   - Upload a real .pfx certificate from FRCS\n\n"
        "Use the Test Signing button to verify your certificate works.\n"
        "Every sale (and credit note) is fiscalized and produces an official receipt.")

    pdf.ln(3)

    # 11. Receipt Designer (Composer 2.5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "11. Customizing Receipts (Composer 2.5)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "In Settings > Receipt Design you can set basic header/footer/logo.\n\n"
        "For full control click 'Open Visual Drag & Drop Composer 2.5'.\n"
        "   - Drag any database field (Order, Branch, Debtor, Fiscal, Totals, etc.)\n"
        "   - Add Free Text / Notes\n"
        "   - See live preview (switch between 80mm and A4)\n"
        "   - Save Layout\n\n"
        "The saved layout is used for all future PDF receipts (both WeasyPrint and FPDF fallback).")

    pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100)
    pdf.cell(0, 6, "This guide is available from the Dashboard (Help card) or top navigation Help link.", align="C")
    pdf.ln(3)
    pdf.cell(0, 5, "For technical installation details see the INSTALL.md file included with the software.", align="C")

    # Save
    output_path = os.path.join(os.path.dirname(__file__), 'app', 'static', 'help', 'Cloud_IT_POS_How_to_Begin_Guide.pdf')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    print(f"PDF generated successfully: {output_path}")


if __name__ == "__main__":
    create_how_to_begin_guide()
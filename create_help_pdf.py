#!/usr/bin/env python3
"""
Generate updated FRCS VMS POS User Guide PDF
Includes new features: Departments + Excel Import + Debtors + Invoice Entry + Stock History
"""

from fpdf import FPDF
import os

class UserGuidePDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(10, 77, 104)
        self.cell(0, 6, "FRCS VMS POS - Complete User Guide", ln=True, align="C")
        self.set_draw_color(10, 77, 104)
        self.line(10, 12, 200, 12)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(128)
        self.cell(0, 10, f"Page {self.page_no()} | FRCS VMS POS System - Fiji Revenue & Customs Service", align="C")


def create_user_guide():
    pdf = UserGuidePDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=14)

    # Title
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(10, 77, 104)
    pdf.cell(0, 11, "FRCS VMS POS System", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(70)
    pdf.cell(0, 6, "Complete User Guide - Fresh Install Version", ln=True, align="C")
    pdf.ln(6)

    # What's New
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(16, 185, 129)  # emerald
    pdf.cell(0, 7, "What's New in This Version", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 5, 
        "- Inventory Departments for proper item classification\n"
        "- Excel Import for bulk product loading\n"
        "- Live Debtor balance in POS + full details on receipts\n"
        "- Advanced Manual Invoice Entry with stock & audit\n"
        "- Per-item Stock Movement History (full traceability)")

    pdf.ln(4)

    # Section 1
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(0)
    pdf.cell(0, 7, "1. First Time Setup (Fresh Install - No Demo Data)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "1. Run the application (python run.py)\n"
        "2. You will be redirected to the Setup Wizard\n"
        "3. Create your first Branch (Name + TIN + VMS UID are mandatory for FRCS)\n"
        "4. Create your Administrator account\n"
        "5. Log in and go to Settings to manually configure Tax Rates and Payment Methods\n\n"
        "Note: There is deliberately no automatic sample data. Everything must be set up by you.")

    pdf.ln(3)

    # Section 2
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "2. Inventory Departments (New Classification)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Go to 'Departments' in the main navigation.\n"
        "Create departments such as Groceries, Electronics, Hardware, Beverages, etc.\n\n"
        "When adding or editing products in Inventory, assign them to a Department using the dropdown.\n"
        "This provides much better structure than free-text categories.")

    pdf.ln(3)

    # Section 3
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "3. Importing Products from Excel", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "In the Inventory page, click the 'Import Excel' button.\n\n"
        "Recommended columns (case insensitive):\n"
        "SKU, Name, Price, Cost, Department, Category, ReorderLevel, Unit, Barcode\n\n"
        "The system will create new products or update existing ones (matched by SKU).\n"
        "Departments are automatically matched or created.\n"
        "Zero stock is initialized for all branches.")

    pdf.ln(3)

    # Section 4
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "4. POS Terminal & Debtors (On-Account Sales)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "When making a sale:\n"
        "- Select a Debtor from the dropdown (live balance is fetched and displayed)\n"
        "- Add items normally\n"
        "- Choose ON_ACCOUNT or CREDIT as payment method\n\n"
        "The official FRCS fiscal receipt will show full debtor details including name, TIN, phone, address, and outstanding balance after the sale.")

    pdf.ln(3)

    # Section 5
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "5. Manual Invoice Entry", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Use this screen for wholesale, B2B, or manual credit invoices.\n"
        "Features: Dynamic line items, debtor selection with live balance, fiscalize toggle, and automatic stock deduction with full movement audit trail.")

    pdf.ln(3)

    # Section 6
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "6. Stock Movement History (Full Traceability)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "In the Inventory screen, click the 'History' button next to any product.\n\n"
        "You will see a complete chronological list of every movement:\n"
        "- POS Sales\n"
        "- Manual Invoice Entry\n"
        "- Goods Receipts\n"
        "- Adjustments & Transfers\n"
        "- Returns / Credit Notes\n\n"
        "Each row shows previous quantity, new quantity, user, date, and reference link.")

    pdf.ln(3)

    # Section 7
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "7. VMS Certificates & Fiscal Invoices", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 4.8,
        "Every sale produces a fiscal invoice.\n\n"
        "Go to 'VMS Certificates' to configure per branch:\n"
        "- Simulate Smart Card (testing)\n"
        "- Upload real .pfx / .p12 certificate from FRCS\n\n"
        "Use the 'Test Signing' button to immediately verify that the certificate is working correctly.")

    pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100)
    pdf.cell(0, 6, "This guide is also available anytime from the Dashboard or top navigation.", align="C")

    # Save
    output_path = os.path.join(os.path.dirname(__file__), 'app', 'static', 'help', 'FRCS_VMS_POS_User_Guide.pdf')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    print(f"PDF generated successfully: {output_path}")


if __name__ == "__main__":
    create_user_guide()
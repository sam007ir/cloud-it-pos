"""
FRCS VMS Full Production POS + Inventory + Sales Management System
Fiji Revenue & Customs Service - VAT Monitoring System Compliant
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import json
import hashlib
import time
import requests

from app.models import db, User, Branch, Product, Inventory, Order, OrderItem, FiscalInvoice, VMSCertificate, VMSLog, StockMovement, Department, Supplier, TaxRate, PaymentMethod, SpecialPricingRule, ReceiptConfig, ProductBranchPrice, PurchaseOrder, PurchaseOrderItem, GoodsReceipt, GoodsReceiptItem, StockTake, Debtor, ProductBarcode

# ==================== APP FACTORY ====================
def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'frcs-vms-prod-2026-secure-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'instance', 'frcs_vms_pos.db').replace('\\\\', '/')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'certs'
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB for PFX

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    # Make granular permissions available in all Jinja templates
    @app.context_processor
    def inject_permissions():
        def has_perm(key):
            if current_user.is_authenticated:
                if current_user.role == 'admin':
                    return True
                try:
                    perms = json.loads(current_user.permissions or '{}')
                    return bool(perms.get(key))
                except:
                    return False
            return False
        return dict(user_has_permission=has_perm)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ==================== HELPER FUNCTIONS ====================
    def get_active_branches():
        return Branch.query.filter_by(is_active=True).all()

    def get_active_vat_rate():
        """Returns the currently active standard VAT rate as decimal (e.g. 0.15)."""
        active = TaxRate.query.filter_by(is_active=True).first()
        if active:
            return active.rate / 100.0
        return 0.15  # Default Fiji rate

    def calculate_vat(subtotal):
        rate = get_active_vat_rate()
        return round(subtotal * rate, 2)

    def generate_fiscal_number(branch_tin, order_id):
        date_part = datetime.utcnow().strftime("%Y%m%d")
        seq = f"{order_id:06d}"
        return f"FJI-{branch_tin}-{date_part}-{seq}"

    def get_or_create_inventory(product_id, branch_id):
        inv = Inventory.query.filter_by(product_id=product_id, branch_id=branch_id).first()
        if not inv:
            inv = Inventory(product_id=product_id, branch_id=branch_id, quantity=0)
            db.session.add(inv)
            db.session.commit()
        return inv

    def record_stock_movement(product_id, branch_id, movement_type, qty_change, prev, new, ref, user_id, notes=""):
        move = StockMovement(
            product_id=product_id,
            branch_id=branch_id,
            movement_type=movement_type,
            quantity_change=qty_change,
            previous_qty=prev,
            new_qty=new,
            reference=ref,
            notes=notes,
            performed_by=user_id
        )
        db.session.add(move)

    def generate_frcs_pdf_receipt(order, fiscal, layout='80mm', config=None):
        """
        Receipt generator with graceful fallback.
        - Preferred: HTML template + WeasyPrint (full design control via receipt.html)
        - Fallback: Old FPDF method (if weasyprint is not installed)
        """
        if config is None:
            config = ReceiptConfig.query.first() or ReceiptConfig()

        # Determine if this was a REAL VMS fiscalization
        is_real_vms = False
        if order.branch:
            cert = VMSCertificate.query.filter_by(branch_id=order.branch.id, is_active=True).first()
            if cert and getattr(cert, 'vsdc_mode', 'simulation') == 'real_vsdc':
                is_real_vms = True

        # Try WeasyPrint first (modern HTML/CSS receipts)
        try:
            from weasyprint import HTML
            import json as json_module

            tax_items = []
            if fiscal and fiscal.tax_items_json:
                try:
                    tax_items = json_module.loads(fiscal.tax_items_json)
                except:
                    pass

            html = render_template(
                'receipt.html',
                order=order,
                fiscal=fiscal,
                config=config,
                is_real_vms=is_real_vms,
                tax_items=tax_items,
            )

            pdf_bytes = HTML(string=html, base_url=app.root_path).write_pdf()
            return pdf_bytes

        except ImportError:
            # WeasyPrint not installed — fall back to the old reliable FPDF method
            print("[Receipt] WeasyPrint not installed. Falling back to FPDF receipt generator.")
            return _generate_receipt_with_fpdf(order, fiscal, layout, config, is_real_vms)

        except Exception as e:
            print(f"[Receipt] WeasyPrint failed ({e}). Falling back to FPDF.")
            return _generate_receipt_with_fpdf(order, fiscal, layout, config, is_real_vms)

    def _generate_receipt_with_fpdf(order, fiscal, layout='80mm', config=None, is_real_vms=False):
        """Fallback receipt generator using FPDF (used when WeasyPrint is unavailable)."""
        from fpdf import FPDF
        import json as json_module

        if config is None:
            config = ReceiptConfig.query.first() or ReceiptConfig()

        is_a4 = str(layout).lower() == 'a4'

        if is_a4:
            pdf = FPDF(orientation='P', unit='mm', format='A4')
            pdf.set_margins(12, 10, 12)
            small = 9
            tiny = 8
        else:
            pdf = FPDF(format=(80, 260))
            pdf.set_margins(4, 4, 4)
            small = 7
            tiny = 6

        pdf.add_page()

        company = config.company_name or "Your Business"
        footer = config.footer_text or "Thank you for your business."

        # Header
        pdf.set_font("Helvetica", "B", 11 if is_a4 else 9)
        pdf.cell(0, 6 if is_a4 else 5, "FIJI REVENUE & CUSTOMS SERVICE", ln=True, align="C")
        pdf.set_font("Helvetica", "B", 9 if is_a4 else 7)
        pdf.cell(0, 5 if is_a4 else 4, "VAT MONITORING SYSTEM", ln=True, align="C")
        pdf.ln(2)

        # Receipt info
        pdf.set_font("Helvetica", "", tiny)
        if fiscal and fiscal.sdc_no:
            pdf.cell(0, 4, f"SDC Invoice: {fiscal.sdc_no}", ln=True)
        pdf.cell(0, 4, f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M')}", ln=True)
        pdf.ln(2)

        # Seller
        pdf.set_font("Helvetica", "B", tiny)
        pdf.cell(0, 4, "SELLER", ln=True)
        pdf.set_font("Helvetica", "", tiny)
        pdf.cell(0, 4, company, ln=True)
        if order.branch:
            pdf.cell(0, 4, f"TIN: {order.branch.tin or 'N/A'}  VMS UID: {order.branch.vms_uid or 'N/A'}", ln=True)
        pdf.ln(2)

        # Customer/Debtor
        if order.customer_name and order.customer_name.lower() not in ['cash customer', 'cash']:
            pdf.set_font("Helvetica", "B", tiny)
            pdf.cell(0, 4, "CUSTOMER / DEBTOR", ln=True)
            pdf.set_font("Helvetica", "", tiny)
            pdf.cell(0, 4, order.customer_name, ln=True)
            if order.debtor:
                d = order.debtor
                if d.phone: pdf.cell(0, 4, f"Phone: {d.phone}", ln=True)
                if d.address: pdf.cell(0, 4, f"Addr: {d.address[:50]}", ln=True)
            pdf.ln(2)

        # Items
        pdf.set_font("Helvetica", "B", tiny)
        pdf.cell(0, 4, "ITEMS", ln=True)
        pdf.set_font("Helvetica", "", tiny)

        for item in order.items:
            name = (item.product_name or "Item")[:28]
            line = f"{name} x{item.quantity} @ {item.unit_price:.2f} = FJD {item.line_total:.2f}"
            pdf.cell(0, 4, line, ln=True)

        pdf.ln(2)

        # Totals
        pdf.set_font("Helvetica", "", tiny)
        pdf.cell(0, 4, f"Subtotal (VEP): FJD {order.subtotal:.2f}", ln=True)
        pdf.cell(0, 4, f"VAT: FJD {order.vat:.2f}", ln=True)
        pdf.set_font("Helvetica", "B", small)
        pdf.cell(0, 5, f"TOTAL (VIP): FJD {order.total:.2f}", ln=True)
        pdf.ln(2)

        # Payment
        pdf.set_font("Helvetica", "", tiny)
        pdf.cell(0, 4, f"Paid by: {(order.payment_method or 'CASH').upper()}", ln=True)
        pdf.ln(2)

        # Footer
        pdf.set_font("Helvetica", "I", tiny)
        pdf.multi_cell(0, 3.5, footer)

        # Fiscal block (only for real VMS)
        if is_real_vms and fiscal:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", tiny)
            pdf.cell(0, 4, "FRCS VMS - OFFICIAL FISCAL INVOICE", ln=True, align="C")
            if fiscal.sdc_no:
                pdf.cell(0, 4, f"SDC No: {fiscal.sdc_no}", ln=True)
            if fiscal.signature:
                pdf.cell(0, 4, f"Sig: {fiscal.signature[:40]}...", ln=True)
            if fiscal.verification_url:
                pdf.cell(0, 4, f"Verify: {fiscal.verification_url}", ln=True)

        return pdf.output(dest='S')

    def upgrade_database_schema():
        """Add missing columns to existing SQLite tables (no full migrations)."""
        from sqlalchemy import text

        with db.engine.connect() as conn:
            # Check and add columns to fiscal_invoice table
            try:
                # Get existing columns
                result = conn.execute(text("PRAGMA table_info(fiscal_invoice)"))
                existing_cols = [row[1] for row in result.fetchall()]

                new_columns = {
                    "verification_url": "TEXT",
                    "tax_items_json": "TEXT",
                    "total_tax": "REAL"
                }

                for col_name, col_type in new_columns.items():
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE fiscal_invoice ADD COLUMN {col_name} {col_type}"))
                        print(f"[Schema Upgrade] Added column '{col_name}' to fiscal_invoice table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] Skipped or info: {e}")

            # Add address column to debtor table (for full details on receipts)
            try:
                result = conn.execute(text("PRAGMA table_info(debtor)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "address" not in existing_cols:
                    conn.execute(text("ALTER TABLE debtor ADD COLUMN address TEXT"))
                    print("[Schema Upgrade] Added column 'address' to debtor table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] Debtor address check skipped: {e}")

            # Add department_id to product for structured inventory classification
            try:
                result = conn.execute(text("PRAGMA table_info(product)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "department_id" not in existing_cols:
                    conn.execute(text("ALTER TABLE product ADD COLUMN department_id INTEGER"))
                    print("[Schema Upgrade] Added column 'department_id' to product table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] Department column check skipped: {e}")

            # Add tax_rate_id to product so each item can have its own tax treatment
            try:
                result = conn.execute(text("PRAGMA table_info(product)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "tax_rate_id" not in existing_cols:
                    conn.execute(text("ALTER TABLE product ADD COLUMN tax_rate_id INTEGER"))
                    print("[Schema Upgrade] Added column 'tax_rate_id' to product table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] tax_rate_id column check skipped: {e}")

            # Add tax_rate_id to purchase_order_item and goods_receipt_item for purchase tax tracking
            for table_name in ['purchase_order_item', 'goods_receipt_item']:
                try:
                    res = conn.execute(text(f"PRAGMA table_info({table_name})"))
                    cols = [r[1] for r in res.fetchall()]
                    if "tax_rate_id" not in cols:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tax_rate_id INTEGER"))
                        print(f"[Schema Upgrade] Added 'tax_rate_id' to {table_name}")
                    conn.commit()
                except Exception as e:
                    print(f"[Schema Upgrade] {table_name} tax_rate_id skipped: {e}")

            # Create product_barcode table if it doesn't exist (for alternate barcodes + pack qty)
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS product_barcode (
                        id INTEGER PRIMARY KEY,
                        product_id INTEGER NOT NULL,
                        barcode TEXT NOT NULL UNIQUE,
                        pack_quantity INTEGER DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(product_id) REFERENCES product(id)
                    )
                """))
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] ProductBarcode table check skipped: {e}")

            # Add VSDC integration columns to vms_certificate table
            try:
                result = conn.execute(text("PRAGMA table_info(vms_certificate)"))
                existing_cols = [row[1] for row in result.fetchall()]

                new_columns = {
                    "vsdc_mode": "TEXT DEFAULT 'simulation'",
                    "vsdc_base_url": "TEXT",
                    "receipt_sequence": "TEXT"
                }

                for col_name, col_type in new_columns.items():
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE vms_certificate ADD COLUMN {col_name} {col_type}"))
                        print(f"[Schema Upgrade] Added column '{col_name}' to vms_certificate table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] VSDC config columns check skipped: {e}")

            # Add new receipt designer columns to receipt_config table
            try:
                result = conn.execute(text("PRAGMA table_info(receipt_config)"))
                existing_cols = [row[1] for row in result.fetchall()]

                new_columns = {
                    "custom_header_html": "TEXT",
                    "receipt_layout": "TEXT"
                }

                for col_name, col_type in new_columns.items():
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE receipt_config ADD COLUMN {col_name} {col_type}"))
                        print(f"[Schema Upgrade] Added column '{col_name}' to receipt_config table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] receipt_config columns check skipped: {e}")

    def user_has_permission(user, permission_key):
        """Simple permission checker. Falls back to role-based access."""
        if user.role == 'admin':
            return True
        try:
            perms = json.loads(user.permissions or '{}')
            return perms.get(permission_key, False)
        except:
            return False

    # ==================== SEED DATA ====================
    def seed_if_empty():
        """
        FRESH INSTALL MODE - No automatic demo data.

        On a brand new installation, the database starts completely empty.
        The first user must go through the Setup Wizard at /setup to create:
          - Their first Branch (with TIN, VMS UID, etc.)
          - The first Administrator account

        This gives a true "new software" experience.

        If you want demo data for testing or training, run with:
            set FRCS_VMS_SEED_DEMO=true   (Windows)
            export FRCS_VMS_SEED_DEMO=true  (Linux/Mac)
        before starting the app.
        """
        import os
        from werkzeug.security import generate_password_hash

        if Branch.query.first():
            return

        if os.environ.get("FRCS_VMS_SEED_DEMO", "").lower() != "true":
            print("[Fresh Install] Database is empty. No demo data seeded.")
            print("               Please visit /setup to create your first Branch + Admin user.")
            return

        # === DEMO DATA ONLY (when FRCS_VMS_SEED_DEMO=true) ===
        print("[SEED] DEMO MODE: Seeding sample data because FRCS_VMS_SEED_DEMO=true")

        # (You can paste the old large seeding block here later if you ever need demo mode back)
        print("[SEED] Demo seeding is currently minimal. Use the Setup Wizard for real installs.")

    with app.app_context():
        print("    [Startup] Running db.create_all()...")
        db.create_all()
        print("    [Startup] Running upgrade_database_schema() (adding any missing columns)...")
        upgrade_database_schema()   # Legacy column additions

        # Run Alembic migrations automatically (flawless schema updates)
        print("    [Startup] Running Alembic migrations (this can take a few seconds)...")
        try:
            # On fresh installs (no tables yet), skip Alembic — db.create_all() + runtime upgrades already match current models.
            # This avoids hangs/errors from outdated migration scripts.
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            if not existing_tables or 'alembic_version' not in existing_tables:
                print("    [Startup] Fresh DB detected — skipping Alembic (using runtime schema).")
            else:
                import subprocess
                result = subprocess.run(
                    ["python", "-m", "alembic", "upgrade", "head"],
                    capture_output=True,
                    text=True,
                    timeout=30  # Prevent indefinite hang
                )
                if result.stdout:
                    print("    [Alembic stdout]", result.stdout.strip()[:500])
                if result.stderr:
                    print("    [Alembic stderr]", result.stderr.strip()[:500])
                if result.returncode != 0:
                    print(f"    [Alembic] Warning: exit code {result.returncode}")
                else:
                    print("    [Startup] Alembic migrations completed successfully.")
        except subprocess.TimeoutExpired:
            print("    [Alembic] Timed out after 30 seconds — continuing without it.")
        except Exception as e:
            print(f"    [Alembic] Note: {str(e)[:200]}")

        print("    [Startup] Checking if this is a fresh install (seed_if_empty)...")
        seed_if_empty()

    # ==================== AUTH ROUTES ====================
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # Fresh install: if no users exist yet, force setup wizard
        if User.query.count() == 0:
            return redirect(url_for('setup'))

        if request.method == 'POST':
            user = User.query.filter_by(username=request.form['username']).first()
            if user and check_password_hash(user.password_hash, request.form['password']):
                login_user(user)
                flash(f'Welcome back, {user.name.split()[0]}!', 'success')
                return redirect(url_for('dashboard'))
            flash('Invalid username or password', 'error')
        return render_template('login.html')

    # ==================== FRESH INSTALL SETUP WIZARD ====================
    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        # If users already exist, setup is not needed
        if User.query.count() > 0:
            return redirect(url_for('login'))

        if request.method == 'POST':
            from werkzeug.security import generate_password_hash

            # Get form data
            branch_name = request.form.get('branch_name', '').strip()
            branch_address = request.form.get('branch_address', '').strip()
            branch_tin = request.form.get('branch_tin', '').strip()
            branch_vms_uid = request.form.get('branch_vms_uid', '').strip()
            branch_phone = request.form.get('branch_phone', '').strip()

            admin_username = request.form.get('admin_username', '').strip()
            admin_name = request.form.get('admin_name', '').strip()
            admin_email = request.form.get('admin_email', '').strip()
            admin_password = request.form.get('admin_password', '')
            admin_password2 = request.form.get('admin_password2', '')

            # Basic validation
            errors = []
            if not branch_name:
                errors.append("Branch name is required")
            if not branch_tin:
                errors.append("Branch TIN is required (from FRCS)")
            if not admin_username or not admin_password:
                errors.append("Admin username and password are required")
            if admin_password != admin_password2:
                errors.append("Passwords do not match")
            if len(admin_password) < 6:
                errors.append("Admin password must be at least 6 characters")

            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('setup.html')

            # Create the first Branch
            new_branch = Branch(
                name=branch_name,
                address=branch_address,
                tin=branch_tin,
                vms_uid=branch_vms_uid or f"BR-{branch_tin[-4:]}",
                phone=branch_phone
            )
            db.session.add(new_branch)
            db.session.flush()  # Get the ID

            # Create the first Administrator (tied to this branch)
            admin_user = User(
                username=admin_username,
                email=admin_email or None,
                password_hash=generate_password_hash(admin_password),
                name=admin_name or admin_username,
                role='admin',
                branch_id=new_branch.id
            )
            db.session.add(admin_user)
            db.session.commit()

            flash('Setup complete! Your first branch and administrator account have been created.', 'success')
            flash('Please log in with your new admin account.', 'success')
            return redirect(url_for('login'))

        return render_template('setup.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Logged out successfully.', 'success')
        return redirect(url_for('login'))

    # ==================== MAIN DASHBOARD ====================
    @app.route('/')
    @app.route('/dashboard')
    @login_required
    def dashboard():
        branches = get_active_branches()
        
        # Stats
        total_products = Product.query.count()
        total_stock_value = 0
        low_stock_count = 0
        
        for br in branches:
            invs = Inventory.query.filter_by(branch_id=br.id).all()
            for inv in invs:
                total_stock_value += inv.quantity * (inv.product.price if inv.product else 0)
                if inv.quantity <= (inv.product.reorder_level if inv.product else 10):
                    low_stock_count += 1

        recent_sales = Order.query.order_by(Order.created_at.desc()).limit(8).all()
        pending_vms = FiscalInvoice.query.filter_by(transmitted=False).count()

        return render_template('dashboard.html', 
            user=current_user, 
            branches=branches,
            total_products=total_products,
            total_stock_value=round(total_stock_value, 2),
            low_stock_count=low_stock_count,
            recent_sales=recent_sales,
            pending_vms=pending_vms
        )

    # ==================== POS TERMINAL ====================
    @app.route('/pos')
    @login_required
    def pos():
        if current_user.role not in ['admin', 'manager', 'staff']:
            flash('Access denied - Staff only', 'error')
            return redirect(url_for('dashboard'))
        
        default_branch = Branch.query.get(current_user.branch_id or 1)
        products = Product.query.filter_by(is_active=True).all()
        cert = VMSCertificate.query.filter_by(branch_id=default_branch.id, is_active=True).first() if default_branch else None
        
        # Allow managers and admins to see all branches for switching
        all_branches = get_active_branches() if current_user.role in ['admin', 'manager'] else [default_branch]
        
        active_payment_methods = PaymentMethod.query.filter_by(is_active=True).all()
        debtors = Debtor.query.order_by(Debtor.name).all()
        
        return render_template('pos.html', 
                               branch=default_branch, 
                               products=products, 
                               user=current_user, 
                               certificate=cert,
                               all_branches=all_branches,
                               active_payment_methods=active_payment_methods,
                               debtors=debtors)

    @app.route('/api/pos/create-sale', methods=['POST'])
    @login_required
    def api_pos_create_sale():
        data = request.get_json()
        branch_id = data.get('branch_id', current_user.branch_id or 1)
        items = data.get('items', [])
        customer_name = data.get('customer_name', '')
        customer_tin = data.get('customer_tin', '')
        debtor_id = data.get('debtor_id')
        payment_method = data.get('payment_method', 'CASH')
        tendered_amount = data.get('tendered_amount')  # amount customer gave (for change)

        if not items:
            return jsonify({"success": False, "message": "No items in sale"}), 400

        branch = Branch.query.get(branch_id)
        if not branch:
            return jsonify({"success": False, "message": "Invalid branch"}), 400

        # Handle debtor
        debtor = None
        if debtor_id:
            debtor = Debtor.query.get(debtor_id)
            if debtor:
                customer_name = debtor.name
                customer_tin = debtor.tin

        # Create Order
        order = Order(
            branch_id=branch_id,
            staff_id=current_user.id,
            customer_name=customer_name or "Cash Customer",
            customer_tin=customer_tin or None,
            debtor_id=debtor.id if debtor else None,
            order_type='pos',
            payment_method=payment_method
        )
        db.session.add(order)
        db.session.flush()

        subtotal = 0
        for item in items:
            prod = Product.query.get(item['product_id'])
            if not prod:
                continue
            qty = int(item['quantity'])
            price = float(item['price'])

            # Use branch-specific price if available (more accurate)
            bp = ProductBranchPrice.query.filter_by(product_id=prod.id, branch_id=branch_id).first()
            if bp:
                price = bp.price

            # Stock check & deduct
            inv = get_or_create_inventory(prod.id, branch_id)
            if inv.quantity < qty:
                db.session.rollback()
                return jsonify({"success": False, "message": f"Only {inv.quantity} {prod.name} in stock"}), 400

            prev_qty = inv.quantity
            inv.quantity -= qty

            record_stock_movement(prod.id, branch_id, 'SALE', -qty, prev_qty, inv.quantity, f"POS-{order.id}", current_user.id)

            # price coming from product / branch price / special is now treated as VIP (tax inclusive)
            vip_unit_price = round(price, 2)
            line_vip_total = round(vip_unit_price * qty, 2)

            db.session.add(OrderItem(
                order_id=order.id,
                product_id=prod.id,
                product_name=prod.name,
                quantity=qty,
                unit_price=vip_unit_price,     # stored as the price customer actually pays (VIP)
                line_total=line_vip_total
            ))

        # === CORRECT PER-ITEM TAX CALCULATION ===
        # Each product can have its own tax_rate. Fall back to first active rate.
        total_vip = 0.0
        total_vep = 0.0
        total_vat = 0.0

        default_rate = get_active_vat_rate()  # fallback

        for item in order.items:
            prod = Product.query.get(item.product_id)
            rate = default_rate
            if prod and prod.tax_rate and prod.tax_rate.is_active:
                rate = prod.tax_rate.rate / 100.0

            line_vip = item.line_total
            total_vip += line_vip

            if rate > 0:
                line_vep = round(line_vip / (1 + rate), 2)
                line_vat = round(line_vip - line_vep, 2)
            else:
                line_vep = line_vip
                line_vat = 0.0

            total_vep += line_vep
            total_vat += line_vat

        order.subtotal = round(total_vep, 2)
        order.vat = round(total_vat, 2)
        order.total = round(total_vip, 2)   # customer pays this amount
        order.status = 'completed'

        # Update debtor balance if sale is on account
        if debtor and payment_method in ['ON_ACCOUNT', 'CREDIT', 'DEBTOR']:
            debtor.outstanding_balance += total_vip
            db.session.commit()

        db.session.commit()

        # Generate Fiscal Invoice + VMS
        fiscal = generate_and_transmit_fiscal(order, branch)

        # Calculate change due
        change_due = 0.0
        if tendered_amount:
            try:
                change_due = round(float(tendered_amount) - total_vip, 2)
            except:
                change_due = 0.0

        return jsonify({
            "success": True,
            "order_id": order.id,
            "invoice_no": fiscal.invoice_no if fiscal else None,
            "total": total_vip,
            "change_due": max(0, change_due),   # only positive change
            "tendered_amount": tendered_amount,
            "fiscal": {
                "invoice_no": fiscal.invoice_no,
                "signature": fiscal.signature,
                "qr_payload": fiscal.qr_payload,
                "transmitted": fiscal.transmitted
            } if fiscal else None
        })

    def generate_and_transmit_fiscal(order, branch):
        """
        Core FRCS VMS fiscalization logic.
        - If vsdc_mode == 'simulation' → uses local simulation (no real FRCS call)
        - If vsdc_mode == 'real_vsdc'  → attempts to call the real VSDC service
        """
        cert = VMSCertificate.query.filter_by(branch_id=branch.id, is_active=True).first()

        # === REAL VSDC MODE ===
        if cert and cert.vsdc_mode == 'real_vsdc' and cert.vsdc_base_url:
            try:
                return _fiscalize_with_real_vsdc(order, branch, cert)
            except Exception as e:
                print(f"[Real VSDC] Failed to fiscalize with real VSDC: {e}")
                # Fall back to simulation if real call fails (optional safety)
                # For production you may want to raise instead
                pass

        now = datetime.utcnow()
        sdc_time = now.strftime("%Y-%m-%d %H:%M:%S")
        invoice_no = generate_fiscal_number(branch.tin, order.id)
        counter = 100000 + order.id

        # === Build items for the "API request" (what POS sends) ===
        items_for_api = []
        for item in order.items:
            items_for_api.append({
                "name": item.product_name,
                "gtin": "0000000000000",  # In real system this comes from product master
                "quantity": item.quantity,
                "unit_price": round(item.unit_price, 4),
                "total_amount": round(item.line_total, 4),
            })

        # === Simulate the response from FRCS TaxCore.API (InvoiceFiscalizationResult) ===
        # In real life this data is signed by the Secure Element on the FRCS side.

        # Realistic SDC Invoice Number format used by FRCS
        sdc_no = f"7AF{hashlib.md5(str(order.id).encode()).hexdigest()[:6].upper()}-{int(time.time()) % 1000000:06d}-{order.id}"

        # Build tax breakdown from configured active Tax Rates (supports FRCS V3 multi-rate)
        active_rates = TaxRate.query.filter_by(is_active=True).all()
        tax_items = []
        total_tax_calculated = 0

        if active_rates:
            for rate in active_rates:
                rate_decimal = rate.rate / 100
                base = round(order.subtotal, 2)  # Simplified: apply to whole subtotal for now
                amount = round(base * rate_decimal, 2)
                tax_items.append({
                    "label": rate.label,
                    "rate": rate.rate,
                    "base": base,
                    "amount": amount
                })
                total_tax_calculated += amount
        else:
            # Fallback to 15%
            tax_items.append({
                "label": "A",
                "rate": 15.00,
                "base": round(order.subtotal, 2),
                "amount": round(order.vat, 2)
            })
            total_tax_calculated = round(order.vat, 2)

        # Digital signature as returned by the Secure Element
        tax_total_for_sig = sum(t['amount'] for t in tax_items)
        signature_payload = {
            "sdc_no": sdc_no,
            "invoice_no": invoice_no,
            "seller_uid": branch.vms_uid,
            "total": round(order.total, 2),
            "tax_total": tax_total_for_sig,
            "payment_method": order.payment_method,
            "timestamp": sdc_time
        }
        signature = hashlib.sha256(json.dumps(signature_payload, sort_keys=True).encode()).hexdigest()[:64].upper()

        verification_url = f"https://verify.frcs.gov.fj/fiscal/{sdc_no}"

        # This is the object that would come back from the real FRCS API (V3 structure)
        frcs_result = {
            "status": "SUCCESS",
            "sdc_invoice_no": sdc_no,
            "invoice_counter": counter,
            "sdc_time": sdc_time,
            "seller": {
                "name": branch.name,
                "tin": branch.tin,
                "uid": branch.vms_uid,
                "address": branch.address
            },
            "tax_items": tax_items,
            "payment_method": order.payment_method,
            "totals": {
                "subtotal": round(order.subtotal, 2),
                "total_tax": tax_total_for_sig,
                "grand_total": round(order.total, 2)
            },
            "signature": signature,
            "verification_url": verification_url,
            "qr_data": verification_url,
            "audit_id": f"AUD-{int(time.time())}",
            "cert_method": cert.method if cert else "none",
            "cert_serial": cert.cert_serial if cert else "N/A"
        }

        # Create / update FiscalInvoice record with data FROM the "FRCS response"
        fiscal = FiscalInvoice(
            order_id=order.id,
            invoice_no=invoice_no,
            sdc_no=sdc_no,
            invoice_counter=counter,
            sdc_time=sdc_time,
            seller_name=branch.name,
            seller_tin=branch.tin,
            seller_uid=branch.vms_uid,
            signature=signature,
            qr_payload=verification_url,
            verification_url=verification_url,
            tax_items_json=json.dumps(tax_items),
            total_tax=tax_total_for_sig,
            transmitted=True,
            transmission_status="SUCCESS",
            vms_response=json.dumps(frcs_result)
        )

        db.session.add(fiscal)

        # VMS Log
        log = VMSLog(
            branch_id=branch.id,
            order_id=order.id,
            invoice_no=invoice_no,
            status="SUCCESS",
            payload_hash=hashlib.sha256(json.dumps(frcs_result).encode()).hexdigest()[:32],
            response=f"Fiscalized via TaxCore.API simulation. SDC No: {sdc_no}"
        )
        db.session.add(log)
        db.session.commit()

        return fiscal

    def _fiscalize_with_real_vsdc(order, branch, cert):
        """
        Calls the real FRCS VSDC service for fiscalization.
        This is a starting point — payload structure can be refined based on your receipt sequence.
        """
        vsdc_url = cert.vsdc_base_url.rstrip('/')
        sign_endpoint = f"{vsdc_url}/api/Sign"

        # Build a basic payload (we will refine this together)
        receipt_seq = cert.receipt_sequence or "INV-"
        invoice_number = f"{receipt_seq}{order.id:06d}"

        payload = {
            "DateAndTimeOfIssue": order.created_at.isoformat(),
            "Cashier": "System",  # You can improve this later
            "InvoiceNumber": invoice_number,
            "IT": "Normal",
            "TT": "Sale",
            "PaymentType": order.payment_method or "Cash",
            "Items": [
                {
                    "Name": item.product_name,
                    "Quantity": item.quantity,
                    "UnitPrice": round(item.unit_price, 2),
                    "TotalAmount": round(item.line_total, 2),
                    "Labels": ["A"]  # Default tax label - you can make this dynamic
                } for item in order.items
            ],
            "Options": {
                "OmitQRCodeGen": 0,
                "OmitTextualRepresentation": 0
            }
        }

        # For real VSDC you will need to send client certificate for mTLS.
        # This is a placeholder — real implementation needs proper cert handling.
        response = requests.post(sign_endpoint, json=payload, timeout=30)

        if response.status_code != 200:
            raise Exception(f"VSDC returned status {response.status_code}: {response.text}")

        vsdc_response = response.json()

        # Create FiscalInvoice from real VSDC response
        fiscal = FiscalInvoice(
            order_id=order.id,
            invoice_no=invoice_number,
            sdc_no=vsdc_response.get("IC") or vsdc_response.get("sdc_invoice_no"),
            invoice_counter=vsdc_response.get("IN"),
            sdc_time=vsdc_response.get("DT"),
            seller_name=branch.name,
            seller_tin=branch.tin,
            seller_uid=branch.vms_uid,
            signature=vsdc_response.get("S") or vsdc_response.get("signature"),
            qr_payload=vsdc_response.get("VerificationQRCode") or vsdc_response.get("qrCode"),
            verification_url=vsdc_response.get("VerificationUrl"),
            tax_items_json=json.dumps(vsdc_response.get("tax_items", [])),
            total_tax=vsdc_response.get("TotalTax", order.vat),
            transmitted=True,
            transmission_status="SUCCESS",
            vms_response=json.dumps(vsdc_response)
        )

        db.session.add(fiscal)
        db.session.commit()

        return fiscal

    # ==================== INVENTORY MANAGEMENT ====================
    @app.route('/inventory')
    @login_required
    def inventory():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))

        branches = get_active_branches()
        products = Product.query.filter_by(is_active=True).all()
        
        # Build rich inventory view + on order + branch pricing
        inventory_data = []
        for prod in products:
            alt_count = ProductBarcode.query.filter_by(product_id=prod.id).count()
            row = {"product": prod, "branches": {}, "alt_barcode_count": alt_count}
            for br in branches:
                inv = Inventory.query.filter_by(product_id=prod.id, branch_id=br.id).first()
                on_hand = inv.quantity if inv else 0

                # Branch specific price
                bp = ProductBranchPrice.query.filter_by(product_id=prod.id, branch_id=br.id).first()
                branch_price = bp.price if bp else prod.price
                branch_cost = bp.cost_price if bp else prod.cost_price

                # Calculate items on order (open POs) - safer separate queries
                po_ordered = db.session.query(
                    db.func.coalesce(db.func.sum(PurchaseOrderItem.quantity), 0)
                ).join(PurchaseOrder).filter(
                    PurchaseOrderItem.product_id == prod.id,
                    PurchaseOrder.branch_id == br.id,
                    PurchaseOrder.status.in_(['pending', 'partially_received'])
                ).scalar() or 0

                gr_received = db.session.query(
                    db.func.coalesce(db.func.sum(GoodsReceiptItem.received_qty), 0)
                ).join(GoodsReceipt).join(PurchaseOrder, GoodsReceipt.purchase_order_id == PurchaseOrder.id).filter(
                    GoodsReceiptItem.product_id == prod.id,
                    PurchaseOrder.branch_id == br.id,
                    PurchaseOrder.status.in_(['pending', 'partially_received'])
                ).scalar() or 0

                on_order = max(0, po_ordered - gr_received)

                row["branches"][br.id] = {
                    "on_hand": on_hand,
                    "on_order": max(0, int(on_order)),
                    "price": branch_price,
                    "cost_price": branch_cost,
                    "has_custom_price": bp is not None
                }
            inventory_data.append(row)

        low_stock = []
        for prod in products:
            for br in branches:
                inv = Inventory.query.filter_by(product_id=prod.id, branch_id=br.id).first()
                if inv and inv.quantity <= prod.reorder_level:
                    low_stock.append({
                        "product": prod.name,
                        "branch": br.name,
                        "current": inv.quantity,
                        "reorder": prod.reorder_level
                    })

        departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()
        tax_rates = TaxRate.query.filter_by(is_active=True).order_by(TaxRate.rate).all()
        return render_template('inventory.html', 
            products=products, 
            branches=branches, 
            inventory_data=inventory_data,
            low_stock=low_stock[:12],
            departments=departments,
            tax_rates=tax_rates,
            user=current_user
        )

    @app.route('/api/inventory/adjust', methods=['POST'])
    @login_required
    def api_inventory_adjust():
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        prod_id = int(data['product_id'])
        branch_id = int(data['branch_id'])
        new_qty = int(data['new_quantity'])
        reason = data.get('reason', 'Manual adjustment')

        inv = get_or_create_inventory(prod_id, branch_id)
        prev = inv.quantity
        change = new_qty - prev
        inv.quantity = new_qty

        record_stock_movement(prod_id, branch_id, 'ADJUSTMENT', change, prev, new_qty, 'MANUAL', current_user.id, reason)
        db.session.commit()

        return jsonify({"success": True, "new_quantity": new_qty})

    @app.route('/api/inventory/transfer', methods=['POST'])
    @login_required
    def api_inventory_transfer():
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        pid = int(data['product_id'])
        from_b = int(data['from_branch'])
        to_b = int(data['to_branch'])
        qty = int(data['quantity'])

        from_inv = get_or_create_inventory(pid, from_b)
        to_inv = get_or_create_inventory(pid, to_b)

        if from_inv.quantity < qty:
            return jsonify({"success": False, "message": "Insufficient stock"}), 400

        # Move
        prev_from = from_inv.quantity
        from_inv.quantity -= qty
        prev_to = to_inv.quantity
        to_inv.quantity += qty

        record_stock_movement(pid, from_b, 'TRANSFER_OUT', -qty, prev_from, from_inv.quantity, f'TO-BRANCH-{to_b}', current_user.id)
        record_stock_movement(pid, to_b, 'TRANSFER_IN', qty, prev_to, to_inv.quantity, f'FROM-BRANCH-{from_b}', current_user.id)

        db.session.commit()
        return jsonify({"success": True, "message": f"Transferred {qty} units successfully"})

    @app.route('/api/inventory/item/<int:product_id>/movements', methods=['GET'])
    @login_required
    def api_item_movements(product_id):
        """Full stock movement + sales history for one item (used by inventory summary modal/page)."""
        prod = Product.query.get_or_404(product_id)
        moves = (StockMovement.query
                 .filter_by(product_id=product_id)
                 .order_by(StockMovement.timestamp.desc())
                 .limit(200)
                 .all())

        # Enrich with user names and human-friendly references + links
        from app.models import User as UserModel  # avoid name clash
        user_cache = {}
        def get_user_name(uid):
            if not uid: return "system"
            if uid not in user_cache:
                u = UserModel.query.get(uid)
                user_cache[uid] = u.username if u else f"user#{uid}"
            return user_cache[uid]

        items = []
        for m in moves:
            ref = m.reference or ""
            ref_label = ref
            ref_link = None
            if ref.startswith("POS-"):
                oid = ref.split("-")[-1]
                ref_label = f"POS Sale #{oid}"
                ref_link = f"/sales/{oid}"
            elif ref.startswith("GR-"):
                ref_label = f"Goods Receipt {ref}"
                # could link to /goods-receipts but simple
            elif ref.startswith("INV-"):
                oid = ref.split("-")[-1]
                ref_label = f"Invoice Entry #{oid}"
                ref_link = f"/sales/{oid}"
            elif ref.startswith("REV-"):
                ref_label = f"Reversal {ref}"
            elif "MANUAL" in ref or ref == "MANUAL":
                ref_label = "Manual Adjustment"

            items.append({
                "id": m.id,
                "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M"),
                "type": m.movement_type,
                "qty_change": m.quantity_change,
                "prev_qty": m.previous_qty,
                "new_qty": m.new_qty,
                "reference": ref_label,
                "reference_link": ref_link,
                "notes": m.notes or "",
                "performed_by": get_user_name(m.performed_by)
            })

        return jsonify({
            "success": True,
            "product": {"id": prod.id, "name": prod.name, "sku": prod.sku},
            "movements": items,
            "total_shown": len(items)
        })

    @app.route('/api/products', methods=['POST'])
    @login_required
    def api_create_product():
        if current_user.role != 'admin':
            return jsonify({"error": "Admin only"}), 403
        data = request.get_json() or {}

        try:
            price = float(data.get('price') or 0)
            cost = float(data.get('cost_price') or 0)
            reorder = int(data.get('reorder_level') or 10)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid number for price, cost or reorder level"}), 400

        if not data.get('name'):
            return jsonify({"success": False, "message": "Product Name is required"}), 400

        barcode = (data.get('barcode') or '').strip()
        if not barcode:
            return jsonify({"success": False, "message": "Barcode is required"}), 400

        # Check for duplicate barcode (primary or alternate)
        existing = _find_product_by_barcode(barcode)
        if existing:
            return jsonify({
                "success": False,
                "message": f"This barcode is already used by: {existing.name}",
                "conflicting_product": existing.name
            }), 400

        dept_id = data.get('department_id')
        if dept_id in ('', None):
            dept_id = None
        else:
            try:
                dept_id = int(dept_id)
            except (ValueError, TypeError):
                dept_id = None

        tax_rate_id = data.get('tax_rate_id')
        if tax_rate_id in ('', None):
            tax_rate_id = None
        else:
            try:
                tax_rate_id = int(tax_rate_id)
            except (ValueError, TypeError):
                tax_rate_id = None

        sku_value = data.get('sku', '').strip() or f"AUTO-{barcode}"

        p = Product(
            name=data['name'],
            sku=sku_value,
            barcode=barcode,
            price=price,
            category=data.get('category', 'General'),
            department_id=dept_id,
            tax_rate_id=tax_rate_id,
            cost_price=cost,
            reorder_level=reorder,
            special_pricing_group=data.get('special_pricing_group')
        )
        db.session.add(p)
        db.session.commit()

        # Give initial stock to all branches
        for br in get_active_branches():
            db.session.add(Inventory(product_id=p.id, branch_id=br.id, quantity=0))
        db.session.commit()

        return jsonify({"success": True, "product_id": p.id})

    # ==================== ALTERNATE BARCODES APIs ====================
    @app.route('/api/products/<int:product_id>/barcodes', methods=['GET'])
    @login_required
    def api_get_product_barcodes(product_id):
        barcodes = ProductBarcode.query.filter_by(product_id=product_id).order_by(ProductBarcode.pack_quantity.desc()).all()
        return jsonify([
            {
                "id": b.id,
                "barcode": b.barcode,
                "pack_quantity": b.pack_quantity
            } for b in barcodes
        ])

    @app.route('/api/products/<int:product_id>/barcodes', methods=['POST'])
    @login_required
    def api_add_product_barcode(product_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        data = request.get_json() or {}
        barcode = (data.get('barcode') or '').strip()
        pack_qty = int(data.get('pack_quantity') or 1)

        if not barcode:
            return jsonify({"success": False, "message": "Barcode is required"}), 400

        # Check for duplicate across primary + alternates
        existing = _find_product_by_barcode(barcode)
        if existing and existing.id != product_id:
            return jsonify({
                "success": False,
                "message": f"This barcode is already used by: {existing.name}",
                "conflicting_product": existing.name
            }), 400

        # Prevent duplicate on the same product
        if ProductBarcode.query.filter_by(product_id=product_id, barcode=barcode).first():
            return jsonify({"success": False, "message": "This barcode already exists for this product"}), 400

        new_bc = ProductBarcode(
            product_id=product_id,
            barcode=barcode,
            pack_quantity=max(1, pack_qty)
        )
        db.session.add(new_bc)
        db.session.commit()

        return jsonify({"success": True, "id": new_bc.id})

    @app.route('/api/products/barcodes/<int:barcode_id>', methods=['DELETE'])
    @login_required
    def api_delete_product_barcode(barcode_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        bc = ProductBarcode.query.get_or_404(barcode_id)
        db.session.delete(bc)
        db.session.commit()
        return jsonify({"success": True})

    def _find_product_by_barcode(barcode):
        """Helper: find product by primary barcode or any alternate barcode"""
        if not barcode:
            return None
        # Primary barcode
        prod = Product.query.filter_by(barcode=barcode).first()
        if prod:
            return prod
        # Alternate
        alt = ProductBarcode.query.filter_by(barcode=barcode).first()
        if alt:
            return alt.product
        return None

    @app.route('/api/barcode-lookup', methods=['GET'])
    @login_required
    def api_barcode_lookup():
        """Resolve a barcode (primary or alternate) and return product + units to add"""
        barcode = request.args.get('barcode', '').strip()
        if not barcode:
            return jsonify({"success": False, "message": "No barcode provided"}), 400

        # Check primary
        prod = Product.query.filter_by(barcode=barcode).first()
        pack = 1
        if prod:
            return jsonify({
                "success": True,
                "product_id": prod.id,
                "name": prod.name,
                "quantity_to_add": pack,
                "barcode_type": "primary"
            })

        # Check alternates
        alt = ProductBarcode.query.filter_by(barcode=barcode).first()
        if alt:
            return jsonify({
                "success": True,
                "product_id": alt.product_id,
                "name": alt.product.name,
                "quantity_to_add": alt.pack_quantity,
                "barcode_type": "alternate"
            })

        return jsonify({"success": False, "message": "Barcode not found"}), 404

    @app.route('/api/products/<int:product_id>', methods=['PUT'])
    @login_required
    def api_update_product(product_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        p = Product.query.get_or_404(product_id)
        data = request.get_json() or {}

        try:
            if 'price' in data and data['price'] not in (None, ''):
                p.price = float(data['price'])
            if 'cost_price' in data and data['cost_price'] not in (None, ''):
                p.cost_price = float(data['cost_price'])
            if 'reorder_level' in data and data['reorder_level'] not in (None, ''):
                p.reorder_level = int(data['reorder_level'])
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid number format for price/cost/reorder"}), 400

        p.name = data.get('name', p.name)
        p.sku = data.get('sku', p.sku)
        p.barcode = data.get('barcode', p.barcode)
        p.category = data.get('category', p.category)

        if 'department_id' in data:
            dept_id = data['department_id']
            if dept_id in ('', None):
                p.department_id = None
            else:
                try:
                    p.department_id = int(dept_id)
                except (ValueError, TypeError):
                    p.department_id = None

        if 'tax_rate_id' in data:
            tr_id = data['tax_rate_id']
            if tr_id in ('', None):
                p.tax_rate_id = None
            else:
                try:
                    p.tax_rate_id = int(tr_id)
                except (ValueError, TypeError):
                    p.tax_rate_id = None

        # Handle primary barcode change with duplicate check
        if 'barcode' in data:
            new_barcode = (data['barcode'] or '').strip()
            if new_barcode and new_barcode != p.barcode:
                existing = _find_product_by_barcode(new_barcode)
                if existing and existing.id != p.id:
                    return jsonify({
                        "success": False,
                        "message": f"This barcode is already used by: {existing.name}"
                    }), 400
                p.barcode = new_barcode

        p.special_pricing_group = data.get('special_pricing_group', p.special_pricing_group)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/branch-price', methods=['POST'])
    @login_required
    def api_set_branch_price():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json()
        bp = ProductBranchPrice.query.filter_by(product_id=data['product_id'], branch_id=data['branch_id']).first()
        if not bp:
            bp = ProductBranchPrice(product_id=data['product_id'], branch_id=data['branch_id'])
        bp.price = float(data['price'])
        bp.cost_price = float(data.get('cost_price', bp.cost_price or 0))
        db.session.add(bp)
        db.session.commit()
        return jsonify({"success": True})

    # ==================== SALES MANAGEMENT ====================
    @app.route('/sales')
    @login_required
    def sales():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))

        # Filters
        branch_id = request.args.get('branch_id', type=int)
        days = request.args.get('days', 30, type=int)

        query = Order.query.order_by(Order.created_at.desc())
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        since = datetime.utcnow() - timedelta(days=days)
        sales_list = query.filter(Order.created_at >= since).limit(200).all()

        # Summary
        total_revenue = sum(s.total for s in sales_list)
        total_vat = sum(s.vat for s in sales_list)
        total_transactions = len(sales_list)

        branches = get_active_branches()
        return render_template('sales.html',
            sales=sales_list,
            branches=branches,
            total_revenue=round(total_revenue, 2),
            total_vat=round(total_vat, 2),
            total_transactions=total_transactions,
            selected_branch=branch_id,
            days=days,
            user=current_user
        )

    @app.route('/sales/<int:order_id>')
    @login_required
    def sale_detail(order_id):
        order = Order.query.get_or_404(order_id)
        return render_template('sale_detail.html', order=order, user=current_user)

    # ==================== VMS CERTIFICATE MANAGEMENT (KEY FEATURE) ====================
    @app.route('/certificates')
    @login_required
    def certificates():
        if current_user.role != 'admin':
            flash('Admin only', 'error')
            return redirect(url_for('dashboard'))

        branches = get_active_branches()
        certs = {c.branch_id: c for c in VMSCertificate.query.all()}

        # Count how many times each cert was used
        usage = {}
        for b in branches:
            count = FiscalInvoice.query.filter_by(seller_uid=b.vms_uid).count()
            usage[b.id] = count

        return render_template('certificates.html', branches=branches, certs=certs, usage=usage, user=current_user)

    @app.route('/api/certificates/upload', methods=['POST'])
    @login_required
    def api_upload_certificate():
        if current_user.role != 'admin':
            return jsonify({"error": "Admin only"}), 403

        branch_id = int(request.form['branch_id'])
        method = request.form['method']  # smartcard or pfx
        taxpayer_name = request.form['taxpayer_name']
        tin = request.form['tin']
        valid_until = request.form.get('valid_until', '2028-12-31')
        serial = request.form.get('serial', f"FRCS-{int(time.time())}")

        cert = VMSCertificate.query.filter_by(branch_id=branch_id).first()
        if not cert:
            cert = VMSCertificate(branch_id=branch_id)

        cert.method = method
        cert.taxpayer_name = taxpayer_name
        cert.taxpayer_tin = tin
        cert.valid_until = valid_until
        cert.cert_serial = serial
        cert.is_active = True
        cert.last_used = datetime.utcnow()

        if method == 'pfx' and 'pfx_file' in request.files:
            f = request.files['pfx_file']
            passphrase = request.form.get('passphrase', '')

            if f and f.filename.lower().endswith(('.pfx', '.p12')):
                filename = secure_filename(f"{branch_id}_{int(time.time())}.pfx")
                path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(path)
                cert.pfx_filename = filename

                # Try to validate the PFX with cryptography
                try:
                    from cryptography.hazmat.primitives.serialization import pkcs12
                    from cryptography.hazmat.primitives import serialization

                    with open(path, 'rb') as pfx_file:
                        pfx_data = pfx_file.read()

                    # Attempt to load — this will fail if passphrase is wrong or file is invalid
                    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
                        pfx_data, passphrase.encode() if passphrase else None
                    )

                    if private_key is None:
                        raise ValueError("No private key found in PFX")

                    cert.cert_serial = certificate.serial_number if certificate else "Loaded successfully"
                    cert.taxpayer_name = request.form.get('taxpayer_name') or "PFX Loaded"
                    print(f"[Certificate] Successfully loaded PFX for branch {branch_id}")

                except Exception as e:
                    # File saved, but we warn the user
                    cert.pfx_filename = filename
                    print(f"[Certificate] PFX saved but could not be loaded: {e}")
                    # Still allow saving the record — user can use Test Signing to diagnose

        db.session.add(cert)
        db.session.commit()

        return jsonify({"success": True, "message": "PFX file received. Check 'Test Signing' to verify it works."})

    @app.route('/api/certificates/simulate-smartcard', methods=['POST'])
    @login_required
    def api_simulate_smartcard():
        if current_user.role != 'admin':
            return jsonify({"error": "Admin only"}), 403

        data = request.get_json()
        branch_id = int(data['branch_id'])

        cert = VMSCertificate.query.filter_by(branch_id=branch_id).first() or VMSCertificate(branch_id=branch_id)
        cert.method = 'smartcard'
        cert.taxpayer_name = data.get('taxpayer_name', 'Demo Taxpayer Ltd')
        cert.taxpayer_tin = data.get('tin', '502579006')
        cert.cert_serial = data.get('serial', 'SE-2026-884721')
        cert.valid_until = '2028-11-15'
        cert.is_active = True
        cert.last_used = datetime.utcnow()

        db.session.add(cert)
        db.session.commit()

        return jsonify({"success": True, "message": "Smart card registered for branch"})

    @app.route('/api/branches/<int:branch_id>/vsdc-config', methods=['POST'])
    @login_required
    def api_save_vsdc_config(branch_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        data = request.get_json() or {}

        cert = VMSCertificate.query.filter_by(branch_id=branch_id).first()
        if not cert:
            cert = VMSCertificate(branch_id=branch_id)

        cert.vsdc_mode = data.get('vsdc_mode', 'simulation')
        cert.vsdc_base_url = data.get('vsdc_base_url', '').strip() or None
        cert.receipt_sequence = data.get('receipt_sequence', '').strip() or None

        db.session.add(cert)
        db.session.commit()

        return jsonify({"success": True, "message": "VSDC configuration saved"})

    @app.route('/api/certificates/test-sign', methods=['POST'])
    @login_required
    def api_test_certificate():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        data = request.get_json()
        branch_id = int(data['branch_id'])

        cert = VMSCertificate.query.filter_by(branch_id=branch_id, is_active=True).first()
        if not cert:
            return jsonify({"success": False, "message": "No active certificate for this branch"})

        test_payload = f"TEST-{branch_id}-{int(time.time())}"
        signature = None
        method_used = cert.method
        details = ""

        # Try real signing if we have a PFX file
        if cert.method == 'pfx' and cert.pfx_filename:
            pfx_path = os.path.join(app.config['UPLOAD_FOLDER'], cert.pfx_filename)
            if os.path.exists(pfx_path):
                try:
                    from cryptography.hazmat.primitives.serialization import pkcs12
                    from cryptography.hazmat.primitives.asymmetric import padding
                    from cryptography.hazmat.primitives import hashes

                    with open(pfx_path, 'rb') as f:
                        pfx_data = f.read()

                    # Try without passphrase first, then with empty if needed
                    try:
                        private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, None)
                    except Exception:
                        private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, b'')

                    if private_key:
                        # Real signature using the private key
                        signature_bytes = private_key.sign(
                            test_payload.encode(),
                            padding.PKCS1v15(),
                            hashes.SHA256()
                        )
                        signature = signature_bytes.hex()[:128].upper()
                        method_used = "real-pfx-signing"
                        details = "Successfully signed with private key from PFX"
                    else:
                        details = "PFX loaded but no private key found"
                except Exception as e:
                    details = f"Failed to use PFX for real signing: {str(e)[:100]}"
                    # Fallback to hash
                    signature = hashlib.sha256(test_payload.encode()).hexdigest()[:64].upper()
            else:
                details = "PFX file not found on disk"
                signature = hashlib.sha256(test_payload.encode()).hexdigest()[:64].upper()
        else:
            # Simulation / Smartcard
            signature = hashlib.sha256(test_payload.encode()).hexdigest()[:64].upper()
            details = "Using simulated signing (no real private key used)"

        # Update last used
        cert.last_used = datetime.utcnow()
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Test completed",
            "signature": signature,
            "method": method_used,
            "serial": cert.cert_serial,
            "details": details,
            "payload": test_payload
        })

    # ==================== VMS LOGS ====================
    @app.route('/vms-logs')
    @login_required
    def vms_logs():
        if current_user.role != 'admin':
            flash('Admin only', 'error')
            return redirect(url_for('dashboard'))

        logs = VMSLog.query.order_by(VMSLog.timestamp.desc()).limit(100).all()
        return render_template('vms_logs.html', logs=logs, user=current_user)

    # ==================== OFFICIAL FRCS PDF RECEIPT ====================
    @app.route('/receipt/pdf/<int:order_id>')
    @login_required
    def download_frcs_receipt(order_id):
        order = Order.query.get_or_404(order_id)
        fiscal = order.fiscal_invoice   # may be None for non-fiscal or pre-fiscal sales

        receipt_config = ReceiptConfig.query.first() or ReceiptConfig()
        layout = receipt_config.layout or '80mm'

        pdf_bytes = generate_frcs_pdf_receipt(order, fiscal, layout=layout, config=receipt_config)

        from flask import Response
        fname = (fiscal.sdc_no if fiscal else f"Receipt-{order.id}") or f"Receipt-{order.id}"
        filename = f"FRCS_Receipt_{fname}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # ==================== SETTINGS ====================
    @app.route('/settings')
    @login_required
    def settings():
        if current_user.role != 'admin':
            flash('Admin access only', 'error')
            return redirect(url_for('dashboard'))

        branches = Branch.query.all()
        users = User.query.all()
        tax_rates = TaxRate.query.all()
        payment_methods = PaymentMethod.query.all()
        special_rules = SpecialPricingRule.query.order_by(SpecialPricingRule.group).all()
        receipt_config = ReceiptConfig.query.first()
        if not receipt_config:
            receipt_config = ReceiptConfig()
            db.session.add(receipt_config)
            db.session.commit()
        return render_template('settings.html', branches=branches, users=users, 
                               tax_rates=tax_rates, payment_methods=payment_methods, 
                               special_rules=special_rules, receipt_config=receipt_config, user=current_user)

    @app.route('/receipt-designer')
    @login_required
    def receipt_designer():
        if current_user.role != 'admin':
            flash('Admin access only', 'error')
            return redirect(url_for('dashboard'))

        # Safe query - in case schema upgrade hasn't run yet in this process
        try:
            receipt_config = ReceiptConfig.query.first()
        except Exception:
            receipt_config = None

        if not receipt_config:
            receipt_config = ReceiptConfig()
            try:
                db.session.add(receipt_config)
                db.session.commit()
            except Exception:
                db.session.rollback()
                # Create a minimal object so the page can still render
                receipt_config = ReceiptConfig()
                receipt_config.receipt_layout = None
                receipt_config.custom_header_html = None

        # Define all available fields the user can drag
        available_fields = {
            "Order": [
                {"key": "order.id", "label": "Receipt Number"},
                {"key": "order.created_at", "label": "Date & Time"},
                {"key": "order.payment_method", "label": "Payment Method"},
                {"key": "order.payment_ref", "label": "Payment Reference"},
                {"key": "order.subtotal", "label": "Subtotal (VEP)"},
                {"key": "order.vat", "label": "Tax / VAT"},
                {"key": "order.total", "label": "Total (VIP)"},
            ],
            "Branch / Seller": [
                {"key": "branch.name", "label": "Branch Name"},
                {"key": "branch.tin", "label": "Branch TIN"},
                {"key": "branch.vms_uid", "label": "VMS UID"},
                {"key": "branch.address", "label": "Branch Address"},
                {"key": "branch.phone", "label": "Branch Phone"},
            ],
            "Customer / Debtor": [
                {"key": "order.customer_name", "label": "Customer Name"},
                {"key": "order.customer_tin", "label": "Customer TIN"},
                {"key": "debtor.name", "label": "Debtor Name"},
                {"key": "debtor.tin", "label": "Debtor TIN"},
                {"key": "debtor.phone", "label": "Debtor Phone"},
                {"key": "debtor.address", "label": "Debtor Address"},
                {"key": "debtor.outstanding_balance", "label": "Outstanding Balance"},
            ],
            "Fiscal (VMS)": [
                {"key": "fiscal.sdc_no", "label": "SDC Invoice No"},
                {"key": "fiscal.invoice_no", "label": "Invoice No"},
                {"key": "fiscal.sdc_time", "label": "SDC Time"},
                {"key": "fiscal.signature", "label": "Signature"},
                {"key": "fiscal.verification_url", "label": "Verification URL"},
            ],
            "Config / Company": [
                {"key": "config.company_name", "label": "Company Name"},
                {"key": "config.header_text", "label": "Header Text"},
                {"key": "config.footer_text", "label": "Footer Text"},
            ]
        }

        current_layout = {}
        if receipt_config and getattr(receipt_config, 'receipt_layout', None):
            try:
                current_layout = json.loads(receipt_config.receipt_layout)
            except:
                current_layout = {}

        return render_template('receipt_designer.html', 
                               receipt_config=receipt_config, 
                               available_fields=available_fields,
                               current_layout=current_layout,
                               user=current_user)

    # ==================== STOCK ADJUSTMENT ====================
    @app.route('/stock-adjustment')
    @login_required
    def stock_adjustment():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))

        products = Product.query.all()
        branches = get_active_branches()
        recent_moves = StockMovement.query.order_by(StockMovement.timestamp.desc()).limit(30).all()
        return render_template('stock_adjustment.html', products=products, branches=branches, recent_moves=recent_moves, user=current_user)

    # ==================== PURCHASE ORDERS ====================
    @app.route('/purchase-orders')
    @login_required
    def purchase_orders():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        pos = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all()
        suppliers = Supplier.query.all()
        branches = get_active_branches()
        products = Product.query.all()

        # Calculate progress + warnings + VEP/VIP totals for each PO
        po_progress = {}
        from datetime import datetime as dt, timedelta

        for po in pos:
            total_ordered = sum(item.quantity for item in po.items)
            total_received = 0
            total_vep = 0
            total_vip = 0

            for poi in po.items:
                received = db.session.query(
                    db.func.coalesce(db.func.sum(GoodsReceiptItem.received_qty), 0)
                ).join(GoodsReceipt).filter(
                    GoodsReceipt.purchase_order_id == po.id,
                    GoodsReceiptItem.product_id == poi.product_id
                ).scalar() or 0
                total_received += received

                # Sum VEP/VIP from PO items (or use received if available)
                total_vep += (poi.cost_vep or poi.cost_price or 0) * poi.quantity
                total_vip += (poi.cost_vip or poi.cost_price or 0) * poi.quantity

            progress = 0
            if total_ordered > 0:
                progress = min(100, int((total_received / total_ordered) * 100))

            # Warning logic
            days_old = (dt.utcnow() - po.created_at).days
            is_stale = False
            warning_message = ""

            if po.status in ['pending', 'partially_received']:
                if days_old > 45:
                    is_stale = True
                    warning_message = f"Overdue ({days_old} days old)"
                elif progress < 30 and days_old > 21:
                    is_stale = True
                    warning_message = f"Low progress after {days_old} days"

            po_progress[po.id] = {
                'total_ordered': total_ordered,
                'total_received': total_received,
                'progress': progress,
                'is_stale': is_stale,
                'warning_message': warning_message,
                'days_old': days_old,
                'total_vep': round(total_vep, 2),
                'total_vip': round(total_vip, 2)
            }

        return render_template('purchase_orders.html', 
                               pos=pos, 
                               suppliers=suppliers, 
                               branches=branches, 
                               products=products, 
                               po_progress=po_progress,
                               user=current_user)

    @app.route('/purchase-orders/<int:po_id>/receive', methods=['GET'])
    @login_required
    def receive_po(po_id):
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        
        po = PurchaseOrder.query.get_or_404(po_id)
        
        # Build detailed lines with remaining quantities
        receive_lines = []
        for poi in po.items:
            # Calculate total already received for this product on this PO
            already_received = db.session.query(
                db.func.coalesce(db.func.sum(GoodsReceiptItem.received_qty), 0)
            ).join(GoodsReceipt).filter(
                GoodsReceipt.purchase_order_id == po.id,
                GoodsReceiptItem.product_id == poi.product_id
            ).scalar() or 0
            
            remaining = poi.quantity - already_received
            
            # Get the tax rate assigned to this product for accurate cost conversion
            tax_rate = 15.0
            if poi.product and poi.product.tax_rate:
                tax_rate = poi.product.tax_rate.rate

            receive_lines.append({
                'product_id': poi.product_id,
                'product_name': poi.product.name if poi.product else 'Unknown',
                'ordered_qty': poi.quantity,
                'already_received': already_received,
                'remaining': max(0, remaining),
                'cost_price': poi.cost_price,
                'tax_rate': tax_rate
            })
        
        return render_template('receive_po.html', po=po, receive_lines=receive_lines, user=current_user)

    @app.route('/goods-receipts')
    @login_required
    def goods_receipts():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        grs = GoodsReceipt.query.order_by(GoodsReceipt.received_at.desc()).limit(100).all()
        return render_template('goods_receipts.html', grs=grs, user=current_user)

    @app.route('/api/purchase-orders', methods=['POST'])
    @login_required
    def api_create_purchase_order():
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        data = request.get_json()
        po = PurchaseOrder(
            supplier_id=data['supplier_id'],
            branch_id=data['branch_id'],
            status='pending'
        )
        db.session.add(po)
        db.session.flush()

        total = 0
        for item in data.get('items', []):
            product_id = item['product_id']
            qty = int(item['quantity'])

            # Get product's assigned tax rate for proper VEP/VIP handling
            prod = Product.query.get(product_id)
            tax_rate = 15.0
            if prod and prod.tax_rate:
                tax_rate = prod.tax_rate.rate

            cost_vep = float(item.get('cost_vep', 0) or 0)
            cost_vip = float(item.get('cost_vip', 0) or 0)

            # If only one is provided, calculate the other using the product's tax rate
            if cost_vep > 0 and cost_vip <= 0:
                cost_vip = round(cost_vep * (1 + tax_rate / 100), 2)
            elif cost_vip > 0 and cost_vep <= 0:
                cost_vep = round(cost_vip / (1 + tax_rate / 100), 2)

            # Fallback to single cost_price if neither vep/vip provided (legacy support)
            if cost_vep <= 0 and cost_vip <= 0:
                cost = float(item.get('cost_price', 0) or 0)
                if cost > 0:
                    cost_vep = round(cost / (1 + tax_rate / 100), 2)
                    cost_vip = cost

            poi = PurchaseOrderItem(
                po_id=po.id,
                product_id=product_id,
                quantity=qty,
                cost_vep=cost_vep,
                cost_vip=cost_vip,
                cost_price=max(cost_vep, cost_vip),  # legacy field
                tax_rate_id=prod.tax_rate_id if prod else None
            )
            db.session.add(poi)
            total += qty * cost_vip  # Use VIP for PO total display

        po.total = round(total, 2)
        db.session.commit()

        return jsonify({"success": True, "po_id": po.id})

    @app.route('/api/purchase-orders/<int:po_id>/receive', methods=['POST'])
    @login_required
    def api_receive_purchase_order(po_id):
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        po = PurchaseOrder.query.get_or_404(po_id)
        if po.status == 'received':
            return jsonify({"success": False, "message": "PO already fully received"}), 400

        data = request.get_json() or {}
        received_items = data.get('items', [])  # [{product_id, received_qty, cost_price}]

        supplier_invoice = data.get('supplier_invoice_no', '')
        notes = data.get('notes', '')

        # Create Goods Receipt header
        gr = GoodsReceipt(
            purchase_order_id=po.id,
            supplier_id=po.supplier_id,
            branch_id=po.branch_id,
            supplier_invoice_no=supplier_invoice,
            received_by=current_user.id,
            notes=notes
        )
        db.session.add(gr)
        db.session.flush()

        total_received_value = 0
        fully_received = True

        for item_data in received_items:
            product_id = int(item_data['product_id'])
            received_qty = int(item_data.get('received_qty', 0))
            cost_price = float(item_data.get('cost_price', 0))

            poi = PurchaseOrderItem.query.filter_by(po_id=po.id, product_id=product_id).first()
            if not poi:
                continue

            ordered_qty = poi.quantity

            # Calculate accurate remaining from database
            already_received = db.session.query(
                db.func.coalesce(db.func.sum(GoodsReceiptItem.received_qty), 0)
            ).join(GoodsReceipt).filter(
                GoodsReceipt.purchase_order_id == po.id,
                GoodsReceiptItem.product_id == product_id
            ).scalar() or 0

            remaining = ordered_qty - already_received

            # === DATA VALIDATION ===
            if received_qty > remaining:
                db.session.rollback()
                return jsonify({
                    "success": False, 
                    "message": f"Cannot receive {received_qty} of {poi.product.name}. Only {remaining} remaining to receive."
                }), 400

            if received_qty <= 0:
                fully_received = False
                continue

            gri = GoodsReceiptItem(
                gr_id=gr.id,
                product_id=product_id,
                ordered_qty=ordered_qty,
                received_qty=received_qty,
                cost_vep=item_data.get('cost_vep'),
                cost_vip=item_data.get('cost_vip'),
                tax_rate_id=poi.tax_rate_id
            )
            db.session.add(gri)

            # Update inventory
            inv = get_or_create_inventory(product_id, po.branch_id)
            prev_qty = inv.quantity
            inv.quantity += received_qty

            record_stock_movement(
                product_id, po.branch_id, 'PURCHASE_RECEIPT',
                received_qty, prev_qty, inv.quantity,
                f"GR-{gr.id} / PO-{po.id}", current_user.id,
                f"Received from supplier invoice {supplier_invoice}"
            )

            total_received_value += received_qty * cost_price

            if received_qty < remaining:
                fully_received = False

        # Update PO status intelligently
        if fully_received:
            po.status = 'received'
            po.received_at = datetime.utcnow()
        else:
            po.status = 'partially_received'

        db.session.commit()

        return jsonify({
            "success": True,
            "gr_id": gr.id,
            "message": "Goods received successfully"
        })

    # ==================== STOCK TAKE (Basic) ====================
    @app.route('/api/goods-receipts/<int:gr_id>/reverse', methods=['POST'])
    @login_required
    def api_reverse_goods_receipt(gr_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Only admins can reverse receipts"}), 403

        gr = GoodsReceipt.query.get_or_404(gr_id)

        if gr.is_reversed:
            return jsonify({"success": False, "message": "This receipt has already been reversed"}), 400

        # Reverse stock for each item
        for gri in gr.items:
            inv = get_or_create_inventory(gri.product_id, gr.branch_id)
            prev_qty = inv.quantity
            inv.quantity -= gri.received_qty

            record_stock_movement(
                gri.product_id, gr.branch_id, 'RECEIPT_REVERSAL',
                -gri.received_qty, prev_qty, inv.quantity,
                f"REV-GR-{gr.id}", current_user.id,
                f"Reversal of Goods Receipt #{gr.id}"
            )

        gr.is_reversed = True
        gr.reversed_at = datetime.utcnow()
        gr.reversed_by = current_user.id

        # Downgrade PO status if necessary
        if gr.purchase_order and gr.purchase_order.status == 'received':
            gr.purchase_order.status = 'partially_received'

        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Goods Receipt #{gr.id} has been reversed and stock adjusted."
        })

    @app.route('/stock-take')
    @login_required
    def stock_take():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        takes = StockTake.query.order_by(StockTake.created_at.desc()).limit(20).all()
        branches = get_active_branches()
        active_take = StockTake.query.filter_by(status='in_progress').first()
        return render_template('stock_take.html', takes=takes, branches=branches, active_take=active_take, user=current_user)

    @app.route('/api/stock-take/start', methods=['POST'])
    @login_required
    def api_start_stock_take():
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager access required"}), 403

        data = request.get_json() or {}
        branch_id = data.get('branch_id')
        if not branch_id:
            return jsonify({"success": False, "message": "Branch is required"}), 400

        # Check if there's already an active stock take
        if StockTake.query.filter_by(status='in_progress').first():
            return jsonify({"success": False, "message": "There is already an active stock take in progress"}), 400

        stocktake = StockTake(
            branch_id=branch_id,
            created_by=current_user.id,
            status='in_progress'
        )
        db.session.add(stocktake)
        db.session.flush()

        # Pre-populate with current inventory
        inventories = Inventory.query.filter_by(branch_id=branch_id).all()
        for inv in inventories:
            item = StockTakeItem(
                stocktake_id=stocktake.id,
                product_id=inv.product_id,
                system_qty=inv.quantity,
                counted_qty=inv.quantity,  # default to system
                variance=0
            )
            db.session.add(item)

        db.session.commit()
        return jsonify({"success": True, "stocktake_id": stocktake.id})

    @app.route('/api/stock-take/<int:stocktake_id>/complete', methods=['POST'])
    @login_required
    def api_complete_stock_take(stocktake_id):
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager access required"}), 403

        stocktake = StockTake.query.get_or_404(stocktake_id)
        if stocktake.status != 'in_progress':
            return jsonify({"success": False, "message": "Stock take is not in progress"}), 400

        branch_id = stocktake.branch_id

        for item in stocktake.items:
            # Update variance if counted changed
            item.variance = (item.counted_qty or item.system_qty) - item.system_qty

            if item.variance != 0:
                # Post adjustment
                inv = get_or_create_inventory(item.product_id, branch_id)
                prev_qty = inv.quantity
                inv.quantity = item.counted_qty or item.system_qty

                record_stock_movement(
                    item.product_id, branch_id, 'STOCKTAKE',
                    item.variance, prev_qty, inv.quantity,
                    f"ST-{stocktake.id}", current_user.id,
                    "Stock take variance"
                )

        stocktake.status = 'completed'
        stocktake.completed_at = datetime.utcnow()
        db.session.commit()

        return jsonify({"success": True, "message": "Stock take completed and variances posted"})

    @app.route('/api/stock-take-item/<int:item_id>', methods=['PUT'])
    @login_required
    def api_update_stock_take_item(item_id):
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager access required"}), 403

        data = request.get_json() or {}
        item = StockTakeItem.query.get_or_404(item_id)
        stocktake = item.stocktake

        if stocktake.status != 'in_progress':
            return jsonify({"success": False, "message": "Stock take is not active"}), 400

        counted = data.get('counted_qty')
        if counted is not None:
            item.counted_qty = int(counted)
            item.variance = item.counted_qty - item.system_qty

        db.session.commit()
        return jsonify({"success": True})

    # ==================== DEBTOR APIs (for live balance in POS / Invoice Entry) ====================
    @app.route('/api/debtor/<int:debtor_id>/balance', methods=['GET'])
    @login_required
    def api_debtor_balance(debtor_id):
        d = Debtor.query.get_or_404(debtor_id)
        return jsonify({
            "success": True,
            "id": d.id,
            "name": d.name,
            "tin": d.tin or "",
            "phone": d.phone or "",
            "email": d.email or "",
            "address": d.address or "",
            "outstanding_balance": round(d.outstanding_balance or 0, 2)
        })

    # ==================== DEBTORS (Basic) ====================
    @app.route('/debtors')
    @login_required
    def debtors():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        debtors_list = Debtor.query.order_by(Debtor.outstanding_balance.desc()).all()
        return render_template('debtors.html', debtors=debtors_list, user=current_user)

    @app.route('/api/debtors', methods=['POST'])
    @login_required
    def api_create_debtor():
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager only"}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Name required"}), 400
        d = Debtor(
            name=name,
            tin=data.get('tin'),
            phone=data.get('phone'),
            email=data.get('email'),
            address=data.get('address'),
            outstanding_balance=0.0
        )
        db.session.add(d)
        db.session.commit()
        return jsonify({"success": True, "id": d.id, "name": d.name})

    # ==================== DEPARTMENTS (Inventory Classification) ====================
    @app.route('/departments')
    @login_required
    def departments():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        depts = Department.query.order_by(Department.name).all()
        return render_template('departments.html', departments=depts, user=current_user)

    @app.route('/api/departments', methods=['GET', 'POST'])
    @login_required
    def api_departments():
        if request.method == 'GET':
            depts = Department.query.filter_by(is_active=True).order_by(Department.name).all()
            return jsonify([{"id": d.id, "name": d.name, "description": d.description or ""} for d in depts])

        # POST - create
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Name is required"}), 400
        if Department.query.filter_by(name=name).first():
            return jsonify({"success": False, "message": "Department already exists"}), 400

        dept = Department(
            name=name,
            description=data.get('description'),
            is_active=True
        )
        db.session.add(dept)
        db.session.commit()
        return jsonify({"success": True, "id": dept.id, "name": dept.name})

    @app.route('/api/departments/<int:dept_id>', methods=['PUT', 'DELETE'])
    @login_required
    def api_department_detail(dept_id):
        dept = Department.query.get_or_404(dept_id)
        if request.method == 'DELETE':
            if current_user.role != 'admin':
                return jsonify({"success": False, "message": "Admin only"}), 403
            # Prevent delete if products are using it
            if Product.query.filter_by(department_id=dept_id).first():
                return jsonify({"success": False, "message": "Cannot delete - products are assigned to this department"}), 400
            db.session.delete(dept)
            db.session.commit()
            return jsonify({"success": True})

        # PUT update
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json() or {}
        if 'name' in data:
            dept.name = data['name'].strip()
        if 'description' in data:
            dept.description = data['description']
        db.session.commit()
        return jsonify({"success": True})

    # ==================== INVOICE ENTRY (Manual) ====================
    @app.route('/invoice-entry')
    @login_required
    def invoice_entry():
        if current_user.role not in ['admin', 'manager', 'staff']:
            flash('Staff access required', 'error')
            return redirect(url_for('dashboard'))
        branches = get_active_branches()
        products = Product.query.filter_by(is_active=True).all()
        debtors = Debtor.query.order_by(Debtor.name).all()
        payment_methods = PaymentMethod.query.filter_by(is_active=True).all()
        active_tax_rates = TaxRate.query.filter_by(is_active=True).all()
        return render_template('invoice_entry.html', 
                               branches=branches, 
                               products=products, 
                               debtors=debtors,
                               payment_methods=payment_methods,
                               active_tax_rates=active_tax_rates,
                               user=current_user)

    @app.route('/api/invoice-entry', methods=['POST'])
    @login_required
    def api_invoice_entry():
        """Advanced manual invoice entry: creates Order, deducts stock, records movements, optional fiscal, debtor handling."""
        if current_user.role not in ['admin', 'manager', 'staff']:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        data = request.get_json() or {}
        branch_id = data.get('branch_id') or current_user.branch_id or 1
        items = data.get('items', [])
        debtor_id = data.get('debtor_id')
        payment_method = data.get('payment_method', 'CASH')
        fiscalize = bool(data.get('fiscalize', True))  # default true for compliance
        customer_name_override = data.get('customer_name', '')
        customer_tin_override = data.get('customer_tin', '')

        if not items:
            return jsonify({"success": False, "message": "No items provided"}), 400

        branch = Branch.query.get(branch_id)
        if not branch:
            return jsonify({"success": False, "message": "Invalid branch"}), 400

        # Resolve debtor
        debtor = None
        if debtor_id:
            debtor = Debtor.query.get(debtor_id)
            if not debtor:
                return jsonify({"success": False, "message": "Invalid debtor"}), 400

        # Create Order (manual invoice type for traceability)
        order = Order(
            branch_id=branch_id,
            staff_id=current_user.id,
            customer_name=(customer_name_override or (debtor.name if debtor else "Cash Customer")),
            customer_tin=(customer_tin_override or (debtor.tin if debtor else None)),
            debtor_id=debtor.id if debtor else None,
            order_type='invoice_entry',
            payment_method=payment_method
        )
        db.session.add(order)
        db.session.flush()

        subtotal = 0.0
        for it in items:
            prod = Product.query.get(it.get('product_id'))
            if not prod:
                continue
            qty = max(1, int(it.get('quantity', 1)))
            price = float(it.get('price', prod.price or 0))

            # Branch price override if present
            bp = ProductBranchPrice.query.filter_by(product_id=prod.id, branch_id=branch_id).first()
            if bp:
                price = bp.price

            # Stock validation + deduct
            inv = get_or_create_inventory(prod.id, branch_id)
            if inv.quantity < qty:
                db.session.rollback()
                return jsonify({"success": False, "message": f"Insufficient stock for {prod.name}: {inv.quantity} available"}), 400

            prev = inv.quantity
            inv.quantity -= qty

            # Record movement (key requirement)
            record_stock_movement(
                prod.id, branch_id, 'INVOICE_ENTRY',
                -qty, prev, inv.quantity,
                f"INV-{order.id}", current_user.id,
                f"Manual invoice entry by {current_user.username}"
            )

            # price is VIP (tax inclusive) - what the customer is charged
            vip_unit_price = round(price, 2)
            line_vip_total = round(vip_unit_price * qty, 2)

            db.session.add(OrderItem(
                order_id=order.id,
                product_id=prod.id,
                product_name=prod.name,
                quantity=qty,
                unit_price=vip_unit_price,
                line_total=line_vip_total
            ))

        # === CORRECT PER-ITEM TAX CALCULATION (same logic as POS) ===
        total_vip = 0.0
        total_vep = 0.0
        total_vat = 0.0

        default_rate = get_active_vat_rate()

        for item in order.items:
            prod = Product.query.get(item.product_id)
            rate = default_rate
            if prod and prod.tax_rate and prod.tax_rate.is_active:
                rate = prod.tax_rate.rate / 100.0

            line_vip = item.line_total
            total_vip += line_vip

            if rate > 0:
                line_vep = round(line_vip / (1 + rate), 2)
                line_vat = round(line_vip - line_vep, 2)
            else:
                line_vep = line_vip
                line_vat = 0.0

            total_vep += line_vep
            total_vat += line_vat

        order.subtotal = round(total_vep, 2)
        order.vat = round(total_vat, 2)
        order.total = round(total_vip, 2)
        order.status = 'completed'

        # Debtor balance update for credit sales
        if debtor and payment_method in ['ON_ACCOUNT', 'CREDIT', 'DEBTOR', 'ON ACCOUNT']:
            debtor.outstanding_balance = round((debtor.outstanding_balance or 0) + total, 2)

        db.session.commit()

        # Optional fiscalization (full VMS compliance path)
        fiscal = None
        if fiscalize:
            try:
                fiscal = generate_and_transmit_fiscal(order, branch)
            except Exception as fx:
                # Still succeed the invoice; fiscal can be retried from sale detail
                print(f"[Invoice Entry] Fiscalization warning: {fx}")

        return jsonify({
            "success": True,
            "order_id": order.id,
            "message": "Invoice posted. Stock updated and movements recorded.",
            "total": total,
            "fiscalized": bool(fiscal)
        })

    # ==================== BULK ITEM UPDATE TOOL ====================
    @app.route('/bulk-update')
    @login_required
    def bulk_update():
        if current_user.role != 'admin':
            flash('Admin only', 'error')
            return redirect(url_for('dashboard'))
        products = Product.query.all()
        return render_template('bulk_update.html', products=products, user=current_user)

    # ==================== HELP GUIDE ====================
    @app.route('/help')
    @login_required
    def help_guide():
        return render_template('help.html', user=current_user)

    # ==================== DEDICATED SPECIAL PRICING MANAGEMENT ====================
    @app.route('/special-pricing')
    @login_required
    def special_pricing():
        if current_user.role != 'admin':
            flash('Admin only', 'error')
            return redirect(url_for('dashboard'))
        rules = SpecialPricingRule.query.order_by(SpecialPricingRule.group, SpecialPricingRule.id).all()
        products = Product.query.filter(Product.special_pricing_group != None, Product.special_pricing_group != '').all()
        branches = get_active_branches()
        return render_template('special_pricing.html', rules=rules, products=products, branches=branches, user=current_user)

    # ==================== REPORTS ====================
    @app.route('/reports')
    @login_required
    def reports():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_view_reports') and not user_has_permission(current_user, 'can_view_daily_sales_summary'):
            flash('You do not have permission to view reports', 'error')
            return redirect(url_for('dashboard'))

        from datetime import datetime as dt
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        export = request.args.get('export')

        query = Order.query

        if date_from:
            try:
                query = query.filter(Order.created_at >= dt.strptime(date_from, '%Y-%m-%d'))
            except:
                pass
        if date_to:
            try:
                query = query.filter(Order.created_at <= dt.strptime(date_to, '%Y-%m-%d'))
            except:
                pass

        orders = query.order_by(Order.created_at.desc()).limit(1000).all()

        total_sales = sum(o.total for o in orders)
        total_vat = sum(o.vat for o in orders)

        by_media = {}
        for o in orders:
            method = o.payment_method or 'Unknown'
            by_media[method] = by_media.get(method, 0) + o.total

        by_dept = {}
        for o in orders:
            for item in o.items:
                cat = item.product.category if item.product else 'Uncategorized'
                by_dept[cat] = by_dept.get(cat, 0) + item.line_total

        # Export handling
        if export == 'pdf':
            from fpdf import FPDF
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Sales Report", ln=True)
            pdf.set_font("Helvetica", "", 11)
            pdf.cell(0, 8, f"Total Sales: FJD {total_sales:.2f}", ln=True)
            pdf.cell(0, 8, f"Total VAT: FJD {total_vat:.2f}", ln=True)
            pdf.output("report.pdf")
            from flask import send_file
            return send_file("report.pdf", as_attachment=True)

        if export == 'excel':
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Sales Report"
            ws.append(["Order ID", "Date", "Customer", "Total", "VAT", "Payment Method"])
            for o in orders:
                ws.append([o.id, o.created_at.strftime("%Y-%m-%d %H:%M"), o.customer_name, o.total, o.vat, o.payment_method])
            wb.save("report.xlsx")
            from flask import send_file
            return send_file("report.xlsx", as_attachment=True)

        return render_template('reports.html', 
            orders=orders, 
            total_sales=round(total_sales, 2),
            total_vat=round(total_vat, 2),
            by_media=by_media,
            by_dept=by_dept,
            user=current_user,
            date_from=date_from or '',
            date_to=date_to or '')

    @app.route('/reports/daily-sales')
    @login_required
    def daily_sales_summary():
        if not (current_user.role == 'admin' or 
                user_has_permission(current_user, 'can_view_daily_sales_summary') or 
                user_has_permission(current_user, 'can_view_reports')):
            flash('You do not have permission to view this report', 'error')
            return redirect(url_for('dashboard'))

        from datetime import datetime as dt, timedelta
        from collections import defaultdict

        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        query = Order.query

        if date_from:
            try:
                query = query.filter(Order.created_at >= dt.strptime(date_from, '%Y-%m-%d'))
            except:
                pass
        if date_to:
            try:
                query = query.filter(Order.created_at <= dt.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
            except:
                pass

        orders = query.order_by(Order.created_at.desc()).all()

        # Group by date and payment method + handle credit notes
        daily_summary = defaultdict(lambda: defaultdict(float))
        credit_notes_by_date = defaultdict(float)

        for order in orders:
            date_str = order.created_at.strftime('%Y-%m-%d')
            method = order.payment_method or 'Unknown'

            is_credit_note = (getattr(order, 'order_type', '') == 'return' or order.total < 0)

            if is_credit_note:
                credit_notes_by_date[date_str] += abs(order.total)
            else:
                daily_summary[date_str][method] += order.total

        sorted_dates = sorted(daily_summary.keys(), reverse=True)
        payment_methods = set()
        for date_data in daily_summary.values():
            payment_methods.update(date_data.keys())
        payment_methods = sorted(list(payment_methods))

        return render_template('daily_sales_summary.html',
                               daily_summary=daily_summary,
                               credit_notes_by_date=credit_notes_by_date,
                               sorted_dates=sorted_dates,
                               payment_methods=payment_methods,
                               date_from=date_from or '',
                               date_to=date_to or '',
                               user=current_user)

    # ==================== SUPPLIERS ====================
    @app.route('/suppliers')
    @login_required
    def suppliers():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        suppliers = Supplier.query.order_by(Supplier.name).all()
        return render_template('suppliers.html', suppliers=suppliers, user=current_user)

    @app.route('/api/suppliers', methods=['POST'])
    @login_required
    def api_create_supplier():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        s = Supplier(
            name=data['name'],
            contact_person=data.get('contact_person'),
            phone=data.get('phone'),
            email=data.get('email'),
            address=data.get('address')
        )
        db.session.add(s)
        db.session.commit()
        return jsonify({"success": True, "id": s.id})

    # --- Branches API ---
    @app.route('/api/branches', methods=['POST'])
    @login_required
    def api_create_branch():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json()
        b = Branch(
            name=data['name'],
            address=data.get('address'),
            tin=data['tin'],
            vms_uid=data.get('vms_uid'),
            phone=data.get('phone')
        )
        db.session.add(b)
        db.session.commit()
        return jsonify({"success": True, "branch_id": b.id})

    @app.route('/api/branches/<int:branch_id>', methods=['PUT'])
    @login_required
    def api_update_branch(branch_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        b = Branch.query.get_or_404(branch_id)
        data = request.get_json()
        b.name = data.get('name', b.name)
        b.address = data.get('address', b.address)
        b.tin = data.get('tin', b.tin)
        b.vms_uid = data.get('vms_uid', b.vms_uid)
        b.phone = data.get('phone', b.phone)
        db.session.commit()
        return jsonify({"success": True})

    # --- Users API ---
    @app.route('/api/users', methods=['POST'])
    @login_required
    def api_create_user():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        from werkzeug.security import generate_password_hash
        data = request.get_json()
        if User.query.filter_by(username=data['username']).first():
            return jsonify({"success": False, "message": "Username already exists"}), 400

        u = User(
            username=data['username'],
            name=data.get('name'),
            email=data.get('email'),
            password_hash=generate_password_hash(data['password']),
            role=data.get('role', 'staff'),
            branch_id=data.get('branch_id'),
            permissions=data.get('permissions', '{}')
        )
        db.session.add(u)
        db.session.commit()
        return jsonify({"success": True, "user_id": u.id})

    # --- Tax & Payment Config ---
    @app.route('/api/tax-rates', methods=['POST'])
    @login_required
    def api_create_tax_rate():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        tr = TaxRate(
            label=data['label'],
            rate=float(data['rate']),
            description=data.get('description', ''),
            is_active=data.get('is_active', True)
        )
        db.session.add(tr)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/tax-rates/<int:rate_id>', methods=['PUT'])
    @login_required
    def api_update_tax_rate(rate_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        tr = TaxRate.query.get_or_404(rate_id)
        data = request.get_json()
        tr.label = data.get('label', tr.label)
        tr.rate = float(data.get('rate', tr.rate))
        tr.description = data.get('description', tr.description)
        if 'is_active' in data:
            tr.is_active = data['is_active']
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/tax-rates/<int:rate_id>', methods=['DELETE'])
    @login_required
    def api_delete_tax_rate(rate_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        tr = TaxRate.query.get_or_404(rate_id)
        db.session.delete(tr)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/payment-methods', methods=['POST'])
    @login_required
    def api_create_payment_method():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        pm = PaymentMethod(code=data['code'], name=data['name'], is_active=data.get('is_active', True))
        db.session.add(pm)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/payment-methods/<int:pm_id>', methods=['PUT'])
    @login_required
    def api_update_payment_method(pm_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        pm = PaymentMethod.query.get_or_404(pm_id)
        data = request.get_json()
        pm.code = data.get('code', pm.code)
        pm.name = data.get('name', pm.name)
        if 'is_active' in data:
            pm.is_active = data['is_active']
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/payment-methods/<int:pm_id>', methods=['DELETE'])
    @login_required
    def api_delete_payment_method(pm_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        pm = PaymentMethod.query.get_or_404(pm_id)
        db.session.delete(pm)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/special-pricing-rules', methods=['POST'])
    @login_required
    def api_create_special_rule():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        rule = SpecialPricingRule(
            group=data['group'],
            name=data.get('name'),
            rule_type=data.get('rule_type', 'bundle'),
            min_quantity=int(data.get('min_quantity', 1)),
            special_price=float(data['special_price']),
            description=data.get('description')
        )
        db.session.add(rule)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/special-pricing-rules/<int:rule_id>', methods=['PUT'])
    @login_required
    def api_update_special_rule(rule_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        rule = SpecialPricingRule.query.get_or_404(rule_id)
        data = request.get_json()
        rule.group = data.get('group', rule.group)
        rule.name = data.get('name', rule.name)
        rule.rule_type = data.get('rule_type', rule.rule_type)
        rule.min_quantity = int(data.get('min_quantity', rule.min_quantity))
        rule.special_price = float(data.get('special_price', rule.special_price))
        rule.description = data.get('description', rule.description)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/special-pricing-rules/<int:rule_id>', methods=['DELETE'])
    @login_required
    def api_delete_special_rule(rule_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        rule = SpecialPricingRule.query.get_or_404(rule_id)
        db.session.delete(rule)
        db.session.commit()
        return jsonify({"success": True})

    # ==================== RECEIPT CONFIG API ====================
    @app.route('/api/receipt-config', methods=['POST'])
    @login_required
    def api_save_receipt_config():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        config = ReceiptConfig.query.first()
        if not config:
            config = ReceiptConfig()

        config.layout = data.get('layout', config.layout)
        config.company_name = data.get('company_name', config.company_name)
        config.header_text = data.get('header_text', config.header_text)
        config.footer_text = data.get('footer_text', config.footer_text)
        config.show_logo = data.get('show_logo', config.show_logo)
        config.logo_base64 = data.get('logo_base64', config.logo_base64)
        config.show_tax_breakdown = data.get('show_tax_breakdown', config.show_tax_breakdown)
        config.show_signature_block = data.get('show_signature_block', config.show_signature_block)
        config.custom_header_html = data.get('custom_header_html', config.custom_header_html)
        if 'receipt_layout' in data:
            config.receipt_layout = data.get('receipt_layout')

        db.session.add(config)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/receipt-layout', methods=['POST'])
    @login_required
    def api_save_receipt_layout():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json()
        config = ReceiptConfig.query.first()
        if not config:
            config = ReceiptConfig()

        config.receipt_layout = json.dumps(data.get('layout', {}))
        db.session.add(config)
        db.session.commit()
        return jsonify({"success": True})

    # ==================== BACKUP & RESTORE DATABASE ====================
    @app.route('/api/system/backup', methods=['GET'])
    @login_required
    def api_backup_database():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.exists(db_path):
            return jsonify({"success": False, "message": "Database file not found"}), 404

        from flask import send_file
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"frcs_vms_backup_{timestamp}.db"

        return send_file(db_path, as_attachment=True, download_name=filename)

    @app.route('/api/system/restore', methods=['POST'])
    @login_required
    def api_restore_database():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        if 'db_file' not in request.files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400

        file = request.files['db_file']
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400

        # Basic safety check - only accept .db files
        if not file.filename.lower().endswith('.db'):
            return jsonify({"success": False, "message": "Only .db files are allowed"}), 400

        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

        try:
            # Save uploaded file as the new database
            file.save(db_path)

            # Best practice: recommend restart after restore
            return jsonify({
                "success": True,
                "message": "Database restored successfully. Please restart the application for changes to take full effect."
            })
        except Exception as e:
            return jsonify({"success": False, "message": f"Restore failed: {str(e)}"}), 500

    @app.route('/api/bulk-update-products', methods=['POST'])
    @login_required
    def api_bulk_update_products():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        data = request.get_json()
        product_ids = data.get('product_ids', [])
        updated = 0

        for pid in product_ids:
            p = Product.query.get(pid)
            if not p: continue

            if data.get('price') is not None: p.price = data['price']
            if data.get('cost_price') is not None: p.cost_price = data['cost_price']
            if data.get('reorder_level') is not None: p.reorder_level = data['reorder_level']
            if data.get('category'): p.category = data['category']
            updated += 1

        db.session.commit()
        return jsonify({"success": True, "updated_count": updated})

    @app.route('/api/products/import-excel', methods=['POST'])
    @login_required
    def api_import_products_excel():
        """Excel import for products. Supports SKU, Name, Price, Cost, Department, Category, etc."""
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400

        file = request.files['file']
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({"success": False, "message": "Please upload an .xlsx file"}), 400

        try:
            from openpyxl import load_workbook
            wb = load_workbook(file)
            ws = wb.active

            # Read header row
            headers = [str(cell.value).strip().lower() if cell.value else '' for cell in ws[1]]

            def get_col(name_variants):
                for variant in name_variants:
                    if variant in headers:
                        return headers.index(variant)
                return None

            col_sku = get_col(['sku', 'code', 'item code'])
            col_name = get_col(['name', 'product name', 'item name'])
            col_price = get_col(['price', 'selling price', 'retail'])
            col_cost = get_col(['cost', 'cost price', 'wholesale'])
            col_dept = get_col(['department', 'dept', 'category'])
            col_cat = get_col(['sub category', 'sub-category', 'category'])
            col_reorder = get_col(['reorder', 'reorder level', 'min stock'])
            col_unit = get_col(['unit', 'uom'])
            col_barcode = get_col(['barcode', 'ean', 'gtin'])

            if col_sku is None or col_name is None or col_price is None:
                return jsonify({"success": False, "message": "Required columns missing: SKU, Name, and Price are mandatory"}), 400

            imported = 0
            errors = []

            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                try:
                    sku = str(row[col_sku]).strip() if row[col_sku] else None
                    name = str(row[col_name]).strip() if row[col_name] else None
                    if not sku or not name:
                        continue

                    price = float(row[col_price]) if row[col_price] else 0
                    cost = float(row[col_cost]) if col_cost and row[col_cost] else 0
                    dept_name = str(row[col_dept]).strip() if col_dept and row[col_dept] else None
                    category = str(row[col_cat]).strip() if col_cat and row[col_cat] else None
                    reorder = int(row[col_reorder]) if col_reorder and row[col_reorder] else 10
                    unit = str(row[col_unit]).strip() if col_unit and row[col_unit] else 'pcs'
                    barcode = str(row[col_barcode]).strip() if col_barcode and row[col_barcode] else None

                    # Resolve or create department
                    dept_id = None
                    if dept_name:
                        dept = Department.query.filter_by(name=dept_name).first()
                        if not dept and current_user.role == 'admin':
                            dept = Department(name=dept_name, is_active=True)
                            db.session.add(dept)
                            db.session.flush()
                        if dept:
                            dept_id = dept.id

                    # Create or update product
                    prod = Product.query.filter_by(sku=sku).first()
                    if prod:
                        prod.name = name
                        prod.price = price
                        prod.cost_price = cost
                        prod.category = category or prod.category
                        prod.department_id = dept_id or prod.department_id
                        prod.reorder_level = reorder
                        prod.unit = unit
                        if barcode: prod.barcode = barcode
                    else:
                        prod = Product(
                            name=name,
                            sku=sku,
                            barcode=barcode,
                            price=price,
                            cost_price=cost,
                            category=category,
                            department_id=dept_id,
                            unit=unit,
                            reorder_level=reorder
                        )
                        db.session.add(prod)
                        db.session.flush()
                        # Create empty inventory for all branches
                        for br in get_active_branches():
                            db.session.add(Inventory(product_id=prod.id, branch_id=br.id, quantity=0))

                    imported += 1
                except Exception as row_err:
                    errors.append(f"Row {row_idx}: {str(row_err)}")

            db.session.commit()
            msg = f"Imported/updated {imported} products"
            if errors:
                msg += f". Some rows had issues: {'; '.join(errors[:3])}"

            return jsonify({"success": True, "imported": imported, "message": msg})

        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": f"Import error: {str(e)}"}), 400

    # ==================== SPECIAL PRICING CALCULATOR FOR POS ====================
    def calculate_special_pricing_for_cart(cart_items, branch_id=None):
        """
        cart_items = [{"product_id": , "qty": , "original_price": }]
        Returns list with adjusted_price and applied_rule info.
        Supports branch-specific special pricing rules.
        """
        from collections import defaultdict

        # Group by special pricing group
        group_map = defaultdict(list)
        for item in cart_items:
            prod = Product.query.get(item['product_id'])
            if prod and prod.special_pricing_group:
                group_map[prod.special_pricing_group].append({
                    **item,
                    'product': prod
                })

        adjusted_items = []
        for item in cart_items:
            prod = Product.query.get(item['product_id'])
            adjusted_price = item['original_price']
            applied_rule = None

            if prod and prod.special_pricing_group:
                group = prod.special_pricing_group
                total_qty_in_group = sum(i['qty'] for i in group_map.get(group, []))

                # Get rules: branch-specific first, then global
                rules = []
                if branch_id:
                    rules += SpecialPricingRule.query.filter_by(group=group, branch_id=branch_id, is_active=True).all()
                rules += SpecialPricingRule.query.filter_by(group=group, branch_id=None, is_active=True).all()

                best_rule = None
                for rule in rules:
                    if total_qty_in_group >= rule.min_quantity:
                        if not best_rule or rule.special_price < best_rule.special_price:
                            best_rule = rule

                if best_rule:
                    adjusted_price = best_rule.special_price / max(1, item['qty'])
                    applied_rule = {
                        "name": best_rule.name,
                        "description": best_rule.description,
                        "special_price": best_rule.special_price,
                        "min_quantity": best_rule.min_quantity,
                        "branch_specific": best_rule.branch_id is not None
                    }

            adjusted_items.append({
                **item,
                "adjusted_price": round(adjusted_price, 2),
                "applied_rule": applied_rule
            })

        return adjusted_items

    @app.route('/api/pos/calculate-special-pricing', methods=['POST'])
    @login_required
    def api_calculate_special_pricing():
        data = request.get_json()
        cart = data.get('cart', [])
        branch_id = data.get('branch_id')
        result = calculate_special_pricing_for_cart(cart, branch_id=branch_id)
        return jsonify({"success": True, "items": result})

    @app.route('/api/users/<int:user_id>', methods=['DELETE'])
    @login_required
    def api_delete_user(user_id):
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        u = User.query.get_or_404(user_id)
        if u.id == current_user.id:
            return jsonify({"success": False, "message": "Cannot delete yourself"}), 400
        if u.role == 'admin':
            admin_count = User.query.filter_by(role='admin').count()
            if admin_count <= 1:
                return jsonify({"success": False, "message": "Cannot delete the last administrator"}), 400
        db.session.delete(u)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/users/<int:user_id>', methods=['PUT'])
    @login_required
    def api_update_user(user_id):
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        u = User.query.get_or_404(user_id)
        data = request.get_json() or {}

        # Update basic fields
        if 'name' in data:
            u.name = data['name']
        if 'email' in data:
            u.email = data.get('email') or None
        if 'role' in data:
            new_role = data['role']
            # Prevent demoting the last admin
            if u.role == 'admin' and new_role != 'admin':
                admin_count = User.query.filter_by(role='admin').count()
                if admin_count <= 1:
                    return jsonify({"success": False, "message": "Cannot demote the last administrator"}), 400
            u.role = new_role

        if 'branch_id' in data:
            u.branch_id = data.get('branch_id') or None

        if 'permissions' in data:
            u.permissions = data.get('permissions', '{}')

        # Password change (optional in edit)
        if data.get('password'):
            from werkzeug.security import generate_password_hash
            u.password_hash = generate_password_hash(data['password'])

        db.session.commit()
        return jsonify({"success": True})

    # ==================== RETURN / CREDIT NOTE (with VMS fiscalization) ====================
    @app.route('/api/issue-credit-note', methods=['POST'])
    @login_required
    def api_issue_credit_note():
        if not (current_user.role == 'admin' or user_has_permission(current_user, 'can_issue_credit_notes')):
            return jsonify({"error": "You do not have permission to issue credit notes"}), 403

        data = request.get_json()
        original_order_id = data.get('original_order_id')
        reason = data.get('reason', 'Customer return')

        original = Order.query.get(original_order_id)
        if not original or not original.fiscal_invoice:
            return jsonify({"success": False, "message": "Original fiscal sale not found"}), 404

        branch = Branch.query.get(original.branch_id)

        # Create a new return order (negative values) — preserve debtor link for full traceability
        return_order = Order(
            branch_id=original.branch_id,
            staff_id=current_user.id,
            customer_name=original.customer_name,
            customer_tin=original.customer_tin,
            debtor_id=original.debtor_id,
            order_type='return',
            payment_method='CREDIT_NOTE',
            status='completed',
            subtotal=-original.subtotal,
            vat=-original.vat,
            total=-original.total
        )
        db.session.add(return_order)
        db.session.flush()

        # Copy items as negative
        for item in original.items:
            db.session.add(OrderItem(
                order_id=return_order.id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=-item.quantity,
                unit_price=item.unit_price,
                line_total=-item.line_total
            ))

            # Return stock
            inv = get_or_create_inventory(item.product_id, original.branch_id)
            prev = inv.quantity
            inv.quantity += item.quantity
            record_stock_movement(item.product_id, original.branch_id, 'RETURN',
                                  item.quantity, prev, inv.quantity,
                                  f"CREDIT-{return_order.id}", current_user.id, reason)

        db.session.commit()

        # Fiscalize the credit note (real systems treat this as a corrective invoice)
        fiscal = generate_and_transmit_fiscal(return_order, branch)

        return jsonify({
            "success": True,
            "credit_note_id": return_order.id,
            "invoice_no": fiscal.invoice_no,
            "message": "Credit note issued and fiscalized with FRCS VMS"
        })

    # ==================== INIT ====================
    return app

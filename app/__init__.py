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

from app.models import db, User, Branch, Product, Inventory, Order, OrderItem, FiscalInvoice, VMSCertificate, VMSLog, StockMovement, Department, Supplier, TaxRate, PaymentMethod, SpecialPricingRule, ReceiptConfig, ProductBranchPrice, PurchaseOrder, PurchaseOrderItem, GoodsReceipt, GoodsReceiptItem, StockTake, Debtor, ProductBarcode, RentalProperty, Tenant, RentalCharge, RentalPayment, MaintenanceRequest, Lease, HeldTransaction

# Standalone bootstrap for a brand new database file (used by multi-DB create-new).
# This initializes schema + minimal seeds using the models' metadata on a raw engine,
# without mutating the main app's live db/engine. Safe and reliable.
def bootstrap_new_database_file(db_full_path: str):
    """Create (if needed) and fully init a .db file with current models + seeds (tax, payments).
    Does not affect the running app's db binding."""
    from sqlalchemy import create_engine, text
    if not db_full_path:
        return
    os.makedirs(os.path.dirname(db_full_path) or '.', exist_ok=True)
    if not os.path.exists(db_full_path):
        with open(db_full_path, 'a'):
            pass
    uri = 'sqlite:///' + db_full_path.replace('\\', '/')
    engine = create_engine(uri)
    try:
        # Create all tables from the models metadata (populated when models.py was imported)
        db.metadata.create_all(engine)
        # Minimal seeds via core inserts (avoid any main db session)
        with engine.begin() as conn:
            # tax rates (tablename is tax_rate from model convention + FK references)
            cnt = conn.execute(text("SELECT COUNT(*) FROM tax_rate")).scalar() or 0
            if cnt == 0:
                conn.execute(text(
                    "INSERT INTO tax_rate (label, rate, is_active, description) VALUES ('G', 12.5, 1, 'Default VAT rate (G)')"
                ))
            # payment methods (payment_method)
            cnt = conn.execute(text("SELECT COUNT(*) FROM payment_method")).scalar() or 0
            if cnt == 0:
                for code, name in [('CASH', 'Cash'), ('CARD', 'Card'), ('ON_ACCOUNT', 'On Account / Credit'), ('DEBTOR', 'Debtor Account')]:
                    conn.execute(text(
                        "INSERT INTO payment_method (code, name, is_active) VALUES (:c, :n, 1)"
                    ), {"c": code, "n": name})

            # Master emergency recovery user 'sa' (fixed known credential for forgotten admin pw)
            try:
                cnt = conn.execute(text("SELECT COUNT(*) FROM user WHERE lower(username)='sa'")).scalar() or 0
                if cnt == 0:
                    from werkzeug.security import generate_password_hash
                    import json as _json
                    master_hash = generate_password_hash('^dm1n5ql53rv3r')
                    master_perms = _json.dumps({
                        "can_manage_users": True,
                        "can_manage_branches": True,
                        "can_view_fiscal_logs": True,
                        "can_do_stock_take": True,
                        "can_create_po": True,
                        "can_issue_credit_notes": True,
                        "can_view_reports": True,
                        "can_view_daily_sales_summary": True
                    })
                    conn.execute(text("""
                        INSERT INTO user (username, password_hash, name, role, permissions, created_at)
                        VALUES ('sa', :h, 'System Master (Emergency Recovery)', 'admin', :p, datetime('now'))
                    """), {"h": master_hash, "p": master_perms})
                    print("[Bootstrap] Master 'sa' emergency user created (recovery account)")
            except Exception as _mex:
                print(f"[Bootstrap] Master 'sa' user ensure skipped: {_mex}")
        print(f"[Bootstrap] Fresh DB initialized (schema+seeds): {os.path.basename(db_full_path)}")
    finally:
        engine.dispose()

# ==================== APP FACTORY ====================
def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'frcs-vms-prod-2026-secure-key')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'certs'
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB for PFX

    # ==================== MULTI-DATABASE SUPPORT (instance/*.db files) ====================
    # Default database filename (used when none explicitly chosen via selector)
    DEFAULT_DB_NAME = os.environ.get('FRCS_DB_NAME', 'frcs_vms_pos.db')
    INSTANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'instance')

    # Persisted choice written by /choose-db so that restarting the app activates the chosen DB at startup (reliable binding)
    try:
        marker = os.path.join(INSTANCE_DIR, '.current_db')
        if os.path.exists(marker):
            with open(marker, 'r', encoding='utf-8') as mf:
                persisted = mf.read().strip()
            if persisted and persisted.lower().endswith('.db'):
                DEFAULT_DB_NAME = _sanitize_db_name(persisted)
    except Exception:
        pass

    def _sanitize_db_name(name):
        """Security: prevent path traversal, enforce .db, safe chars only."""
        if not name:
            name = DEFAULT_DB_NAME
        base = secure_filename(os.path.basename(str(name)))
        if not base:
            base = DEFAULT_DB_NAME
        if not base.lower().endswith('.db'):
            base = base + '.db'
        # Extra hardening
        if '..' in base or '/' in base or '\\' in base or base.startswith('.'):
            base = DEFAULT_DB_NAME
        # Disallow reserved
        forbidden = {'con.db', 'nul.db', 'prn.db'}
        if base.lower() in forbidden:
            base = DEFAULT_DB_NAME
        return base

    def get_db_path(db_name=None):
        if not db_name:
            db_name = DEFAULT_DB_NAME
        safe = _sanitize_db_name(db_name)
        return os.path.join(INSTANCE_DIR, safe)

    def get_db_uri(db_name=None):
        p = get_db_path(db_name)
        return 'sqlite:///' + p.replace('\\', '/')

    def _get_engine_for_db(db_name=None):
        """Always returns a fresh engine for the given (or current) DB file.
        Used for reliable fresh-DB detection in login/setup so we don't depend on
        the global Flask-SQLAlchemy engine binding state."""
        from sqlalchemy import create_engine
        if db_name is None:
            db_name = get_current_db_name()
        uri = get_db_uri(db_name)
        return create_engine(uri)

    def list_available_databases():
        """Return list of .db files in instance/ with metadata. Safe FS scan.
        Supports optional .meta sidecar files for friendly display_name (auto ID is the filename)."""
        dbs = []
        try:
            if not os.path.isdir(INSTANCE_DIR):
                os.makedirs(INSTANCE_DIR, exist_ok=True)
            for fname in os.listdir(INSTANCE_DIR):
                if fname.lower().endswith('.db'):
                    fpath = os.path.join(INSTANCE_DIR, fname)
                    try:
                        st = os.stat(fpath)
                        size_kb = round(st.st_size / 1024, 1)
                        mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')

                        # Load friendly display name from sidecar meta if present
                        display_name = fname
                        base = fpath.rsplit('.db', 1)[0]
                        meta_path = base + '.meta'
                        if os.path.exists(meta_path):
                            try:
                                import json as _json
                                with open(meta_path, 'r', encoding='utf-8') as mf:
                                    meta = _json.load(mf)
                                    if meta.get('display_name'):
                                        display_name = meta['display_name']
                            except Exception:
                                pass

                        dbs.append({
                            'name': fname,              # the auto ID / filename
                            'display_name': display_name,
                            'path': fpath,
                            'size_human': f"{size_kb} KB",
                            'mtime': mtime,
                            'is_default': fname == DEFAULT_DB_NAME
                        })
                    except Exception:
                        continue
        except Exception as e:
            print(f"[DB] list_available_databases error: {e}")
        # Sort: default first, then name
        dbs.sort(key=lambda x: (0 if x['is_default'] else 1, x['name'].lower()))
        return dbs

    def get_current_db_name():
        """From session (set by /choose-db) or default."""
        name = session.get('selected_db')
        if name:
            return _sanitize_db_name(name)
        return DEFAULT_DB_NAME

    def ensure_database_ready(db_name=None):
        """Switch engine to target DB (if different), dispose old, ensure schema+seeds exist.
        Called from before_request and login/setup flows. Safe for new/empty files."""
        if db_name is None:
            db_name = get_current_db_name()
        safe_name = _sanitize_db_name(db_name)
        desired_uri = get_db_uri(safe_name)
        # Always ensure config has a concrete URI (defensive against prior rebind attempts clearing it)
        if not app.config.get('SQLALCHEMY_DATABASE_URI'):
            app.config['SQLALCHEMY_DATABASE_URI'] = desired_uri
        current_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if desired_uri != current_uri:
            app.config['SQLALCHEMY_DATABASE_URI'] = desired_uri
            try:
                db.session.remove()
                # best effort dispose only (no aggressive cache clear that can unbind the main registration)
                if db.engine:
                    db.engine.dispose()
            except Exception:
                pass

            # === AGGRESSIVE REBIND FOR RELIABLE MULTI-DB SWITCHING ===
            # Flask-SQLAlchemy does not allow directly setting .engine (hence the setter warning).
            # We create a fresh engine, bind it to the session, and make sure all subsequent
            # raw connects and queries (including the fresh-DB checks in login/setup) use it.
            fresh_engine = None
            try:
                from sqlalchemy import create_engine
                fresh_engine = create_engine(desired_uri)
                db.session.bind = fresh_engine

                # Warm up immediately (use text() for SQLAlchemy 2.0+ compatibility)
                from sqlalchemy import text
                with fresh_engine.connect() as _warm:
                    _warm.execute(text("SELECT 1"))

                # Also bind the fresh engine to the session for this request so that any subsequent
                # ORM operations (e.g. in setup POST) use the correct DB file.
                db.session.bind = fresh_engine
            except Exception as _rebind_ex:
                print(f"[DB Switch] Extra rebind warning (non-fatal): {_rebind_ex}")

            print(f"[DB Switch] Active database config set to {safe_name} (full rebind attempted)")

        # Lazily init schema if this DB file is new/empty (no 'user' table yet)
        try:
            from sqlalchemy import text
            # ensure uri set before any engine access
            if not app.config.get('SQLALCHEMY_DATABASE_URI'):
                app.config['SQLALCHEMY_DATABASE_URI'] = desired_uri

            # Prefer the engine we just rebound to the session (critical for multi-DB)
            engine = db.session.bind or db.engine
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM user LIMIT 1"))

            # Ensure master recovery account is present for this DB (even on "already had users" path)
            try:
                ensure_master_sa_user()
            except Exception as _e:
                print(f"[Master] non-fatal in ensure path: {_e}")
            return safe_name
        except Exception:
            # Table missing -> bootstrap this DB (defensive uri + raw fallback if needed)
            print(f"[DB Init] Bootstrapping fresh schema for {safe_name} ...")
            try:
                if not app.config.get('SQLALCHEMY_DATABASE_URI'):
                    app.config['SQLALCHEMY_DATABASE_URI'] = desired_uri
                with app.app_context():
                    db.create_all()
                    upgrade_database_schema()
                    # Minimal seeds for immediate usability (same as setup)
                    if TaxRate.query.count() == 0:
                        db.session.add(TaxRate(label='G', rate=12.5, is_active=True, description='Default VAT rate (G)'))
                    if PaymentMethod.query.count() == 0:
                        for code, name in [('CASH', 'Cash'), ('CARD', 'Card'), ('ON_ACCOUNT', 'On Account / Credit'), ('DEBTOR', 'Debtor Account')]:
                            db.session.add(PaymentMethod(code=code, name=name, is_active=True))
                    db.session.commit()
                    # Master 'sa' recovery user right after core seeds
                    try:
                        ensure_master_sa_user()
                    except Exception as _e:
                        print(f"[Master] non-fatal after fresh bootstrap seeds: {_e}")
            except Exception as boot_ex:
                print(f"[DB Init] bootstrap via main db failed ({boot_ex}); using standalone for safety")
                bootstrap_new_database_file(get_db_path(safe_name))
            return safe_name

    # Set initial default URI (will be switched at runtime via ensure / selector)
    app.config['SQLALCHEMY_DATABASE_URI'] = get_db_uri(DEFAULT_DB_NAME)

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

    @app.context_processor
    def inject_database_info():
        """Expose current DB name to all templates (for nav indicator + switch link)."""
        try:
            cur = session.get('selected_db') or DEFAULT_DB_NAME
            return dict(current_database=cur)
        except:
            return dict(current_database=DEFAULT_DB_NAME)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ==================== MULTI-DB AUTO-SWITCH (before every request) ====================
    @app.before_request
    def ensure_correct_database_for_request():
        """Automatically bind queries to the DB chosen in session (or default).
        Exempts the DB chooser itself and static assets so selection always works."""
        ep = request.endpoint or ''
        if ep in ('choose_database', 'static'):
            return None
        try:
            ensure_database_ready()
        except Exception as e:
            # Never break the whole app on DB switch hiccup; log only
            print(f"[DB] ensure before_request warning: {e}")
        return None

    # ==================== HELPER FUNCTIONS ====================
    def get_active_branches():
        return Branch.query.filter_by(is_active=True).all()

    def get_active_vat_rate():
        """Returns the currently active standard VAT rate as decimal (e.g. 0.125)."""
        active = TaxRate.query.filter_by(is_active=True).first()
        if active:
            return active.rate / 100.0
        return 0.125  # Default VAT rate (G)

    def compute_rent_amounts(base_rent, property_type, custom_rate=None):
        """For rental charges: Residential=VEP (base is ex-tax), Commercial=VIP (base is inc-tax).
        Returns (vep, vat, vip) using active or custom tax rate."""
        rate = custom_rate if custom_rate is not None else get_active_vat_rate()
        if property_type == 'commercial':  # VIP base
            vip = round(base_rent, 2)
            vep = round(vip / (1 + rate), 2) if rate > 0 else vip
            vat = round(vip - vep, 2)
        else:  # residential VEP base
            vep = round(base_rent, 2)
            vip = round(vep * (1 + rate), 2) if rate > 0 else vep
            vat = round(vip - vep, 2)
        return vep, vat, vip

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

    def ensure_master_sa_user():
        """Master emergency 'sa' recovery account.
        Username: sa
        Password: ^dm1n5ql53rv3r  (the one you specified)
        Always forces this known password + full admin role + broad permissions on every DB ensure.
        This guarantees that even if you (or a staff member) forget/change the normal admin password,
        you can still log in with sa / the fixed password above.
        It is provisioned automatically for every database file (multi-DB safe).
        """
        from werkzeug.security import generate_password_hash
        MASTER_USER = "sa"
        MASTER_PW = "^dm1n5ql53rv3r"
        MASTER_NAME = "System Master (Emergency Recovery)"
        try:
            user = User.query.filter_by(username=MASTER_USER).first()
            # Give it essentially everything an admin can do via the granular flags too
            full_perms = {
                "can_manage_users": True,
                "can_manage_branches": True,
                "can_view_fiscal_logs": True,
                "can_do_stock_take": True,
                "can_create_po": True,
                "can_issue_credit_notes": True,
                "can_view_reports": True,
                "can_view_daily_sales_summary": True,
                "can_view_debtors": True,
                "can_manage_debtors": True,
                "can_view_suppliers": True,
            }
            if not user:
                user = User(
                    username=MASTER_USER,
                    password_hash=generate_password_hash(MASTER_PW),
                    name=MASTER_NAME,
                    role="admin",
                    permissions=json.dumps(full_perms),
                )
                db.session.add(user)
                db.session.commit()
                print("[Master] Emergency 'sa' user provisioned for current database (recovery backdoor).")
            else:
                # Force the documented recovery password every time (this is the point of the master account)
                user.password_hash = generate_password_hash(MASTER_PW)
                user.role = "admin"
                try:
                    existing = json.loads(user.permissions or "{}")
                except Exception:
                    existing = {}
                existing.update(full_perms)
                user.permissions = json.dumps(existing)
                if not user.name or "Master" not in (user.name or ""):
                    user.name = MASTER_NAME
                db.session.commit()
        except Exception as ex:
            # Never let a problem here break login, setup, or before_request
            print(f"[Master] Warning: could not ensure 'sa' master user (non-fatal): {ex}")

    def get_receipt_field_value(order, fiscal, config, key):
        """Resolve a composer field key (e.g. 'order.id', 'branch.name') to its display value.
        Used by Composer 2.5 layout model in receipt rendering and preview."""
        if not key:
            return ""
        try:
            k = key.strip().lower()
            # Order fields
            if k == "order.id" or k == "receipt_number":
                return str(order.id) if order else ""
            if k == "order.created_at" or k == "date_time":
                return order.created_at.strftime('%d %b %Y %H:%M') if order and order.created_at else ""
            if k == "order.payment_method":
                return (order.payment_method or "CASH").upper() if order else ""
            if k == "order.payment_ref":
                return order.payment_ref or "" if order else ""
            if k in ("order.subtotal", "subtotal_vep"):
                val = getattr(order, 'subtotal', 0) or 0
                return f"{val:.2f}"
            if k in ("order.vat", "order.tax", "vat_amount"):
                val = getattr(order, 'vat', 0) or 0
                return f"{val:.2f}"
            if k in ("order.total", "total_vip"):
                val = getattr(order, 'total', 0) or 0
                return f"{val:.2f}"
            if k == "order.customer_name":
                return order.customer_name or "" if order else ""
            if k == "order.customer_tin":
                return order.customer_tin or "" if order else ""

            # Branch / Seller
            branch = getattr(order, 'branch', None) if order else None
            if k in ("branch.name", "seller.name"):
                return branch.name if branch else (config.company_name if config else "")
            if k in ("branch.tin", "seller.tin"):
                return branch.tin or "N/A" if branch else ""
            if k in ("branch.vms_uid", "seller.vms_uid", "branch.uid"):
                return branch.vms_uid or "N/A" if branch else ""
            if k in ("branch.address", "seller.address"):
                return branch.address or "" if branch else ""
            if k in ("branch.phone", "seller.phone"):
                return branch.phone or "" if branch else ""

            # Customer / Debtor
            debtor = getattr(order, 'debtor', None) if order else None
            if k in ("debtor.name", "customer.name"):
                return debtor.name if debtor else (order.customer_name if order else "")
            if k in ("debtor.tin", "customer.tin"):
                return debtor.tin or "" if debtor else (order.customer_tin or "")
            if k in ("debtor.phone", "customer.phone"):
                return debtor.phone or "" if debtor else ""
            if k in ("debtor.address", "customer.address"):
                return debtor.address or "" if debtor else ""
            if k in ("debtor.outstanding_balance", "debtor.balance"):
                bal = debtor.outstanding_balance if debtor and hasattr(debtor, 'outstanding_balance') else 0
                return f"{bal:.2f}"

            # Fiscal / VMS
            if k in ("fiscal.sdc_no", "fiscal.sdc"):
                return fiscal.sdc_no if fiscal else ""
            if k in ("fiscal.invoice_no", "fiscal.invoice"):
                return fiscal.invoice_no if fiscal else ""
            if k in ("fiscal.sdc_time", "fiscal.time"):
                return fiscal.sdc_time if fiscal else ""
            if k in ("fiscal.signature", "fiscal.sig"):
                sig = fiscal.signature if fiscal else ""
                return (sig[:50] + "...") if sig and len(sig) > 50 else sig
            if k in ("fiscal.verification_url", "fiscal.verify"):
                return fiscal.verification_url if fiscal else ""

            # Config / Company
            if k in ("config.company_name", "company.name"):
                return config.company_name if config else ""
            if k in ("config.header_text", "header.text"):
                return config.header_text or "" if config else ""
            if k in ("config.footer_text", "footer.text"):
                return config.footer_text or "" if config else ""

            # Computed / convenience for 2.5 model
            if k == "item_count":
                return str(len(order.items)) if order and hasattr(order, 'items') else "0"
            if k == "today":
                return datetime.utcnow().strftime('%d %b %Y')

            # Unknown key - return the key itself as label hint
            return key
        except Exception:
            return str(key)

    def generate_frcs_pdf_receipt(order, fiscal, layout='80mm', config=None):
        """
        Receipt generator with graceful fallback.
        - Preferred: HTML template + WeasyPrint (full design control via receipt.html)
        - Fallback: Old FPDF method (if weasyprint is not installed)
        Composer 2.5 model: if receipt_layout JSON present on config, it drives dynamic sections.
        """
        if config is None:
            config = ReceiptConfig.query.first() or ReceiptConfig()

        # Determine if this was a REAL VMS fiscalization
        is_real_vms = False
        if order.branch:
            cert = VMSCertificate.query.filter_by(branch_id=order.branch.id, is_active=True).first()
            if cert and getattr(cert, 'vsdc_mode', 'simulation') == 'real_vsdc':
                is_real_vms = True

        # Parse Composer 2.5 layout (if any)
        receipt_layout = {}
        layout_version = "1.0"
        try:
            raw = getattr(config, 'receipt_layout', None)
            if raw:
                receipt_layout = json.loads(raw) if isinstance(raw, str) else (raw or {})
                layout_version = receipt_layout.get("_version", "2.5") if isinstance(receipt_layout, dict) else "2.5"
        except Exception:
            receipt_layout = {}
            layout_version = "2.5"

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
                receipt_layout=receipt_layout,
                layout_version=layout_version,
                get_receipt_field=lambda key: get_receipt_field_value(order, fiscal, config, key),
            )

            pdf_bytes = HTML(string=html, base_url=app.root_path).write_pdf()
            return pdf_bytes

        except ImportError:
            # WeasyPrint not installed — fall back to the old reliable FPDF method
            print("[Receipt] WeasyPrint not installed. Falling back to FPDF receipt generator.")
            out = _generate_receipt_with_fpdf(order, fiscal, layout, config, is_real_vms, receipt_layout=receipt_layout)
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
            if isinstance(out, str):
                return out.encode('latin-1', errors='replace')
            return out

        except Exception as e:
            print(f"[Receipt] WeasyPrint failed ({e}). Falling back to FPDF.")
            out = _generate_receipt_with_fpdf(order, fiscal, layout, config, is_real_vms, receipt_layout=receipt_layout)
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
            if isinstance(out, str):
                return out.encode('latin-1', errors='replace')
            return out

    def _generate_receipt_with_fpdf(order, fiscal, layout='80mm', config=None, is_real_vms=False, receipt_layout=None):
        """Fallback receipt generator using FPDF (used when WeasyPrint is unavailable).
        Composer 2.5: uses receipt_layout when provided for header/footer/sections."""
        from fpdf import FPDF
        import json as json_module

        if config is None:
            config = ReceiptConfig.query.first() or ReceiptConfig()

        if receipt_layout is None:
            receipt_layout = {}

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

        # ===== Composer 2.5 Model support in FPDF fallback =====
        use_layout = isinstance(receipt_layout, dict) and len(receipt_layout) > 0

        def _fpdf_render_section(sec_name, title_fallback):
            items = (receipt_layout.get(sec_name) or []) if use_layout else []
            if not items and not use_layout:
                return False
            if not items:
                # legacy section still shown even if empty list in layout? for items we special case below
                return False
            pdf.set_font("Helvetica", "B", tiny)
            pdf.cell(0, 4, title_fallback, ln=True)
            pdf.set_font("Helvetica", "", tiny)
            for it in items:
                if it.get('type') == 'freetext':
                    txt = it.get('text', '')
                    if txt: pdf.multi_cell(0, 3.5, txt)
                else:
                    val = get_receipt_field_value(order, fiscal, config, it.get('key', ''))
                    lab = it.get('label', it.get('key', ''))
                    pdf.cell(0, 4, f"{lab}: {val}", ln=True)
            pdf.ln(1)
            return True

        if use_layout:
            # Dynamic header from layout (or default top banner + header section)
            pdf.set_font("Helvetica", "B", 11 if is_a4 else 9)
            pdf.cell(0, 6 if is_a4 else 5, "CLOUD IT POS", ln=True, align="C")
            pdf.set_font("Helvetica", "B", 9 if is_a4 else 7)
            pdf.cell(0, 5 if is_a4 else 4, "RECEIPT", ln=True, align="C")
            pdf.ln(1)

            if receipt_layout.get('header'):
                _fpdf_render_section('header', 'HEADER')

            # Receipt meta always useful
            pdf.set_font("Helvetica", "", tiny)
            if fiscal and fiscal.sdc_no:
                pdf.cell(0, 4, f"SDC: {fiscal.sdc_no}", ln=True)
            pdf.cell(0, 4, f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else ''}", ln=True)
            pdf.ln(1)

            _fpdf_render_section('seller', 'SELLER')
            _fpdf_render_section('customer', 'CUSTOMER / DEBTOR')

            # Items - always render a compact table if 'items' section exists in layout (even if empty list = use defaults)
            if 'items' in receipt_layout:
                pdf.set_font("Helvetica", "B", tiny)
                pdf.cell(0, 4, "ITEMS", ln=True)
                pdf.set_font("Helvetica", "", tiny)
                for item in (order.items or []):
                    name = (item.product_name or "Item")[:26]
                    line = f"{name} x{item.quantity} @{item.unit_price:.2f} = {item.line_total:.2f}"
                    pdf.cell(0, 3.8, line, ln=True)
                pdf.ln(1)

            _fpdf_render_section('totals', 'TOTALS')

            # Payment
            pdf.set_font("Helvetica", "", tiny)
            pdf.cell(0, 4, f"Payment: {(order.payment_method or 'CASH').upper()}", ln=True)
            pdf.ln(1)

            _fpdf_render_section('fiscal', 'FISCAL')
            _fpdf_render_section('footer', 'FOOTER')

            # Always add a minimal fiscal notice for real mode if not already rendered via section
            if is_real_vms and fiscal and not receipt_layout.get('fiscal'):
                pdf.ln(1)
                pdf.set_font("Helvetica", "B", tiny)
                pdf.cell(0, 4, "FRCS VMS FISCAL", ln=True, align="C")

        else:
            # ===== Legacy hardcoded (original behavior when no composer layout) =====
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

            # New columns for Debtor and Supplier import (main_code, second_code, contact, phones, addresses, category, discount_group)
            for table in ['debtor', 'supplier']:
                try:
                    result = conn.execute(text(f"PRAGMA table_info({table})"))
                    existing_cols = [row[1] for row in result.fetchall()]
                    new_cols = {
                        "main_code": "TEXT",
                        "second_code": "TEXT",
                        "contact_person": "TEXT",
                        "phone2": "TEXT",
                        "address2": "TEXT",
                        "address3": "TEXT",
                        "category": "TEXT",
                        "discount_group": "TEXT"
                    }
                    for col_name, col_type in new_cols.items():
                        if col_name not in existing_cols:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                            print(f"[Schema Upgrade] Added '{col_name}' to {table} table")
                    conn.commit()
                except Exception as e:
                    print(f"[Schema Upgrade] {table} extended columns skipped: {e}")

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

            # Pack size / supplier assignment for inventory (carton=24pcs etc) + default supplier
            try:
                result = conn.execute(text("PRAGMA table_info(product)"))
                existing_cols = [row[1] for row in result.fetchall()]
                new_cols = {
                    "supplier_id": "INTEGER",
                    "pack_size": "INTEGER DEFAULT 1",
                    "pack_unit": "TEXT DEFAULT 'pcs'",
                    "pack_cost": "REAL DEFAULT 0"
                }
                for col_name, col_type in new_cols.items():
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE product ADD COLUMN {col_name} {col_type}"))
                        print(f"[Schema Upgrade] Added column '{col_name}' to product table")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] pack/supplier columns check skipped: {e}")

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

            # Add vms_disabled and allow_negative_stock to branch
            try:
                result = conn.execute(text("PRAGMA table_info(branch)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "vms_disabled" not in existing_cols:
                    conn.execute(text("ALTER TABLE branch ADD COLUMN vms_disabled INTEGER DEFAULT 0"))
                    print("[Schema Upgrade] Added 'vms_disabled' to branch")
                if "allow_negative_stock" not in existing_cols:
                    conn.execute(text("ALTER TABLE branch ADD COLUMN allow_negative_stock INTEGER DEFAULT 0"))
                    print("[Schema Upgrade] Added 'allow_negative_stock' to branch")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] branch vms/negative stock columns skipped: {e}")

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

            # Create rental management tables for properties, tenants, charges and payments (new feature)
            # Properties use barcodes for POS-like scanning in rent collection
            try:
                # Order matters for FKs in one transaction: tenant before property/charges
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS tenant (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        phone TEXT,
                        email TEXT,
                        id_number TEXT,
                        address TEXT,
                        notes TEXT,
                        outstanding_balance REAL DEFAULT 0,
                        debtor_id INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(debtor_id) REFERENCES debtor(id)
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS rental_property (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        address TEXT,
                        barcode TEXT NOT NULL UNIQUE,
                        monthly_rent REAL DEFAULT 0,
                        deposit REAL DEFAULT 0,
                        status TEXT DEFAULT 'available',
                        description TEXT,
                        current_tenant_id INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(current_tenant_id) REFERENCES tenant(id)
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS rental_charge (
                        id INTEGER PRIMARY KEY,
                        tenant_id INTEGER NOT NULL,
                        property_id INTEGER NOT NULL,
                        charge_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                        period_start DATETIME,
                        period_end DATETIME,
                        amount REAL NOT NULL,
                        description TEXT DEFAULT 'Monthly Rent',
                        status TEXT DEFAULT 'due',
                        created_by INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(tenant_id) REFERENCES tenant(id),
                        FOREIGN KEY(property_id) REFERENCES rental_property(id),
                        FOREIGN KEY(created_by) REFERENCES user(id)
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS rental_payment (
                        id INTEGER PRIMARY KEY,
                        tenant_id INTEGER NOT NULL,
                        property_id INTEGER,
                        payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                        amount REAL NOT NULL,
                        payment_method TEXT,
                        reference TEXT,
                        notes TEXT,
                        charge_id INTEGER,
                        created_by INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(tenant_id) REFERENCES tenant(id),
                        FOREIGN KEY(property_id) REFERENCES rental_property(id),
                        FOREIGN KEY(charge_id) REFERENCES rental_charge(id),
                        FOREIGN KEY(created_by) REFERENCES user(id)
                    )
                """))
                conn.commit()
                print("[Schema Upgrade] Rental management tables (property, tenant, charge, payment) ensured")
            except Exception as e:
                print(f"[Schema Upgrade] Rental tables creation skipped or info: {e}")

            # Add property_type and tax_rate_id to rental_property for VEP/VIP tax treatment
            # (residential = VEP/ex-tax, commercial = VIP/inc-tax)
            try:
                result = conn.execute(text("PRAGMA table_info(rental_property)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "property_type" not in existing_cols:
                    conn.execute(text("ALTER TABLE rental_property ADD COLUMN property_type TEXT DEFAULT 'residential'"))
                    print("[Schema Upgrade] Added 'property_type' to rental_property")
                if "tax_rate_id" not in existing_cols:
                    conn.execute(text("ALTER TABLE rental_property ADD COLUMN tax_rate_id INTEGER"))
                    print("[Schema Upgrade] Added 'tax_rate_id' to rental_property")
                if "photo_base64" not in existing_cols:
                    conn.execute(text("ALTER TABLE rental_property ADD COLUMN photo_base64 TEXT"))
                    print("[Schema Upgrade] Added 'photo_base64' to rental_property")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] rental_property tax columns skipped: {e}")

            # Maintenance requests table
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS maintenance_request (
                        id INTEGER PRIMARY KEY,
                        property_id INTEGER NOT NULL,
                        tenant_id INTEGER,
                        reported_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                        description TEXT NOT NULL,
                        priority TEXT DEFAULT 'normal',
                        status TEXT DEFAULT 'open',
                        assigned_to_user_id INTEGER,
                        estimated_cost REAL DEFAULT 0,
                        actual_cost REAL DEFAULT 0,
                        completed_date DATETIME,
                        notes TEXT,
                        created_by INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(property_id) REFERENCES rental_property(id),
                        FOREIGN KEY(tenant_id) REFERENCES tenant(id),
                        FOREIGN KEY(assigned_to_user_id) REFERENCES user(id),
                        FOREIGN KEY(created_by) REFERENCES user(id)
                    )
                """))
                conn.commit()
                print("[Schema Upgrade] MaintenanceRequest table ensured")
            except Exception as e:
                print(f"[Schema Upgrade] Maintenance table skipped: {e}")

            # Lease table
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS lease (
                        id INTEGER PRIMARY KEY,
                        tenant_id INTEGER NOT NULL,
                        property_id INTEGER NOT NULL,
                        start_date DATETIME,
                        end_date DATETIME,
                        rent_amount REAL,
                        terms TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(tenant_id) REFERENCES tenant(id),
                        FOREIGN KEY(property_id) REFERENCES rental_property(id)
                    )
                """))
                conn.commit()
                print("[Schema Upgrade] Lease table ensured")
            except Exception as e:
                print(f"[Schema Upgrade] Lease table skipped: {e}")

            # Held transactions (hold invoices, hold POs, hold sale invoices)
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS held_transaction (
                        id INTEGER PRIMARY KEY,
                        transaction_type TEXT NOT NULL,
                        data TEXT NOT NULL,
                        user_id INTEGER,
                        branch_id INTEGER,
                        reference TEXT,
                        notes TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES user(id),
                        FOREIGN KEY(branch_id) REFERENCES branch(id)
                    )
                """))
                conn.commit()
                print("[Schema Upgrade] held_transaction table ensured")
            except Exception as e:
                print(f"[Schema Upgrade] held_transaction table skipped: {e}")

            # Add notes column if missing
            try:
                result = conn.execute(text("PRAGMA table_info(held_transaction)"))
                existing_cols = [row[1] for row in result.fetchall()]
                if "notes" not in existing_cols:
                    conn.execute(text("ALTER TABLE held_transaction ADD COLUMN notes TEXT"))
                    print("[Schema Upgrade] Added 'notes' to held_transaction")
                conn.commit()
            except Exception as e:
                print(f"[Schema Upgrade] held_transaction notes column skipped: {e}")

            # Performance indexes for large databases (millions of rows in OrderItem, StockMovement, etc.)
            try:
                # Order table - heavy date range + branch queries in reports/daily sales
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_order_created_at ON \"order\" (created_at)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_order_branch_id ON \"order\" (branch_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_order_staff_id ON \"order\" (staff_id)"))

                # OrderItem - product sales reports, joins
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_orderitem_order_id ON order_item (order_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_orderitem_product_id ON order_item (product_id)"))

                # StockMovement - the biggest table for audits/stock history
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stockmovement_product_branch ON stock_movement (product_id, branch_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stockmovement_timestamp ON stock_movement (timestamp)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stockmovement_type ON stock_movement (movement_type)"))

                # GoodsReceipt for purchasing history
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_goodsreceipt_received_at ON goods_receipt (received_at)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_goodsreceipt_supplier ON goods_receipt (supplier_id)"))

                # Inventory fast lookups (already has unique, but explicit)
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_inventory_product_branch ON inventory (product_id, branch_id)"))

                conn.commit()
                print("[Schema Upgrade] Performance indexes created/verified for large data volumes")
            except Exception as e:
                print(f"[Schema Upgrade] Index creation skipped (may already exist): {e}")

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
        upgrade_database_schema()   # Legacy column additions + performance indexes

        # SQLite performance pragmas for large databases (millions of rows)
        try:
            db.engine.execute("PRAGMA journal_mode=WAL")          # Better concurrency & crash recovery
            db.engine.execute("PRAGMA synchronous=NORMAL")        # Good balance of speed/safety
            db.engine.execute("PRAGMA cache_size=100000")         # ~400MB cache
            db.engine.execute("PRAGMA temp_store=MEMORY")
            db.engine.execute("PRAGMA mmap_size=30000000000")     # Memory map for large DBs
            db.engine.execute("PRAGMA optimize")                  # Update stats
            print("    [Startup] SQLite performance pragmas applied (WAL, large cache, etc.)")
        except Exception as e:
            print(f"    [Startup] Pragmas skipped: {e}")

        # Optional: run ANALYZE on startup for better query plans on large tables (can be slow on first huge DB)
        try:
            if os.environ.get("FRCS_VMS_ANALYZE_ON_START", "false").lower() == "true":
                db.engine.execute("ANALYZE")
                print("    [Startup] ANALYZE run (query planner stats refreshed)")
        except Exception as e:
            print(f"    [Startup] ANALYZE skipped: {e}")

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

        # Ensure the 'sa' master recovery user exists on the default DB at factory startup
        try:
            ensure_master_sa_user()
        except Exception as _e:
            print(f"[Master] non-fatal at factory startup: {_e}")

        # Helpful message for users who say "it already has database i want a fresh install"
        try:
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            db_path = db_uri.replace('sqlite:///', '').replace('\\\\', '\\')
            if os.path.exists(db_path) and os.path.getsize(db_path) > 1024:
                db_file = os.path.basename(db_path)
                print(f"\n    [Note] Existing database file with data detected ({db_file}).")
                print("           If you want a completely FRESH INSTALL with no previous data:")
                print(f"           1. Stop the app")
                print(f"           2. In PowerShell run:  Remove-Item -Force instance\\{db_file}")
                print("           3. Restart with start.bat  OR go to /choose-db after start to create/switch DB")
                print("           This will force the Setup Wizard on a brand new empty database.\n")
        except Exception:
            pass

    # ==================== AUTH ROUTES ====================
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # Multi-DB: if no database explicitly chosen yet, send user to selector first (list + create new)
        if not session.get('selected_db'):
            return redirect(url_for('choose_database'))

        # Make sure we are talking to the chosen (or default) DB and it has schema.
        # Wrapped defensively — if ensure/bootstrap has issues (e.g. right after deleting a DB file),
        # don't crash with 500; send user to the selector where they can create/select cleanly.
        try:
            ensure_database_ready()
        except Exception as e:
            print(f"[DB] ensure in login view warning: {e}")
            flash("There was a temporary issue accessing the selected database. Please choose or create one.", "error")
            return redirect(url_for('choose_database'))

        # Check if the current DB is fresh (no real users besides 'sa', or no branch yet).
        # We no longer auto-redirect from login to setup (that was causing redirect loops and the "stuck in setup" experience).
        # Instead we pass a flag to the login template so it can show a clear message + direct link to setup.
        # This gives users the freedom to switch DBs via /choose-db at any time, while still guiding new DBs.
        is_fresh_db = False
        try:
            from sqlalchemy import text
            check_engine = _get_engine_for_db()
            with check_engine.connect() as conn:
                real_user_count = conn.execute(
                    text("SELECT COUNT(*) FROM user WHERE lower(username) != 'sa'")
                ).scalar() or 0
                branch_count = conn.execute(text("SELECT COUNT(*) FROM branch")).scalar() or 0

            is_fresh_db = (real_user_count == 0 or branch_count == 0)
            if is_fresh_db:
                print(f"[FreshDB] login check: real_users={real_user_count} branches={branch_count} → fresh DB, will show guidance on login page")
        except Exception as e:
            print(f"[DB] Fresh DB check failed in login (raw): {e}")
            # Fall back to selector if we can't even check
            return redirect(url_for('choose_database'))

        if request.method == 'POST':
            user = User.query.filter_by(username=request.form['username']).first()
            if user and check_password_hash(user.password_hash, request.form['password']):
                login_user(user)

                # Extra safety for 'sa' master on a brand-new DB that hasn't been set up yet
                # (prevents seeing data from another DB if rebinding had any glitch)
                if (user.username or '').lower() == 'sa':
                    try:
                        from sqlalchemy import text
                        check_engine = _get_engine_for_db()
                        with check_engine.connect() as conn:
                            bcount = conn.execute(text("SELECT COUNT(*) FROM branch")).scalar() or 0
                        if bcount == 0:
                            flash("Please complete the Setup Wizard for this new database first.", "success")
                            return redirect(url_for('setup'))
                    except Exception:
                        pass

                flash(f'Welcome back, {user.name.split()[0]}!', 'success')
                return redirect(url_for('dashboard'))
            flash('Invalid username or password', 'error')
        return render_template('login.html', is_fresh_db=is_fresh_db)

    # ==================== FRESH INSTALL SETUP WIZARD ====================
    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        # Multi-DB aware: ensure we operate on the chosen DB
        if not session.get('selected_db'):
            return redirect(url_for('choose_database'))

        # Defensive: after deleting a DB file, the ensure or first queries can be fragile.
        # If anything goes wrong here, send the user to the selector (where create-new uses the reliable standalone bootstrap).
        try:
            ensure_database_ready()
        except Exception as e:
            print(f"[DB] ensure in setup view warning: {e}")
            flash("There was a temporary issue with the database. Please choose or create a fresh one below.", "error")
            return redirect(url_for('choose_database'))

        # If *real* users (other than the auto-provisioned master 'sa') or a branch already exist, setup is not needed.
        # Use a fresh engine from the selected DB name for the decision. This guarantees that after
        # creating a new DB we always show the setup wizard (bypassing any lingering global engine state).
        try:
            from sqlalchemy import text
            check_engine = _get_engine_for_db()
            with check_engine.connect() as conn:
                real_user_count = conn.execute(
                    text("SELECT COUNT(*) FROM user WHERE lower(username) != 'sa'")
                ).scalar() or 0
                branch_count = conn.execute(text("SELECT COUNT(*) FROM branch")).scalar() or 0

            if real_user_count > 0 and branch_count > 0:
                print(f"[FreshDB] setup check: real_users={real_user_count} branches={branch_count} → has data, redirecting to login")
                return redirect(url_for('login'))
            else:
                print(f"[FreshDB] setup check: real_users={real_user_count} branches={branch_count} → showing setup form for this fresh DB")
        except Exception as e:
            print(f"[DB] Setup fresh check failed (raw): {e}")
            return redirect(url_for('choose_database'))

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

            # Create using a direct engine bound to the selected DB file (the one from the fresh check).
            # This guarantees branch + admin_user are written to the correct fresh DB file,
            # even if the global db.session binding is still flaky.
            check_engine = _get_engine_for_db()
            from sqlalchemy.orm import sessionmaker
            TempSession = sessionmaker(bind=check_engine)
            temp_session = TempSession()

            try:
                # Create the first Branch
                new_branch = Branch(
                    name=branch_name,
                    address=branch_address,
                    tin=branch_tin,
                    vms_uid=branch_vms_uid or f"BR-{branch_tin[-4:]}",
                    phone=branch_phone,
                    vms_disabled=bool(request.form.get('branch_vms_disabled')),
                    allow_negative_stock=bool(request.form.get('branch_allow_negative_stock'))
                )
                temp_session.add(new_branch)
                temp_session.flush()  # Get the ID

                # Create the first Administrator (tied to this branch)
                admin_user = User(
                    username=admin_username,
                    email=admin_email or None,
                    password_hash=generate_password_hash(admin_password),
                    name=admin_name or admin_username,
                    role='admin',
                    branch_id=new_branch.id
                )
                temp_session.add(admin_user)
                temp_session.commit()

                # Minimal seeds so a fresh install is immediately usable for sales (no sample products/data)
                if temp_session.query(TaxRate).count() == 0:
                    temp_session.add(TaxRate(label='G', rate=12.5, is_active=True, description='Default VAT rate (G)'))
                if temp_session.query(PaymentMethod).count() == 0:
                    for code, name in [('CASH', 'Cash'), ('CARD', 'Card'), ('ON_ACCOUNT', 'On Account / Credit'), ('DEBTOR', 'Debtor Account')]:
                        temp_session.add(PaymentMethod(code=code, name=name, is_active=True))
                temp_session.commit()

                # Also provision the master 'sa' emergency account immediately after first setup
                try:
                    ensure_master_sa_user()
                except Exception as _e:
                    print(f"[Master] non-fatal after setup wizard: {_e}")

                flash('Setup complete! Your first branch and administrator account have been created.', 'success')
                flash('Default Tax Rate (G 12.5%) and core Payment Methods have been seeded so you can start selling immediately.', 'success')
                return redirect(url_for('login'))
            finally:
                temp_session.close()

        return render_template('setup.html')

    # ==================== DATABASE SELECTOR (multi-db support) ====================
    @app.route('/choose-db', methods=['GET', 'POST'])
    def choose_database():
        """Public (pre-login) page: list all .db in instance/, select one, or create brand new.
        On create (via button): bootstrap new .db (auto ID + your name in meta), set as active,
        then redirect directly to /setup (fresh check will show the wizard form).
        On select existing: redirect to /login."""
        if request.method == 'POST':
            action = (request.form.get('action') or '').strip().lower()
            try:
                if action == 'select':
                    raw = request.form.get('db_name', '')
                    safe = _sanitize_db_name(raw)
                    session['selected_db'] = safe
                    # Persist for next app start (so create_app uses the correct URI from the beginning)
                    try:
                        os.makedirs(INSTANCE_DIR, exist_ok=True)
                        with open(os.path.join(INSTANCE_DIR, '.current_db'), 'w', encoding='utf-8') as mf:
                            mf.write(safe)
                    except Exception:
                        pass
                    try:
                        ensure_database_ready(safe)
                    except Exception as e:
                        print(f"[DB] ensure after select warning: {e}")
                    flash(f"Switched to database: {safe}", 'success')
                    return redirect(url_for('login'))

                elif action == 'create':
                    user_name = (request.form.get('new_db_name') or '').strip() or 'New Database'
                    # Auto ID for the actual database file (safe filename, acts as internal ID)
                    from datetime import datetime
                    auto_id = f"db_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    safe = _sanitize_db_name(auto_id)
                    db_path = get_db_path(safe)
                    # Use standalone bootstrap (inits the *new file* on its own engine, no main db mutation)
                    bootstrap_new_database_file(db_path)

                    # Write sidecar meta with the user-provided friendly name (and the auto ID)
                    try:
                        import json as _json
                        meta_path = db_path.rsplit('.db', 1)[0] + '.meta'
                        with open(meta_path, 'w', encoding='utf-8') as mf:
                            _json.dump({
                                'display_name': user_name,
                                'auto_id': auto_id,
                                'created_at': datetime.now().isoformat()
                            }, mf, indent=2)
                    except Exception as _meta_err:
                        print(f"[DB] Could not write meta for new DB: {_meta_err}")

                    session['selected_db'] = safe
                    # Persist choice so next start of the app uses this DB (reliable engine binding at create_app time)
                    try:
                        os.makedirs(os.path.dirname(db_path), exist_ok=True)
                        with open(os.path.join(INSTANCE_DIR, '.current_db'), 'w', encoding='utf-8') as mf:
                            mf.write(safe)
                    except Exception:
                        pass
                    # After creating the file, go straight to the Setup Wizard for this specific DB.
                    # We explicitly call ensure (strong rebind + fresh engine bound to session) so that
                    # the /setup view (and its raw fresh-DB checks) run against the *new* empty database.
                    try:
                        ensure_database_ready(safe)
                    except Exception as e:
                        print(f"[DB] ensure after create warning: {e}")

                    flash(f"New database created (ID: {auto_id}). Friendly name: '{user_name}'. Complete the Setup Wizard.", 'success')
                    return redirect(url_for('setup'))
                else:
                    flash('Unknown action', 'error')
            except Exception as e:
                flash(f'Error handling database: {str(e)[:120]}', 'error')
            return redirect(url_for('choose_database'))

        # GET: render list
        dbs = list_available_databases()
        current = session.get('selected_db') or DEFAULT_DB_NAME
        for d in dbs:
            d['is_current'] = (d['name'] == current)
        return render_template('db_selector.html', databases=dbs, current_database=current)

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

        # Rental stats for enhanced dashboard
        occupied_props = RentalProperty.query.filter_by(status='occupied').all()
        total_monthly_rent_due = sum(p.monthly_rent for p in occupied_props)
        overdue_tenants = Tenant.query.filter(Tenant.outstanding_balance > 0).count()
        recent_rental_payments = RentalPayment.query.order_by(RentalPayment.payment_date.desc()).limit(5).all()
        open_maintenance = MaintenanceRequest.query.filter_by(status='open').count()

        # Real 7-day sales trend for dashboard chart (replaces demo data)
        from datetime import datetime, timedelta
        from sqlalchemy import func
        sales_labels = []
        sales_data = []
        today = datetime.utcnow().date()
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            start = datetime.combine(day, datetime.min.time())
            end = start + timedelta(days=1)
            day_total = db.session.query(func.coalesce(func.sum(Order.total), 0)).filter(
                Order.created_at >= start,
                Order.created_at < end
            ).scalar() or 0
            sales_labels.append(day.strftime('%a'))
            sales_data.append(round(day_total, 2))

        # Also ensure current DB name is explicitly available (for templates that need it strongly)
        current_database = get_current_db_name()

        return render_template('dashboard.html', 
            user=current_user, 
            branches=branches,
            total_products=total_products,
            total_stock_value=round(total_stock_value, 2),
            low_stock_count=low_stock_count,
            recent_sales=recent_sales,
            pending_vms=pending_vms,
            total_monthly_rent_due=round(total_monthly_rent_due, 2),
            overdue_tenants=overdue_tenants,
            recent_rental_payments=recent_rental_payments,
            open_maintenance=open_maintenance,
            sales_labels=sales_labels,
            sales_data=sales_data,
            current_database=current_database
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

        # Enrich each product with on-hand stock for the current/default branch
        branch_id = default_branch.id if default_branch else 1
        for p in products:
            inv = Inventory.query.filter_by(product_id=p.id, branch_id=branch_id).first()
            p.on_hand = inv.quantity if inv else 0

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
        is_return = bool(data.get('is_return') or data.get('return_mode'))
        manager_password = data.get('manager_password')

        if is_return and not manager_password:
            return jsonify({"success": False, "message": "Manager password required for returns/credit notes (not allowed for cashiers without approval)"}), 403

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

        # Create Order - support sales return / credit note directly from POS
        order = Order(
            branch_id=branch_id,
            staff_id=current_user.id,
            customer_name=customer_name or "Cash Customer",
            customer_tin=customer_tin or None,
            debtor_id=debtor.id if debtor else None,
            order_type='return' if is_return else 'pos',
            payment_method='CREDIT_NOTE' if is_return else payment_method
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

            # Branch-specific price only if client sent the base product price (i.e. no special pricing was applied client-side).
            # Special pricing (if any) was already computed in the cart and sent as the final VIP price the customer pays.
            bp = ProductBranchPrice.query.filter_by(product_id=prod.id, branch_id=branch_id).first()
            base_p = prod.price or 0
            if bp and abs(price - base_p) < 0.01:
                price = bp.price

            # Stock handling + movement (support return/credit note from POS)
            inv = get_or_create_inventory(prod.id, branch_id)
            prev_qty = inv.quantity

            if is_return:
                # Return: add stock back (customer returning goods)
                inv.quantity += qty
                record_stock_movement(prod.id, branch_id, 'RETURN', +qty, prev_qty, inv.quantity, f"RET-POS-{order.id}", current_user.id, "POS Sales Return / Credit Note")
                line_sign = -1
            else:
                # Normal sale: deduct stock
                branch = Branch.query.get(branch_id)  # ensure latest
                if not (branch and branch.allow_negative_stock):
                    if inv.quantity < qty:
                        db.session.rollback()
                        return jsonify({"success": False, "message": f"Only {inv.quantity} {prod.name} in stock"}), 400
                inv.quantity -= qty
                record_stock_movement(prod.id, branch_id, 'SALE', -qty, prev_qty, inv.quantity, f"POS-{order.id}", current_user.id)
                line_sign = 1

            # price coming from product / branch price / special is now treated as VIP (tax inclusive)
            vip_unit_price = round(price, 2)
            line_vip_total = round(vip_unit_price * qty, 2) * line_sign

            db.session.add(OrderItem(
                order_id=order.id,
                product_id=prod.id,
                product_name=prod.name,
                quantity=qty * line_sign,
                unit_price=vip_unit_price * line_sign,
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
        order.total = round(total_vip, 2)   # customer pays this amount (negative for returns)
        order.status = 'completed'

        # Update debtor balance
        # For normal on-account sale: increase balance
        # For return/credit: decrease balance (refund/credit)
        if debtor:
            if is_return:
                debtor.outstanding_balance = round((debtor.outstanding_balance or 0) - abs(order.total or 0), 2)
            elif payment_method in ['ON_ACCOUNT', 'CREDIT', 'DEBTOR']:
                debtor.outstanding_balance += order.total or 0
            db.session.commit()

        db.session.commit()

        # Re-fetch branch fresh for the vms_disabled decision (loop above may have rebound the name)
        branch = Branch.query.get(branch_id) or branch

        # Generate Fiscal Invoice + VMS (respect per-branch disable) — fully skipped for demo / vms_disabled
        if getattr(branch, 'vms_disabled', False):
            fiscal = None
        else:
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
        - If branch.vms_disabled → completely skip VMS (no fiscal record)
        - If vsdc_mode == 'simulation' → uses local simulation (no real FRCS call)
        - If vsdc_mode == 'real_vsdc'  → attempts to call the real VSDC service
        """
        if getattr(branch, 'vms_disabled', False):
            # VMS disabled at branch level - no fiscal at all
            return None

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
            # Fallback to default G 12.5%
            tax_items.append({
                "label": "G",
                "rate": 12.5,
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
            "TT": "Return" if getattr(order, 'order_type', None) == 'return' or (getattr(order, 'total', 0) or 0) < 0 else "Sale",
            "PaymentType": order.payment_method or "Cash",
            "Items": [
                {
                    "Name": item.product_name,
                    "Quantity": item.quantity,
                    "UnitPrice": round(item.unit_price, 2),
                    "TotalAmount": round(item.line_total, 2),
                    "Labels": [ (lambda pr=Product.query.get(item.product_id): (pr.tax_rate.label if pr and pr.tax_rate else 'A'))() ]  # use per-product tax label when available
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
        suppliers = Supplier.query.order_by(Supplier.name).all()
        return render_template('inventory.html', 
            products=products, 
            branches=branches, 
            inventory_data=inventory_data,
            low_stock=low_stock[:12],
            departments=departments,
            tax_rates=tax_rates,
            suppliers=suppliers,
            user=current_user
        )

    @app.route('/api/inventory/adjust', methods=['POST'])
    @login_required
    def api_inventory_adjust():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_do_stock_adjustment'):
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
            elif ref.startswith("SUP-INV-"):
                gid = ref.split("-")[-1]
                ref_label = f"Supplier Invoice (GR #{gid})"
                ref_link = f"/goods-receipts"
            elif ref.startswith("INV-"):
                # legacy from old debtor invoice_entry (pre-rewrite); no longer created
                oid = ref.split("-")[-1]
                ref_label = f"Legacy Invoice Entry #{oid}"
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
            special_pricing_group=data.get('special_pricing_group'),
            supplier_id=data.get('supplier_id'),
            pack_size=int(data.get('pack_size') or 1),
            pack_unit=data.get('pack_unit') or 'pcs',
            pack_cost=float(data.get('pack_cost') or 0)
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

        # Pack sizing + supplier (for Supplier Invoice Entry) + unit
        if 'unit' in data:
            p.unit = data.get('unit') or 'pcs'
        if 'pack_size' in data:
            try:
                p.pack_size = int(data.get('pack_size') or 1)
            except (ValueError, TypeError):
                pass
        if 'pack_unit' in data:
            p.pack_unit = data.get('pack_unit') or 'pcs'
        if 'pack_cost' in data:
            try:
                p.pack_cost = float(data.get('pack_cost') or 0)
            except (ValueError, TypeError):
                pass
        if 'supplier_id' in data:
            sup_id = data.get('supplier_id')
            if sup_id in ('', None):
                p.supplier_id = None
            else:
                try:
                    p.supplier_id = int(sup_id)
                except (ValueError, TypeError):
                    p.supplier_id = None

        db.session.commit()
        return jsonify({"success": True})

    @app.route('/api/products/search')
    @login_required
    def api_products_search():
        """Fast search for adding items in POS, Invoice Entry, PO, etc.
        Supports name, SKU, barcode partial match. Returns pack + tax + supplier + on_hand stock for the branch.
        """
        q = (request.args.get('q') or '').strip()
        try:
            limit = min(int(request.args.get('limit', 20)), 50)
        except:
            limit = 20

        branch_id = request.args.get('branch_id', type=int) or current_user.branch_id or 1

        query = Product.query.filter_by(is_active=True)
        if q:
            like = f'%{q}%'
            query = query.filter(
                db.or_(
                    Product.name.ilike(like),
                    Product.sku.ilike(like),
                    Product.barcode.ilike(like)
                )
            )
        prods = query.order_by(Product.name).limit(limit).all()

        results = []
        for p in prods:
            tr = p.tax_rate
            inv = Inventory.query.filter_by(product_id=p.id, branch_id=branch_id).first()
            on_hand = inv.quantity if inv else 0
            results.append({
                'id': p.id,
                'name': p.name,
                'sku': p.sku,
                'barcode': p.barcode or '',
                'price': p.price or 0,
                'cost_price': p.cost_price or 0,
                'pack_size': p.pack_size or 1,
                'pack_unit': p.pack_unit or 'pcs',
                'pack_cost': p.pack_cost or 0,
                'tax_rate': (tr.rate if tr else 12.5),
                'tax_rate_id': (tr.id if tr else None),
                'supplier_id': p.supplier_id,
                'supplier_name': (p.supplier.name if p.supplier else None),
                'unit': p.unit or 'pcs',
                'on_hand': on_hand
            })
        return jsonify({'success': True, 'results': results, 'q': q, 'branch_id': branch_id})

    @app.route('/api/debtors/search')
    @login_required
    def api_debtors_search():
        q = (request.args.get('q') or '').strip().lower()
        limit = min(int(request.args.get('limit', 20)), 50)
        query = Debtor.query
        if q:
            like = f'%{q}%'
            query = query.filter(
                db.or_(
                    Debtor.name.ilike(like),
                    Debtor.main_code.ilike(like),
                    Debtor.second_code.ilike(like),
                    Debtor.phone.ilike(like),
                    Debtor.contact_person.ilike(like)
                )
            )
        ds = query.order_by(Debtor.name).limit(limit).all()
        results = [{
            'id': d.id,
            'name': d.name,
            'main_code': d.main_code or '',
            'phone': d.phone or '',
            'outstanding_balance': d.outstanding_balance or 0,
            'tin': d.tin or ''
        } for d in ds]
        return jsonify({'success': True, 'results': results})

    @app.route('/api/suppliers/search')
    @login_required
    def api_suppliers_search():
        q = (request.args.get('q') or '').strip().lower()
        limit = min(int(request.args.get('limit', 20)), 50)
        query = Supplier.query
        if q:
            like = f'%{q}%'
            query = query.filter(
                db.or_(
                    Supplier.name.ilike(like),
                    Supplier.main_code.ilike(like),
                    Supplier.phone.ilike(like),
                    Supplier.contact_person.ilike(like)
                )
            )
        ss = query.order_by(Supplier.name).limit(limit).all()
        results = [{
            'id': s.id,
            'name': s.name,
            'main_code': s.main_code or '',
            'phone': s.phone or ''
        } for s in ss]
        return jsonify({'success': True, 'results': results})

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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_view_sales'):
            flash('You do not have permission to view sales', 'error')
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
        if current_user.role != 'admin' and not user_has_permission(current_user, 'can_view_fiscal_logs'):
            flash('You do not have permission to view VMS logs', 'error')
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
        layout = request.args.get('layout') or (receipt_config.layout or '80mm')

        # Respect explicit designer=1, or VMS disabled (demo), OR if a layout was saved in the Designer.
        # This ensures: "even if VMS enabled it should use the one from the designer"
        # and "when disabled the receipt layout should be the one we set in the designer".
        has_saved_designer_layout = bool((getattr(receipt_config, 'receipt_layout', None) or '').strip())
        use_designer = (request.args.get('designer') == '1') or getattr(order.branch, 'vms_disabled', False) or has_saved_designer_layout

        # When designer preference is active, default to a sensible thermal size if none specified
        if use_designer and layout not in ('80mm', 'a4'):
            layout = '80mm'

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

        # Define all available fields the user can drag (Composer 2.5 model)
        available_fields = {
            "Order / Sale": [
                {"key": "order.id", "label": "Receipt Number"},
                {"key": "order.created_at", "label": "Date & Time"},
                {"key": "order.payment_method", "label": "Payment Method"},
                {"key": "order.payment_ref", "label": "Payment Reference"},
                {"key": "order.subtotal", "label": "Subtotal (VEP)"},
                {"key": "order.vat", "label": "Tax / VAT"},
                {"key": "order.total", "label": "Total (VIP)"},
                {"key": "order.customer_name", "label": "Customer Name"},
                {"key": "order.customer_tin", "label": "Customer TIN"},
                {"key": "item_count", "label": "Item Count"},
            ],
            "Branch / Seller": [
                {"key": "branch.name", "label": "Branch Name"},
                {"key": "branch.tin", "label": "Branch TIN"},
                {"key": "branch.vms_uid", "label": "VMS UID"},
                {"key": "branch.address", "label": "Branch Address"},
                {"key": "branch.phone", "label": "Branch Phone"},
            ],
            "Customer / Debtor": [
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
            ],
            "Totals (shortcuts)": [
                {"key": "subtotal_vep", "label": "Subtotal VEP"},
                {"key": "vat_amount", "label": "VAT Amount"},
                {"key": "total_vip", "label": "Total VIP"},
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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_do_stock_adjustment'):
            flash('You do not have permission to do stock adjustments', 'error')
            return redirect(url_for('dashboard'))

        products = Product.query.all()
        branches = get_active_branches()
        recent_moves = StockMovement.query.order_by(StockMovement.timestamp.desc()).limit(30).all()
        return render_template('stock_adjustment.html', products=products, branches=branches, recent_moves=recent_moves, user=current_user)

    # ==================== PURCHASE ORDERS ====================
    @app.route('/purchase-orders')
    @login_required
    def purchase_orders():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_create_po'):
            flash('You do not have permission to view purchase orders', 'error')
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
            tax_rate = 12.5
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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_create_po'):
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
            tax_rate = 12.5
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

    @app.route('/api/supplier-return', methods=['POST'])
    @login_required
    def api_supplier_return():
        """Record a return of goods to a supplier. Reduces stock and creates audit movement.
        Can be linked to a previously entered Goods Receipt (invoice from supplier)."""
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager or admin access required"}), 403

        data = request.get_json() or {}
        try:
            product_id = int(data.get('product_id'))
            branch_id = int(data.get('branch_id'))
            qty = int(data.get('quantity', 0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid product, branch or quantity"}), 400

        reason = data.get('reason', 'Return to supplier')
        gr_id = data.get('goods_receipt_id')

        if qty <= 0:
            return jsonify({"success": False, "message": "Return quantity must be greater than zero"}), 400

        inv = get_or_create_inventory(product_id, branch_id)
        prev_qty = inv.quantity
        inv.quantity -= qty

        ref = f"RET-SUP-{gr_id}" if gr_id else "RET-SUP"
        record_stock_movement(
            product_id, branch_id, 'SUPPLIER_RETURN',
            -qty, prev_qty, inv.quantity,
            ref, current_user.id, reason
        )

        # Optional: if linked to GR, we could update received counts but for audit we just record the movement
        if gr_id:
            gr = GoodsReceipt.query.get(gr_id)
            if gr and not gr.is_reversed:
                # Just note it; full reversal is separate
                pass

        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"Returned {qty} unit(s) to supplier. Stock reduced and movement recorded."
        })

    @app.route('/stock-take')
    @login_required
    def stock_take():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_stock_take'):
            flash('You do not have permission to perform stock takes', 'error')
            return redirect(url_for('dashboard'))
        takes = StockTake.query.order_by(StockTake.created_at.desc()).limit(20).all()
        branches = get_active_branches()
        active_take = StockTake.query.filter_by(status='in_progress').first()
        return render_template('stock_take.html', takes=takes, branches=branches, active_take=active_take, user=current_user)

    @app.route('/api/stock-take/start', methods=['POST'])
    @login_required
    def api_start_stock_take():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_stock_take'):
            return jsonify({"success": False, "message": "Permission denied"}), 403

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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_stock_take'):
            return jsonify({"success": False, "message": "Permission denied"}), 403

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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_stock_take'):
            return jsonify({"success": False, "message": "Permission denied"}), 403

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

    # ==================== DEBTOR APIs (for live balance in POS when using DEBTOR/ON_ACCOUNT payment) ====================
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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_view_debtors') and not user_has_permission(current_user, 'can_manage_debtors'):
            flash('You do not have permission to view debtors', 'error')
            return redirect(url_for('dashboard'))
        debtors_list = Debtor.query.order_by(Debtor.outstanding_balance.desc()).all()
        return render_template('debtors.html', debtors=debtors_list, user=current_user)

    @app.route('/api/debtors', methods=['POST'])
    @login_required
    def api_create_debtor():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_manage_debtors'):
            return jsonify({"success": False, "message": "Permission denied"}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Name required"}), 400

        # Auto-generate main_code if not provided
        main_code = (data.get('main_code') or '').strip()
        if not main_code:
            last = Debtor.query.order_by(Debtor.id.desc()).first()
            next_num = (last.id + 1) if last else 1
            main_code = f"CUST-{str(next_num).zfill(4)}"

        d = Debtor(
            main_code=main_code,
            second_code=data.get('second_code'),
            name=name,
            contact_person=data.get('contact_person'),
            tin=data.get('tin'),
            phone=data.get('phone'),
            phone2=data.get('phone2'),
            email=data.get('email'),
            address=data.get('address'),
            address2=data.get('address2'),
            address3=data.get('address3'),
            category=data.get('category'),
            discount_group=data.get('discount_group'),
            outstanding_balance=0.0
        )
        db.session.add(d)
        db.session.commit()
        return jsonify({"success": True, "id": d.id, "name": d.name, "main_code": d.main_code})

    @app.route('/api/debtors/<int:debtor_id>/payment', methods=['POST'])
    @login_required
    def api_debtor_payment(debtor_id):
        """Record a payment against a debtor balance (reduces AR). Optional mini fiscal receipt for audit."""
        if current_user.role not in ['admin', 'manager']:
            return jsonify({"success": False, "message": "Manager or admin only"}), 403
        d = Debtor.query.get_or_404(debtor_id)
        data = request.get_json() or {}
        amount = float(data.get('amount', 0))
        notes = data.get('notes', 'Debtor payment / settlement')
        fiscalize = bool(data.get('fiscalize', False))

        if amount <= 0:
            return jsonify({"success": False, "message": "Amount must be positive"}), 400

        prev_bal = d.outstanding_balance or 0
        d.outstanding_balance = round(max(0, prev_bal - amount), 2)

        # Optional lightweight fiscal record (for audit trail / receipt)
        fiscal = None
        if fiscalize:
            # Create a minimal negative "payment receipt" order for the books
            pay_order = Order(
                branch_id=current_user.branch_id or (Branch.query.first().id if Branch.query.first() else 1),
                staff_id=current_user.id,
                customer_name=d.name,
                debtor_id=d.id,
                order_type='debtor_payment',
                payment_method='CASH',
                status='completed',
                subtotal=-amount,
                vat=0,
                total=-amount
            )
            db.session.add(pay_order)
            db.session.flush()
            try:
                fiscal = generate_and_transmit_fiscal(pay_order, Branch.query.get(pay_order.branch_id))
            except Exception as fx:
                print(f"[Debtor Payment] Fiscal warning: {fx}")

        db.session.commit()
        return jsonify({
            "success": True,
            "new_balance": d.outstanding_balance,
            "message": f"Payment of FJD {amount:.2f} recorded. New balance: FJD {d.outstanding_balance:.2f}",
            "fiscalized": bool(fiscal)
        })

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

    # ==================== INVOICE ENTRY (Supplier Invoice) - Stock entry per supplier invoice. NO sales, NO VMS fiscal. ====================
    @app.route('/invoice-entry')
    @login_required
    def invoice_entry():
        if current_user.role not in ['admin', 'manager', 'staff']:
            flash('Staff access required', 'error')
            return redirect(url_for('dashboard'))
        # Only suppliers + active products (with pack/supplier fields) are needed for the supplier stock entry form
        suppliers = Supplier.query.order_by(Supplier.name).all()
        products = Product.query.filter_by(is_active=True).order_by(Product.name).all()
        return render_template('invoice_entry.html',
                               suppliers=suppliers,
                               products=products,
                               user=current_user)

    @app.route('/api/invoice-entry', methods=['POST'])
    @login_required
    def api_invoice_entry():
        """
        Supplier Invoice stock entry ONLY.
        - Record supplier invoice # + payment type.
        - Add stock (positive) using pack_qty * Product.pack_size (default 1).
        - Creates GoodsReceipt + GoodsReceiptItem(s) for audit trail.
        - Creates SUPPLIER_INVOICE typed StockMovement(s).
        - NEVER deducts stock, NEVER calls VMS fiscalize, NEVER creates Order/sales.
        """
        if current_user.role not in ['admin', 'manager', 'staff']:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        data = request.get_json() or {}
        # Enforce supplier mode only (ignore any legacy sales payload)
        if not data.get('is_supplier_invoice'):
            return jsonify({"success": False, "message": "This endpoint is now Supplier Invoice (stock in) only. Use POS for sales."}), 400

        branch_id = data.get('branch_id') or current_user.branch_id or 1
        items = data.get('items', [])

        if not items:
            return jsonify({"success": False, "message": "No items provided"}), 400

        supplier_id = data.get('supplier_id')
        supplier_invoice_no = (data.get('supplier_invoice_no') or '').strip()
        payment_type = data.get('payment_type', 'CASH')

        if not supplier_id:
            return jsonify({"success": False, "message": "Supplier is required"}), 400
        if not supplier_invoice_no:
            return jsonify({"success": False, "message": "Supplier Invoice Number is required"}), 400

        branch = Branch.query.get(branch_id)
        if not branch:
            return jsonify({"success": False, "message": "Invalid branch"}), 400
        supplier = Supplier.query.get(supplier_id)
        if not supplier:
            return jsonify({"success": False, "message": "Invalid supplier"}), 400

        # Create Goods Receipt (audit record for the supplier invoice, source = manual supplier entry)
        gr = GoodsReceipt(
            supplier_id=supplier_id,
            supplier_invoice_no=supplier_invoice_no,
            branch_id=branch_id,
            notes=f"Supplier Invoice Entry - Payment: {payment_type}",
            received_by=current_user.id
        )
        db.session.add(gr)
        db.session.flush()

        total_pieces_added = 0
        total_lines = 0

        for item in items:
            product_id = item.get('product_id')
            pack_qty = int(item.get('pack_qty') or item.get('quantity') or 1)
            cost_vep = float(item.get('cost_vep') or item.get('vep') or 0)
            cost_vip = float(item.get('cost_vip') or item.get('vip') or 0)

            prod = Product.query.get(product_id)
            if not prod:
                continue

            pack_size = int(prod.pack_size or 1)
            pieces = pack_qty * pack_size
            if pieces < 1:
                pieces = pack_qty  # safety

            # GR line (received_qty in pieces; costs are per-piece as entered in UI)
            gri = GoodsReceiptItem(
                gr_id=gr.id,
                product_id=product_id,
                ordered_qty=0,
                received_qty=pieces,
                cost_vep=round(cost_vep, 4),
                cost_vip=round(cost_vip, 4)
            )
            db.session.add(gri)

            # Inventory add (per branch)
            inv = get_or_create_inventory(product_id, branch_id)
            prev = inv.quantity or 0
            inv.quantity = (inv.quantity or 0) + pieces

            # Typed movement for full audit (positive for stock in)
            record_stock_movement(
                product_id=product_id,
                branch_id=branch_id,
                movement_type='SUPPLIER_INVOICE',
                qty_change=+pieces,
                prev=prev,
                new=inv.quantity,
                ref=f"SUP-INV-{gr.id}",
                user_id=current_user.id,
                notes=f"Supplier {supplier.name} invoice {supplier_invoice_no} (pack {pack_qty} x {pack_size})"
            )

            total_pieces_added += pieces
            total_lines += 1

        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Supplier invoice #{supplier_invoice_no} recorded. Stock added: {total_pieces_added} pieces across {total_lines} line(s). (No VMS fiscalization.)",
            "gr_id": gr.id,
            "pieces_added": total_pieces_added
        })

    # ==================== HELD TRANSACTIONS (Hold Sale / Hold PO / Hold Supplier Invoice) ====================
    @app.route('/api/held-transactions', methods=['GET', 'POST'])
    @login_required
    def api_held_transactions():
        if request.method == 'POST':
            data = request.get_json() or {}
            tx_type = data.get('type')
            tx_data = data.get('data')
            reference = data.get('reference', '')
            notes = data.get('notes', '')

            if tx_type not in ('sale', 'purchase_order', 'supplier_invoice'):
                return jsonify({"success": False, "message": "Invalid transaction type"}), 400
            if not tx_data:
                return jsonify({"success": False, "message": "No data provided"}), 400

            held = HeldTransaction(
                transaction_type=tx_type,
                data=json.dumps(tx_data),
                user_id=current_user.id,
                branch_id=data.get('branch_id') or current_user.branch_id or 1,
                reference=reference[:150] if reference else None,
                notes=notes
            )
            db.session.add(held)
            db.session.commit()
            return jsonify({"success": True, "held_id": held.id})

        # GET list
        tx_type = request.args.get('type')
        query = HeldTransaction.query.filter_by(user_id=current_user.id)
        if tx_type:
            query = query.filter_by(transaction_type=tx_type)
        held_list = query.order_by(HeldTransaction.updated_at.desc()).all()

        results = []
        for h in held_list:
            try:
                parsed = json.loads(h.data)
            except:
                parsed = {}
            results.append({
                "id": h.id,
                "type": h.transaction_type,
                "reference": h.reference or "",
                "notes": h.notes or "",
                "created_at": h.created_at.isoformat() if h.created_at else None,
                "updated_at": h.updated_at.isoformat() if h.updated_at else None,
                "data": parsed
            })
        return jsonify({"success": True, "held": results})

    @app.route('/api/held-transactions/<int:held_id>/recall', methods=['POST'])
    @login_required
    def api_recall_held(held_id):
        held = HeldTransaction.query.filter_by(id=held_id, user_id=current_user.id).first()
        if not held:
            return jsonify({"success": False, "message": "Held transaction not found"}), 404

        try:
            data = json.loads(held.data)
        except:
            data = {}

        # Delete after recall (or keep if you prefer archive)
        db.session.delete(held)
        db.session.commit()

        return jsonify({"success": True, "type": held.transaction_type, "data": data})

    @app.route('/api/held-transactions/<int:held_id>', methods=['DELETE'])
    @login_required
    def api_delete_held(held_id):
        held = HeldTransaction.query.filter_by(id=held_id, user_id=current_user.id).first()
        if not held:
            return jsonify({"success": False, "message": "Not found"}), 404
        db.session.delete(held)
        db.session.commit()
        return jsonify({"success": True})

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

    # ==================== RENTAL MANAGEMENT ====================
    # Properties have barcodes so they can be "scanned" like products in a POS-style rent collection interface.
    # Tenants linked to properties. Charges and payments recorded separately (no inventory impact).
    # Uses existing PaymentMethod for consistency.

    @app.route('/rental-properties')
    @login_required
    def rental_properties():
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_view_suppliers'):  # reuse a perm or add later
            flash('Access required', 'error')
            return redirect(url_for('dashboard'))
        props = RentalProperty.query.order_by(RentalProperty.name).all()
        tenants = Tenant.query.order_by(Tenant.name).all()
        tax_rates = TaxRate.query.filter_by(is_active=True).all()
        return render_template('rental_properties.html', properties=props, tenants=tenants, tax_rates=tax_rates, user=current_user)

    @app.route('/api/rental-properties', methods=['GET', 'POST'])
    @login_required
    def api_rental_properties():
        if request.method == 'GET':
            props = RentalProperty.query.order_by(RentalProperty.name).all()
            return jsonify([{
                'id': p.id,
                'name': p.name,
                'address': p.address or '',
                'barcode': p.barcode,
                'monthly_rent': p.monthly_rent,
                'deposit': p.deposit,
                'status': p.status,
                'property_type': p.property_type,
                'tax_rate_id': p.tax_rate_id,
                'tax_rate_label': p.tax_rate.label if p.tax_rate else None,
                'current_tenant_id': p.current_tenant_id,
                'current_tenant_name': p.current_tenant.name if p.current_tenant else None
            } for p in props])

        # POST - create
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        barcode = (data.get('barcode') or '').strip()
        if not name or not barcode:
            return jsonify({"success": False, "message": "Name and barcode required"}), 400

        # Check duplicate barcode (like products)
        if RentalProperty.query.filter_by(barcode=barcode).first():
            return jsonify({"success": False, "message": f"Barcode '{barcode}' already assigned to another property"}), 400

        p = RentalProperty(
            name=name,
            address=data.get('address'),
            barcode=barcode,
            monthly_rent=float(data.get('monthly_rent', 0)),
            deposit=float(data.get('deposit', 0)),
            status=data.get('status', 'available'),
            description=data.get('description'),
            property_type=data.get('property_type', 'residential'),
            tax_rate_id=data.get('tax_rate_id') or None,
            current_tenant_id=data.get('current_tenant_id') or None,
            photo_base64 = data.get('photo_base64')
        )
        db.session.add(p)
        db.session.commit()
        return jsonify({"success": True, "id": p.id})

    @app.route('/api/rental-properties/<int:prop_id>', methods=['PUT', 'DELETE'])
    @login_required
    def api_rental_property_detail(prop_id):
        p = RentalProperty.query.get_or_404(prop_id)
        if request.method == 'DELETE':
            if current_user.role != 'admin':
                return jsonify({"success": False, "message": "Admin only"}), 403
            db.session.delete(p)
            db.session.commit()
            return jsonify({"success": True})

        # PUT update
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json() or {}
        p.name = data.get('name', p.name)
        if 'barcode' in data:
            new_bar = data['barcode'].strip()
            if new_bar != p.barcode and RentalProperty.query.filter_by(barcode=new_bar).first():
                return jsonify({"success": False, "message": "Barcode already in use"}), 400
            p.barcode = new_bar
        p.address = data.get('address', p.address)
        p.monthly_rent = float(data.get('monthly_rent', p.monthly_rent))
        p.deposit = float(data.get('deposit', p.deposit))
        p.status = data.get('status', p.status)
        p.description = data.get('description', p.description)
        p.property_type = data.get('property_type', p.property_type)
        if 'tax_rate_id' in data:
            p.tax_rate_id = data['tax_rate_id'] or None
        if 'current_tenant_id' in data:
            p.current_tenant_id = data['current_tenant_id'] or None
        if 'photo_base64' in data:
            p.photo_base64 = data['photo_base64']
        db.session.commit()
        return jsonify({"success": True})

    # Tenants (for rental)
    @app.route('/rental-tenants')
    @login_required
    def rental_tenants():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        tenants = Tenant.query.order_by(Tenant.name).all()
        props = RentalProperty.query.order_by(RentalProperty.name).all()
        return render_template('rental_tenants.html', tenants=tenants, properties=props, user=current_user)

    @app.route('/api/rental-tenants', methods=['GET', 'POST'])
    @login_required
    def api_rental_tenants():
        if request.method == 'GET':
            ts = Tenant.query.order_by(Tenant.name).all()
            return jsonify([{
                'id': t.id, 'name': t.name, 'phone': t.phone or '', 'email': t.email or '',
                'outstanding_balance': t.outstanding_balance or 0,
                'assigned_property_count': len(getattr(t, 'assigned_properties', []))
            } for t in ts])

        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Name required"}), 400
        t = Tenant(
            name=name,
            phone=data.get('phone'),
            email=data.get('email'),
            id_number=data.get('id_number'),
            address=data.get('address'),
            notes=data.get('notes')
        )
        db.session.add(t)
        db.session.commit()
        return jsonify({"success": True, "id": t.id})

    @app.route('/api/rental-tenants/<int:tid>', methods=['PUT', 'DELETE'])
    @login_required
    def api_rental_tenant_detail(tid):
        t = Tenant.query.get_or_404(tid)
        if request.method == 'DELETE':
            if current_user.role != 'admin':
                return jsonify({"success": False}), 403
            db.session.delete(t)
            db.session.commit()
            return jsonify({"success": True})
        data = request.get_json() or {}
        t.name = data.get('name', t.name)
        t.phone = data.get('phone', t.phone)
        t.email = data.get('email', t.email)
        t.id_number = data.get('id_number', t.id_number)
        t.address = data.get('address', t.address)
        t.notes = data.get('notes', t.notes)
        if 'outstanding_balance' in data:
            t.outstanding_balance = float(data['outstanding_balance'])
        db.session.commit()
        return jsonify({"success": True})

    # ==================== RENTAL COLLECTION (POS-like for rent) ====================
    # Works almost exactly like the main POS: scan property barcode (or search), add rent charges/payments to cart,
    # use Amount Received + change, complete to record using existing payment methods. No stock impact.
    @app.route('/rental-collection')
    @login_required
    def rental_collection():
        if current_user.role not in ['admin', 'manager', 'staff']:
            flash('Access required', 'error')
            return redirect(url_for('dashboard'))
        # Pass branches and payment methods like POS for consistency
        branches = get_active_branches()
        payment_methods = PaymentMethod.query.filter_by(is_active=True).all()
        # Current branch default from user or first
        current_branch = None
        if current_user.branch_id:
            current_branch = Branch.query.get(current_user.branch_id)
        if not current_branch:
            current_branch = branches[0] if branches else None
        return render_template('rental_collection.html', branches=branches, payment_methods=payment_methods, current_branch=current_branch, user=current_user)

    @app.route('/api/rental/lookup-by-barcode', methods=['POST'])
    @login_required
    def api_rental_lookup_by_barcode():
        """Scan property barcode (like product in POS) -> return property + tenant + suggested rent amount"""
        data = request.get_json() or {}
        code = (data.get('barcode') or '').strip()
        if not code:
            return jsonify({"success": False, "message": "No barcode"}), 400

        prop = RentalProperty.query.filter_by(barcode=code).first()
        if not prop:
            # Also allow lookup by tenant name partial? but focus on barcode for property
            return jsonify({"success": False, "message": "Property not found for barcode"}), 404

        tenant = prop.current_tenant
        due = prop.monthly_rent or 0
        if tenant:
            due = max(due, tenant.outstanding_balance or 0)  # show arrears if higher

        return jsonify({
            "success": True,
            "property": {
                "id": prop.id,
                "name": prop.name,
                "barcode": prop.barcode,
                "monthly_rent": prop.monthly_rent,
                "property_type": prop.property_type,
                "tax_rate": prop.tax_rate.rate if prop.tax_rate else None
            },
            "tenant": {
                "id": tenant.id if tenant else None,
                "name": tenant.name if tenant else "Unassigned",
                "outstanding": tenant.outstanding_balance if tenant else 0
            },
            "suggested_amount": round(due, 2)
        })

    @app.route('/api/rental/process', methods=['POST'])
    @login_required
    def api_rental_process():
        """Process a 'cart' of rental charges/payments. Similar to api_pos_create_sale but for rent."""
        data = request.get_json() or {}
        items = data.get('items', [])  # [{property_id, tenant_id, amount, type: 'charge' or 'payment'}]
        payment_method = data.get('payment_method', 'CASH')
        tendered = float(data.get('tendered_amount', 0)) or 0

        if not items:
            return jsonify({"success": False, "message": "No items"}), 400

        total = 0.0
        created_payments = []
        created_charges = []

        for it in items:
            pid = it.get('property_id')
            tid = it.get('tenant_id')
            amt = float(it.get('amount', 0))
            itype = it.get('type', 'payment')  # charge or payment

            prop = RentalProperty.query.get(pid) if pid else None
            ten = Tenant.query.get(tid) if tid else None
            if not ten:
                continue

            if itype == 'charge':
                # Compute tax components based on property type (Residential VEP, Commercial VIP)
                prop_for_tax = prop or RentalProperty.query.get(pid)
                ptype = prop_for_tax.property_type if prop_for_tax else 'residential'
                custom_rate = (prop_for_tax.tax_rate.rate / 100.0) if (prop_for_tax and prop_for_tax.tax_rate) else None
                vep, vat, vip = compute_rent_amounts(amt, ptype, custom_rate)
                ch = RentalCharge(
                    tenant_id=ten.id,
                    property_id=pid,
                    amount=vip,  # store the final charged amount (VIP for display/balance)
                    description=it.get('description', 'Rent Charge'),
                    created_by=current_user.id
                )
                # Optionally store breakdown in future (add columns if needed); for now use description
                ch.description = f"{it.get('description', 'Rent Charge')} (VEP {vep:.2f} + VAT {vat:.2f} = VIP {vip:.2f})"
                db.session.add(ch)
                created_charges.append(ch)
                # increase balance by the charged VIP amount
                ten.outstanding_balance = round((ten.outstanding_balance or 0) + vip, 2)
                total += vip  # actual charged for this item
            else:
                # payment
                pay = RentalPayment(
                    tenant_id=ten.id,
                    property_id=pid,
                    amount=amt,
                    payment_method=payment_method,
                    reference=data.get('payment_ref'),
                    notes=it.get('notes', 'Rent payment via collection'),
                    created_by=current_user.id
                )
                db.session.add(pay)
                created_payments.append(pay)
                # reduce balance
                ten.outstanding_balance = round(max(0, (ten.outstanding_balance or 0) - amt), 2)
                total += amt  # payment amount

        db.session.commit()

        change = round(tendered - total, 2) if tendered > total else 0

        return jsonify({
            "success": True,
            "message": f"Processed {len(created_payments)} payment(s) / {len(created_charges)} charge(s). Total FJD {total:.2f}",
            "total": round(total, 2),
            "change": change,
            "payments": [p.id for p in created_payments],
            "charges": [c.id for c in created_charges]
        })

    @app.route('/rental-history')
    @login_required
    def rental_history():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        charges = RentalCharge.query.order_by(RentalCharge.charge_date.desc()).limit(50).all()
        payments = RentalPayment.query.order_by(RentalPayment.payment_date.desc()).limit(50).all()
        return render_template('rental_history.html', charges=charges, payments=payments, user=current_user)

    # Maintenance for rentals/properties
    @app.route('/maintenance')
    @login_required
    def maintenance():
        if current_user.role not in ['admin', 'manager', 'staff']:
            flash('Access required', 'error')
            return redirect(url_for('dashboard'))
        reqs = MaintenanceRequest.query.order_by(MaintenanceRequest.reported_date.desc()).limit(100).all()
        props = RentalProperty.query.order_by(RentalProperty.name).all()
        tenants = Tenant.query.order_by(Tenant.name).all()
        users = User.query.filter(User.role.in_(['admin','manager','staff'])).all()
        return render_template('maintenance.html', requests=reqs, properties=props, tenants=tenants, staff=users, user=current_user)

    @app.route('/api/maintenance', methods=['GET', 'POST'])
    @login_required
    def api_maintenance():
        if request.method == 'GET':
            reqs = MaintenanceRequest.query.order_by(MaintenanceRequest.reported_date.desc()).all()
            return jsonify([{
                'id': r.id,
                'property_id': r.property_id,
                'property_name': r.property.name if r.property else '',
                'tenant_name': r.tenant.name if r.tenant else '',
                'description': r.description,
                'priority': r.priority,
                'status': r.status,
                'actual_cost': r.actual_cost,
                'reported_date': r.reported_date.isoformat() if r.reported_date else None
            } for r in reqs])

        data = request.get_json() or {}
        prop_id = data.get('property_id')
        if not prop_id:
            return jsonify({"success": False, "message": "Property required"}), 400
        m = MaintenanceRequest(
            property_id=prop_id,
            tenant_id=data.get('tenant_id') or None,
            description=data.get('description', 'Maintenance request'),
            priority=data.get('priority', 'normal'),
            estimated_cost=float(data.get('estimated_cost', 0)),
            notes=data.get('notes'),
            created_by=current_user.id
        )
        db.session.add(m)
        db.session.commit()
        return jsonify({"success": True, "id": m.id})

    @app.route('/api/maintenance/<int:mid>', methods=['PUT'])
    @login_required
    def api_update_maintenance(mid):
        m = MaintenanceRequest.query.get_or_404(mid)
        data = request.get_json() or {}
        m.status = data.get('status', m.status)
        if 'actual_cost' in data:
            m.actual_cost = float(data['actual_cost'])
        if 'notes' in data:
            m.notes = data['notes']
        if m.status == 'completed' and not m.completed_date:
            m.completed_date = datetime.utcnow()
        db.session.commit()
        # If completed and cost, optionally record stock movement or expense - simple for now
        return jsonify({"success": True})

    # Leases
    @app.route('/leases')
    @login_required
    def leases():
        if current_user.role not in ['admin', 'manager']:
            flash('Manager access required', 'error')
            return redirect(url_for('dashboard'))
        leases = Lease.query.order_by(Lease.start_date.desc()).all()
        props = RentalProperty.query.all()
        tenants = Tenant.query.all()
        return render_template('leases.html', leases=leases, properties=props, tenants=tenants, user=current_user)

    @app.route('/api/leases', methods=['POST'])
    @login_required
    def api_create_lease():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json() or {}
        l = Lease(
            tenant_id=data['tenant_id'],
            property_id=data['property_id'],
            start_date = data.get('start_date') and __import__('datetime').datetime.fromisoformat(data['start_date']),
            end_date = data.get('end_date') and __import__('datetime').datetime.fromisoformat(data['end_date']),
            rent_amount = float(data.get('rent_amount', 0)),
            terms = data.get('terms')
        )
        db.session.add(l)
        db.session.commit()
        return jsonify({"success": True, "id": l.id})

    @app.route('/api/leases/generate-charges', methods=['POST'])
    @login_required
    def api_generate_lease_charges():
        """Bulk create charges for active leases (demo for recurring)"""
        from datetime import datetime as dt
        today = dt.utcnow().date()
        active_leases = Lease.query.filter_by(is_active=True).all()
        created = 0
        for lease in active_leases:
            # simple: if no charge this month for the property
            existing = RentalCharge.query.filter(
                RentalCharge.property_id == lease.property_id,
                RentalCharge.charge_date >= dt(today.year, today.month, 1)
            ).first()
            if not existing:
                ch = RentalCharge(
                    tenant_id=lease.tenant_id,
                    property_id=lease.property_id,
                    amount=lease.rent_amount or (lease.property.monthly_rent if lease.property else 0),
                    description=f"Recurring from lease #{lease.id}",
                    created_by=current_user.id
                )
                db.session.add(ch)
                created += 1
        db.session.commit()
        return jsonify({"success": True, "created": created})

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

        # For very large DBs (millions of rows), avoid loading full objects + Python loops.
        # Use DB-side aggregation + limited recent sample for the main list.
        orders = query.order_by(Order.created_at.desc()).limit(500).all()  # reduced from 1000

        # Efficient aggregates using SQL (much faster than Python loops on millions of rows).
        # These now respect the same date_from/date_to filters as the orders list.
        base_query = query  # already has date filters applied above

        total_sales = base_query.with_entities(db.func.coalesce(db.func.sum(Order.total), 0)).scalar() or 0
        total_vat = base_query.with_entities(db.func.coalesce(db.func.sum(Order.vat), 0)).scalar() or 0

        by_media = {}
        media_rows = base_query.with_entities(
            Order.payment_method,
            db.func.sum(Order.total)
        ).group_by(Order.payment_method).all()
        for method, amt in media_rows:
            by_media[method or 'Unknown'] = amt or 0

        by_dept = {}
        # Department aggregation (join only on the filtered orders)
        dept_rows = db.session.query(
            db.func.coalesce(Product.category, 'Uncategorized'),
            db.func.sum(OrderItem.line_total)
        ).join(OrderItem, OrderItem.product_id == Product.id)\
         .join(Order, OrderItem.order_id == Order.id)\
         .filter(Order.id.in_(base_query.with_entities(Order.id)))\
         .group_by(Product.category).all()
        for cat, amt in dept_rows:
            by_dept[cat or 'Uncategorized'] = amt or 0

        report_type = request.args.get('report_type', 'summary')

        # Department sales report
        dept_sales = {}
        if report_type == 'department':
            for o in orders:
                for item in o.items:
                    dept = item.product.department.name if item.product and item.product.department else (item.product.category or 'Uncategorized')
                    if dept not in dept_sales:
                        dept_sales[dept] = {'total_vip': 0, 'total_vep': 0, 'count': 0}
                    dept_sales[dept]['total_vip'] += item.line_total or 0
                    # rough vep
                    rate = (item.product.tax_rate.rate / 100) if item.product and item.product.tax_rate else 0.125
                    vep = (item.line_total or 0) / (1 + rate) if rate > 0 else (item.line_total or 0)
                    dept_sales[dept]['total_vep'] += vep
                    dept_sales[dept]['count'] += item.quantity or 0

        # Product sales report
        product_sales = {}
        if report_type == 'product':
            for o in orders:
                for item in o.items:
                    pid = item.product_id
                    if pid not in product_sales:
                        product_sales[pid] = {'name': item.product_name or (item.product.name if item.product else 'Unknown'), 'sku': item.product.sku if item.product else '', 'total_vip': 0, 'qty': 0}
                    product_sales[pid]['total_vip'] += item.line_total or 0
                    product_sales[pid]['qty'] += item.quantity or 0

        # Cashier sales report
        cashier_sales = {}
        if report_type == 'cashier':
            for o in orders:
                staff = o.staff.username if o.staff else 'Unknown'
                if staff not in cashier_sales:
                    cashier_sales[staff] = {'total_vip': 0, 'count': 0}
                cashier_sales[staff]['total_vip'] += o.total or 0
                cashier_sales[staff]['count'] += 1

        # Tax report
        tax_breakdown = {'total_vat': total_vat, 'by_rate': {}}
        if report_type == 'tax':
            for o in orders:
                for item in o.items:
                    rate = 15
                    if item.product and item.product.tax_rate:
                        rate = item.product.tax_rate.rate
                    key = f"{rate}%"
                    if key not in tax_breakdown['by_rate']:
                        tax_breakdown['by_rate'][key] = {'vat': 0, 'vip': 0}
                    tax_breakdown['by_rate'][key]['vip'] += item.line_total or 0
                    vep = (item.line_total or 0) / (1 + rate/100) if rate > 0 else (item.line_total or 0)
                    tax_breakdown['by_rate'][key]['vat'] += (item.line_total or 0) - vep

        # Stock on hand report
        stock_data = []
        if report_type == 'stock':
            invs = Inventory.query.all()
            for inv in invs:
                prod = inv.product
                if prod:
                    stock_data.append({
                        'product': prod.name,
                        'sku': prod.sku,
                        'branch': inv.branch.name if inv.branch else 'Main',
                        'on_hand': inv.quantity,
                        'value_vip': (inv.quantity or 0) * (prod.price or 0)
                    })

        # Export handling (enhanced for reports)
        if export in ('pdf', 'excel'):
            from datetime import datetime as dt
            fname_base = f"report_{report_type}_{dt.now().strftime('%Y%m%d')}"

            if export == 'pdf':
                from fpdf import FPDF
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 16)
                pdf.cell(0, 10, f"{report_type.replace('_', ' ').title()} Report", ln=True)
                pdf.set_font("Helvetica", "", 10)
                pdf.cell(0, 8, f"Period: {date_from or 'All'} to {date_to or 'All'}", ln=True)
                pdf.ln(5)

                if report_type == 'department':
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 8, "Department Sales", ln=True)
                    for dept, vals in dept_sales.items():
                        pdf.set_font("Helvetica", "", 10)
                        pdf.cell(0, 7, f"{dept}: VIP {vals['total_vip']:.2f} | VEP {vals['total_vep']:.2f} | Qty {vals['count']}", ln=True)
                elif report_type == 'product':
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 8, "Product Sales", ln=True)
                    for p in sorted(product_sales.values(), key=lambda x: -x['total_vip'])[:50]:
                        pdf.set_font("Helvetica", "", 10)
                        pdf.cell(0, 7, f"{p['name']} ({p['sku']}): VIP {p['total_vip']:.2f} | Qty {p['qty']}", ln=True)
                elif report_type == 'cashier':
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 8, "Cashier Sales", ln=True)
                    for c, vals in cashier_sales.items():
                        pdf.set_font("Helvetica", "", 10)
                        pdf.cell(0, 7, f"{c}: VIP {vals['total_vip']:.2f} | Txns {vals['count']}", ln=True)
                elif report_type == 'tax':
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 8, "Tax Report", ln=True)
                    pdf.set_font("Helvetica", "", 10)
                    pdf.cell(0, 7, f"Total VAT: {tax_breakdown['total_vat']:.2f}", ln=True)
                    for rate, vals in tax_breakdown['by_rate'].items():
                        pdf.cell(0, 7, f"{rate}: VIP {vals['vip']:.2f} | VAT {vals['vat']:.2f}", ln=True)
                elif report_type == 'stock':
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 8, "Stock on Hand", ln=True)
                    for s in stock_data[:100]:
                        pdf.set_font("Helvetica", "", 9)
                        pdf.cell(0, 6, f"{s['product']} ({s['sku']}) @ {s['branch']}: {s['on_hand']} units | VIP Value {s['value_vip']:.2f}", ln=True)
                else:
                    pdf.set_font("Helvetica", "", 11)
                    pdf.cell(0, 8, f"Total Sales: FJD {total_sales:.2f}", ln=True)
                    pdf.cell(0, 8, f"Total VAT: FJD {total_vat:.2f}", ln=True)

                pdf.output(f"{fname_base}.pdf")
                from flask import send_file
                return send_file(f"{fname_base}.pdf", as_attachment=True)

            if export == 'excel':
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = report_type.title()

                if report_type == 'department':
                    ws.append(["Department", "Total VIP", "Total VEP (approx)", "Qty Sold"])
                    for dept, vals in dept_sales.items():
                        ws.append([dept, vals['total_vip'], vals['total_vep'], vals['count']])
                elif report_type == 'product':
                    ws.append(["Product", "SKU", "Total VIP", "Qty Sold"])
                    for p in product_sales.values():
                        ws.append([p['name'], p['sku'], p['total_vip'], p['qty']])
                elif report_type == 'cashier':
                    ws.append(["Cashier/Staff", "Total VIP", "Transactions"])
                    for c, vals in cashier_sales.items():
                        ws.append([c, vals['total_vip'], vals['count']])
                elif report_type == 'tax':
                    ws.append(["Rate", "Total VIP", "Total VAT"])
                    ws.append(["Overall", total_sales, total_vat])
                    for rate, vals in tax_breakdown['by_rate'].items():
                        ws.append([rate, vals['vip'], vals['vat']])
                elif report_type == 'stock':
                    ws.append(["Product", "SKU", "Branch", "On Hand", "VIP Value"])
                    for s in stock_data:
                        ws.append([s['product'], s['sku'], s['branch'], s['on_hand'], s['value_vip']])
                else:
                    ws.append(["Order ID", "Date", "Customer", "Total (VIP)", "VAT", "Payment Method"])
                    for o in orders:
                        ws.append([o.id, o.created_at.strftime("%Y-%m-%d %H:%M"), o.customer_name, o.total, o.vat, o.payment_method])

                wb.save(f"{fname_base}.xlsx")
                from flask import send_file
                return send_file(f"{fname_base}.xlsx", as_attachment=True)

        return render_template('reports.html', 
            orders=orders, 
            total_sales=round(total_sales, 2),
            total_vat=round(total_vat, 2),
            by_media=by_media,
            by_dept=by_dept,
            user=current_user,
            date_from=date_from or '',
            date_to=date_to or '',
            report_type=report_type,
            dept_sales=dept_sales,
            product_sales=product_sales,
            cashier_sales=cashier_sales,
            tax_breakdown=tax_breakdown,
            stock_data=stock_data)

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
        if current_user.role not in ['admin', 'manager'] and not user_has_permission(current_user, 'can_view_suppliers'):
            flash('You do not have permission to view suppliers', 'error')
            return redirect(url_for('dashboard'))
        suppliers = Supplier.query.order_by(Supplier.name).all()
        return render_template('suppliers.html', suppliers=suppliers, user=current_user)

    @app.route('/api/suppliers', methods=['POST'])
    @login_required
    def api_create_supplier():
        if current_user.role != 'admin':
            return jsonify({"success": False}), 403
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Name required"}), 400

        # Auto-generate main_code if not provided
        main_code = (data.get('main_code') or '').strip()
        if not main_code:
            last = Supplier.query.order_by(Supplier.id.desc()).first()
            next_num = (last.id + 1) if last else 1
            main_code = f"SUP-{str(next_num).zfill(4)}"

        s = Supplier(
            main_code=main_code,
            second_code=data.get('second_code'),
            name=name,
            contact_person=data.get('contact_person'),
            phone=data.get('phone'),
            phone2=data.get('phone2'),
            email=data.get('email'),
            address=data.get('address'),
            address2=data.get('address2'),
            address3=data.get('address3'),
            category=data.get('category'),
            discount_group=data.get('discount_group')
        )
        db.session.add(s)
        db.session.commit()
        return jsonify({"success": True, "id": s.id, "name": s.name, "main_code": s.main_code})

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
            phone=data.get('phone'),
            vms_disabled=bool(data.get('vms_disabled', False)),
            allow_negative_stock=bool(data.get('allow_negative_stock', False))
        )
        db.session.add(b)
        db.session.commit()

        # Backfill zero inventory for all existing products on the new branch
        # (prevents stock take / inventory views from being empty for new branches)
        for prod in Product.query.all():
            if not Inventory.query.filter_by(product_id=prod.id, branch_id=b.id).first():
                db.session.add(Inventory(product_id=prod.id, branch_id=b.id, quantity=0))
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
        b.vms_disabled = bool(data.get('vms_disabled', b.vms_disabled))
        b.allow_negative_stock = bool(data.get('allow_negative_stock', b.allow_negative_stock))
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
            description=data.get('description'),
            branch_id = int(data.get('branch_id')) if data.get('branch_id') else None
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
        if 'branch_id' in data:
            rule.branch_id = int(data['branch_id']) if data.get('branch_id') else None
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
            # Create an automatic backup of the current DB before overwriting
            if os.path.exists(db_path):
                backup_before = db_path + f".pre-restore.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                import shutil
                shutil.copy2(db_path, backup_before)

            # Validate it's a real SQLite file (header check)
            file_content = file.read(16)
            if not file_content.startswith(b'SQLite format 3\x00'):
                return jsonify({"success": False, "message": "Uploaded file does not appear to be a valid SQLite database"}), 400

            # Rewind and save
            file.seek(0)
            file.save(db_path)

            # Best practice: recommend restart after restore
            return jsonify({
                "success": True,
                "message": "Database restored successfully. A pre-restore backup was created automatically. Please restart the application for changes to take full effect."
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
                    for i, h in enumerate(headers):
                        if variant in h:   # partial match to be forgiving
                            return i
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

    @app.route('/api/products/import-template', methods=['GET'])
    @login_required
    def api_products_import_template():
        """Download a ready-to-fill Excel template for product import.
        Instructions are on a separate 'Instructions' sheet so the 'Data' sheet headers are clean.
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        wb = Workbook()

        # === DATA SHEET (clean headers in row 1) ===
        ws_data = wb.active
        ws_data.title = "Data"

        headers = [
            "SKU", "Barcode", "Name", "Price (VIP Selling)", "Cost Price",
            "Category", "Department", "Reorder Level", "Unit", "Special Pricing Group",
            "Tax Rate Label", "Active (yes/no)"
        ]
        header_fill = PatternFill(start_color="0A4D68", end_color="0A4D68", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for col, header in enumerate(headers, 1):
            cell = ws_data.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

        # Sample row
        sample = ["PROD-001", "1234567890123", "Sample Widget", 25.50, 12.00, "General", "Hardware", 10, "pcs", "bundle-5", "Standard", "yes"]
        for col, val in enumerate(sample, 1):
            ws_data.cell(row=2, column=col, value=val)

        widths = [16, 16, 25, 18, 14, 14, 14, 14, 8, 20, 16, 14]
        for i, w in enumerate(widths, 1):
            ws_data.column_dimensions[get_column_letter(i)].width = w

        # === INSTRUCTIONS SHEET ===
        ws_inst = wb.create_sheet("Instructions")
        ws_inst['A1'] = "CLOUD IT POS - PRODUCT IMPORT INSTRUCTIONS"
        ws_inst['A1'].font = Font(bold=True, size=14, color="0A4D68")
        ws_inst.merge_cells('A1:B1')

        instructions = [
            "",
            "HOW TO USE THIS TEMPLATE:",
            "1. Go to the 'Data' sheet.",
            "2. Fill in your products starting from row 2 (row 1 has the headers).",
            "3. Required columns (must match exactly): SKU, Name, Price (VIP Selling)",
            "4. SKU is the main identifier for matching/updating existing products.",
            "5. Price (VIP Selling) is the selling price the customer pays (tax-inclusive by default).",
            "6. Tax Rate Label must match exactly what you have in Settings → Tax Rates (e.g. 'Standard').",
            "7. Department: will be created automatically if it doesn't exist.",
            "8. Special Pricing Group: used for quantity/bundle discounts (see Special Pricing page).",
            "9. Active (yes/no): set to 'no' to hide the product.",
            "",
            "TIP: Copy the sample row in row 2 and modify it.",
            "After filling, save the file and use the 'Import Excel' button in Inventory Management."
        ]
        for i, line in enumerate(instructions, 2):
            ws_inst.cell(row=i, column=1, value=line)

        ws_inst.column_dimensions['A'].width = 90

        # Return as download
        from io import BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        from flask import send_file
        return send_file(
            output,
            as_attachment=True,
            download_name="product_import_template.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # ==================== DEBTOR / CUSTOMER IMPORT ====================
    @app.route('/api/debtors/import-template', methods=['GET'])
    @login_required
    def api_debtors_import_template():
        """Download Excel template for debtors (customers) import.
        Instructions are on a separate 'Instructions' sheet.
        Main code is auto-generated if left blank in the Data sheet.
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        wb = Workbook()

        # === DATA SHEET (clean headers in row 1) ===
        ws_data = wb.active
        ws_data.title = "Data"

        headers = [
            "Main Code", "Second Code", "Name", "Contact Person",
            "Address", "Address 2", "Address 3", "Phone 1", "Phone 2", "Email",
            "TIN", "Category", "Discount Group", "Initial Outstanding Balance"
        ]
        header_fill = PatternFill(start_color="0A4D68", end_color="0A4D68", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for col, header in enumerate(headers, 1):
            cell = ws_data.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

        # Sample row (Main Code blank = will be auto-generated)
        sample = ["", "CUST-OLD-42", "John Doe Ltd", "John Doe", "123 Main St", "Suva", "Fiji", "1234567", "9876543", "john@doe.com", "123456789", "Wholesale", "VIP-Customers", 1500.00]
        for col, val in enumerate(sample, 1):
            ws_data.cell(row=2, column=col, value=val)

        for i, w in enumerate([18, 14, 20, 16, 18, 12, 12, 12, 12, 20, 14, 12, 16, 22], 1):
            ws_data.column_dimensions[get_column_letter(i)].width = w

        # === INSTRUCTIONS SHEET ===
        ws_inst = wb.create_sheet("Instructions")
        ws_inst['A1'] = "CLOUD IT POS - DEBTOR / CUSTOMER IMPORT INSTRUCTIONS"
        ws_inst['A1'].font = Font(bold=True, size=14, color="0A4D68")
        ws_inst.merge_cells('A1:B1')

        instructions = [
            "",
            "HOW TO USE THIS TEMPLATE:",
            "1. Go to the 'Data' sheet.",
            "2. Fill in your customers starting from row 2 (row 1 has the headers).",
            "3. Main Code: Leave blank to auto-generate (e.g. CUST-0001).",
            "4. Discount Group: Use this to give the customer special pricing (matches Special Pricing rules).",
            "5. Address 1/2/3, Phone 1/2, TIN, Category are all supported.",
            "6. Initial Outstanding Balance: optional starting balance.",
            "",
            "TIP: The import will create new debtors or update existing ones by Main Code.",
            "After filling, save and use the 'Import Excel' button on the Debtors page."
        ]
        for i, line in enumerate(instructions, 2):
            ws_inst.cell(row=i, column=1, value=line)

        ws_inst.column_dimensions['A'].width = 90

        from io import BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        from flask import send_file
        return send_file(output, as_attachment=True, download_name="debtor_import_template.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.route('/api/suppliers/import-template', methods=['GET'])
    @login_required
    def api_suppliers_import_template():
        """Download Excel template for suppliers import.
        Instructions are on a separate 'Instructions' sheet.
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        wb = Workbook()

        # === DATA SHEET (clean headers in row 1) ===
        ws_data = wb.active
        ws_data.title = "Data"

        headers = [
            "Main Code", "Second Code", "Name", "Contact Person",
            "Address", "Address 2", "Address 3", "Phone 1", "Phone 2", "Email",
            "TIN / Tax ID", "Category", "Discount Group", "Notes"
        ]
        header_fill = PatternFill(start_color="0A4D68", end_color="0A4D68", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for col, header in enumerate(headers, 1):
            cell = ws_data.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

        sample = ["", "SUP-OLD-99", "Acme Supplies", "Jane Smith", "Industrial Zone", "Lautoka", "", "3344556", "", "sales@acme.com", "987654321", "Preferred", "", "Reliable supplier"]
        for col, val in enumerate(sample, 1):
            ws_data.cell(row=2, column=col, value=val)

        for i, w in enumerate([18, 14, 20, 16, 18, 12, 12, 12, 12, 20, 14, 12, 16, 22], 1):
            ws_data.column_dimensions[get_column_letter(i)].width = w

        # === INSTRUCTIONS SHEET ===
        ws_inst = wb.create_sheet("Instructions")
        ws_inst['A1'] = "CLOUD IT POS - SUPPLIER IMPORT INSTRUCTIONS"
        ws_inst['A1'].font = Font(bold=True, size=14, color="0A4D68")
        ws_inst.merge_cells('A1:B1')

        instructions = [
            "",
            "HOW TO USE THIS TEMPLATE:",
            "1. Go to the 'Data' sheet.",
            "2. Fill in your suppliers starting from row 2 (row 1 has the headers).",
            "3. Main Code: Leave blank to auto-generate (e.g. SUP-0001).",
            "4. All address, phone, and contact fields are supported.",
            "5. Discount Group and Category are available for future filtering/special rules.",
            "",
            "TIP: The import will create new suppliers or update by Main Code.",
            "After filling, save and use the 'Import Excel' button on the Suppliers page."
        ]
        for i, line in enumerate(instructions, 2):
            ws_inst.cell(row=i, column=1, value=line)

        ws_inst.column_dimensions['A'].width = 90

        from io import BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        from flask import send_file
        return send_file(output, as_attachment=True, download_name="supplier_import_template.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ==================== DEBTORS & SUPPLIERS IMPORT LOGIC ====================
    @app.route('/api/debtors/import-excel', methods=['POST'])
    @login_required
    def api_import_debtors_excel():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file"}), 400
        file = request.files['file']
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({"success": False, "message": "xlsx only"}), 400

        try:
            from openpyxl import load_workbook
            wb = load_workbook(file)
            ws = wb.active
            headers = [str(c.value or '').strip().lower() for c in ws[1]]

            def find_col(variants):
                for v in variants:
                    for i, h in enumerate(headers):
                        if v in h:   # partial match
                            return i
                return None

            imported = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                try:
                    main_code = str(row[find_col(['main code', 'main_code', 'code'])] or '').strip() if find_col(['main code','main_code','code']) is not None else ''
                    second_code = str(row[find_col(['second code', 'second_code'])] or '').strip() if find_col(['second code','second_code']) is not None else ''
                    name = str(row[find_col(['name'])] or '').strip()
                    if not name: continue

                    contact = str(row[find_col(['contact', 'contact person'])] or '').strip() if find_col(['contact','contact person']) is not None else ''
                    addr = str(row[find_col(['address'])] or '').strip() if find_col(['address']) is not None else ''
                    addr2 = str(row[find_col(['address 2','address2'])] or '').strip() if find_col(['address 2','address2']) is not None else ''
                    addr3 = str(row[find_col(['address 3','address3'])] or '').strip() if find_col(['address 3','address3']) is not None else ''
                    phone = str(row[find_col(['phone 1','phone'])] or '').strip() if find_col(['phone 1','phone']) is not None else ''
                    phone2 = str(row[find_col(['phone 2'])] or '').strip() if find_col(['phone 2']) is not None else ''
                    email = str(row[find_col(['email'])] or '').strip() if find_col(['email']) is not None else ''
                    tin = str(row[find_col(['tin'])] or '').strip() if find_col(['tin']) is not None else ''
                    category = str(row[find_col(['category'])] or '').strip() if find_col(['category']) is not None else ''
                    disc_group = str(row[find_col(['discount group','discount_group'])] or '').strip() if find_col(['discount group','discount_group']) is not None else ''
                    balance = float(row[find_col(['initial outstanding','balance','outstanding'])] or 0) if find_col(['initial outstanding','balance','outstanding']) is not None else 0

                    if not main_code:
                        # auto main code
                        last = Debtor.query.order_by(Debtor.id.desc()).first()
                        next_num = (last.id + 1) if last else 1
                        main_code = f"CUST-{str(next_num).zfill(4)}"

                    existing = Debtor.query.filter_by(main_code=main_code).first() if main_code else None
                    if existing:
                        # update
                        existing.name = name or existing.name
                        existing.contact_person = contact or existing.contact_person
                        existing.address = addr or existing.address
                        existing.phone = phone or existing.phone
                        # ... more fields if wanted
                    else:
                        d = Debtor(
                            main_code=main_code,
                            second_code=second_code,
                            name=name,
                            contact_person=contact,
                            phone=phone,
                            phone2=phone2,
                            email=email,
                            address=addr,
                            address2=addr2,
                            address3=addr3,
                            tin=tin,
                            category=category,
                            discount_group=disc_group,
                            outstanding_balance=balance
                        )
                        db.session.add(d)
                    imported += 1
                except Exception:
                    pass
            db.session.commit()
            return jsonify({"success": True, "imported": imported})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": str(e)}), 400

    @app.route('/api/suppliers/import-excel', methods=['POST'])
    @login_required
    def api_import_suppliers_excel():
        if current_user.role != 'admin':
            return jsonify({"success": False, "message": "Admin only"}), 403

        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file"}), 400
        file = request.files['file']
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({"success": False, "message": "xlsx only"}), 400

        try:
            from openpyxl import load_workbook
            wb = load_workbook(file)
            ws = wb.active
            headers = [str(c.value or '').strip().lower() for c in ws[1]]

            def find_col(variants):
                for v in variants:
                    for i, h in enumerate(headers):
                        if v in h:   # partial match
                            return i
                return None

            imported = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                try:
                    main_code = str(row[find_col(['main code', 'main_code', 'code'])] or '').strip() if find_col(['main code','main_code','code']) is not None else ''
                    second_code = str(row[find_col(['second code', 'second_code'])] or '').strip() if find_col(['second code','second_code']) is not None else ''
                    name = str(row[find_col(['name'])] or '').strip()
                    if not name: continue

                    contact = str(row[find_col(['contact', 'contact person'])] or '').strip() if find_col(['contact','contact person']) is not None else ''
                    addr = str(row[find_col(['address'])] or '').strip() if find_col(['address']) is not None else ''
                    addr2 = str(row[find_col(['address 2','address2'])] or '').strip() if find_col(['address 2','address2']) is not None else ''
                    addr3 = str(row[find_col(['address 3','address3'])] or '').strip() if find_col(['address 3','address3']) is not None else ''
                    phone = str(row[find_col(['phone 1','phone'])] or '').strip() if find_col(['phone 1','phone']) is not None else ''
                    phone2 = str(row[find_col(['phone 2'])] or '').strip() if find_col(['phone 2']) is not None else ''
                    email = str(row[find_col(['email'])] or '').strip() if find_col(['email']) is not None else ''
                    tin = str(row[find_col(['tin', 'tax id'])] or '').strip() if find_col(['tin','tax id']) is not None else ''
                    category = str(row[find_col(['category'])] or '').strip() if find_col(['category']) is not None else ''
                    disc_group = str(row[find_col(['discount group','discount_group'])] or '').strip() if find_col(['discount group','discount_group']) is not None else ''

                    if not main_code:
                        last = Supplier.query.order_by(Supplier.id.desc()).first()
                        next_num = (last.id + 1) if last else 1
                        main_code = f"SUP-{str(next_num).zfill(4)}"

                    existing = Supplier.query.filter_by(main_code=main_code).first() if main_code else None
                    if existing:
                        existing.name = name or existing.name
                    else:
                        s = Supplier(
                            main_code=main_code,
                            second_code=second_code,
                            name=name,
                            contact_person=contact,
                            phone=phone,
                            phone2=phone2,
                            email=email,
                            address=addr,
                            address2=addr2,
                            address3=addr3,
                            category=category,
                            discount_group=disc_group
                        )
                        db.session.add(s)
                    imported += 1
                except Exception:
                    pass
            db.session.commit()
            return jsonify({"success": True, "imported": imported})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": str(e)}), 400

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

                # Get rules: strictly prefer branch-specific if any qualify, else global.
                # This makes branch-wise special pricing take precedence.
                branch_rules = []
                global_rules = []
                if branch_id:
                    branch_rules = SpecialPricingRule.query.filter_by(group=group, branch_id=branch_id, is_active=True).all()
                global_rules = SpecialPricingRule.query.filter_by(group=group, branch_id=None, is_active=True).all()

                best_rule = None
                candidate_rules = branch_rules if branch_rules else global_rules
                for rule in candidate_rules:
                    if total_qty_in_group >= rule.min_quantity:
                        if not best_rule or rule.special_price < best_rule.special_price:
                            best_rule = rule
                # If no branch rule qualified but globals did, fall back
                if not best_rule and branch_rules:
                    for rule in global_rules:
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
        # Protect the master emergency recovery account
        if (u.username or '').lower() == 'sa':
            return jsonify({"success": False, "message": "The master emergency account 'sa' cannot be deleted (it is the recovery backdoor)."}), 400
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

        # Reduce debtor balance on credit/return (if the original sale was on account)
        if return_order.debtor_id:
            d = Debtor.query.get(return_order.debtor_id)
            if d:
                d.outstanding_balance = round((d.outstanding_balance or 0) - (original.total or 0), 2)

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

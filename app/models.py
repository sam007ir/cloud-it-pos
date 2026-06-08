from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import json

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100))
    role = db.Column(db.String(20), default='staff')  # admin, manager, staff, cashier
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    permissions = db.Column(db.Text, default='{}')   # JSON: {"can_manage_users": true, "can_do_stock_take": true, ...}
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Branch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200))
    tin = db.Column(db.String(20))          # FRCS Taxpayer Identification Number
    vms_uid = db.Column(db.String(30))      # VMS Device/Branch UID
    phone = db.Column(db.String(30))
    is_active = db.Column(db.Boolean, default=True)
    vms_disabled = db.Column(db.Boolean, default=False)  # If true, skip all VMS/fiscal
    allow_negative_stock = db.Column(db.Boolean, default=False)  # Allow sales with 0 or negative stock
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship for certificate
    certificate = db.relationship('VMSCertificate', backref='branch', uselist=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    sku = db.Column(db.String(50), unique=True, nullable=False)
    barcode = db.Column(db.String(50))
    price = db.Column(db.Float, nullable=False)          # Default/global price
    category = db.Column(db.String(60))
    unit = db.Column(db.String(20), default='pcs')
    cost_price = db.Column(db.Float, default=0.0)        # Default/global cost
    reorder_level = db.Column(db.Integer, default=10)
    is_active = db.Column(db.Boolean, default=True)
    special_pricing_group = db.Column(db.String(50))
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)  # New structured classification
    tax_rate_id = db.Column(db.Integer, db.ForeignKey('tax_rate.id'), nullable=True)      # Per-item tax rate (critical for correct VAT calc)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)      # Default supplier for this item
    pack_size = db.Column(db.Integer, default=1)          # e.g. 24 pieces per carton
    pack_unit = db.Column(db.String(20), default='pcs')   # e.g. 'carton', 'box'
    pack_cost = db.Column(db.Float, default=0.0)          # Cost for one full pack
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Branch-specific pricing
    branch_prices = db.relationship('ProductBranchPrice', backref='product', lazy=True, cascade='all, delete-orphan')
    department = db.relationship('Department', backref='products')
    tax_rate = db.relationship('TaxRate', backref='products')
    supplier = db.relationship('Supplier', backref='products')

class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = db.relationship('Product', backref='inventories')
    branch = db.relationship('Branch', backref='inventories')

    __table_args__ = (db.UniqueConstraint('product_id', 'branch_id', name='_prod_branch_uc'),)

class StockMovement(db.Model):
    """Audit log for all inventory changes"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    movement_type = db.Column(db.String(30))  # SALE, ADJUSTMENT, TRANSFER_IN, TRANSFER_OUT, STOCKTAKE, RETURN
    quantity_change = db.Column(db.Integer)   # positive or negative
    previous_qty = db.Column(db.Integer)
    new_qty = db.Column(db.Integer)
    reference = db.Column(db.String(100))     # Order ID, Transfer ID, etc.
    notes = db.Column(db.String(200))
    performed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    customer_name = db.Column(db.String(100))
    customer_tin = db.Column(db.String(20))   # For B2B fiscal invoices
    debtor_id = db.Column(db.Integer, db.ForeignKey('debtor.id'), nullable=True)  # For on-account sales
    order_type = db.Column(db.String(20), default='pos')  # pos, online, wholesale
    status = db.Column(db.String(20), default='completed')
    subtotal = db.Column(db.Float, default=0.0)
    vat = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    payment_method = db.Column(db.String(30))
    payment_ref = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')
    fiscal_invoice = db.relationship('FiscalInvoice', backref='order', uselist=False, cascade='all, delete-orphan')
    branch = db.relationship('Branch', backref='orders')
    debtor = db.relationship('Debtor', backref='orders')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product_name = db.Column(db.String(150))  # snapshot
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)

class FiscalInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), unique=True)
    invoice_no = db.Column(db.String(50), unique=True)
    sdc_no = db.Column(db.String(80))           # e.g. 7AF4D923-E3B30A31-234 (from real API)
    invoice_counter = db.Column(db.Integer)
    sdc_time = db.Column(db.String(30))
    seller_name = db.Column(db.String(100))
    seller_tin = db.Column(db.String(20))
    seller_uid = db.Column(db.String(30))
    signature = db.Column(db.String(256))
    qr_payload = db.Column(db.String(300))
    verification_url = db.Column(db.String(300))  # Official verify.frcs link from response
    tax_items_json = db.Column(db.Text)           # JSON of tax breakdown as returned by API
    total_tax = db.Column(db.Float)
    transmitted = db.Column(db.Boolean, default=False)
    transmission_status = db.Column(db.String(20))
    vms_response = db.Column(db.Text)             # Full simulated InvoiceFiscalizationResult
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class VMSCertificate(db.Model):
    """Stores certificate info for each branch (supports smart card or PFX upload)"""
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'), unique=True)
    method = db.Column(db.String(20))          # 'smartcard' or 'pfx'

    # Real VSDC Integration Settings
    vsdc_mode = db.Column(db.String(20), default='simulation')   # 'simulation' or 'real_vsdc'
    vsdc_base_url = db.Column(db.String(255))                   # e.g. https://vsdc.sandbox.vms.frcs.org.fj
    receipt_sequence = db.Column(db.String(50))                 # e.g. "INV-", "POS-", etc. (user configurable)

    cert_serial = db.Column(db.String(100))
    taxpayer_name = db.Column(db.String(150))
    taxpayer_tin = db.Column(db.String(20))
    valid_until = db.Column(db.String(20))
    pfx_filename = db.Column(db.String(200))
    pfx_passphrase_hash = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    last_used = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class VMSLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    order_id = db.Column(db.Integer)
    invoice_no = db.Column(db.String(50))
    status = db.Column(db.String(20))          # SUCCESS, FAILED, PENDING
    payload_hash = db.Column(db.String(64))
    response = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    main_code = db.Column(db.String(50), unique=True)          # auto generated
    second_code = db.Column(db.String(50))
    name = db.Column(db.String(150), nullable=False)
    contact_person = db.Column(db.String(100))               # contact
    phone = db.Column(db.String(30))
    phone2 = db.Column(db.String(30))
    email = db.Column(db.String(100))
    address = db.Column(db.String(200))
    address2 = db.Column(db.String(200))
    address3 = db.Column(db.String(200))
    category = db.Column(db.String(50))
    discount_group = db.Column(db.String(50))                # for special pricing if applicable
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Department(db.Model):
    """Inventory classification / department for better item organization"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TaxRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(10))      # A, B, C, etc.
    rate = db.Column(db.Float)            # 12.5 for default G rate (example)
    description = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)

class PaymentMethod(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True)
    name = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)

class PurchaseOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    status = db.Column(db.String(20), default='pending')  # pending, partially_received, received, cancelled
    total = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    received_at = db.Column(db.DateTime)
    supplier = db.relationship('Supplier')
    branch = db.relationship('Branch')
    items = db.relationship('PurchaseOrderItem', backref='purchase_order', cascade='all, delete-orphan')

class PurchaseOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer)
    cost_price = db.Column(db.Float)           # Legacy / default cost
    cost_vep = db.Column(db.Float)             # Cost Value Excluding Tax
    cost_vip = db.Column(db.Float)             # Cost Value Including Tax
    tax_rate_id = db.Column(db.Integer, db.ForeignKey('tax_rate.id'))  # Tax treatment for purchase cost
    product = db.relationship('Product')
    tax_rate = db.relationship('TaxRate')

class StockTake(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    status = db.Column(db.String(20), default='in_progress')  # in_progress, completed
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    branch = db.relationship('Branch')
    items = db.relationship('StockTakeItem', backref='stocktake', cascade='all, delete-orphan')

class StockTakeItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stocktake_id = db.Column(db.Integer, db.ForeignKey('stock_take.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    system_qty = db.Column(db.Integer)
    counted_qty = db.Column(db.Integer)
    variance = db.Column(db.Integer)
    product = db.relationship('Product')

class Debtor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    main_code = db.Column(db.String(50), unique=True)          # auto generated customer code
    second_code = db.Column(db.String(50))
    name = db.Column(db.String(150))
    contact_person = db.Column(db.String(100))               # contact
    tin = db.Column(db.String(20))
    phone = db.Column(db.String(30))
    phone2 = db.Column(db.String(30))
    email = db.Column(db.String(100))
    address = db.Column(db.String(200))
    address2 = db.Column(db.String(200))
    address3 = db.Column(db.String(200))
    category = db.Column(db.String(50))
    discount_group = db.Column(db.String(50))                # links to special pricing / discount group
    outstanding_balance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class GoodsReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    supplier_invoice_no = db.Column(db.String(100))   # Invoice number from supplier
    received_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.String(200))
    is_reversed = db.Column(db.Boolean, default=False)
    reversed_at = db.Column(db.DateTime)
    reversed_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    supplier = db.relationship('Supplier')
    branch = db.relationship('Branch')
    items = db.relationship('GoodsReceiptItem', backref='goods_receipt', cascade='all, delete-orphan')
    purchase_order = db.relationship('PurchaseOrder')

class GoodsReceiptItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    gr_id = db.Column(db.Integer, db.ForeignKey('goods_receipt.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    ordered_qty = db.Column(db.Integer, default=0)   # from PO if linked
    received_qty = db.Column(db.Integer)
    cost_vep = db.Column(db.Float)             # Cost at receipt time (ex tax)
    cost_vip = db.Column(db.Float)             # Cost at receipt time (inc tax)
    tax_rate_id = db.Column(db.Integer, db.ForeignKey('tax_rate.id'))
    product = db.relationship('Product')
    tax_rate = db.relationship('TaxRate')

class SpecialPricingRule(db.Model):
    """Defines special pricing / bundles / quantity deals"""
    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(50), nullable=False)           # Matches product.special_pricing_group
    name = db.Column(db.String(100))                           # e.g. "Family Combo" or "Buy 3 for $2"
    rule_type = db.Column(db.String(30), default='bundle')     # bundle | quantity_discount
    min_quantity = db.Column(db.Integer, default=1)
    special_price = db.Column(db.Float)                        # Fixed price for the bundle/qualifying items
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'), nullable=True)  # NULL = applies to all branches
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    branch = db.relationship('Branch')

class ReceiptConfig(db.Model):
    """Customizable receipt settings"""
    id = db.Column(db.Integer, primary_key=True)
    layout = db.Column(db.String(20), default='80mm')          # 80mm or a4
    company_name = db.Column(db.String(150), default='Your Business Name')
    header_text = db.Column(db.String(200), default='Thank you for shopping with us!')
    footer_text = db.Column(db.String(300), default='This is a fiscal receipt under FRCS VMS.')
    show_logo = db.Column(db.Boolean, default=False)
    logo_base64 = db.Column(db.Text)                           # base64 encoded image
    show_tax_breakdown = db.Column(db.Boolean, default=True)
    show_signature_block = db.Column(db.Boolean, default=True)
    custom_header_html = db.Column(db.Text)   # For full custom top header / logo area
    receipt_layout = db.Column(db.Text)       # JSON for drag-and-drop visual designer layout
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProductBranchPrice(db.Model):
    """Branch-specific pricing for products"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'), nullable=False)
    price = db.Column(db.Float, nullable=False)           # Selling price for this branch
    cost_price = db.Column(db.Float, default=0.0)         # Cost price for this branch
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    branch = db.relationship('Branch')

    __table_args__ = (db.UniqueConstraint('product_id', 'branch_id', name='_product_branch_price_uc'),)


class ProductBarcode(db.Model):
    """Alternate barcodes for a product (supports multiple barcodes + pack sizes, e.g. carton of 6)"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    barcode = db.Column(db.String(50), nullable=False, unique=True)
    pack_quantity = db.Column(db.Integer, default=1)   # e.g. 1 = single, 6 = carton
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='alternate_barcodes')


class RentalProperty(db.Model):
    """Rental properties/units with assigned barcode for quick lookup like POS products.
    property_type determines tax treatment: 'residential' = VEP (ex-tax), 'commercial' = VIP (inc-tax).
    Optional per-property tax_rate like products."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)  # e.g. "Apartment 2B - Sunset Villas"
    address = db.Column(db.String(250))
    barcode = db.Column(db.String(50), nullable=False, unique=True)  # Scanned like product barcode in rent collection / POS
    monthly_rent = db.Column(db.Float, default=0.0)  # base amount; tax treatment per property_type
    deposit = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='available')  # available, occupied, maintenance
    description = db.Column(db.String(300))
    property_type = db.Column(db.String(20), default='residential')  # 'residential' (VEP) or 'commercial' (VIP)
    tax_rate_id = db.Column(db.Integer, db.ForeignKey('tax_rate.id'), nullable=True)
    current_tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    current_tenant = db.relationship('Tenant', foreign_keys=[current_tenant_id], backref='assigned_properties')
    tax_rate = db.relationship('TaxRate')
    photo_base64 = db.Column(db.Text)  # optional photo (base64 like logo)

    def __repr__(self):
        return f'<RentalProperty {self.barcode} {self.name} ({self.property_type})>'


class Tenant(db.Model):
    """Tenants / renters linked to properties"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(100))
    id_number = db.Column(db.String(50))  # national ID, passport etc.
    address = db.Column(db.String(250))
    notes = db.Column(db.String(500))
    outstanding_balance = db.Column(db.Float, default=0.0)  # current arrears / due rent
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Optional link to main Debtor for unified AR if desired (can sync in future)
    debtor_id = db.Column(db.Integer, db.ForeignKey('debtor.id'), nullable=True)
    debtor = db.relationship('Debtor', backref='tenant_record')


class RentalCharge(db.Model):
    """Record of rent charges/dues for a tenant/property (like an invoice line)"""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('rental_property.id'), nullable=False)
    charge_date = db.Column(db.DateTime, default=datetime.utcnow)
    period_start = db.Column(db.DateTime)
    period_end = db.Column(db.DateTime)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200), default='Monthly Rent')
    status = db.Column(db.String(20), default='due')  # due, paid, partial, waived
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship('Tenant', backref='rental_charges')
    property = db.relationship('RentalProperty', backref='charges')


class RentalPayment(db.Model):
    """Payments received for rentals. Uses existing PaymentMethod codes."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('rental_property.id'), nullable=True)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(30))  # e.g. CASH, CARD from PaymentMethod.code
    reference = db.Column(db.String(100))  # receipt #, cheque etc.
    notes = db.Column(db.String(300))
    charge_id = db.Column(db.Integer, db.ForeignKey('rental_charge.id'), nullable=True)  # link to specific charge if allocated
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship('Tenant', backref='rental_payments')
    property = db.relationship('RentalProperty', backref='payments')
    charge = db.relationship('RentalCharge', backref='payments')


class MaintenanceRequest(db.Model):
    """Maintenance / work orders for rental properties (and potentially other assets)"""
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('rental_property.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)  # who reported
    reported_date = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(500), nullable=False)
    priority = db.Column(db.String(20), default='normal')  # low, normal, high, urgent
    status = db.Column(db.String(20), default='open')  # open, in_progress, completed, cancelled
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    estimated_cost = db.Column(db.Float, default=0)
    actual_cost = db.Column(db.Float, default=0)
    completed_date = db.Column(db.DateTime)
    notes = db.Column(db.String(1000))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    property = db.relationship('RentalProperty', backref='maintenance_requests')
    tenant = db.relationship('Tenant', backref='maintenance_requests')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_user_id])


class Lease(db.Model):
    """Simple lease/agreement for tenant-property with dates and terms"""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('rental_property.id'), nullable=False)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    rent_amount = db.Column(db.Float)
    terms = db.Column(db.String(1000))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship('Tenant', backref='leases')
    property = db.relationship('RentalProperty', backref='leases')


class HeldTransaction(db.Model):
    """Holds in-progress transactions (sales, POs, supplier invoices) so users can pause and resume later."""
    id = db.Column(db.Integer, primary_key=True)
    transaction_type = db.Column(db.String(30), nullable=False)  # 'sale', 'purchase_order', 'supplier_invoice'
    data = db.Column(db.Text, nullable=False)  # JSON of current cart/lines + metadata
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branch.id'))
    reference = db.Column(db.String(150))  # e.g. customer name, PO supplier, invoice no for display
    notes = db.Column(db.Text)  # optional notes when holding
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User')
    branch = db.relationship('Branch')

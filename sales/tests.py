from decimal import Decimal
import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone
from django.core.exceptions import ValidationError

from accounts.models import CustomerProfile, SalesStaffProfile
from catalog.models import Category, ProductService, StockAdjustment
from sales.models import Sale, SaleItem, Invoice, Payment

User = get_user_model()


class SalesConstraintAndIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='sales_staff_user',
            email='staff@example.com',
            password='Password123!',
            role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.user,
            designation='Sales Executive',
            department='Sales'
        )
        self.customer_user = User.objects.create_user(
            username='customer_user',
            email='customer@example.com',
            password='Password123!',
            role='CUSTOMER'
        )
        self.customer_profile = CustomerProfile.objects.create(
            user=self.customer_user,
            company='Acme Corp',
            address='123 Tech Park',
            city='Bangalore',
            state='Karnataka',
            postal_code='560001'
        )
        self.category = Category.objects.create(
            category_name='Hardware',
            description='Computer hardware'
        )
        self.product = ProductService.objects.create(
            category=self.category,
            product_name='Enterprise Laptop',
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('50000.00'),
            stock_quantity=10,
            reorder_level=2
        )
        self.service = ProductService.objects.create(
            category=self.category,
            product_name='Annual Maintenance',
            item_type=ProductService.ItemType.SERVICE,
            unit_price=Decimal('10000.00'),
            stock_quantity=0,
            reorder_level=0
        )

    # -------------------------------------------------------------------------
    # 1. Sale Constraints
    # -------------------------------------------------------------------------
    def test_sale_discount_lte_subtotal_constraint(self):
        """Sale constraint: discount <= subtotal"""
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Sale.objects.create(
                    sale_number='SO-TEST-001',
                    customer=self.customer_profile,
                    sales_staff=self.staff_profile,
                    subtotal=Decimal('100.00'),
                    discount=Decimal('150.00'),  # discount > subtotal violates chk_sale_discount_lte_subtotal
                    taxable_amount=Decimal('0.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('0.00'),
                    net_amount=Decimal('0.00'),
                    status=Sale.Status.DRAFT,
                    stock_deducted=False
                )

    def test_sale_tax_rate_range_constraint(self):
        """Sale constraint: 0 <= tax_rate <= 100"""
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Sale.objects.create(
                    sale_number='SO-TEST-002',
                    customer=self.customer_profile,
                    sales_staff=self.staff_profile,
                    subtotal=Decimal('100.00'),
                    discount=Decimal('10.00'),
                    taxable_amount=Decimal('90.00'),
                    tax_rate=Decimal('105.00'),  # > 100 violates chk_sale_tax_rate_range
                    tax_amount=Decimal('94.50'),
                    net_amount=Decimal('184.50'),
                    status=Sale.Status.DRAFT,
                    stock_deducted=False
                )

    def test_sale_net_amount_calc_constraint(self):
        """Sale constraint: net_amount = taxable_amount + tax_amount"""
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Sale.objects.create(
                    sale_number='SO-TEST-003',
                    customer=self.customer_profile,
                    sales_staff=self.staff_profile,
                    subtotal=Decimal('100.00'),
                    discount=Decimal('0.00'),
                    taxable_amount=Decimal('100.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('18.00'),
                    net_amount=Decimal('150.00'),  # Mismatch: 100 + 18 != 150
                    status=Sale.Status.DRAFT,
                    stock_deducted=False
                )

    def test_sale_status_stock_deducted_consistency_constraint(self):
        """
        Sale constraint:
        - DRAFT and CANCELLED require stock_deducted=False
        - CONFIRMED and COMPLETED require stock_deducted=True
        """
        # DRAFT with stock_deducted=True must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Sale.objects.create(
                    sale_number='SO-TEST-004',
                    customer=self.customer_profile,
                    sales_staff=self.staff_profile,
                    subtotal=Decimal('100.00'),
                    discount=Decimal('0.00'),
                    taxable_amount=Decimal('100.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('18.00'),
                    net_amount=Decimal('118.00'),
                    status=Sale.Status.DRAFT,
                    stock_deducted=True  # Invalid for DRAFT
                )

        # CONFIRMED with stock_deducted=False must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Sale.objects.create(
                    sale_number='SO-TEST-005',
                    customer=self.customer_profile,
                    sales_staff=self.staff_profile,
                    subtotal=Decimal('100.00'),
                    discount=Decimal('0.00'),
                    taxable_amount=Decimal('100.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('18.00'),
                    net_amount=Decimal('118.00'),
                    status=Sale.Status.CONFIRMED,
                    stock_deducted=False  # Invalid for CONFIRMED
                )

        # Valid states must succeed
        sale_draft = Sale.objects.create(
            sale_number='SO-TEST-006',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.DRAFT,
            stock_deducted=False
        )
        self.assertIsNotNone(sale_draft.pk)

        sale_confirmed = Sale.objects.create(
            sale_number='SO-TEST-007',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        self.assertIsNotNone(sale_confirmed.pk)

    # -------------------------------------------------------------------------
    # 2. SaleItem Constraints
    # -------------------------------------------------------------------------
    def test_saleitem_line_subtotal_and_total_calc_constraints(self):
        """SaleItem constraints: line_subtotal = qty * price; line_total = line_subtotal - discount"""
        sale = Sale.objects.create(
            sale_number='SO-TEST-ITEM-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100000.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18000.00'),
            net_amount=Decimal('118000.00'),
            status=Sale.Status.DRAFT,
            stock_deducted=False
        )

        # Wrong line_subtotal via bulk_create (bypassing model calculate_line_totals)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                SaleItem.objects.bulk_create([
                    SaleItem(
                        sale=sale,
                        product=self.product,
                        quantity=2,
                        unit_price=Decimal('50000.00'),
                        line_subtotal=Decimal('99999.00'),  # 2 * 50000 != 99999
                        discount=Decimal('0.00'),
                        line_total=Decimal('99999.00')
                    )
                ])

        # Discount > line_subtotal via bulk_create
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                SaleItem.objects.bulk_create([
                    SaleItem(
                        sale=sale,
                        product=self.product,
                        quantity=1,
                        unit_price=Decimal('50000.00'),
                        line_subtotal=Decimal('50000.00'),
                        discount=Decimal('55000.00'),  # discount > subtotal
                        line_total=Decimal('0.00')
                    )
                ])

    def test_saleitem_unique_sale_product_line_constraint(self):
        """SaleItem unique constraint: (sale, product)"""
        sale = Sale.objects.create(
            sale_number='SO-TEST-ITEM-02',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('50000.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('50000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('9000.00'),
            net_amount=Decimal('59000.00'),
            status=Sale.Status.DRAFT,
            stock_deducted=False
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50000.00'),
            line_subtotal=Decimal('50000.00'),
            discount=Decimal('0.00'),
            line_total=Decimal('50000.00')
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                SaleItem.objects.create(
                    sale=sale,
                    product=self.product,  # Duplicate product on same sale
                    quantity=1,
                    unit_price=Decimal('50000.00'),
                    line_subtotal=Decimal('50000.00'),
                    discount=Decimal('0.00'),
                    line_total=Decimal('50000.00')
                )

    # -------------------------------------------------------------------------
    # 3. Invoice Constraints
    # -------------------------------------------------------------------------
    def test_invoice_non_negative_and_calc_constraints(self):
        """Invoice constraints: subtotal >= 0, discount >= 0, discount <= subtotal, taxable_amount >= 0, tax_amount >= 0"""
        sale = Sale.objects.create(
            sale_number='SO-INV-TEST-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )

        # discount > subtotal must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Invoice.objects.create(
                    invoice_number='INV-TEST-001',
                    sale=sale,
                    customer=self.customer_profile,
                    due_date=timezone.now().date(),
                    subtotal=Decimal('100.00'),
                    discount=Decimal('120.00'),  # Violates chk_invoice_discount_lte_subtotal
                    taxable_amount=Decimal('0.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('0.00'),
                    total_amount=Decimal('0.00'),
                    paid_amount=Decimal('0.00'),
                    balance_amount=Decimal('0.00')
                )

        # paid_amount > total_amount must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Invoice.objects.create(
                    invoice_number='INV-TEST-002',
                    sale=sale,
                    customer=self.customer_profile,
                    due_date=timezone.now().date(),
                    subtotal=Decimal('100.00'),
                    discount=Decimal('0.00'),
                    taxable_amount=Decimal('100.00'),
                    tax_rate=Decimal('18.00'),
                    tax_amount=Decimal('18.00'),
                    total_amount=Decimal('118.00'),
                    paid_amount=Decimal('150.00'),  # Violates chk_invoice_paid_lte_total
                    balance_amount=Decimal('0.00')
                )

    # -------------------------------------------------------------------------
    # 4. Payment Reversal & Idempotency Constraints
    # -------------------------------------------------------------------------
    def test_payment_idempotency_key_uniqueness(self):
        """Payment.idempotency_key must be unique and non-null"""
        sale = Sale.objects.create(
            sale_number='SO-PAY-TEST-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-PAY-TEST-01',
            sale=sale,
            customer=self.customer_profile,
            due_date=timezone.now().date(),
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            total_amount=Decimal('118.00'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('118.00')
        )

        idempotency_token = str(uuid.uuid4())
        Payment.objects.create(
            payment_number='PAY-TEST-001',
            idempotency_key=idempotency_token,
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('50.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='UPI-TXN-123456',
            payment_status=Payment.Status.COMPLETED
        )

        # Duplicate submission with same idempotency_key must fail at database level
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Payment.objects.create(
                    payment_number='PAY-TEST-002',
                    idempotency_key=idempotency_token,  # Duplicate!
                    invoice=invoice,
                    customer=self.customer_profile,
                    amount=Decimal('50.00'),
                    payment_method=Payment.Method.UPI,
                    transaction_reference='UPI-TXN-789012',
                    payment_status=Payment.Status.COMPLETED
                )

    def test_payment_unique_non_cash_tx_reference_constraint(self):
        """Non-cash payments cannot reuse the same transaction_reference for same method"""
        sale = Sale.objects.create(
            sale_number='SO-PAY-TEST-02',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('200.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('200.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('36.00'),
            net_amount=Decimal('236.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-PAY-TEST-02',
            sale=sale,
            customer=self.customer_profile,
            due_date=timezone.now().date(),
            subtotal=Decimal('200.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('200.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('36.00'),
            total_amount=Decimal('236.00'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('236.00')
        )

        Payment.objects.create(
            payment_number='PAY-TEST-003',
            idempotency_key='token-ref-1',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('100.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='NEFT-REF-8888',
            payment_status=Payment.Status.COMPLETED
        )

        # Same non-cash reference must raise IntegrityError
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Payment.objects.create(
                    payment_number='PAY-TEST-004',
                    idempotency_key='token-ref-2',
                    invoice=invoice,
                    customer=self.customer_profile,
                    amount=Decimal('50.00'),
                    payment_method=Payment.Method.BANK_TRANSFER,
                    transaction_reference='NEFT-REF-8888',  # Duplicate!
                    payment_status=Payment.Status.COMPLETED
                )

        # Cash payments CAN have empty reference or duplicate reference without error
        p_cash1 = Payment.objects.create(
            payment_number='PAY-TEST-005',
            idempotency_key='token-cash-1',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('10.00'),
            payment_method=Payment.Method.CASH,
            transaction_reference='',
            payment_status=Payment.Status.COMPLETED
        )
        p_cash2 = Payment.objects.create(
            payment_number='PAY-TEST-006',
            idempotency_key='token-cash-2',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('10.00'),
            payment_method=Payment.Method.CASH,
            transaction_reference='',
            payment_status=Payment.Status.COMPLETED
        )
        self.assertIsNotNone(p_cash1.pk)
        self.assertIsNotNone(p_cash2.pk)

    def test_payment_reversal_consistency_constraints(self):
        """
        Payment reversal consistency constraints:
        - status=REVERSED requires is_reversed=True, reversed_at, non-empty reversal_reason
        - non-reversed payments must not have reversal metadata
        """
        sale = Sale.objects.create(
            sale_number='SO-PAY-REV-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-PAY-REV-01',
            sale=sale,
            customer=self.customer_profile,
            due_date=timezone.now().date(),
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            total_amount=Decimal('118.00'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('118.00')
        )

        # REVERSED status but is_reversed=False must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Payment.objects.create(
                    payment_number='PAY-REV-FAIL-1',
                    idempotency_key='token-rev-fail-1',
                    invoice=invoice,
                    customer=self.customer_profile,
                    amount=Decimal('50.00'),
                    payment_method=Payment.Method.UPI,
                    transaction_reference='TXN-R1',
                    payment_status=Payment.Status.REVERSED,
                    is_reversed=False,  # Inconsistent!
                    reversed_at=timezone.now(),
                    reversal_reason='Chargeback'
                )

        # REVERSED status without reversal_reason must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Payment.objects.create(
                    payment_number='PAY-REV-FAIL-2',
                    idempotency_key='token-rev-fail-2',
                    invoice=invoice,
                    customer=self.customer_profile,
                    amount=Decimal('50.00'),
                    payment_method=Payment.Method.UPI,
                    transaction_reference='TXN-R2',
                    payment_status=Payment.Status.REVERSED,
                    is_reversed=True,
                    reversed_at=timezone.now(),
                    reversal_reason=''  # Empty reason violates constraint!
                )

        # COMPLETED status with reversal metadata must fail
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Payment.objects.create(
                    payment_number='PAY-REV-FAIL-3',
                    idempotency_key='token-rev-fail-3',
                    invoice=invoice,
                    customer=self.customer_profile,
                    amount=Decimal('50.00'),
                    payment_method=Payment.Method.UPI,
                    transaction_reference='TXN-R3',
                    payment_status=Payment.Status.COMPLETED,
                    is_reversed=False,
                    reversed_at=timezone.now(),  # Non-null reversed_at on non-reversed payment!
                    reversal_reason=''
                )

        # Correctly formatted reversed payment must succeed
        p_reversed = Payment.objects.create(
            payment_number='PAY-REV-OK-1',
            idempotency_key='token-rev-ok-1',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('50.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='TXN-R4',
            payment_status=Payment.Status.REVERSED,
            is_reversed=True,
            reversed_at=timezone.now(),
            reversal_reason='Customer requested full refund'
        )
        self.assertIsNotNone(p_reversed.pk)

    def test_invoice_paid_amount_calculated_only_from_completed_non_reversed_payments(self):
        """Invoice paid_amount is calculated only from COMPLETED, non-reversed payments"""
        sale = Sale.objects.create(
            sale_number='SO-CALC-TEST-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-CALC-TEST-01',
            sale=sale,
            customer=self.customer_profile,
            due_date=timezone.now().date(),
            subtotal=Decimal('100.00'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('100.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('18.00'),
            total_amount=Decimal('118.00'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('118.00')
        )

        # Add a completed payment
        Payment.objects.create(
            payment_number='PAY-CALC-001',
            idempotency_key='token-calc-1',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('60.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='CALC-TXN-1',
            payment_status=Payment.Status.COMPLETED,
            is_reversed=False
        )
        invoice.update_payment_balance(save=True)
        self.assertEqual(invoice.paid_amount, Decimal('60.00'))
        self.assertEqual(invoice.balance_amount, Decimal('58.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PARTIALLY_PAID)

        # Add a reversed payment - must NOT increase paid_amount
        Payment.objects.create(
            payment_number='PAY-CALC-002',
            idempotency_key='token-calc-2',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('30.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='CALC-TXN-2',
            payment_status=Payment.Status.REVERSED,
            is_reversed=True,
            reversed_at=timezone.now(),
            reversal_reason='Disputed transaction'
        )
        invoice.update_payment_balance(save=True)
        # Paid amount must remain 60.00
        self.assertEqual(invoice.paid_amount, Decimal('60.00'))
        self.assertEqual(invoice.balance_amount, Decimal('58.00'))

        # Add a pending payment - must NOT increase paid_amount
        Payment.objects.create(
            payment_number='PAY-CALC-003',
            idempotency_key='token-calc-3',
            invoice=invoice,
            customer=self.customer_profile,
            amount=Decimal('58.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='CALC-TXN-3',
            payment_status=Payment.Status.PENDING,
            is_reversed=False
        )
        invoice.update_payment_balance(save=True)
        self.assertEqual(invoice.paid_amount, Decimal('60.00'))
        self.assertEqual(invoice.balance_amount, Decimal('58.00'))


class SalesServiceUnitTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_svc', email='admin_svc@example.com', password='Password123!', role='ADMIN'
        )
        self.staff_user = User.objects.create_user(
            username='staff_svc', email='staff_svc@example.com', password='Password123!', role='SALES_STAFF'
        )
        self.staff = SalesStaffProfile.objects.create(
            user=self.staff_user, designation='Executive', department='Enterprise'
        )
        self.customer_user = User.objects.create_user(
            username='cust_svc', email='cust_svc@example.com', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(
            user=self.customer_user,
            company='Global Services',
            assigned_staff=self.staff,
            address='742 Evergreen Terrace',
            city='Springfield',
            state='Oregon',
            postal_code='97477'
        )
        self.category = Category.objects.create(category_name='Hardware Svc', description='Hardware test')
        self.product = ProductService.objects.create(
            category=self.category,
            product_name='ThinkPad X1',
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('1000.00'),
            stock_quantity=5,
            reorder_level=2
        )
        self.service = ProductService.objects.create(
            category=self.category,
            product_name='Priority Support',
            item_type=ProductService.ItemType.SERVICE,
            unit_price=Decimal('500.00'),
            stock_quantity=0,
            reorder_level=0
        )

    def test_create_sale_order_draft(self):
        """Creates DRAFT sales order without stock deduction"""
        from sales.services import create_sale_order
        items = [
            {'product': self.product, 'quantity': 2, 'unit_price': Decimal('1000.00'), 'discount': Decimal('100.00')},
            {'product': self.service, 'quantity': 1, 'unit_price': Decimal('500.00'), 'discount': Decimal('0.00')}
        ]
        # line1: 2 * 1000 = 2000 - 100 = 1900
        # line2: 1 * 500 = 500 - 0 = 500
        # items_total = 2400
        # order discount = 400
        # taxable = 2000
        # tax 18% = 360
        # net = 2360
        sale = create_sale_order(
            customer=self.customer,
            sales_staff=self.staff,
            notes='Test draft sale',
            tax_rate=Decimal('18.00'),
            discount=Decimal('400.00'),
            items_data=items
        )
        self.assertEqual(sale.status, Sale.Status.DRAFT)
        self.assertFalse(sale.stock_deducted)
        self.assertEqual(sale.subtotal, Decimal('2500.00'))
        self.assertEqual(sale.discount, Decimal('400.00'))
        self.assertEqual(sale.taxable_amount, Decimal('2000.00'))
        self.assertEqual(sale.tax_amount, Decimal('360.00'))
        self.assertEqual(sale.net_amount, Decimal('2360.00'))
        self.assertEqual(sale.items.count(), 2)
        # Stock untouched
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def test_confirm_sale_order_deducts_stock_and_creates_invoice(self):
        """Confirming draft sale deducts stock once, creates invoice, and logs audit"""
        from sales.services import create_sale_order, confirm_sale_order
        items = [
            {'product': self.product, 'quantity': 3, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')},
            {'product': self.service, 'quantity': 1, 'unit_price': Decimal('500.00'), 'discount': Decimal('0.00')}
        ]
        sale = create_sale_order(
            customer=self.customer,
            sales_staff=self.staff,
            tax_rate=Decimal('18.00'),
            discount=Decimal('0.00'),
            items_data=items
        )

        sale, invoice = confirm_sale_order(sale=sale, user=self.staff_user)

        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertTrue(sale.stock_deducted)
        self.assertIsNotNone(sale.confirmed_at)

        # Physical product stock decreased by 3 (from 5 to 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 2)

        # Service stock remains 0
        self.service.refresh_from_db()
        self.assertEqual(self.service.stock_quantity, 0)

        # StockAdjustment created with type SALE
        adj = StockAdjustment.objects.filter(product=self.product, adjustment_type=StockAdjustment.AdjustmentType.SALE).first()
        self.assertIsNotNone(adj)
        self.assertEqual(adj.quantity_changed, -3)
        self.assertEqual(adj.quantity_before, 5)
        self.assertEqual(adj.quantity_after, 2)

        # Invoice created with snapshot
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.sale, sale)
        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.customer_name, self.customer_user.username)
        self.assertEqual(invoice.billing_address, '742 Evergreen Terrace')
        self.assertEqual(invoice.total_amount, sale.net_amount)
        self.assertEqual(invoice.balance_amount, sale.net_amount)
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.UNPAID)

    def test_confirm_sale_order_insufficient_stock_rollback(self):
        """When product has insufficient stock, confirmation fails and rolls back completely"""
        from sales.services import create_sale_order, confirm_sale_order
        items = [
            {'product': self.product, 'quantity': 10, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')}
        ]
        sale = create_sale_order(
            customer=self.customer,
            sales_staff=self.staff,
            items_data=items
        )
        with self.assertRaises(ValidationError):
            confirm_sale_order(sale=sale, user=self.staff_user)

        # State rolled back
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.DRAFT)
        self.assertFalse(sale.stock_deducted)
        self.assertFalse(hasattr(sale, 'invoice'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def test_cancel_confirmed_sale_order_restores_stock(self):
        """Cancelling a confirmed sale order restores stock exact-once with SALE_REVERSAL"""
        from sales.services import create_sale_order, confirm_sale_order, cancel_sale_order
        items = [
            {'product': self.product, 'quantity': 2, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')}
        ]
        sale = create_sale_order(customer=self.customer, sales_staff=self.staff, items_data=items)
        sale, invoice = confirm_sale_order(sale=sale, user=self.staff_user)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)

        sale = cancel_sale_order(
            sale=sale,
            cancellation_reason='Customer changed requirements',
            user=self.staff_user
        )

        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertFalse(sale.stock_deducted)
        self.assertEqual(sale.cancellation_reason, 'Customer changed requirements')

        # Stock restored
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

        # Check reversal StockAdjustment
        rev_adj = StockAdjustment.objects.filter(
            product=self.product,
            adjustment_type=StockAdjustment.AdjustmentType.SALE_REVERSAL
        ).first()
        self.assertIsNotNone(rev_adj)
        self.assertEqual(rev_adj.quantity_changed, 2)
        self.assertEqual(rev_adj.quantity_before, 3)
        self.assertEqual(rev_adj.quantity_after, 5)

        # Invoice cancelled
        invoice.refresh_from_db()
        self.assertEqual(invoice.invoice_status, Invoice.InvoiceStatus.CANCELLED)

    def test_payment_recording_and_server_side_idempotency(self):
        """Payment recording transitions invoice status and handles idempotency without double count"""
        from sales.services import create_sale_order, confirm_sale_order, record_payment
        items = [
            {'product': self.product, 'quantity': 1, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')}
        ]
        sale = create_sale_order(
            customer=self.customer, sales_staff=self.staff, tax_rate=Decimal('18.00'), items_data=items
        )
        sale, invoice = confirm_sale_order(sale=sale, user=self.staff_user)
        # Total: 1000 + 180 = 1180.00
        self.assertEqual(invoice.total_amount, Decimal('1180.00'))

        token_1 = 'idemp-tok-part-1'
        p1, created1 = record_payment(
            invoice=invoice,
            amount=Decimal('500.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='UPI-PAY-111',
            idempotency_key=token_1,
            user=self.staff_user
        )
        self.assertTrue(created1)
        self.assertEqual(p1.amount, Decimal('500.00'))

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('500.00'))
        self.assertEqual(invoice.balance_amount, Decimal('680.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PARTIALLY_PAID)

        # Resubmitting EXACT SAME idempotency token MUST be recognized as duplicate
        p1_dup, created_dup = record_payment(
            invoice=invoice,
            amount=Decimal('500.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='UPI-PAY-111',
            idempotency_key=token_1,
            user=self.staff_user
        )
        self.assertFalse(created_dup)
        self.assertEqual(p1_dup.pk, p1.pk)

        # Invoice totals must NOT be deducted twice
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('500.00'))
        self.assertEqual(invoice.balance_amount, Decimal('680.00'))

        # Record remaining balance
        token_2 = 'idemp-tok-part-2'
        p2, created2 = record_payment(
            invoice=invoice,
            amount=Decimal('680.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='NEFT-PAY-222',
            idempotency_key=token_2,
            user=self.staff_user
        )
        self.assertTrue(created2)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('1180.00'))
        self.assertEqual(invoice.balance_amount, Decimal('0.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PAID)

    def test_cannot_record_payment_exceeding_balance(self):
        """Payment amount exceeding balance is rejected with ValidationError"""
        from sales.services import create_sale_order, confirm_sale_order, record_payment
        items = [{'product': self.product, 'quantity': 1, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')}]
        sale = create_sale_order(customer=self.customer, sales_staff=self.staff, tax_rate=Decimal('0.00'), items_data=items)
        sale, invoice = confirm_sale_order(sale=sale, user=self.staff_user)

        with self.assertRaises(ValidationError):
            record_payment(
                invoice=invoice,
                amount=Decimal('1500.00'),  # 1500 > 1000
                payment_method=Payment.Method.CASH,
                transaction_reference='',
                idempotency_key='tok-excess',
                user=self.staff_user
            )

    def test_payment_reversal_and_sale_demotion(self):
        """Admin payment reversal recalculates invoice balance and demotes completed sale to confirmed"""
        from sales.services import create_sale_order, confirm_sale_order, complete_sale_order, record_payment, reverse_payment
        items = [{'product': self.product, 'quantity': 1, 'unit_price': Decimal('1000.00'), 'discount': Decimal('0.00')}]
        sale = create_sale_order(customer=self.customer, sales_staff=self.staff, tax_rate=Decimal('0.00'), items_data=items)
        sale, invoice = confirm_sale_order(sale=sale, user=self.staff_user)

        p, _ = record_payment(
            invoice=invoice,
            amount=Decimal('1000.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='UPI-TXN-REV-TEST',
            idempotency_key='tok-rev-1',
            user=self.staff_user
        )

        sale = complete_sale_order(sale=sale, user=self.staff_user)
        self.assertEqual(sale.status, Sale.Status.COMPLETED)

        # Admin reverses payment
        reversed_p = reverse_payment(
            payment=p,
            reversal_reason='Disputed transaction / chargeback requested',
            user=self.admin
        )

        self.assertTrue(reversed_p.is_reversed)
        self.assertEqual(reversed_p.payment_status, Payment.Status.REVERSED)
        self.assertEqual(reversed_p.reversal_reason, 'Disputed transaction / chargeback requested')
        self.assertEqual(reversed_p.reversed_by, self.admin)

        # Invoice balance updated
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))
        self.assertEqual(invoice.balance_amount, Decimal('1000.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.UNPAID)

        # Completed sale demoted to confirmed
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)


class SalesViewAndPermissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='view_admin', email='view_admin@example.com', password='Password123!', role='ADMIN'
        )
        self.staff_user1 = User.objects.create_user(
            username='view_staff1', email='view_staff1@example.com', password='Password123!', role='SALES_STAFF'
        )
        self.staff1 = SalesStaffProfile.objects.create(
            user=self.staff_user1, designation='Senior Rep', department='Direct Sales'
        )
        self.staff_user2 = User.objects.create_user(
            username='view_staff2', email='view_staff2@example.com', password='Password123!', role='SALES_STAFF'
        )
        self.staff2 = SalesStaffProfile.objects.create(
            user=self.staff_user2, designation='Junior Rep', department='Telesales'
        )
        self.cust_user1 = User.objects.create_user(
            username='view_cust1', email='view_cust1@example.com', password='Password123!', role='CUSTOMER'
        )
        self.customer1 = CustomerProfile.objects.create(
            user=self.cust_user1, company='Alpha Ltd', assigned_staff=self.staff1
        )
        self.cust_user2 = User.objects.create_user(
            username='view_cust2', email='view_cust2@example.com', password='Password123!', role='CUSTOMER'
        )
        self.customer2 = CustomerProfile.objects.create(
            user=self.cust_user2, company='Beta Ltd', assigned_staff=self.staff2
        )

        self.category = Category.objects.create(category_name='Test Cat', description='Test')
        self.product = ProductService.objects.create(
            category=self.category,
            product_name='Monitor 27',
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('15000.00'),
            stock_quantity=20,
            reorder_level=5
        )

        # Sale assigned to staff1 / customer1
        from sales.services import create_sale_order, confirm_sale_order
        self.sale1 = create_sale_order(
            customer=self.customer1,
            sales_staff=self.staff1,
            tax_rate=Decimal('18.00'),
            items_data=[{'product': self.product, 'quantity': 1, 'unit_price': Decimal('15000.00'), 'discount': Decimal('0.00')}]
        )
        self.sale1, self.invoice1 = confirm_sale_order(sale=self.sale1, user=self.admin)

    def test_admin_can_access_all_sales_and_invoices(self):
        """Admin can view sales and invoices across all staff and customers"""
        from django.urls import reverse
        self.client.login(username='view_admin', password='Password123!')
        res_sale = self.client.get(reverse('sales:sale_list'))
        self.assertEqual(res_sale.status_code, 200)
        res_inv = self.client.get(reverse('sales:invoice_list'))
        self.assertEqual(res_inv.status_code, 200)
        res_pay = self.client.get(reverse('sales:payment_list'))
        self.assertEqual(res_pay.status_code, 200)

    def test_sales_staff_permission_isolation(self):
        """Sales staff can access their assigned sales, but gets 403 on other staff's sales"""
        from django.urls import reverse
        # Staff 1 can access sale1
        self.client.login(username='view_staff1', password='Password123!')
        res = self.client.get(reverse('sales:sale_detail', kwargs={'pk': self.sale1.pk}))
        self.assertEqual(res.status_code, 200)

        # Staff 2 cannot access sale1
        self.client.login(username='view_staff2', password='Password123!')
        res2 = self.client.get(reverse('sales:sale_detail', kwargs={'pk': self.sale1.pk}))
        self.assertEqual(res2.status_code, 403)

    def test_customer_portal_isolation(self):
        """Customer can access own purchases and invoices, but gets 403 on other customers' records"""
        from django.urls import reverse
        # Customer 1 can access own purchase
        self.client.login(username='view_cust1', password='Password123!')
        res1 = self.client.get(reverse('sales:customer_sale_detail', kwargs={'pk': self.sale1.pk}))
        self.assertEqual(res1.status_code, 200)
        res_inv1 = self.client.get(reverse('sales:customer_invoice_detail', kwargs={'pk': self.invoice1.pk}))
        self.assertEqual(res_inv1.status_code, 200)

        # Customer 2 gets 403 when trying to view customer 1's sale or invoice
        self.client.login(username='view_cust2', password='Password123!')
        res2 = self.client.get(reverse('sales:customer_sale_detail', kwargs={'pk': self.sale1.pk}))
        self.assertEqual(res2.status_code, 403)
        res_inv2 = self.client.get(reverse('sales:customer_invoice_detail', kwargs={'pk': self.invoice1.pk}))
        self.assertEqual(res_inv2.status_code, 403)

    def test_customer_cannot_access_staff_views(self):
        """Customer accessing internal staff/admin sales views receives 403 or redirect"""
        from django.urls import reverse
        self.client.login(username='view_cust1', password='Password123!')
        res = self.client.get(reverse('sales:sale_list'))
        # StaffRequiredMixin redirects to staff login or throws 403
        self.assertIn(res.status_code, [302, 403])

    def test_printable_invoice_and_receipt_render_successfully(self):
        """Printable invoice and receipt pages render HTTP 200 without layout chrome"""
        from django.urls import reverse
        self.client.login(username='view_admin', password='Password123!')
        res_inv_print = self.client.get(reverse('sales:invoice_print', kwargs={'pk': self.invoice1.pk}))
        self.assertEqual(res_inv_print.status_code, 200)
        self.assertContains(res_inv_print, self.invoice1.invoice_number)


class PaymentWorkflowRegressionTests(TestCase):
    """
    Regression and verification tests for PaymentCreateView and the payment creation workflow.
    Validates:
    - Fix for RelatedObjectDoesNotExist on POST /sales/invoices/<id>/pay/
    - Partial payment calculation: ₹50 against ₹163.94 leaves balance ₹113.94 and PARTIALLY_PAID status
    - Server-side idempotency: duplicate form resubmission does not create duplicate payment or alter balances
    - Rejection of payment amounts exceeding outstanding balance
    - Full balance settlement transitioning invoice to PAID
    """
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='pay_staff',
            email='pay_staff@example.com',
            password='Password123!',
            role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.staff_user,
            designation='Billing Specialist',
            department='Finance'
        )
        self.customer_user = User.objects.create_user(
            username='pay_customer',
            email='pay_customer@example.com',
            password='Password123!',
            role='CUSTOMER'
        )
        self.customer_profile = CustomerProfile.objects.create(
            user=self.customer_user,
            company='Pay Test Corp',
            assigned_staff=self.staff_profile,
            address='100 Market St',
            city='Mumbai',
            state='Maharashtra',
            postal_code='400001'
        )
        self.category = Category.objects.create(
            category_name='Billing Test Cat',
            description='Test category'
        )
        self.product = ProductService.objects.create(
            category=self.category,
            product_name='Consulting Hours',
            item_type=ProductService.ItemType.SERVICE,
            unit_price=Decimal('138.93'),
            stock_quantity=0,
            reorder_level=0
        )

        # Invoice total: 138.93 + 18% tax (25.01) = 163.94
        self.sale = Sale.objects.create(
            sale_number='SO-REGRESS-01',
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            subtotal=Decimal('138.93'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('138.93'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('25.01'),
            net_amount=Decimal('163.94'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        self.invoice = Invoice.objects.create(
            invoice_number='INV-REGRESS-01',
            sale=self.sale,
            customer=self.customer_profile,
            customer_name='Pay Test Corp',
            billing_address='100 Market St',
            due_date=timezone.now().date(),
            subtotal=Decimal('138.93'),
            discount=Decimal('0.00'),
            taxable_amount=Decimal('138.93'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('25.01'),
            total_amount=Decimal('163.94'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('163.94'),
            payment_status=Invoice.PaymentStatus.UNPAID
        )

    def test_regression_payment_create_view_post_partial_payment(self):
        """
        Regression test: POST /sales/invoices/<id>/pay/ must not raise RelatedObjectDoesNotExist
        and must record partial payment of ₹50 against ₹163.94 resulting in balance ₹113.94
        and PARTIALLY_PAID status.
        """
        from django.urls import reverse
        self.client.login(username='pay_staff', password='Password123!')

        url = reverse('sales:payment_create', kwargs={'invoice_pk': self.invoice.pk})
        idempotency_token = 'token-regress-50'

        post_data = {
            'amount': '50.00',
            'payment_method': Payment.Method.CASH,
            'transaction_reference': '',
            'notes': 'Partial cash payment',
            'idempotency_key': idempotency_token,
        }

        response = self.client.post(url, post_data)

        # Must redirect to payment_detail without raising RelatedObjectDoesNotExist
        self.assertEqual(response.status_code, 302)

        payment = Payment.objects.filter(invoice=self.invoice).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal('50.00'))
        self.assertEqual(payment.payment_method, Payment.Method.CASH)
        self.assertEqual(payment.idempotency_key, idempotency_token)
        self.assertEqual(payment.customer, self.customer_profile)
        self.assertEqual(payment.invoice, self.invoice)

        # Verify invoice totals and status after partial payment
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('50.00'))
        self.assertEqual(self.invoice.balance_amount, Decimal('113.94'))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PARTIALLY_PAID)

    def test_resubmitting_same_form_is_idempotent_and_creates_no_duplicate(self):
        """
        Submitting the form with the same idempotency token must not duplicate payment or modify balance.
        """
        from django.urls import reverse
        self.client.login(username='pay_staff', password='Password123!')

        url = reverse('sales:payment_create', kwargs={'invoice_pk': self.invoice.pk})
        idempotency_token = 'token-regress-idemp-01'

        post_data = {
            'amount': '50.00',
            'payment_method': Payment.Method.UPI,
            'transaction_reference': 'UPI-REGRESS-1001',
            'notes': 'Initial submission',
            'idempotency_key': idempotency_token,
        }

        # First submission
        res1 = self.client.post(url, post_data)
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 1)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('50.00'))
        self.assertEqual(self.invoice.balance_amount, Decimal('113.94'))

        # Second submission with identical form payload
        res2 = self.client.post(url, post_data)
        self.assertEqual(res2.status_code, 302)
        # Payment count must still be 1
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 1)

        # Invoice balances must remain unchanged
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('50.00'))
        self.assertEqual(self.invoice.balance_amount, Decimal('113.94'))

    def test_payment_amount_exceeding_invoice_balance_rejected(self):
        """
        Payment amount above the outstanding balance is rejected and creates no payment.
        """
        from django.urls import reverse
        self.client.login(username='pay_staff', password='Password123!')

        url = reverse('sales:payment_create', kwargs={'invoice_pk': self.invoice.pk})

        # Balance is 163.94; attempt 200.00
        post_data = {
            'amount': '200.00',
            'payment_method': Payment.Method.CASH,
            'transaction_reference': '',
            'notes': 'Excess amount test',
            'idempotency_key': 'token-excess-01',
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'amount', 'Payment amount cannot exceed the invoice balance of ₹163.94.')
        self.assertContains(response, 'Payment amount cannot exceed the invoice balance of')

        # No payment created
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 0)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance_amount, Decimal('163.94'))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.UNPAID)

    def test_full_payment_workflow_from_partial_to_paid(self):
        """
        Test two-step payment: partial ₹50.00 followed by balance ₹113.94 transitions to PAID.
        """
        from django.urls import reverse
        self.client.login(username='pay_staff', password='Password123!')

        url = reverse('sales:payment_create', kwargs={'invoice_pk': self.invoice.pk})

        # Step 1: Pay 50.00
        res1 = self.client.post(url, {
            'amount': '50.00',
            'payment_method': Payment.Method.CASH,
            'transaction_reference': '',
            'idempotency_key': 'tok-step-1',
        })
        self.assertEqual(res1.status_code, 302)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('50.00'))
        self.assertEqual(self.invoice.balance_amount, Decimal('113.94'))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PARTIALLY_PAID)

        # Step 2: Pay remaining 113.94
        res2 = self.client.post(url, {
            'amount': '113.94',
            'payment_method': Payment.Method.BANK_TRANSFER,
            'transaction_reference': 'NEFT-STEP-2',
            'idempotency_key': 'tok-step-2',
        })
        self.assertEqual(res2.status_code, 302)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('163.94'))
        self.assertEqual(self.invoice.balance_amount, Decimal('0.00'))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PAID)

    def test_unsaved_payment_clean_and_str_do_not_raise_related_object_does_not_exist(self):
        """
        Unsaved Payment instance without an invoice does not crash on clean() or __str__().
        """
        p = Payment(idempotency_key='tok-clean-test', amount=Decimal('10.00'), payment_method=Payment.Method.CASH)
        # Should not raise RelatedObjectDoesNotExist
        p.clean()
        str_repr = str(p)
        self.assertIn("No Invoice", str_repr)



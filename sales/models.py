import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile, SalesStaffProfile
from catalog.models import ProductService


class Sale(models.Model):
    """
    Sales Order model.

    Financial calculation principles:
    - Sale.subtotal: sum of all line subtotals (quantity * unit_price) before line discounts.
    - items_total: sum of all line totals (line_subtotal - line_discount) after line discounts.
    - Sale.discount: order-level fixed discount.
      * Database constraint enforces: discount <= subtotal.
      * Service validation and calculate_totals enforce: order discount <= items_total.
    - taxable_amount: max(items_total - discount, Decimal('0.00')).
    - tax_amount: (taxable_amount * tax_rate / 100).quantize(Decimal('0.01')).
    - net_amount: taxable_amount + tax_amount.

    Stock and status rules:
    - DRAFT and CANCELLED require stock_deducted=False.
    - CONFIRMED and COMPLETED require stock_deducted=True.
      Treat stock_deducted as confirmation stock-processing completion even for service-only orders.
    """
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    sale_number = models.CharField(max_length=30, unique=True, db_index=True)
    customer = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name='sales')
    sales_staff = models.ForeignKey(SalesStaffProfile, on_delete=models.PROTECT, related_name='sales_handled')
    sale_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # Order-level financial fields:
    # Sale.subtotal = sum of all line subtotals (quantity * unit_price) before line discounts
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Sum of all item line subtotals before line discounts.")
    )
    # Order-level fixed discount only (enforced <= items_total in service, <= subtotal in DB)
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Order-level fixed discount.")
    )
    taxable_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Taxable base after line totals and order-level discount.")
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('18.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
        help_text=_("Tax rate percentage (0.00 to 100.00). Default is 18.00%.")
    )
    tax_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    net_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    notes = models.TextField(blank=True)

    # State tracking & Audit timestamps
    # DRAFT and CANCELLED require stock_deducted=False; CONFIRMED and COMPLETED require stock_deducted=True
    stock_deducted = models.BooleanField(
        default=False,
        help_text=_("Tracks confirmation stock-processing completion even for service-only orders.")
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sale_date', '-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0) & models.Q(tax_rate__lte=100),
                name='chk_sale_tax_rate_range'
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0),
                name='chk_sale_subtotal_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(discount__gte=0),
                name='chk_sale_discount_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(discount__lte=models.F('subtotal')),
                name='chk_sale_discount_lte_subtotal'
            ),
            models.CheckConstraint(
                condition=models.Q(taxable_amount__gte=0),
                name='chk_sale_taxable_amount_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__gte=0),
                name='chk_sale_tax_amount_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(net_amount__gte=0),
                name='chk_sale_net_amount_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(net_amount=models.F('taxable_amount') + models.F('tax_amount')),
                name='chk_sale_net_amount_calc'
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(status__in=['DRAFT', 'CANCELLED']) & models.Q(stock_deducted=False)) |
                    (models.Q(status__in=['CONFIRMED', 'COMPLETED']) & models.Q(stock_deducted=True))
                ),
                name='chk_sale_status_stock_deducted_consistency'
            ),
        ]

    def clean(self):
        super().clean()
        if self.tax_rate is not None and (self.tax_rate < Decimal('0.00') or self.tax_rate > Decimal('100.00')):
            raise ValidationError({'tax_rate': _('Tax rate must be between 0.00% and 100.00%.')})
        if self.discount is not None and self.discount < Decimal('0.00'):
            raise ValidationError({'discount': _('Order discount cannot be negative.')})
        if self.subtotal is not None and self.discount is not None and self.discount > self.subtotal:
            raise ValidationError({'discount': _('Order discount cannot exceed order subtotal.')})
        if self.status in [self.Status.DRAFT, self.Status.CANCELLED] and self.stock_deducted:
            raise ValidationError({'stock_deducted': _('Draft or cancelled sales must have stock_deducted=False.')})
        if self.status in [self.Status.CONFIRMED, self.Status.COMPLETED] and not self.stock_deducted:
            raise ValidationError({'stock_deducted': _('Confirmed or completed sales must have stock_deducted=True.')})

    def save(self, *args, **kwargs):
        if not self.sale_number:
            year = timezone.now().year
            while True:
                candidate = f"SO-{year}-{uuid.uuid4().hex[:6].upper()}"
                if not Sale.objects.filter(sale_number=candidate).exists():
                    self.sale_number = candidate
                    break

        # If net_amount is set but taxable_amount and tax_amount were left at default 0,
        # ensure financial consistency with chk_sale_net_amount_calc: net_amount = taxable_amount + tax_amount
        if self.net_amount is not None and self.net_amount > Decimal('0.00'):
            if (self.taxable_amount is None or self.taxable_amount == Decimal('0.00')) and \
               (self.tax_amount is None or self.tax_amount == Decimal('0.00')):
                self.taxable_amount = self.net_amount
                self.tax_amount = Decimal('0.00')
        elif self.net_amount is None or self.net_amount == Decimal('0.00'):
            taxable = self.taxable_amount or Decimal('0.00')
            tax = self.tax_amount or Decimal('0.00')
            if taxable > Decimal('0.00') or tax > Decimal('0.00'):
                self.net_amount = (taxable + tax).quantize(Decimal('0.01'))

        super().save(*args, **kwargs)

    def calculate_totals(self, save=True):
        """
        Recalculates all line items and order totals strictly following:
        1. line_subtotal = quantity * unit_price
        2. line_total = line_subtotal - line_discount
        3. items_total = sum(line_total)
        4. taxable_amount = max(items_total - order_discount, Decimal('0.00'))
        5. tax_amount = (taxable_amount * tax_rate / 100).quantize(Decimal('0.01'))
        6. net_amount = taxable_amount + tax_amount
        """
        items = list(self.items.all())
        for item in items:
            item.calculate_line_totals(save=True)

        items_total = sum((item.line_total for item in items), Decimal('0.00')).quantize(Decimal('0.01'))
        raw_subtotal = sum((item.line_subtotal for item in items), Decimal('0.00')).quantize(Decimal('0.01'))

        # Reject order discount exceeding items_total
        order_discount = min(self.discount or Decimal('0.00'), items_total).quantize(Decimal('0.01'))
        self.discount = order_discount
        self.subtotal = raw_subtotal

        taxable = max(items_total - order_discount, Decimal('0.00')).quantize(Decimal('0.01'))
        rate = self.tax_rate if self.tax_rate is not None else Decimal('18.00')
        tax = (taxable * rate / Decimal('100.00')).quantize(Decimal('0.01'))
        net = (taxable + tax).quantize(Decimal('0.01'))

        self.taxable_amount = taxable
        self.tax_amount = tax
        self.net_amount = net

        if save:
            self.save(update_fields=[
                'subtotal', 'discount', 'taxable_amount', 'tax_rate',
                'tax_amount', 'net_amount', 'updated_at'
            ])

    @property
    def has_invoice(self):
        return hasattr(self, 'invoice') and self.invoice is not None

    def __str__(self):
        return f"{self.sale_number} - {self.customer} (₹{self.net_amount}) [{self.get_status_display()}]"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(ProductService, on_delete=models.PROTECT, related_name='sale_items')
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    line_subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("quantity * unit_price")
    )
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Line-level fixed discount.")
    )
    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("line_subtotal - line_discount")
    )

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['sale', 'product'], name='unique_sale_product_line'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='chk_saleitem_qty_gt_zero'),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name='chk_saleitem_unit_price_non_negative'),
            models.CheckConstraint(condition=models.Q(discount__gte=0), name='chk_saleitem_discount_non_negative'),
            models.CheckConstraint(condition=models.Q(line_subtotal__gte=0), name='chk_saleitem_line_subtotal_non_negative'),
            models.CheckConstraint(condition=models.Q(line_total__gte=0), name='chk_saleitem_line_total_non_negative'),
            models.CheckConstraint(condition=models.Q(discount__lte=models.F('line_subtotal')), name='chk_saleitem_discount_lte_subtotal'),
            models.CheckConstraint(condition=models.Q(line_subtotal=models.F('quantity') * models.F('unit_price')), name='chk_saleitem_line_subtotal_calc'),
            models.CheckConstraint(condition=models.Q(line_total=models.F('line_subtotal') - models.F('discount')), name='chk_saleitem_line_total_calc'),
        ]

    def calculate_line_totals(self, save=False):
        qty = Decimal(str(self.quantity or 1))
        price = Decimal(str(self.unit_price or '0.00'))
        raw_subtotal = (qty * price).quantize(Decimal('0.01'))
        disc = Decimal(str(self.discount or '0.00')).quantize(Decimal('0.01'))
        # Line discount cannot exceed line subtotal
        capped_disc = min(disc, raw_subtotal)

        self.line_subtotal = raw_subtotal
        self.discount = capped_disc
        self.line_total = max(raw_subtotal - capped_disc, Decimal('0.00')).quantize(Decimal('0.01'))

        if save and self.pk:
            self.save(update_fields=['line_subtotal', 'discount', 'line_total'])

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': _('Quantity must be greater than zero.')})
        if self.unit_price is not None and self.unit_price < Decimal('0.00'):
            raise ValidationError({'unit_price': _('Unit price cannot be negative.')})
        if self.discount is not None and self.discount < Decimal('0.00'):
            raise ValidationError({'discount': _('Discount cannot be negative.')})
        if self.quantity and self.unit_price:
            sub = (Decimal(str(self.quantity)) * Decimal(str(self.unit_price))).quantize(Decimal('0.01'))
            if self.discount and self.discount > sub:
                raise ValidationError({'discount': _('Line discount cannot exceed line subtotal (₹%(sub)s).') % {'sub': sub}})

    def save(self, *args, **kwargs):
        self.calculate_line_totals(save=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.product_name} x {self.quantity} = ₹{self.line_total}"


class Invoice(models.Model):
    class PaymentStatus(models.TextChoices):
        UNPAID = 'UNPAID', _('Unpaid')
        PARTIALLY_PAID = 'PARTIALLY_PAID', _('Partially Paid')
        PAID = 'PAID', _('Fully Paid')

    class InvoiceStatus(models.TextChoices):
        ISSUED = 'ISSUED', _('Issued')
        OVERDUE = 'OVERDUE', _('Overdue')
        CANCELLED = 'CANCELLED', _('Cancelled')

    invoice_number = models.CharField(max_length=30, unique=True, db_index=True)
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name='invoice')
    customer = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name='invoices')
    invoice_date = models.DateField(default=timezone.now)
    due_date = models.DateField()

    # Customer billing details snapshot at time of invoice creation
    customer_name = models.CharField(max_length=150, default="")
    customer_email = models.EmailField(blank=True, default="")
    customer_phone = models.CharField(max_length=20, blank=True, default="")
    customer_company = models.CharField(max_length=150, blank=True, default="")
    billing_address = models.CharField(max_length=255, blank=True, default="")
    billing_city = models.CharField(max_length=100, blank=True, default="")
    billing_state = models.CharField(max_length=100, blank=True, default="")
    billing_postal_code = models.CharField(max_length=20, blank=True, default="")

    # Financial snapshots copied from finalized Sale
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('18.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))]
    )
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])

    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    invoice_status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.ISSUED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-invoice_date', '-created_at']
        constraints = [
            models.CheckConstraint(condition=models.Q(tax_rate__gte=0) & models.Q(tax_rate__lte=100), name='chk_invoice_tax_rate_range'),
            models.CheckConstraint(condition=models.Q(subtotal__gte=0), name='chk_invoice_subtotal_non_negative'),
            models.CheckConstraint(condition=models.Q(discount__gte=0), name='chk_invoice_discount_non_negative'),
            models.CheckConstraint(condition=models.Q(discount__lte=models.F('subtotal')), name='chk_invoice_discount_lte_subtotal'),
            models.CheckConstraint(condition=models.Q(taxable_amount__gte=0), name='chk_invoice_taxable_amount_non_negative'),
            models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='chk_invoice_tax_amount_non_negative'),
            models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='chk_invoice_total_non_negative'),
            models.CheckConstraint(condition=models.Q(paid_amount__gte=0), name='chk_invoice_paid_non_negative'),
            models.CheckConstraint(condition=models.Q(balance_amount__gte=0), name='chk_invoice_balance_non_negative'),
            models.CheckConstraint(condition=models.Q(paid_amount__lte=models.F('total_amount')), name='chk_invoice_paid_lte_total'),
            models.CheckConstraint(condition=models.Q(balance_amount=models.F('total_amount') - models.F('paid_amount')), name='chk_invoice_balance_calc'),
            models.CheckConstraint(condition=models.Q(total_amount=models.F('taxable_amount') + models.F('tax_amount')), name='chk_invoice_total_calc'),
        ]

    def clean(self):
        super().clean()
        if self.subtotal is not None and self.subtotal < Decimal('0.00'):
            raise ValidationError({'subtotal': _('Invoice subtotal cannot be negative.')})
        if self.discount is not None and self.discount < Decimal('0.00'):
            raise ValidationError({'discount': _('Invoice discount cannot be negative.')})
        if self.subtotal is not None and self.discount is not None and self.discount > self.subtotal:
            raise ValidationError({'discount': _('Invoice discount cannot exceed subtotal.')})
        if self.taxable_amount is not None and self.taxable_amount < Decimal('0.00'):
            raise ValidationError({'taxable_amount': _('Invoice taxable amount cannot be negative.')})
        if self.tax_amount is not None and self.tax_amount < Decimal('0.00'):
            raise ValidationError({'tax_amount': _('Invoice tax amount cannot be negative.')})

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            year = timezone.now().year
            while True:
                candidate = f"INV-{year}-{uuid.uuid4().hex[:6].upper()}"
                if not Invoice.objects.filter(invoice_number=candidate).exists():
                    self.invoice_number = candidate
                    break
        if self.balance_amount is None and self.total_amount is not None:
            paid = self.paid_amount or Decimal('0.00')
            self.balance_amount = max(self.total_amount - paid, Decimal('0.00')).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def update_payment_balance(self, save=True):
        """
        Safely recalculates paid and balance amounts strictly from completed,
        non-reversed payments.
        """
        completed_payments = self.payments.filter(
            payment_status=Payment.Status.COMPLETED,
            is_reversed=False
        )
        total_paid = sum((p.amount for p in completed_payments), Decimal('0.00')).quantize(Decimal('0.01'))
        self.paid_amount = min(total_paid, self.total_amount).quantize(Decimal('0.01'))
        self.balance_amount = max(self.total_amount - self.paid_amount, Decimal('0.00')).quantize(Decimal('0.01'))

        if self.balance_amount == Decimal('0.00'):
            self.payment_status = self.PaymentStatus.PAID
        elif self.paid_amount > Decimal('0.00'):
            self.payment_status = self.PaymentStatus.PARTIALLY_PAID
        else:
            self.payment_status = self.PaymentStatus.UNPAID

        # Update overdue status if applicable (and not cancelled)
        if self.invoice_status != self.InvoiceStatus.CANCELLED:
            if self.is_overdue:
                self.invoice_status = self.InvoiceStatus.OVERDUE
            elif self.invoice_status == self.InvoiceStatus.OVERDUE:
                self.invoice_status = self.InvoiceStatus.ISSUED

        if save:
            self.save(update_fields=['paid_amount', 'balance_amount', 'payment_status', 'invoice_status', 'updated_at'])

    @property
    def is_overdue(self):
        return (
            self.due_date < timezone.now().date() and
            self.balance_amount > Decimal('0.00') and
            self.invoice_status != self.InvoiceStatus.CANCELLED
        )

    @property
    def has_successful_payments(self):
        return self.payments.filter(payment_status=Payment.Status.COMPLETED, is_reversed=False).exists()

    def __str__(self):
        return f"{self.invoice_number} ({self.customer_name}) - ₹{self.total_amount} [{self.get_payment_status_display()}]"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = 'CASH', _('Cash')
        BANK_TRANSFER = 'BANK_TRANSFER', _('Bank Transfer / NEFT')
        UPI = 'UPI', _('UPI / Digital Wallet')
        CREDIT_CARD = 'CREDIT_CARD', _('Credit / Debit Card')
        CHEQUE = 'CHEQUE', _('Cheque')
        OTHER = 'OTHER', _('Other')

    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', _('Completed')
        PENDING = 'PENDING', _('Pending')
        FAILED = 'FAILED', _('Failed')
        REVERSED = 'REVERSED', _('Reversed / Refunded')

    payment_number = models.CharField(max_length=30, unique=True, db_index=True)
    # Non-null, non-blank unique idempotency key (no redundant db_index=True)
    idempotency_key = models.CharField(
        max_length=64,
        unique=True,
        help_text=_("Unique form-rendered operation token to prevent duplicate payment processing.")
    )
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    customer = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name='payments')
    payment_date = models.DateTimeField(default=timezone.now)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    payment_method = models.CharField(max_length=20, choices=Method.choices, default=Method.UPI)
    transaction_reference = models.CharField(
        max_length=100, blank=True,
        help_text=_("UTR / Cheque No / Card Reference ID")
    )
    payment_status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    # Reversal audit fields
    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reversed_payments'
    )
    reversal_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date']
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='chk_payment_amount_gt_zero'),
            models.UniqueConstraint(
                fields=['payment_method', 'transaction_reference'],
                condition=~models.Q(payment_method='CASH') & ~models.Q(transaction_reference=''),
                name='unique_non_cash_tx_reference'
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(payment_status='REVERSED') &
                        models.Q(is_reversed=True) &
                        models.Q(reversed_at__isnull=False) &
                        ~models.Q(reversal_reason='')
                    ) |
                    (
                        ~models.Q(payment_status='REVERSED') &
                        models.Q(is_reversed=False) &
                        models.Q(reversed_at__isnull=True) &
                        models.Q(reversal_reason='') &
                        models.Q(reversed_by__isnull=True)
                    )
                ),
                name='chk_payment_reversal_consistency'
            ),
        ]

    def clean(self):
        super().clean()
        if not (self.idempotency_key or '').strip():
            raise ValidationError({'idempotency_key': _('Idempotency key is required.')})
        if self.amount is not None and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': _('Payment amount must be greater than zero.')})

        try:
            invoice = self.invoice
        except ObjectDoesNotExist:
            invoice = None

        try:
            customer = self.customer
        except ObjectDoesNotExist:
            customer = None

        if invoice and customer and invoice.customer != customer:
            raise ValidationError({'customer': _('Payment customer must match the invoice customer.')})

        if self.pk is None and invoice and self.amount is not None:
            if self.amount > invoice.balance_amount:
                raise ValidationError({
                    'amount': _('Payment amount (₹%(amt)s) cannot exceed invoice balance (₹%(bal)s).') % {
                        'amt': self.amount, 'bal': invoice.balance_amount
                    }
                })

        # For non-cash payments, require a transaction reference
        if self.payment_method != self.Method.CASH and not (self.transaction_reference or '').strip():
            raise ValidationError({'transaction_reference': _('Transaction reference is required for non-cash payments.')})

        # Reversal consistency validation
        if self.payment_status == self.Status.REVERSED or self.is_reversed:
            if self.payment_status != self.Status.REVERSED:
                raise ValidationError({'payment_status': _('Reversed payment must have status REVERSED.')})
            if not self.is_reversed:
                raise ValidationError({'is_reversed': _('Reversed payment must have is_reversed=True.')})
            if not self.reversed_at:
                raise ValidationError({'reversed_at': _('Reversed payment must have reversed_at timestamp.')})
            if not (self.reversal_reason or '').strip():
                raise ValidationError({'reversal_reason': _('Reversed payment must have a non-empty reversal reason.')})
        else:
            if self.is_reversed or self.reversed_at or (self.reversal_reason or '').strip() or self.reversed_by:
                raise ValidationError(_('Non-reversed payment cannot have reversal metadata.'))

    def save(self, *args, **kwargs):
        if not self.payment_number:
            year = timezone.now().year
            while True:
                candidate = f"PAY-{year}-{uuid.uuid4().hex[:6].upper()}"
                if not Payment.objects.filter(payment_number=candidate).exists():
                    self.payment_number = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        try:
            inv_str = self.invoice.invoice_number
        except ObjectDoesNotExist:
            inv_str = "No Invoice"
        return f"{self.payment_number} - ₹{self.amount} for {inv_str} [{self.get_payment_status_display()}]"

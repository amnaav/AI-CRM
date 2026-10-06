import uuid
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from catalog.models import ProductService, StockAdjustment
from catalog.services import adjust_stock
from sales.models import Sale, SaleItem, Invoice, Payment


def calculate_financials(items_data, discount, tax_rate):
    """
    Calculates raw line subtotals, line totals, items total, taxable amount,
    tax amount, and net amount strictly adhering to calculation principles:
    1. line_subtotal = quantity * unit_price
    2. line_total = line_subtotal - line_discount
    3. items_total = sum(line_total)
    4. order_discount <= items_total
    5. taxable_amount = max(items_total - order_discount, 0.00)
    6. tax_amount = (taxable_amount * tax_rate / 100).quantize(0.01)
    7. net_amount = taxable_amount + tax_amount
    """
    subtotal = Decimal('0.00')
    items_total = Decimal('0.00')
    processed_items = []

    for item in items_data:
        qty = int(item.get('quantity', 1))
        if qty <= 0:
            raise ValidationError(_("Item quantity must be greater than zero."))
        price = Decimal(str(item.get('unit_price', '0.00'))).quantize(Decimal('0.01'))
        if price < Decimal('0.00'):
            raise ValidationError(_("Item unit price cannot be negative."))
        line_disc = Decimal(str(item.get('discount', '0.00'))).quantize(Decimal('0.01'))
        if line_disc < Decimal('0.00'):
            raise ValidationError(_("Item discount cannot be negative."))

        line_sub = (Decimal(qty) * price).quantize(Decimal('0.01'))
        if line_disc > line_sub:
            raise ValidationError(
                _("Line discount (₹%(disc)s) cannot exceed line subtotal (₹%(sub)s).") % {
                    'disc': line_disc, 'sub': line_sub
                }
            )
        line_tot = (line_sub - line_disc).quantize(Decimal('0.01'))

        subtotal += line_sub
        items_total += line_tot

        processed_items.append({
            'product': item.get('product'),
            'quantity': qty,
            'unit_price': price,
            'line_subtotal': line_sub,
            'discount': line_disc,
            'line_total': line_tot,
        })

    order_discount = Decimal(str(discount or '0.00')).quantize(Decimal('0.01'))
    if order_discount < Decimal('0.00'):
        raise ValidationError(_("Order discount cannot be negative."))
    if order_discount > items_total:
        raise ValidationError(
            _("Order discount (₹%(disc)s) cannot exceed total items value (₹%(items)s).") % {
                'disc': order_discount, 'items': items_total
            }
        )

    rate = Decimal(str(tax_rate if tax_rate is not None else '18.00')).quantize(Decimal('0.01'))
    if rate < Decimal('0.00') or rate > Decimal('100.00'):
        raise ValidationError(_("Tax rate must be between 0.00% and 100.00%."))

    taxable = max(items_total - order_discount, Decimal('0.00')).quantize(Decimal('0.01'))
    tax = (taxable * rate / Decimal('100.00')).quantize(Decimal('0.01'))
    net = (taxable + tax).quantize(Decimal('0.01'))

    return {
        'items': processed_items,
        'subtotal': subtotal,
        'items_total': items_total,
        'discount': order_discount,
        'taxable_amount': taxable,
        'tax_rate': rate,
        'tax_amount': tax,
        'net_amount': net,
    }


def create_sale_order(customer, sales_staff, notes="", tax_rate=Decimal('18.00'), discount=Decimal('0.00'), items_data=None, sale_date=None, request=None):
    """
    Creates a new Sale Order with line items in DRAFT status.
    Items data: list of dicts with keys: product, quantity, unit_price, discount
    """
    if not items_data:
        raise ValidationError(_("At least one sale item is required."))

    # Validate unique products
    products = [item['product'] for item in items_data]
    if len(products) != len(set(products)):
        raise ValidationError(_("Each product can only appear once in a sale order."))

    financials = calculate_financials(items_data, discount, tax_rate)

    with transaction.atomic():
        sale = Sale.objects.create(
            customer=customer,
            sales_staff=sales_staff,
            sale_date=sale_date or timezone.now().date(),
            status=Sale.Status.DRAFT,
            subtotal=financials['subtotal'],
            discount=financials['discount'],
            taxable_amount=financials['taxable_amount'],
            tax_rate=financials['tax_rate'],
            tax_amount=financials['tax_amount'],
            net_amount=financials['net_amount'],
            notes=notes or "",
            stock_deducted=False
        )

        for item in financials['items']:
            SaleItem.objects.create(
                sale=sale,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                line_subtotal=item['line_subtotal'],
                discount=item['discount'],
                line_total=item['line_total']
            )

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.CREATE,
                module_name="Sales - Orders",
                record_id=sale.sale_number,
                description=f"Created Sales Order {sale.sale_number} for customer {customer} (Net: ₹{sale.net_amount})."
            )

        return sale


def update_sale_order(sale, customer, sales_staff, notes="", tax_rate=Decimal('18.00'), discount=Decimal('0.00'), items_data=None, sale_date=None, request=None):
    """
    Updates an existing Sale Order. Can only update DRAFT orders.
    """
    if sale.status != Sale.Status.DRAFT:
        raise ValidationError(_("Only DRAFT sales orders can be modified."))

    if not items_data:
        raise ValidationError(_("At least one sale item is required."))

    products = [item['product'] for item in items_data]
    if len(products) != len(set(products)):
        raise ValidationError(_("Each product can only appear once in a sale order."))

    financials = calculate_financials(items_data, discount, tax_rate)

    with transaction.atomic():
        sale.customer = customer
        sale.sales_staff = sales_staff
        if sale_date:
            sale.sale_date = sale_date
        sale.notes = notes or ""
        sale.subtotal = financials['subtotal']
        sale.discount = financials['discount']
        sale.taxable_amount = financials['taxable_amount']
        sale.tax_rate = financials['tax_rate']
        sale.tax_amount = financials['tax_amount']
        sale.net_amount = financials['net_amount']
        sale.save()

        # Recreate items
        sale.items.all().delete()
        for item in financials['items']:
            SaleItem.objects.create(
                sale=sale,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                line_subtotal=item['line_subtotal'],
                discount=item['discount'],
                line_total=item['line_total']
            )

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Sales - Orders",
                record_id=sale.sale_number,
                description=f"Updated Sales Order {sale.sale_number} (Net: ₹{sale.net_amount})."
            )

        return sale


def confirm_sale_order(sale, user, due_days=15, request=None):
    """
    Confirms a DRAFT sale order:
    1. Row lock on sale and catalog items.
    2. Atomically deducts stock for PRODUCT items (using adjust_stock with type SALE).
    3. Sets stock_deducted=True and status=CONFIRMED.
    4. Automatically creates the Invoice with full customer billing and financial snapshot.
    5. Audit log.
    """
    with transaction.atomic():
        locked_sale = Sale.objects.select_for_update().get(pk=sale.pk)

        if locked_sale.status != Sale.Status.DRAFT:
            raise ValidationError(_("Only DRAFT sales orders can be confirmed."))

        items = list(locked_sale.items.select_related('product').all())
        if not items:
            raise ValidationError(_("Cannot confirm a sale order with no items."))

        # 1. Pre-check stock for all product items
        for item in items:
            if item.product.item_type == ProductService.ItemType.PRODUCT:
                prod = ProductService.objects.select_for_update().get(pk=item.product.pk)
                if prod.stock_quantity < item.quantity:
                    raise ValidationError(
                        _("Insufficient stock for %(name)s. Available: %(avail)d, Required: %(req)d.") % {
                            'name': prod.product_name,
                            'avail': prod.stock_quantity,
                            'req': item.quantity
                        }
                    )

        # 2. Deduct stock for all physical product items
        for item in items:
            if item.product.item_type == ProductService.ItemType.PRODUCT:
                adjust_stock(
                    product=item.product,
                    quantity_changed=-item.quantity,
                    adjustment_type=StockAdjustment.AdjustmentType.SALE,
                    reason=f"Sale order {locked_sale.sale_number} confirmed",
                    user=user,
                    request=request,
                    is_manual=False
                )

        # 3. Update sale state
        locked_sale.status = Sale.Status.CONFIRMED
        locked_sale.stock_deducted = True
        locked_sale.confirmed_at = timezone.now()
        locked_sale.save()

        # 4. Generate Invoice with billing snapshot
        customer = locked_sale.customer
        user_obj = customer.user
        cust_name = user_obj.full_name or user_obj.username
        cust_phone = user_obj.phone or ""
        cust_email = user_obj.email or ""
        cust_company = customer.company or ""
        b_addr = customer.address or ""
        b_city = customer.city or ""
        b_state = customer.state or ""
        b_postal = customer.postal_code or ""

        due_date = locked_sale.sale_date + timedelta(days=due_days)

        invoice = Invoice.objects.create(
            sale=locked_sale,
            customer=customer,
            invoice_date=locked_sale.sale_date,
            due_date=due_date,
            customer_name=cust_name,
            customer_email=cust_email,
            customer_phone=cust_phone,
            customer_company=cust_company,
            billing_address=b_addr,
            billing_city=b_city,
            billing_state=b_state,
            billing_postal_code=b_postal,
            subtotal=locked_sale.subtotal,
            discount=locked_sale.discount,
            taxable_amount=locked_sale.taxable_amount,
            tax_rate=locked_sale.tax_rate,
            tax_amount=locked_sale.tax_amount,
            total_amount=locked_sale.net_amount,
            paid_amount=Decimal('0.00'),
            balance_amount=locked_sale.net_amount,
            payment_status=Invoice.PaymentStatus.UNPAID,
            invoice_status=Invoice.InvoiceStatus.ISSUED
        )

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Sales - Orders",
                record_id=locked_sale.sale_number,
                description=f"Confirmed Sales Order {locked_sale.sale_number}. Deducted stock and generated Invoice {invoice.invoice_number}."
            )
            log_activity(
                request=request,
                action=ActivityLog.Action.CREATE,
                module_name="Sales - Invoices",
                record_id=invoice.invoice_number,
                description=f"Generated Invoice {invoice.invoice_number} for Sale {locked_sale.sale_number} (Total: ₹{invoice.total_amount})."
            )

        return locked_sale, invoice


def complete_sale_order(sale, user, request=None):
    """
    Marks a confirmed sale order as COMPLETED.
    Requires that the linked invoice is fully paid.
    """
    with transaction.atomic():
        locked_sale = Sale.objects.select_for_update().get(pk=sale.pk)

        if locked_sale.status != Sale.Status.CONFIRMED:
            raise ValidationError(_("Only CONFIRMED sales orders can be marked as completed."))

        if not hasattr(locked_sale, 'invoice') or locked_sale.invoice is None:
            raise ValidationError(_("Sale order has no invoice."))

        if locked_sale.invoice.payment_status != Invoice.PaymentStatus.PAID:
            raise ValidationError(
                _("Cannot complete sale. Invoice %(inv)s is not fully paid (Balance: ₹%(bal)s).") % {
                    'inv': locked_sale.invoice.invoice_number,
                    'bal': locked_sale.invoice.balance_amount
                }
            )

        locked_sale.status = Sale.Status.COMPLETED
        locked_sale.completed_at = timezone.now()
        locked_sale.save()

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Sales - Orders",
                record_id=locked_sale.sale_number,
                description=f"Marked Sales Order {locked_sale.sale_number} as COMPLETED."
            )

        return locked_sale


def cancel_sale_order(sale, cancellation_reason, user, request=None):
    """
    Cancels a DRAFT or CONFIRMED sale order:
    1. Row lock on sale and linked invoice.
    2. Rejects if order is COMPLETED or already CANCELLED.
    3. Rejects if Invoice has any completed, non-reversed payments.
    4. If stock was deducted (CONFIRMED), restores stock for all PRODUCT items (using SALE_REVERSAL).
    5. Sets stock_deducted=False, status=CANCELLED, cancelled_at=now, cancellation_reason=reason.
    6. If invoice exists, sets invoice_status=CANCELLED.
    7. Audit log.
    """
    if not (cancellation_reason or '').strip():
        raise ValidationError(_("A cancellation reason is required."))

    with transaction.atomic():
        locked_sale = Sale.objects.select_for_update().get(pk=sale.pk)

        if locked_sale.status == Sale.Status.CANCELLED:
            raise ValidationError(_("This sale order is already cancelled."))
        if locked_sale.status == Sale.Status.COMPLETED:
            raise ValidationError(_("Completed sales orders cannot be cancelled."))

        # Check payments
        if hasattr(locked_sale, 'invoice') and locked_sale.invoice:
            inv = locked_sale.invoice
            if inv.has_successful_payments:
                raise ValidationError(
                    _("Cannot cancel sale order %(sale)s: Invoice %(inv)s has active payments. Refund or reverse them first.") % {
                        'sale': locked_sale.sale_number,
                        'inv': inv.invoice_number
                    }
                )

        # Restore stock if deducted
        if locked_sale.stock_deducted:
            items = list(locked_sale.items.select_related('product').all())
            for item in items:
                if item.product.item_type == ProductService.ItemType.PRODUCT:
                    adjust_stock(
                        product=item.product,
                        quantity_changed=item.quantity,
                        adjustment_type=StockAdjustment.AdjustmentType.SALE_REVERSAL,
                        reason=f"Sale order {locked_sale.sale_number} cancelled: {cancellation_reason.strip()}",
                        user=user,
                        request=request,
                        is_manual=False
                    )
            locked_sale.stock_deducted = False

        locked_sale.status = Sale.Status.CANCELLED
        locked_sale.cancelled_at = timezone.now()
        locked_sale.cancellation_reason = cancellation_reason.strip()
        locked_sale.save()

        if hasattr(locked_sale, 'invoice') and locked_sale.invoice:
            inv = locked_sale.invoice
            inv.invoice_status = Invoice.InvoiceStatus.CANCELLED
            inv.save(update_fields=['invoice_status', 'updated_at'])

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Sales - Orders",
                record_id=locked_sale.sale_number,
                description=f"Cancelled Sales Order {locked_sale.sale_number}. Reason: {cancellation_reason.strip()}"
            )

        return locked_sale


def record_payment(invoice, amount, payment_method, transaction_reference, idempotency_key, user, notes="", request=None):
    """
    Safely records a payment against an Invoice:
    - Guaranteed server-side idempotency: checks if idempotency_key already exists.
      If it exists for the same invoice, returns the existing Payment without altering invoice totals twice.
      If it exists for a different invoice, raises ValidationError.
    - Locks invoice row with select_for_update().
    - Validates amount > 0 and <= balance_amount.
    - Non-cash payment requires transaction_reference.
    - Creates Payment with COMPLETED status and non-null idempotency_key.
    - Recalculates invoice.paid_amount and balance_amount strictly from COMPLETED, non-reversed payments.
    - Logs activity.
    """
    key = (idempotency_key or '').strip()
    if not key:
        raise ValidationError({'idempotency_key': _("Idempotency key is required.")})

    amt = Decimal(str(amount or '0.00')).quantize(Decimal('0.01'))
    if amt <= Decimal('0.00'):
        raise ValidationError({'amount': _("Payment amount must be greater than zero.")})

    with transaction.atomic():
        # Check idempotency first
        existing = Payment.objects.filter(idempotency_key=key).first()
        if existing:
            if existing.invoice_id == invoice.pk:
                # Idempotent response: return existing payment without duplicating
                return existing, False
            else:
                raise ValidationError({
                    'idempotency_key': _("This idempotency key has already been used for another payment.")
                })

        locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

        if locked_invoice.invoice_status == Invoice.InvoiceStatus.CANCELLED:
            raise ValidationError(_("Cannot record payment on a cancelled invoice."))

        if amt > locked_invoice.balance_amount:
            raise ValidationError({
                'amount': _("Payment amount (₹%(amt)s) cannot exceed invoice balance (₹%(bal)s).") % {
                    'amt': amt, 'bal': locked_invoice.balance_amount
                }
            })

        tx_ref = (transaction_reference or '').strip()
        if payment_method != Payment.Method.CASH and not tx_ref:
            raise ValidationError({
                'transaction_reference': _("Transaction reference is required for non-cash payments.")
            })

        if payment_method != Payment.Method.CASH and tx_ref:
            if Payment.objects.filter(payment_method=payment_method, transaction_reference=tx_ref).exists():
                raise ValidationError({
                    'transaction_reference': _("A payment with this %(method)s reference already exists.") % {
                        'method': dict(Payment.Method.choices).get(payment_method, payment_method)
                    }
                })

        payment = Payment.objects.create(
            invoice=locked_invoice,
            customer=locked_invoice.customer,
            amount=amt,
            payment_method=payment_method,
            transaction_reference=tx_ref,
            idempotency_key=key,
            payment_status=Payment.Status.COMPLETED,
            received_by=user if (user and user.is_authenticated) else None,
            notes=notes or "",
            is_reversed=False
        )

        # Update invoice totals
        locked_invoice.update_payment_balance(save=True)

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.CREATE,
                module_name="Sales - Payments",
                record_id=payment.payment_number,
                description=f"Recorded Payment {payment.payment_number} of ₹{payment.amount} for Invoice {locked_invoice.invoice_number} [{payment.get_payment_method_display()}]."
            )

        return payment, True


def reverse_payment(payment, reversal_reason, user, request=None):
    """
    Reverses an existing payment under strict controlled conditions:
    1. Only COMPLETED, non-reversed payments can be reversed.
    2. Requires non-empty reversal_reason.
    3. Sets status=REVERSED, is_reversed=True, reversed_at=now, reversed_by=user, reversal_reason=reason.
    4. Recalculates invoice.paid_amount and balance_amount (which excludes reversed payments).
    5. If linked sale was COMPLETED, demotes status to CONFIRMED because balance is now unpaid.
    6. Audit log.
    """
    reason = (reversal_reason or '').strip()
    if not reason:
        raise ValidationError(_("A reversal reason is required."))

    with transaction.atomic():
        locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)

        if locked_payment.is_reversed or locked_payment.payment_status == Payment.Status.REVERSED:
            raise ValidationError(_("This payment has already been reversed."))

        if locked_payment.payment_status != Payment.Status.COMPLETED:
            raise ValidationError(_("Only COMPLETED payments can be reversed."))

        locked_payment.payment_status = Payment.Status.REVERSED
        locked_payment.is_reversed = True
        locked_payment.reversed_at = timezone.now()
        locked_payment.reversed_by = user if (user and user.is_authenticated) else None
        locked_payment.reversal_reason = reason
        locked_payment.save()

        # Update invoice balance
        inv = locked_payment.invoice
        inv.update_payment_balance(save=True)

        # If sale was marked completed, demote to confirmed since invoice is no longer fully paid
        sale = inv.sale
        if sale.status == Sale.Status.COMPLETED and inv.balance_amount > Decimal('0.00'):
            sale.status = Sale.Status.CONFIRMED
            sale.save(update_fields=['status', 'updated_at'])

        if request:
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Sales - Payments",
                record_id=locked_payment.payment_number,
                description=f"Reversed Payment {locked_payment.payment_number} of ₹{locked_payment.amount} for Invoice {inv.invoice_number}. Reason: {reason}."
            )

        return locked_payment

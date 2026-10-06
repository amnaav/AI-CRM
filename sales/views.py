import json
import uuid
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, F, Count, Sum
from django.http import HttpResponseNotAllowed, Http404, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.mixins import AdminRequiredMixin, StaffRequiredMixin, CustomerRequiredMixin
from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from accounts.models import CustomerProfile, SalesStaffProfile
from catalog.models import ProductService
from sales.models import Sale, SaleItem, Invoice, Payment
from sales.forms import SaleForm, SaleItemFormSet, PaymentForm, PaymentReversalForm, SaleCancellationForm
from sales.services import (
    create_sale_order, update_sale_order, confirm_sale_order,
    complete_sale_order, cancel_sale_order, record_payment, reverse_payment
)


# ==============================================================================
# PERMISSION HELPERS
# ==============================================================================

def get_permitted_sale_or_403(user, pk):
    sale = get_object_or_404(Sale.objects.select_related('customer__user', 'sales_staff__user', 'invoice'), pk=pk)
    if user.is_admin:
        return sale
    if user.is_sales_staff:
        staff_profile = getattr(user, 'staff_profile', None)
        if staff_profile and (sale.sales_staff == staff_profile or sale.customer.assigned_staff == staff_profile):
            return sale
        raise PermissionDenied(_("You do not have permission to view or manage this sales order."))
    if user.is_customer:
        cust_profile = getattr(user, 'customer_profile', None)
        if cust_profile and sale.customer == cust_profile:
            return sale
        raise PermissionDenied(_("You do not have permission to view this sales order."))
    raise PermissionDenied(_("Unauthorized access."))


def get_permitted_invoice_or_403(user, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer__user', 'sale__sales_staff__user').prefetch_related('payments__received_by'),
        pk=pk
    )
    if user.is_admin:
        return invoice
    if user.is_sales_staff:
        staff_profile = getattr(user, 'staff_profile', None)
        if staff_profile and (invoice.sale.sales_staff == staff_profile or invoice.customer.assigned_staff == staff_profile):
            return invoice
        raise PermissionDenied(_("You do not have permission to view or manage this invoice."))
    if user.is_customer:
        cust_profile = getattr(user, 'customer_profile', None)
        if cust_profile and invoice.customer == cust_profile:
            return invoice
        raise PermissionDenied(_("You do not have permission to view this invoice."))
    raise PermissionDenied(_("Unauthorized access."))


# ==============================================================================
# SALES ORDER VIEWS (ADMIN & SALES STAFF)
# ==============================================================================

class SaleListView(StaffRequiredMixin, View):
    """
    Sales Orders Directory with Search, Multi-criteria Filtering, and Pagination.
    """
    template_name = 'sales/sale_list.html'

    def get(self, request):
        qs = Sale.objects.select_related('customer__user', 'sales_staff__user', 'invoice').all()

        # Scope for sales staff
        if request.user.is_sales_staff and not request.user.is_admin:
            staff = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(Q(sales_staff=staff) | Q(customer__assigned_staff=staff))

        # Search
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(sale_number__icontains=q) |
                Q(customer__user__first_name__icontains=q) |
                Q(customer__user__last_name__icontains=q) |
                Q(customer__company__icontains=q) |
                Q(sales_staff__user__first_name__icontains=q) |
                Q(invoice__invoice_number__icontains=q)
            )

        # Filters
        status = request.GET.get('status', '').strip()
        if status in dict(Sale.Status.choices):
            qs = qs.filter(status=status)

        customer_id = request.GET.get('customer', '').strip()
        if customer_id.isdigit():
            qs = qs.filter(customer_id=customer_id)

        staff_id = request.GET.get('staff', '').strip()
        if staff_id.isdigit():
            qs = qs.filter(sales_staff_id=staff_id)

        date_from = request.GET.get('date_from', '').strip()
        if date_from:
            qs = qs.filter(sale_date__gte=date_from)

        date_to = request.GET.get('date_to', '').strip()
        if date_to:
            qs = qs.filter(sale_date__lte=date_to)

        # Sorting
        sort = request.GET.get('sort', '-sale_date')
        allowed_sorts = ['sale_date', '-sale_date', 'net_amount', '-net_amount', 'created_at', '-created_at']
        if sort in allowed_sorts:
            qs = qs.order_by(sort)

        # Pagination
        paginator = Paginator(qs, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        # Metrics for badges
        total_sales_count = qs.count()
        total_revenue = qs.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED]).aggregate(
            rev=Sum('net_amount')
        )['rev'] or Decimal('0.00')

        context = {
            'sales': page_obj,
            'page_obj': page_obj,
            'search_query': q,
            'status_filter': status,
            'status_choices': Sale.Status.choices,
            'customer_filter': customer_id,
            'staff_filter': staff_id,
            'date_from': date_from,
            'date_to': date_to,
            'sort': sort,
            'total_sales_count': total_sales_count,
            'total_revenue': total_revenue,
            'customers': CustomerProfile.objects.filter(is_active=True).select_related('user').order_by('user__first_name'),
            'staff_list': SalesStaffProfile.objects.filter(user__is_active=True).select_related('user').order_by('user__first_name'),
            'page_title': _('Sales Orders'),
            'breadcrumbs': [
                {'title': _('Commerce & Finance'), 'url': ''},
                {'title': _('Sales Orders'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class SaleDetailView(StaffRequiredMixin, View):
    """
    Detailed view of a Sales Order, its line items, financials, and invoice state.
    """
    template_name = 'sales/sale_detail.html'

    def get(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        items = sale.items.select_related('product__category').all()

        cancel_form = SaleCancellationForm()

        context = {
            'sale': sale,
            'items': items,
            'cancel_form': cancel_form,
            'page_title': f"{sale.sale_number} - {_('Sales Order Details')}",
            'breadcrumbs': [
                {'title': _('Sales Orders'), 'url': '/sales/'},
                {'title': sale.sale_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


def _get_active_products_catalog():
    """Retrieve active product and service items for dynamic sales order calculations."""
    products = list(
        ProductService.objects.filter(status=ProductService.Status.ACTIVE).values(
            'id', 'product_name', 'product_code', 'item_type', 'unit_price', 'stock_quantity'
        )
    )
    for p in products:
        p['unit_price'] = float(p['unit_price'])
    return products


class SaleCreateView(StaffRequiredMixin, View):
    """
    Create a new Sales Order with dynamic line items in DRAFT status.
    """
    template_name = 'sales/sale_form.html'

    def get(self, request):
        form = SaleForm(user=request.user)
        formset = SaleItemFormSet(prefix='items')
        products_list = _get_active_products_catalog()

        context = {
            'form': form,
            'formset': formset,
            'products_list': products_list,
            'products_json': json.dumps(products_list),
            'page_title': _('New Sales Order'),
            'is_edit': False,
            'breadcrumbs': [
                {'title': _('Sales Orders'), 'url': '/sales/'},
                {'title': _('New Order'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request):
        form = SaleForm(request.POST, user=request.user)
        formset = SaleItemFormSet(request.POST, prefix='items')

        if form.is_valid() and formset.is_valid():
            customer = form.cleaned_data['customer']
            # If sales staff, assign self
            if request.user.is_sales_staff and not request.user.is_admin:
                sales_staff = request.user.staff_profile
            else:
                sales_staff = form.cleaned_data['sales_staff']

            notes = form.cleaned_data.get('notes', '')
            tax_rate = form.cleaned_data.get('tax_rate', Decimal('18.00'))
            discount = form.cleaned_data.get('discount', Decimal('0.00'))
            sale_date = form.cleaned_data.get('sale_date')

            items_data = []
            for item_form in formset.forms:
                if not item_form.cleaned_data or item_form.cleaned_data.get('DELETE'):
                    continue
                items_data.append({
                    'product': item_form.cleaned_data['product'],
                    'quantity': item_form.cleaned_data['quantity'],
                    'unit_price': item_form.cleaned_data['unit_price'],
                    'discount': item_form.cleaned_data.get('discount', Decimal('0.00'))
                })

            try:
                sale = create_sale_order(
                    customer=customer,
                    sales_staff=sales_staff,
                    notes=notes,
                    tax_rate=tax_rate,
                    discount=discount,
                    items_data=items_data,
                    sale_date=sale_date,
                    request=request
                )
                messages.success(request, _("Sales Order %(so)s created successfully in DRAFT status.") % {'so': sale.sale_number})
                return redirect('sales:sale_detail', pk=sale.pk)
            except ValidationError as e:
                if hasattr(e, 'message_dict'):
                    for field, errs in e.message_dict.items():
                        for err in errs:
                            messages.error(request, f"{field}: {err}")
                else:
                    messages.error(request, e.message or str(e))
            except Exception as e:
                messages.error(request, _("Error creating sales order: %(err)s") % {'err': str(e)})

        products_list = _get_active_products_catalog()
        context = {
            'form': form,
            'formset': formset,
            'products_list': products_list,
            'products_json': json.dumps(products_list),
            'page_title': _('New Sales Order'),
            'is_edit': False,
            'breadcrumbs': [
                {'title': _('Sales Orders'), 'url': '/sales/'},
                {'title': _('New Order'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class SaleUpdateView(StaffRequiredMixin, View):
    """
    Edit an existing DRAFT sales order.
    """
    template_name = 'sales/sale_form.html'

    def get(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        if sale.status != Sale.Status.DRAFT:
            messages.warning(request, _("Only DRAFT sales orders can be edited. This order is %(status)s.") % {'status': sale.get_status_display()})
            return redirect('sales:sale_detail', pk=sale.pk)

        form = SaleForm(instance=sale, user=request.user)
        initial_items = [
            {
                'product': item.product,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'discount': item.discount,
            }
            for item in sale.items.all()
        ]
        formset = SaleItemFormSet(initial=initial_items, prefix='items', extra=0)
        products_list = _get_active_products_catalog()

        context = {
            'form': form,
            'formset': formset,
            'sale': sale,
            'products_list': products_list,
            'products_json': json.dumps(products_list),
            'page_title': f"{_('Edit')} {sale.sale_number}",
            'is_edit': True,
            'breadcrumbs': [
                {'title': _('Sales Orders'), 'url': '/sales/'},
                {'title': sale.sale_number, 'url': f'/sales/{sale.pk}/'},
                {'title': _('Edit'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        if sale.status != Sale.Status.DRAFT:
            messages.error(request, _("Only DRAFT sales orders can be edited."))
            return redirect('sales:sale_detail', pk=sale.pk)

        form = SaleForm(request.POST, instance=sale, user=request.user)
        formset = SaleItemFormSet(request.POST, prefix='items')

        if form.is_valid() and formset.is_valid():
            customer = form.cleaned_data['customer']
            if request.user.is_sales_staff and not request.user.is_admin:
                sales_staff = request.user.staff_profile
            else:
                sales_staff = form.cleaned_data['sales_staff']

            notes = form.cleaned_data.get('notes', '')
            tax_rate = form.cleaned_data.get('tax_rate', Decimal('18.00'))
            discount = form.cleaned_data.get('discount', Decimal('0.00'))
            sale_date = form.cleaned_data.get('sale_date')

            items_data = []
            for item_form in formset.forms:
                if not item_form.cleaned_data or item_form.cleaned_data.get('DELETE'):
                    continue
                items_data.append({
                    'product': item_form.cleaned_data['product'],
                    'quantity': item_form.cleaned_data['quantity'],
                    'unit_price': item_form.cleaned_data['unit_price'],
                    'discount': item_form.cleaned_data.get('discount', Decimal('0.00'))
                })

            try:
                sale = update_sale_order(
                    sale=sale,
                    customer=customer,
                    sales_staff=sales_staff,
                    notes=notes,
                    tax_rate=tax_rate,
                    discount=discount,
                    items_data=items_data,
                    sale_date=sale_date,
                    request=request
                )
                messages.success(request, _("Sales Order %(so)s updated successfully.") % {'so': sale.sale_number})
                return redirect('sales:sale_detail', pk=sale.pk)
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, 'message') else str(e))
            except Exception as e:
                messages.error(request, _("Error updating sales order: %(err)s") % {'err': str(e)})

        products_list = _get_active_products_catalog()
        context = {
            'form': form,
            'formset': formset,
            'sale': sale,
            'products_list': products_list,
            'products_json': json.dumps(products_list),
            'page_title': f"{_('Edit')} {sale.sale_number}",
            'is_edit': True,
            'breadcrumbs': [
                {'title': _('Sales Orders'), 'url': '/sales/'},
                {'title': sale.sale_number, 'url': f'/sales/{sale.pk}/'},
                {'title': _('Edit'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class SaleConfirmView(StaffRequiredMixin, View):
    """
    POST-only action: Confirms a DRAFT sales order, deducts stock, and generates invoice.
    """
    def post(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        try:
            sale, invoice = confirm_sale_order(sale=sale, user=request.user, request=request)
            messages.success(
                request,
                _("Sales Order %(so)s confirmed! Stock deducted and Invoice %(inv)s generated.") % {
                    'so': sale.sale_number, 'inv': invoice.invoice_number
                }
            )
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, 'message') else str(e))
        except Exception as e:
            messages.error(request, _("Confirmation failed: %(err)s") % {'err': str(e)})

        return redirect('sales:sale_detail', pk=sale.pk)


class SaleCompleteView(StaffRequiredMixin, View):
    """
    POST-only action: Marks a CONFIRMED, fully-paid sales order as COMPLETED.
    """
    def post(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        try:
            sale = complete_sale_order(sale=sale, user=request.user, request=request)
            messages.success(request, _("Sales Order %(so)s marked as COMPLETED.") % {'so': sale.sale_number})
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, 'message') else str(e))
        except Exception as e:
            messages.error(request, _("Completion failed: %(err)s") % {'err': str(e)})

        return redirect('sales:sale_detail', pk=sale.pk)


class SaleCancelView(StaffRequiredMixin, View):
    """
    POST-only action: Cancels a DRAFT or CONFIRMED sales order, restoring stock.
    """
    def post(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        form = SaleCancellationForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['cancellation_reason']
            try:
                sale = cancel_sale_order(sale=sale, cancellation_reason=reason, user=request.user, request=request)
                messages.success(request, _("Sales Order %(so)s has been cancelled and any deducted stock has been restored.") % {'so': sale.sale_number})
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, 'message') else str(e))
            except Exception as e:
                messages.error(request, _("Cancellation failed: %(err)s") % {'err': str(e)})
        else:
            messages.error(request, _("Please provide a valid cancellation reason."))

        return redirect('sales:sale_detail', pk=sale.pk)


# ==============================================================================
# INVOICE VIEWS (ADMIN & SALES STAFF)
# ==============================================================================

class InvoiceListView(StaffRequiredMixin, View):
    """
    Invoice directory with payment status filters, overdue tracking, and search.
    """
    template_name = 'sales/invoice_list.html'

    def get(self, request):
        qs = Invoice.objects.select_related('customer__user', 'sale__sales_staff__user').all()

        if request.user.is_sales_staff and not request.user.is_admin:
            staff = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(Q(sale__sales_staff=staff) | Q(customer__assigned_staff=staff))

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(invoice_number__icontains=q) |
                Q(customer_name__icontains=q) |
                Q(sale__sale_number__icontains=q)
            )

        p_status = request.GET.get('payment_status', '').strip()
        if p_status in dict(Invoice.PaymentStatus.choices):
            qs = qs.filter(payment_status=p_status)

        inv_status = request.GET.get('invoice_status', '').strip()
        if inv_status in dict(Invoice.InvoiceStatus.choices):
            qs = qs.filter(invoice_status=inv_status)

        date_from = request.GET.get('date_from', '').strip()
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)

        date_to = request.GET.get('date_to', '').strip()
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)

        paginator = Paginator(qs, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        # Summary KPIs
        kpis = qs.aggregate(
            total_billed=Sum('total_amount'),
            total_collected=Sum('paid_amount'),
            total_outstanding=Sum('balance_amount')
        )

        context = {
            'invoices': page_obj,
            'page_obj': page_obj,
            'search_query': q,
            'payment_status_filter': p_status,
            'payment_status_choices': Invoice.PaymentStatus.choices,
            'invoice_status_filter': inv_status,
            'invoice_status_choices': Invoice.InvoiceStatus.choices,
            'date_from': date_from,
            'date_to': date_to,
            'total_billed': kpis['total_billed'] or Decimal('0.00'),
            'total_collected': kpis['total_collected'] or Decimal('0.00'),
            'total_outstanding': kpis['total_outstanding'] or Decimal('0.00'),
            'page_title': _('Invoices & Billing'),
            'breadcrumbs': [
                {'title': _('Commerce & Finance'), 'url': ''},
                {'title': _('Invoices'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class InvoiceDetailView(StaffRequiredMixin, View):
    """
    Detailed view of an Invoice with snapshot addresses, items, and payments.
    """
    template_name = 'sales/invoice_detail.html'

    def get(self, request, pk):
        invoice = get_permitted_invoice_or_403(request.user, pk)
        payments = invoice.payments.select_related('received_by', 'reversed_by').order_by('-payment_date')
        reversal_form = PaymentReversalForm()

        context = {
            'invoice': invoice,
            'sale': invoice.sale,
            'items': invoice.sale.items.select_related('product').all(),
            'payments': payments,
            'reversal_form': reversal_form,
            'page_title': f"{invoice.invoice_number} - {_('Invoice Details')}",
            'breadcrumbs': [
                {'title': _('Invoices'), 'url': '/sales/invoices/'},
                {'title': invoice.invoice_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class InvoicePrintView(StaffRequiredMixin, View):
    """
    Clean printable tax invoice format.
    """
    template_name = 'sales/invoice_print.html'

    def get(self, request, pk):
        invoice = get_permitted_invoice_or_403(request.user, pk)
        items = invoice.sale.items.select_related('product').all()
        payments = invoice.payments.filter(payment_status=Payment.Status.COMPLETED, is_reversed=False)

        context = {
            'invoice': invoice,
            'sale': invoice.sale,
            'items': items,
            'payments': payments,
            'page_title': f"{_('Print Invoice')} - {invoice.invoice_number}",
        }
        return render(request, self.template_name, context)


# ==============================================================================
# PAYMENT VIEWS (ADMIN & SALES STAFF)
# ==============================================================================

class PaymentListView(StaffRequiredMixin, View):
    """
    Payment transactions directory.
    """
    template_name = 'sales/payment_list.html'

    def get(self, request):
        qs = Payment.objects.select_related('invoice__customer__user', 'customer__user', 'received_by', 'reversed_by').all()

        if request.user.is_sales_staff and not request.user.is_admin:
            staff = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(Q(invoice__sale__sales_staff=staff) | Q(customer__assigned_staff=staff))

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(payment_number__icontains=q) |
                Q(invoice__invoice_number__icontains=q) |
                Q(customer__user__first_name__icontains=q) |
                Q(transaction_reference__icontains=q)
            )

        method = request.GET.get('method', '').strip()
        if method in dict(Payment.Method.choices):
            qs = qs.filter(payment_method=method)

        status = request.GET.get('status', '').strip()
        if status in dict(Payment.Status.choices):
            qs = qs.filter(payment_status=status)

        paginator = Paginator(qs, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        reversal_form = PaymentReversalForm()

        context = {
            'payments': page_obj,
            'page_obj': page_obj,
            'search_query': q,
            'method_filter': method,
            'method_choices': Payment.Method.choices,
            'status_filter': status,
            'status_choices': Payment.Status.choices,
            'reversal_form': reversal_form,
            'page_title': _('Payment Transactions'),
            'breadcrumbs': [
                {'title': _('Commerce & Finance'), 'url': ''},
                {'title': _('Payments'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class PaymentCreateView(StaffRequiredMixin, View):
    """
    Record a new payment for a specific invoice with server-side idempotency.
    """
    template_name = 'sales/payment_form.html'

    def get(self, request, invoice_pk):
        invoice = get_permitted_invoice_or_403(request.user, invoice_pk)
        if invoice.balance_amount <= Decimal('0.00'):
            messages.info(request, _("Invoice %(inv)s is already fully paid.") % {'inv': invoice.invoice_number})
            return redirect('sales:invoice_detail', pk=invoice.pk)

        if invoice.invoice_status == Invoice.InvoiceStatus.CANCELLED:
            messages.error(request, _("Cannot record payment on a cancelled invoice."))
            return redirect('sales:invoice_detail', pk=invoice.pk)

        payment_instance = Payment(invoice=invoice, customer=invoice.customer)
        form = PaymentForm(
            invoice=invoice,
            instance=payment_instance,
            max_amount=invoice.balance_amount,
            initial={'amount': invoice.balance_amount, 'idempotency_key': uuid.uuid4().hex}
        )

        context = {
            'form': form,
            'invoice': invoice,
            'page_title': f"{_('Record Payment')} - {invoice.invoice_number}",
            'breadcrumbs': [
                {'title': _('Invoices'), 'url': '/sales/invoices/'},
                {'title': invoice.invoice_number, 'url': f'/sales/invoices/{invoice.pk}/'},
                {'title': _('Record Payment'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request, invoice_pk):
        invoice = get_permitted_invoice_or_403(request.user, invoice_pk)
        payment_instance = Payment(invoice=invoice, customer=invoice.customer)
        form = PaymentForm(
            request.POST,
            invoice=invoice,
            instance=payment_instance,
            max_amount=invoice.balance_amount
        )
        # Ensure the selected invoice is assigned to the unsaved Payment instance
        # before any model validation, property access, or save operation
        form.instance.invoice = invoice
        form.instance.customer = invoice.customer

        if form.is_valid():
            amount = form.cleaned_data['amount']
            method = form.cleaned_data['payment_method']
            tx_ref = form.cleaned_data.get('transaction_reference', '')
            notes = form.cleaned_data.get('notes', '')
            key = form.cleaned_data['idempotency_key']

            try:
                payment, created = record_payment(
                    invoice=invoice,
                    amount=amount,
                    payment_method=method,
                    transaction_reference=tx_ref,
                    idempotency_key=key,
                    user=request.user,
                    notes=notes,
                    request=request
                )
                if created:
                    messages.success(
                        request,
                        _("Payment %(num)s of ₹%(amt)s recorded successfully!") % {
                            'num': payment.payment_number, 'amt': payment.amount
                        }
                    )
                else:
                    messages.info(
                        request,
                        _("Duplicate submission recognized. Displaying recorded payment %(num)s.") % {
                            'num': payment.payment_number
                        }
                    )
                return redirect('sales:payment_detail', pk=payment.pk)
            except ValidationError as e:
                if hasattr(e, 'message_dict'):
                    for field, errs in e.message_dict.items():
                        for err in errs:
                            if field in form.fields:
                                form.add_error(field, err)
                            else:
                                form.add_error(None, err)
                            messages.error(request, f"{field}: {err}")
                elif hasattr(e, 'messages'):
                    for err in e.messages:
                        form.add_error(None, err)
                        messages.error(request, err)
                else:
                    form.add_error(None, e.message if hasattr(e, 'message') else str(e))
                    messages.error(request, e.message if hasattr(e, 'message') else str(e))
            except Exception as e:
                messages.error(request, _("Payment processing failed: %(err)s") % {'err': str(e)})

        context = {
            'form': form,
            'invoice': invoice,
            'page_title': f"{_('Record Payment')} - {invoice.invoice_number}",
            'breadcrumbs': [
                {'title': _('Invoices'), 'url': '/sales/invoices/'},
                {'title': invoice.invoice_number, 'url': f'/sales/invoices/{invoice.pk}/'},
                {'title': _('Record Payment'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class PaymentDetailView(StaffRequiredMixin, View):
    """
    Receipt view of a payment record.
    """
    template_name = 'sales/payment_detail.html'

    def get(self, request, pk):
        payment = get_object_or_404(
            Payment.objects.select_related('invoice__customer__user', 'customer__user', 'received_by', 'reversed_by'),
            pk=pk
        )
        # Check permissions
        if not request.user.is_admin:
            staff = getattr(request.user, 'staff_profile', None)
            if not staff or (payment.invoice.sale.sales_staff != staff and payment.customer.assigned_staff != staff):
                raise PermissionDenied(_("You do not have permission to view this payment."))

        reversal_form = PaymentReversalForm()

        context = {
            'payment': payment,
            'invoice': payment.invoice,
            'reversal_form': reversal_form,
            'page_title': f"{payment.payment_number} - {_('Payment Receipt')}",
            'breadcrumbs': [
                {'title': _('Payments'), 'url': '/sales/payments/'},
                {'title': payment.payment_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class PaymentReceiptPrintView(StaffRequiredMixin, View):
    """
    Clean printable payment receipt page.
    """
    template_name = 'sales/payment_receipt_print.html'

    def get(self, request, pk):
        payment = get_object_or_404(
            Payment.objects.select_related('invoice__customer__user', 'customer__user', 'received_by'),
            pk=pk
        )
        if not request.user.is_admin:
            staff = getattr(request.user, 'staff_profile', None)
            if not staff or (payment.invoice.sale.sales_staff != staff and payment.customer.assigned_staff != staff):
                raise PermissionDenied(_("You do not have permission to view this receipt."))

        context = {
            'payment': payment,
            'invoice': payment.invoice,
            'page_title': f"{_('Print Receipt')} - {payment.payment_number}",
        }
        return render(request, self.template_name, context)


class PaymentReverseView(AdminRequiredMixin, View):
    """
    POST-only action: Admin reversal of a completed payment.
    """
    def post(self, request, pk):
        payment = get_object_or_404(Payment.objects.select_related('invoice__sale'), pk=pk)
        form = PaymentReversalForm(request.POST)

        if form.is_valid():
            reason = form.cleaned_data['reversal_reason']
            try:
                payment = reverse_payment(payment=payment, reversal_reason=reason, user=request.user, request=request)
                messages.success(
                    request,
                    _("Payment %(num)s of ₹%(amt)s was successfully reversed.") % {
                        'num': payment.payment_number, 'amt': payment.amount
                    }
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, 'message') else str(e))
            except Exception as e:
                messages.error(request, _("Reversal failed: %(err)s") % {'err': str(e)})
        else:
            messages.error(request, _("A valid reversal reason must be provided."))

        return redirect('sales:payment_detail', pk=payment.pk)


# ==============================================================================
# CUSTOMER PORTAL VIEWS (READ-ONLY FINANCIAL PAGES)
# ==============================================================================

class CustomerSaleListView(CustomerRequiredMixin, View):
    """
    Customer portal: View own purchases and sales orders.
    """
    template_name = 'sales/customer/sale_list.html'

    def get(self, request):
        cust = getattr(request.user, 'customer_profile', None)
        if not cust:
            messages.error(request, _("No customer profile found."))
            return redirect('dashboard:customer_dashboard')

        sales = Sale.objects.filter(customer=cust).select_related('invoice').order_by('-sale_date')
        paginator = Paginator(sales, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'sales': page_obj,
            'page_obj': page_obj,
            'page_title': _('My Purchases & Orders'),
            'breadcrumbs': [
                {'title': _('My Portal'), 'url': '/dashboard/customer/'},
                {'title': _('My Purchases'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerSaleDetailView(CustomerRequiredMixin, View):
    """
    Customer portal: View single purchase details.
    """
    template_name = 'sales/customer/sale_detail.html'

    def get(self, request, pk):
        sale = get_permitted_sale_or_403(request.user, pk)
        items = sale.items.select_related('product').all()

        context = {
            'sale': sale,
            'items': items,
            'page_title': f"{_('Order')} {sale.sale_number}",
            'breadcrumbs': [
                {'title': _('My Purchases'), 'url': '/sales/portal/purchases/'},
                {'title': sale.sale_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerInvoiceListView(CustomerRequiredMixin, View):
    """
    Customer portal: View own invoices and payment statuses.
    """
    template_name = 'sales/customer/invoice_list.html'

    def get(self, request):
        cust = getattr(request.user, 'customer_profile', None)
        if not cust:
            messages.error(request, _("No customer profile found."))
            return redirect('dashboard:customer_dashboard')

        invoices = Invoice.objects.filter(customer=cust).select_related('sale').order_by('-invoice_date')
        paginator = Paginator(invoices, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'invoices': page_obj,
            'page_obj': page_obj,
            'page_title': _('My Invoices'),
            'breadcrumbs': [
                {'title': _('My Portal'), 'url': '/dashboard/customer/'},
                {'title': _('My Invoices'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerInvoiceDetailView(CustomerRequiredMixin, View):
    """
    Customer portal: View invoice breakdown and payments.
    """
    template_name = 'sales/customer/invoice_detail.html'

    def get(self, request, pk):
        invoice = get_permitted_invoice_or_403(request.user, pk)
        items = invoice.sale.items.select_related('product').all()
        payments = invoice.payments.filter(payment_status=Payment.Status.COMPLETED, is_reversed=False).order_by('-payment_date')

        context = {
            'invoice': invoice,
            'sale': invoice.sale,
            'items': items,
            'payments': payments,
            'page_title': f"{_('Invoice')} {invoice.invoice_number}",
            'breadcrumbs': [
                {'title': _('My Invoices'), 'url': '/sales/portal/invoices/'},
                {'title': invoice.invoice_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerPaymentListView(CustomerRequiredMixin, View):
    """
    Customer portal: View payment receipts history.
    """
    template_name = 'sales/customer/payment_list.html'

    def get(self, request):
        cust = getattr(request.user, 'customer_profile', None)
        if not cust:
            messages.error(request, _("No customer profile found."))
            return redirect('dashboard:customer_dashboard')

        payments = Payment.objects.filter(customer=cust).select_related('invoice').order_by('-payment_date')
        paginator = Paginator(payments, 10)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'payments': page_obj,
            'page_obj': page_obj,
            'page_title': _('Payment Receipts'),
            'breadcrumbs': [
                {'title': _('My Portal'), 'url': '/dashboard/customer/'},
                {'title': _('Payment Receipts'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerPaymentDetailView(CustomerRequiredMixin, View):
    """
    Customer portal: View payment receipt details.
    """
    template_name = 'sales/customer/payment_detail.html'

    def get(self, request, pk):
        payment = get_object_or_404(Payment.objects.select_related('invoice', 'customer__user'), pk=pk)
        if payment.customer.user != request.user:
            raise PermissionDenied(_("You do not have permission to view this receipt."))

        context = {
            'payment': payment,
            'invoice': payment.invoice,
            'page_title': f"{_('Receipt')} {payment.payment_number}",
            'breadcrumbs': [
                {'title': _('Payment Receipts'), 'url': '/sales/portal/payments/'},
                {'title': payment.payment_number, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

import uuid
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.forms import formset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile, SalesStaffProfile
from catalog.models import ProductService
from sales.models import Sale, SaleItem, Invoice, Payment


class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ['customer', 'sales_staff', 'sale_date', 'tax_rate', 'discount', 'notes']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select select2-enable'}),
            'sales_staff': forms.Select(attrs={'class': 'form-select'}),
            'sale_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': _('Optional notes or terms...')}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter active customers
        self.fields['customer'].queryset = CustomerProfile.objects.filter(
            is_active=True, user__is_active=True
        ).select_related('user').order_by('user__first_name', 'company')

        self.fields['sales_staff'].queryset = SalesStaffProfile.objects.filter(
            user__is_active=True
        ).select_related('user').order_by('user__first_name')

        if not self.instance.pk:
            self.fields['sale_date'].initial = timezone.now().date()
            self.fields['tax_rate'].initial = Decimal('18.00')
            self.fields['discount'].initial = Decimal('0.00')

        # If user is sales staff, restrict customers to assigned and lock staff field
        if user and user.is_sales_staff:
            try:
                staff_profile = user.staff_profile
                self.fields['customer'].queryset = self.fields['customer'].queryset.filter(assigned_staff=staff_profile)
                self.fields['sales_staff'].initial = staff_profile
                self.fields['sales_staff'].widget = forms.HiddenInput()
                self.fields['sales_staff'].required = False
            except Exception:
                pass

    def clean_tax_rate(self):
        rate = self.cleaned_data.get('tax_rate')
        if rate is not None and (rate < Decimal('0.00') or rate > Decimal('100.00')):
            raise ValidationError(_('Tax rate must be between 0.00% and 100.00%.'))
        return rate

    def clean_discount(self):
        disc = self.cleaned_data.get('discount')
        if disc is not None and disc < Decimal('0.00'):
            raise ValidationError(_('Order discount cannot be negative.'))
        return disc


class SaleItemForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=ProductService.objects.filter(status=ProductService.Status.ACTIVE).order_by('product_name'),
        widget=forms.Select(attrs={'class': 'form-select product-select'}),
        empty_label=_("-- Select Catalog Item --")
    )
    quantity = forms.IntegerField(
        min_value=1, initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-control item-qty', 'min': '1', 'step': '1'})
    )
    unit_price = forms.DecimalField(
        min_value=Decimal('0.00'), decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control item-price', 'step': '0.01', 'min': '0'})
    )
    discount = forms.DecimalField(
        min_value=Decimal('0.00'), decimal_places=2, initial=Decimal('0.00'), required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control item-discount', 'step': '0.01', 'min': '0'})
    )
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def clean(self):
        cleaned_data = super().clean()
        qty = cleaned_data.get('quantity')
        price = cleaned_data.get('unit_price')
        disc = cleaned_data.get('discount') or Decimal('0.00')

        if qty and price is not None:
            sub = (Decimal(qty) * price).quantize(Decimal('0.01'))
            if disc > sub:
                self.add_error('discount', _('Line discount (₹%(disc)s) cannot exceed line subtotal (₹%(sub)s).') % {
                    'disc': disc, 'sub': sub
                })
        return cleaned_data


class BaseSaleItemFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        valid_items = []
        products_seen = set()

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue

            product = form.cleaned_data.get('product')
            if not product:
                continue

            if product.pk in products_seen:
                form.add_error('product', _("Duplicate product in order: %(name)s is already listed.") % {
                    'name': product.product_name
                })
            else:
                products_seen.add(product.pk)
                valid_items.append(form.cleaned_data)

        if not valid_items:
            raise ValidationError(_("At least one product or service line item is required."))


SaleItemFormSet = formset_factory(SaleItemForm, formset=BaseSaleItemFormSet, extra=1, can_delete=True)


class PaymentForm(forms.ModelForm):
    idempotency_key = forms.CharField(widget=forms.HiddenInput(), required=True)

    class Meta:
        model = Payment
        fields = ['amount', 'payment_method', 'transaction_reference', 'notes', 'idempotency_key']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01', 'placeholder': '0.00'}),
            'payment_method': forms.Select(attrs={'class': 'form-select', 'id': 'id_payment_method'}),
            'transaction_reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('UTR / Cheque No / Reference ID')}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': _('Optional payment remarks...')}),
        }

    def __init__(self, *args, invoice=None, max_amount=None, **kwargs):
        if invoice is not None and 'instance' not in kwargs:
            kwargs['instance'] = Payment(invoice=invoice, customer=getattr(invoice, 'customer', None))
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if self.invoice is None and self.instance:
            try:
                self.invoice = self.instance.invoice
            except ObjectDoesNotExist:
                self.invoice = None

        if invoice is not None and self.instance:
            self.instance.invoice = invoice
            if hasattr(invoice, 'customer') and invoice.customer:
                self.instance.customer = invoice.customer

        if max_amount is None and self.invoice is not None:
            max_amount = self.invoice.balance_amount
        self.max_amount = max_amount

        if not self.initial.get('idempotency_key'):
            self.initial['idempotency_key'] = uuid.uuid4().hex

        if self.max_amount is not None:
            self.fields['amount'].widget.attrs['max'] = str(self.max_amount)
            self.fields['amount'].help_text = _("Remaining balance: ₹%(bal)s") % {'bal': self.max_amount}

    def _post_clean(self):
        """
        Preserve server-side idempotency:
        If a payment with this idempotency key already exists for this invoice,
        bypass model full_clean constraint/unique checks that would fail on
        resubmitting the identical completed payment. The record_payment service
        handles returning the existing payment without duplicating.
        """
        key = self.cleaned_data.get('idempotency_key')
        invoice = self.invoice
        if not invoice and self.instance:
            try:
                invoice = self.instance.invoice
            except ObjectDoesNotExist:
                invoice = None

        if key and invoice:
            existing = Payment.objects.filter(idempotency_key=key).first()
            if existing and existing.invoice_id == invoice.pk:
                return
        super()._post_clean()

    def validate_unique(self):
        """
        Preserve server-side idempotency:
        If a payment with this idempotency key already exists for this invoice,
        bypass unique constraint checks on the form so record_payment service
        can handle the duplicate submission idempotently.
        """
        key = self.cleaned_data.get('idempotency_key')
        invoice = self.invoice
        if not invoice and self.instance:
            try:
                invoice = self.instance.invoice
            except ObjectDoesNotExist:
                invoice = None

        if key and invoice:
            existing = Payment.objects.filter(idempotency_key=key).first()
            if existing and existing.invoice_id == invoice.pk:
                return
        super().validate_unique()

    def clean(self):
        cleaned_data = super().clean()
        amt = cleaned_data.get('amount')
        method = cleaned_data.get('payment_method')
        tx_ref = (cleaned_data.get('transaction_reference') or '').strip()

        if amt is not None and amt <= Decimal('0.00'):
            self.add_error('amount', _('Payment amount must be greater than zero.'))

        max_amt = self.max_amount
        if max_amt is None and self.invoice is not None:
            max_amt = self.invoice.balance_amount

        if max_amt is not None and amt is not None and amt > max_amt:
            self.add_error('amount', _('Payment amount cannot exceed the invoice balance of ₹%(bal)s.') % {
                'bal': max_amt
            })

        if method != Payment.Method.CASH and not tx_ref:
            self.add_error('transaction_reference', _('Transaction reference is required for non-cash payments.'))

        return cleaned_data


class PaymentReversalForm(forms.Form):
    reversal_reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': _('Provide the specific reason for reversing this payment...')}),
        required=True,
        help_text=_("Mandatory justification for reversing and voiding this payment record.")
    )

    def clean_reversal_reason(self):
        reason = self.cleaned_data.get('reversal_reason', '').strip()
        if not reason:
            raise ValidationError(_('A non-empty reversal reason is required.'))
        return reason


class SaleCancellationForm(forms.Form):
    cancellation_reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': _('Provide the specific reason for cancelling this sales order...')}),
        required=True,
        help_text=_("Mandatory justification for cancelling the sales order and restoring stock.")
    )

    def clean_cancellation_reason(self):
        reason = self.cleaned_data.get('cancellation_reason', '').strip()
        if not reason:
            raise ValidationError(_('A non-empty cancellation reason is required.'))
        return reason

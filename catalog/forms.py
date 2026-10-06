from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower, Trim
from django.utils.translation import gettext_lazy as _

from catalog.models import Category, ProductService, StockAdjustment
from catalog.services import validate_catalog_image


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['category_name', 'description', 'status']
        widgets = {
            'category_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('e.g. Enterprise Hardware, Cloud Hosting'),
                'required': True,
                'autocomplete': 'off'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Describe this category...')
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
        }

    def clean_category_name(self):
        name = self.cleaned_data.get('category_name', '')
        name = name.strip() if name else ''
        if not name:
            raise ValidationError(_('Category name cannot be empty or whitespace only.'))

        # Check case-and-whitespace-insensitive uniqueness
        qs = Category.objects.annotate(
            norm_name=Lower(Trim('category_name'))
        ).filter(norm_name=name.lower())

        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise ValidationError(_('A category with this name already exists (case and whitespace insensitive).'))

        return name


class ProductServiceForm(forms.ModelForm):
    class Meta:
        model = ProductService
        fields = [
            'product_name', 'item_type', 'category', 'description',
            'unit_price', 'stock_quantity', 'reorder_level', 'image', 'status'
        ]
        widgets = {
            'product_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('e.g. Dell PowerEdge Server, Annual SLA Support'),
                'required': True
            }),
            'item_type': forms.Select(attrs={
                'class': 'form-select',
                'id': 'id_item_type'
            }),
            'category': forms.Select(attrs={
                'class': 'form-select'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Detailed specifications, features, and scope...')
            }),
            'unit_price': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': '0.00'
            }),
            'stock_quantity': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'id': 'id_stock_quantity'
            }),
            'reorder_level': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'id': 'id_reorder_level'
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/jpeg,image/png,image/webp'
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Categories: show active categories, or keep current category if editing an item
        if self.instance and self.instance.pk:
            self.fields['category'].queryset = Category.objects.filter(
                models_q_filter := forms.models.Q(status=Category.Status.ACTIVE) | forms.models.Q(pk=self.instance.category_id)
            ).order_by('category_name')
            # If item has sales history, disallow direct uncontrolled editing of stock_quantity
            if self.instance.has_sales_history:
                self.fields['stock_quantity'].disabled = True
                self.fields['stock_quantity'].help_text = _(
                    'Direct stock modification is disabled because this item has sales history. Use the Stock Adjustment form.'
                )
        else:
            self.fields['category'].queryset = Category.objects.filter(
                status=Category.Status.ACTIVE
            ).order_by('category_name')

    def clean_product_name(self):
        name = self.cleaned_data.get('product_name', '')
        return name.strip() if name else ''

    def clean_unit_price(self):
        price = self.cleaned_data.get('unit_price')
        if price is not None and price < Decimal('0.00'):
            raise ValidationError(_('Unit price cannot be negative.'))
        return price

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and hasattr(image, 'file'):
            validate_catalog_image(image)
        return image

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get('item_type')
        stock_quantity = cleaned_data.get('stock_quantity')
        reorder_level = cleaned_data.get('reorder_level')

        # If editing an item with sales history, retain existing stock_quantity
        if self.instance and self.instance.pk and self.instance.has_sales_history:
            cleaned_data['stock_quantity'] = self.instance.stock_quantity

        if item_type == ProductService.ItemType.SERVICE:
            cleaned_data['stock_quantity'] = 0
            cleaned_data['reorder_level'] = 0
        else:
            if stock_quantity is not None and stock_quantity < 0:
                self.add_error('stock_quantity', _('Stock quantity cannot be negative.'))
            if reorder_level is not None and reorder_level < 0:
                self.add_error('reorder_level', _('Reorder level cannot be negative.'))

        return cleaned_data


class StockAdjustmentForm(forms.Form):
    """
    Form for manual stock adjustments by Administrators.
    Excludes the SALE adjustment type which is strictly automated.
    """
    MANUAL_ADJUSTMENT_TYPES = [
        (k, v) for k, v in StockAdjustment.AdjustmentType.choices
        if k != StockAdjustment.AdjustmentType.SALE
    ]

    adjustment_type = forms.ChoiceField(
        choices=MANUAL_ADJUSTMENT_TYPES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Adjustment Reason / Type')
    )
    quantity_changed = forms.IntegerField(
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': _('e.g. 15 to add stock, or -5 to deduct')
        }),
        label=_('Quantity Change'),
        help_text=_('Enter positive (+) number to add stock, or negative (-) number to deduct.')
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': _('Explain why this adjustment is being made (e.g. PO-981 Restock, Damaged in transit, Physical count audit)...')
        }),
        required=True,
        label=_('Detailed Reason / Memo')
    )

    def __init__(self, product, *args, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

    def clean_quantity_changed(self):
        qty_change = self.cleaned_data.get('quantity_changed')
        if qty_change == 0:
            raise ValidationError(_('Adjustment quantity cannot be zero.'))

        if self.product.item_type != ProductService.ItemType.PRODUCT:
            raise ValidationError(_('Services do not carry physical stock.'))

        resulting_stock = self.product.stock_quantity + qty_change
        if resulting_stock < 0:
            raise ValidationError(
                _('Insufficient stock. Current quantity is %(current)d. Cannot deduct %(deduct)d.'),
                params={'current': self.product.stock_quantity, 'deduct': abs(qty_change)}
            )
        return qty_change

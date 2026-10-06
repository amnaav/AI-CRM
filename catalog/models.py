import os
import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models.functions import Lower, Trim
from django.utils.translation import gettext_lazy as _


def catalog_image_upload_path(instance, filename):
    """
    Generate a secure, randomized filename using UUID4 to prevent
    path traversal and file overwrites.
    """
    ext = os.path.splitext(filename)[1].lower()
    return f"catalog/images/{uuid.uuid4().hex}{ext}"


class Category(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        INACTIVE = 'INACTIVE', _('Inactive')

    category_name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['category_name']
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('category_name')),
                name='unique_category_name_ci_trim'
            )
        ]

    def clean(self):
        super().clean()
        if self.category_name:
            norm_name = self.category_name.strip().lower()
            if not norm_name:
                raise ValidationError({'category_name': _('Category name cannot be empty or whitespace only.')})
            qs = Category.objects.annotate(
                norm=Lower(Trim('category_name'))
            ).filter(norm=norm_name)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'category_name': _('A category with this name already exists (case and whitespace insensitive).')})

    def save(self, *args, **kwargs):
        if self.category_name:
            self.category_name = self.category_name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.category_name


class ProductService(models.Model):
    class ItemType(models.TextChoices):
        PRODUCT = 'PRODUCT', _('Physical Product')
        SERVICE = 'SERVICE', _('Service / Consulting')

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        INACTIVE = 'INACTIVE', _('Inactive')

    product_code = models.CharField(max_length=30, unique=True, db_index=True)
    product_name = models.CharField(max_length=200)
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.PRODUCT)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    stock_quantity = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)]
    )
    reorder_level = models.IntegerField(
        default=5,
        validators=[MinValueValidator(0)]
    )
    image = models.ImageField(upload_to=catalog_image_upload_path, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product_name']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name='chk_product_unit_price_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(stock_quantity__gte=0),
                name='chk_product_stock_qty_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(reorder_level__gte=0),
                name='chk_product_reorder_level_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(item_type='PRODUCT') | (
                    models.Q(item_type='SERVICE', stock_quantity=0, reorder_level=0)
                ),
                name='chk_service_zero_stock_and_reorder'
            ),
        ]

    def clean(self):
        super().clean()
        if self.unit_price is not None and self.unit_price < Decimal('0.00'):
            raise ValidationError({'unit_price': _('Unit price cannot be negative.')})
        if self.item_type == self.ItemType.SERVICE:
            self.stock_quantity = 0
            self.reorder_level = 0
        else:
            if self.stock_quantity is not None and self.stock_quantity < 0:
                raise ValidationError({'stock_quantity': _('Stock quantity cannot be negative.')})
            if self.reorder_level is not None and self.reorder_level < 0:
                raise ValidationError({'reorder_level': _('Reorder level cannot be negative.')})

    def save(self, *args, **kwargs):
        if self.product_name:
            self.product_name = self.product_name.strip()
        if self.item_type == self.ItemType.SERVICE:
            self.stock_quantity = 0
            self.reorder_level = 0
        if not self.product_code:
            prefix = 'PRD' if self.item_type == self.ItemType.PRODUCT else 'SRV'
            while True:
                code = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
                if not ProductService.objects.filter(product_code=code).exists():
                    self.product_code = code
                    break
        super().save(*args, **kwargs)

    @property
    def is_out_of_stock(self):
        return self.item_type == self.ItemType.PRODUCT and self.stock_quantity == 0

    @property
    def is_low_stock(self):
        return self.item_type == self.ItemType.PRODUCT and 0 < self.stock_quantity <= self.reorder_level

    @property
    def is_in_stock(self):
        return self.item_type == self.ItemType.PRODUCT and self.stock_quantity > self.reorder_level

    @property
    def stock_status(self):
        if self.item_type != self.ItemType.PRODUCT:
            return None
        if self.stock_quantity == 0:
            return 'OUT_OF_STOCK'
        if self.stock_quantity <= self.reorder_level:
            return 'LOW_STOCK'
        return 'IN_STOCK'

    @property
    def has_sales_history(self):
        return self.sale_items.exists()

    def __str__(self):
        return f"{self.product_name} ({self.product_code}) - ₹{self.unit_price}"


class StockAdjustment(models.Model):
    class AdjustmentType(models.TextChoices):
        RESTOCK = 'RESTOCK', _('Restock / Stock In')
        DAMAGE = 'DAMAGE', _('Damage / Write-Off')
        CORRECTION = 'CORRECTION', _('Inventory Correction')
        RETURN = 'RETURN', _('Customer Return')
        SALE = 'SALE', _('Sale Deduction')
        SALE_REVERSAL = 'SALE_REVERSAL', _('Sale Reversal / Cancellation')
        OTHER = 'OTHER', _('Other Adjustment')

    product = models.ForeignKey(
        ProductService,
        on_delete=models.PROTECT,
        related_name='stock_adjustments'
    )
    adjustment_type = models.CharField(
        max_length=20,
        choices=AdjustmentType.choices,
        default=AdjustmentType.CORRECTION
    )
    quantity_changed = models.IntegerField(help_text=_("Signed difference in stock quantity."))
    quantity_before = models.IntegerField()
    quantity_after = models.IntegerField()
    reason = models.TextField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_adjustments'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Stock Adjustment')
        verbose_name_plural = _('Stock Adjustments')
        indexes = [
            models.Index(fields=['-created_at'], name='idx_stock_adj_created_at'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_before__gte=0),
                name='chk_stock_adj_qty_before_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_after__gte=0),
                name='chk_stock_adj_qty_after_non_negative'
            ),
            models.CheckConstraint(
                condition=~models.Q(quantity_changed=0),
                name='chk_stock_adj_qty_changed_not_zero'
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_after=models.F('quantity_before') + models.F('quantity_changed')),
                name='chk_stock_adj_math_correct'
            ),
        ]

    def __str__(self):
        actor = self.user.username if self.user else "System"
        return f"{self.product.product_code} - {self.get_adjustment_type_display()} ({self.quantity_changed:+d}) by {actor}"

import io
import os
from decimal import Decimal
from PIL import Image
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from catalog.models import ProductService, StockAdjustment

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_PILLOW_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def validate_catalog_image(image_file):
    """
    Validate uploaded image file:
    1. Checks file size (<= 5 MB).
    2. Checks file extension (.jpg, .jpeg, .png, .webp).
    3. Uses Pillow to verify the actual file payload is a valid image in an allowed format.
    4. Resets the file pointer to 0.
    """
    if not image_file:
        return

    # 1. Size check
    if hasattr(image_file, 'size') and image_file.size > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError(
            _('Image file size must not exceed %(max_mb)s MB. Current size: %(size_mb).2f MB.'),
            params={
                'max_mb': MAX_IMAGE_SIZE_BYTES / (1024 * 1024),
                'size_mb': image_file.size / (1024 * 1024)
            }
        )

    # 2. Extension check
    filename = getattr(image_file, 'name', '')
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            _('Unsupported file extension "%(ext)s". Allowed formats: JPEG, PNG, WebP.'),
            params={'ext': ext}
        )

    # 3. Content inspection with Pillow
    try:
        image_file.seek(0)
        img = Image.open(image_file)
        img.verify()
        if img.format not in ALLOWED_PILLOW_FORMATS:
            raise ValidationError(
                _('Invalid image format "%(fmt)s". Allowed formats: JPEG, PNG, WebP.'),
                params={'fmt': img.format}
            )
    except Exception as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(_('The uploaded file is not a valid or corrupted image.')) from e
    finally:
        image_file.seek(0)


def adjust_stock(product, quantity_changed, adjustment_type, reason, user, request=None, is_manual=True):
    """
    Safely adjusts product stock in an atomic database transaction.
    - Uses select_for_update() to prevent race conditions and lost concurrent updates.
    - Rejects stock adjustments for SERVICE items.
    - Rejects manual adjustments with type SALE.
    - Rejects zero-quantity adjustments.
    - Rejects adjustments that would cause stock to become negative.
    - Creates an immutable StockAdjustment record.
    - Logs the event in ActivityLog.
    """
    if quantity_changed == 0:
        raise ValidationError(_('Stock adjustment quantity cannot be zero.'))

    if is_manual and adjustment_type == StockAdjustment.AdjustmentType.SALE:
        raise ValidationError(_('Manual stock adjustment cannot use the SALE adjustment type.'))

    if adjustment_type not in dict(StockAdjustment.AdjustmentType.choices):
        raise ValidationError(_('Invalid adjustment type.'))

    with transaction.atomic():
        # Row lock on the product to guarantee concurrency safety
        locked_product = ProductService.objects.select_for_update().get(pk=product.pk)

        if locked_product.item_type != ProductService.ItemType.PRODUCT:
            raise ValidationError(_('Stock adjustments cannot be applied to Services.'))

        qty_before = locked_product.stock_quantity
        qty_after = qty_before + quantity_changed

        if qty_after < 0:
            raise ValidationError(
                _('Insufficient stock. Current stock is %(current)d, cannot reduce by %(reduce)d.'),
                params={'current': qty_before, 'reduce': abs(quantity_changed)}
            )

        # Update product stock
        locked_product.stock_quantity = qty_after
        locked_product.save(update_fields=['stock_quantity', 'updated_at'])

        # Create immutable stock adjustment audit history
        adjustment = StockAdjustment.objects.create(
            product=locked_product,
            adjustment_type=adjustment_type,
            quantity_changed=quantity_changed,
            quantity_before=qty_before,
            quantity_after=qty_after,
            reason=reason.strip() if reason else '',
            user=user if (user and user.is_authenticated) else None
        )

        # Audit log in ActivityLog
        if request:
            action_desc = (
                f"Stock adjusted for {locked_product.product_code} ({locked_product.product_name}): "
                f"{quantity_changed:+d} units [{adjustment.get_adjustment_type_display()}]. "
                f"Before: {qty_before}, After: {qty_after}. Reason: {reason or 'N/A'}"
            )
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Catalog - Stock",
                record_id=locked_product.product_code,
                description=action_desc
            )

        return adjustment

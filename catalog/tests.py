import io
from decimal import Decimal
from PIL import Image

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from accounts.models import CustomUser
from activity_logs.models import ActivityLog
from catalog.models import Category, ProductService, StockAdjustment
from catalog.services import adjust_stock, validate_catalog_image
from catalog.admin import StockAdjustmentAdmin
from django.contrib.admin.sites import AdminSite

User = get_user_model()


def create_test_image(format='PNG', size=(50, 50), color='blue'):
    """Generate in-memory image bytes for test uploads."""
    file_obj = io.BytesIO()
    image = Image.new('RGB', size, color=color)
    image.save(file_obj, format=format)
    file_obj.seek(0)
    return file_obj


class CategoryModelAndConstraintTests(TestCase):
    """Test Category model, trimming, case-insensitive uniqueness, and soft deactivation."""

    def test_category_creation_and_string_representation(self):
        cat = Category.objects.create(category_name="Hardware", description="Physical hardware")
        self.assertEqual(str(cat), "Hardware")
        self.assertEqual(cat.status, Category.Status.ACTIVE)

    def test_category_name_trimmed_on_save(self):
        cat = Category.objects.create(category_name="   Networking Equipment   ")
        self.assertEqual(cat.category_name, "Networking Equipment")

    def test_category_case_insensitive_uniqueness(self):
        Category.objects.create(category_name="Cloud Services")
        # Direct save of lowercase equivalent should violate database constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(category_name="cloud services")

    def test_category_whitespace_and_case_uniqueness(self):
        Category.objects.create(category_name="Security Solutions")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(category_name="   security solutions   ")

    def test_category_clean_validation(self):
        Category.objects.create(category_name="Consulting")
        duplicate = Category(category_name="  consulting  ")
        with self.assertRaises(ValidationError):
            duplicate.clean()

    def test_empty_or_whitespace_category_name_rejected(self):
        cat = Category(category_name="   ")
        with self.assertRaises(ValidationError):
            cat.clean()

    def test_deactivating_category_preserves_linked_items(self):
        cat = Category.objects.create(category_name="Software")
        prod = ProductService.objects.create(
            product_name="CRM License",
            item_type=ProductService.ItemType.PRODUCT,
            category=cat,
            unit_price=Decimal("1500.00"),
            stock_quantity=10,
            reorder_level=2,
            status=ProductService.Status.ACTIVE
        )
        cat.status = Category.Status.INACTIVE
        cat.save()

        prod.refresh_from_db()
        self.assertEqual(cat.status, Category.Status.INACTIVE)
        # Linked product status must remain unchanged
        self.assertEqual(prod.status, ProductService.Status.ACTIVE)

    def test_category_with_products_protected_from_db_deletion(self):
        cat = Category.objects.create(category_name="Databases")
        ProductService.objects.create(
            product_name="PostgreSQL Support",
            item_type=ProductService.ItemType.SERVICE,
            category=cat,
            unit_price=Decimal("5000.00")
        )
        with self.assertRaises(ProtectedError):
            cat.delete()


class ProductServiceModelAndConstraintTests(TestCase):
    """Test ProductService creation, item codes, prices, and stock constraints."""

    def setUp(self):
        self.category = Category.objects.create(category_name="Test Hardware")

    def test_product_creation_and_auto_code(self):
        prod = ProductService.objects.create(
            product_name="Server Rack",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category,
            unit_price=Decimal("45000.00"),
            stock_quantity=8,
            reorder_level=2
        )
        self.assertTrue(prod.product_code.startswith("PRD-"))
        self.assertIn("Server Rack", str(prod))

    def test_service_creation_auto_code_and_forced_zero_stock(self):
        srv = ProductService.objects.create(
            product_name="Cloud Migration Consulting",
            item_type=ProductService.ItemType.SERVICE,
            category=self.category,
            unit_price=Decimal("12000.00"),
            stock_quantity=15,  # Attempting to assign stock
            reorder_level=5
        )
        self.assertTrue(srv.product_code.startswith("SRV-"))
        # Service stock and reorder level must be forced to 0
        self.assertEqual(srv.stock_quantity, 0)
        self.assertEqual(srv.reorder_level, 0)

    def test_service_zero_stock_database_check_constraint(self):
        """Direct database insertion with nonzero stock on SERVICE must fail constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductService.objects.bulk_create([
                    ProductService(
                        product_code="SRV-FAIL01",
                        product_name="Invalid Service",
                        item_type=ProductService.ItemType.SERVICE,
                        category=self.category,
                        unit_price=Decimal("100.00"),
                        stock_quantity=5,  # Violates chk_service_zero_stock_and_reorder
                        reorder_level=0
                    )
                ])

    def test_negative_unit_price_rejected_by_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductService.objects.bulk_create([
                    ProductService(
                        product_code="PRD-NEG01",
                        product_name="Negative Item",
                        item_type=ProductService.ItemType.PRODUCT,
                        category=self.category,
                        unit_price=Decimal("-10.00"),
                        stock_quantity=0,
                        reorder_level=0
                    )
                ])

    def test_negative_stock_quantity_rejected_by_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductService.objects.bulk_create([
                    ProductService(
                        product_code="PRD-NEG02",
                        product_name="Negative Stock Item",
                        item_type=ProductService.ItemType.PRODUCT,
                        category=self.category,
                        unit_price=Decimal("10.00"),
                        stock_quantity=-5,
                        reorder_level=0
                    )
                ])

    def test_product_stock_status_properties(self):
        prod = ProductService.objects.create(
            product_name="Monitor",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category,
            unit_price=Decimal("15000.00"),
            stock_quantity=10,
            reorder_level=5
        )
        # In stock: quantity (10) > reorder (5)
        self.assertTrue(prod.is_in_stock)
        self.assertFalse(prod.is_low_stock)
        self.assertFalse(prod.is_out_of_stock)
        self.assertEqual(prod.stock_status, 'IN_STOCK')

        # Low stock: 0 < quantity (4) <= reorder (5)
        prod.stock_quantity = 4
        prod.save()
        self.assertFalse(prod.is_in_stock)
        self.assertTrue(prod.is_low_stock)
        self.assertFalse(prod.is_out_of_stock)
        self.assertEqual(prod.stock_status, 'LOW_STOCK')

        # Out of stock: quantity == 0
        prod.stock_quantity = 0
        prod.save()
        self.assertFalse(prod.is_in_stock)
        self.assertFalse(prod.is_low_stock)
        self.assertTrue(prod.is_out_of_stock)
        self.assertEqual(prod.stock_status, 'OUT_OF_STOCK')


class StockAdjustmentAndHistoryTests(TestCase):
    """Test stock adjustment service, atomic transactions, history logging, and constraints."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='stock_admin',
            email='stock_admin@example.com',
            password='Password123!',
            role=CustomUser.Role.ADMIN
        )
        self.category = Category.objects.create(category_name="Components")
        self.product = ProductService.objects.create(
            product_name="SSD 1TB",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category,
            unit_price=Decimal("8000.00"),
            stock_quantity=20,
            reorder_level=5
        )

    def test_stock_adjustment_addition(self):
        adj = adjust_stock(
            product=self.product,
            quantity_changed=15,
            adjustment_type=StockAdjustment.AdjustmentType.RESTOCK,
            reason="PO-402 Delivery received",
            user=self.admin
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 35)
        self.assertEqual(adj.quantity_before, 20)
        self.assertEqual(adj.quantity_after, 35)
        self.assertEqual(adj.quantity_changed, 15)
        self.assertEqual(adj.adjustment_type, StockAdjustment.AdjustmentType.RESTOCK)

    def test_stock_adjustment_deduction(self):
        adj = adjust_stock(
            product=self.product,
            quantity_changed=-8,
            adjustment_type=StockAdjustment.AdjustmentType.DAMAGE,
            reason="Water damage in warehouse",
            user=self.admin
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 12)
        self.assertEqual(adj.quantity_before, 20)
        self.assertEqual(adj.quantity_after, 12)
        self.assertEqual(adj.quantity_changed, -8)

    def test_negative_stock_prevention(self):
        """Attempting to deduct more stock than available must fail validation and rollback."""
        with self.assertRaises(ValidationError):
            adjust_stock(
                product=self.product,
                quantity_changed=-25,
                adjustment_type=StockAdjustment.AdjustmentType.CORRECTION,
                reason="Excessive deduction test",
                user=self.admin
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 20)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_zero_quantity_adjustment_rejected(self):
        with self.assertRaises(ValidationError):
            adjust_stock(
                product=self.product,
                quantity_changed=0,
                adjustment_type=StockAdjustment.AdjustmentType.CORRECTION,
                reason="Zero change test",
                user=self.admin
            )

    def test_manual_adjustment_cannot_select_sale(self):
        with self.assertRaises(ValidationError):
            adjust_stock(
                product=self.product,
                quantity_changed=-2,
                adjustment_type=StockAdjustment.AdjustmentType.SALE,
                reason="Manual SALE deduction",
                user=self.admin,
                is_manual=True
            )

    def test_service_stock_adjustment_rejected(self):
        service = ProductService.objects.create(
            product_name="Setup Consulting",
            item_type=ProductService.ItemType.SERVICE,
            category=self.category,
            unit_price=Decimal("2000.00")
        )
        with self.assertRaises(ValidationError):
            adjust_stock(
                product=service,
                quantity_changed=5,
                adjustment_type=StockAdjustment.AdjustmentType.RESTOCK,
                reason="Attempt to restock service",
                user=self.admin
            )

    def test_stock_adjustment_database_constraints(self):
        """Test database level check constraints on StockAdjustment model."""
        # 1. quantity_changed != 0
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockAdjustment.objects.create(
                    product=self.product,
                    adjustment_type=StockAdjustment.AdjustmentType.CORRECTION,
                    quantity_changed=0,
                    quantity_before=10,
                    quantity_after=10,
                    reason="Invalid zero"
                )

        # 2. quantity_after = quantity_before + quantity_changed math check
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockAdjustment.objects.create(
                    product=self.product,
                    adjustment_type=StockAdjustment.AdjustmentType.RESTOCK,
                    quantity_changed=5,
                    quantity_before=10,
                    quantity_after=20,  # Invalid math (10 + 5 != 20)
                    reason="Invalid math"
                )

        # 3. quantity_after >= 0
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockAdjustment.objects.create(
                    product=self.product,
                    adjustment_type=StockAdjustment.AdjustmentType.DAMAGE,
                    quantity_changed=-15,
                    quantity_before=10,
                    quantity_after=-5,  # Invalid negative
                    reason="Invalid negative after"
                )

    def test_stock_history_protected_from_product_deletion(self):
        adjust_stock(
            product=self.product,
            quantity_changed=5,
            adjustment_type=StockAdjustment.AdjustmentType.RESTOCK,
            reason="Initial restock",
            user=self.admin
        )
        # Attempting to delete product with stock adjustment history must raise ProtectedError
        with self.assertRaises(ProtectedError):
            self.product.delete()

    def test_stock_adjustment_admin_is_read_only(self):
        admin_site = AdminSite()
        adj_admin = StockAdjustmentAdmin(StockAdjustment, admin_site)
        self.assertFalse(adj_admin.has_add_permission(None))
        self.assertFalse(adj_admin.has_change_permission(None))
        self.assertFalse(adj_admin.has_delete_permission(None))


class ImageValidationTests(TestCase):
    """Test image upload validation, content inspection with Pillow, and UUID naming."""

    def test_valid_jpeg_and_png_accepted(self):
        img_io = create_test_image(format='JPEG')
        uploaded = SimpleUploadedFile("product_photo.jpg", img_io.read(), content_type="image/jpeg")
        # Should not raise
        validate_catalog_image(uploaded)

        img_png_io = create_test_image(format='PNG')
        uploaded_png = SimpleUploadedFile("product_photo.png", img_png_io.read(), content_type="image/png")
        validate_catalog_image(uploaded_png)

    def test_invalid_extension_rejected(self):
        img_io = create_test_image(format='PNG')
        uploaded = SimpleUploadedFile("script.exe", img_io.read(), content_type="application/octet-stream")
        with self.assertRaises(ValidationError) as ctx:
            validate_catalog_image(uploaded)
        self.assertIn("Unsupported file extension", str(ctx.exception))

    def test_fake_image_content_rejected(self):
        """A text/script file masquerading as a .jpg must be detected and rejected."""
        fake_content = b"<?php echo 'malicious code'; ?>"
        uploaded = SimpleUploadedFile("exploit.jpg", fake_content, content_type="image/jpeg")
        with self.assertRaises(ValidationError) as ctx:
            validate_catalog_image(uploaded)
        self.assertIn("not a valid or corrupted image", str(ctx.exception))

    def test_oversized_image_rejected(self):
        # Create a dummy file with large size attribute
        uploaded = SimpleUploadedFile("huge.jpg", b"0" * 100, content_type="image/jpeg")
        uploaded.size = 6 * 1024 * 1024  # 6 MB (> 5 MB limit)
        with self.assertRaises(ValidationError) as ctx:
            validate_catalog_image(uploaded)
        self.assertIn("must not exceed", str(ctx.exception))


class CatalogRolePermissionsAndViewsTests(TestCase):
    """Test role access, permissions, views, and activity logging."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='admin_user',
            email='admin@example.com',
            password='Password123!',
            role=CustomUser.Role.ADMIN
        )
        self.staff = User.objects.create_user(
            username='staff_user',
            email='staff@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        self.customer = User.objects.create_user(
            username='customer_user',
            email='customer@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER
        )

        self.cat_active = Category.objects.create(category_name="Active Category", status=Category.Status.ACTIVE)
        self.cat_inactive = Category.objects.create(category_name="Inactive Category", status=Category.Status.INACTIVE)

        self.item_active = ProductService.objects.create(
            product_name="Active Product",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.cat_active,
            unit_price=Decimal("999.00"),
            stock_quantity=20,
            reorder_level=5,
            status=ProductService.Status.ACTIVE
        )
        self.item_inactive = ProductService.objects.create(
            product_name="Inactive Product",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.cat_active,
            unit_price=Decimal("499.00"),
            stock_quantity=0,
            reorder_level=0,
            status=ProductService.Status.INACTIVE
        )

    # --- ANONYMOUS ACCESS ---
    def test_anonymous_redirected_to_login(self):
        urls = [
            reverse('catalog:product_list'),
            reverse('catalog:product_create'),
            reverse('catalog:product_detail', kwargs={'pk': self.item_active.pk}),
            reverse('catalog:category_list'),
            reverse('catalog:customer_catalog'),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"URL {url} did not redirect anonymous user.")
            self.assertIn('/accounts/login/', resp.url)

    # --- ADMIN ACCESS ---
    def test_admin_can_access_category_and_product_management(self):
        self.client.force_login(self.admin)

        resp = self.client.get(reverse('catalog:category_list'))
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Product")
        self.assertContains(resp, "Inactive Product")

    def test_admin_create_category_and_activity_log(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('catalog:category_create'), {
            'category_name': 'Storage Solutions',
            'description': 'Storage description',
            'status': 'ACTIVE'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Category.objects.filter(category_name='Storage Solutions').exists())
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CREATE, module_name="Catalog - Category").exists())

    def test_admin_create_product_and_activity_log(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('catalog:product_create'), {
            'product_name': 'Enterprise Router',
            'item_type': 'PRODUCT',
            'category': self.cat_active.pk,
            'description': 'High speed router',
            'unit_price': '25000.00',
            'stock_quantity': '10',
            'reorder_level': '2',
            'status': 'ACTIVE'
        })
        self.assertEqual(resp.status_code, 302)
        item = ProductService.objects.get(product_name='Enterprise Router')
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CREATE, record_id=item.product_code).exists())

    def test_admin_toggle_status_requires_post(self):
        self.client.force_login(self.admin)
        # GET is rejected with 405 Method Not Allowed
        resp = self.client.get(reverse('catalog:product_toggle_status', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 405)

        # POST toggles status
        resp = self.client.post(reverse('catalog:product_toggle_status', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 302)
        self.item_active.refresh_from_db()
        self.assertEqual(self.item_active.status, ProductService.Status.INACTIVE)

    def test_admin_stock_adjustment_flow(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('catalog:product_stock_adjust', kwargs={'pk': self.item_active.pk}),
            {
                'adjustment_type': StockAdjustment.AdjustmentType.RESTOCK,
                'quantity_changed': 10,
                'reason': 'Shipment Batch #201'
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.item_active.refresh_from_db()
        self.assertEqual(self.item_active.stock_quantity, 30)
        self.assertTrue(StockAdjustment.objects.filter(product=self.item_active, quantity_changed=10).exists())

    # --- SALES STAFF ACCESS ---
    def test_sales_staff_read_only_active_items(self):
        self.client.force_login(self.staff)

        # Can view product catalog
        resp = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Product")
        self.assertNotContains(resp, "Inactive Product")  # Sales staff cannot see inactive items

        # Can view active product detail
        resp = self.client.get(reverse('catalog:product_detail', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 200)
        # Stock adjustment form and history table must NOT be visible to sales staff
        self.assertNotContains(resp, "Stock Adjustment Audit History")
        self.assertNotContains(resp, "Adjust Inventory")

        # Inactive item gives 404
        resp = self.client.get(reverse('catalog:product_detail', kwargs={'pk': self.item_inactive.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_sales_staff_cannot_create_edit_or_adjust_stock(self):
        self.client.force_login(self.staff)

        # Cannot access Category management (403 Forbidden)
        resp = self.client.get(reverse('catalog:category_list'))
        self.assertEqual(resp.status_code, 403)

        # Cannot access item creation (403)
        resp = self.client.get(reverse('catalog:product_create'))
        self.assertEqual(resp.status_code, 403)

        # Cannot edit item (403)
        resp = self.client.get(reverse('catalog:product_update', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 403)

        # Cannot post stock adjustment (403)
        resp = self.client.post(
            reverse('catalog:product_stock_adjust', kwargs={'pk': self.item_active.pk}),
            {'adjustment_type': 'RESTOCK', 'quantity_changed': 5, 'reason': 'Staff attempt'}
        )
        self.assertEqual(resp.status_code, 403)

    # --- CUSTOMER PORTAL ACCESS ---
    def test_customer_portal_sees_only_active_items_from_active_categories(self):
        self.client.force_login(self.customer)

        # Item under inactive category
        item_in_inactive_cat = ProductService.objects.create(
            product_name="Product in Inactive Cat",
            item_type=ProductService.ItemType.PRODUCT,
            category=self.cat_inactive,
            unit_price=Decimal("100.00"),
            status=ProductService.Status.ACTIVE
        )

        resp = self.client.get(reverse('catalog:customer_catalog'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Product")
        self.assertNotContains(resp, "Inactive Product")
        self.assertNotContains(resp, "Product in Inactive Cat")

        # Customer viewing active product detail
        resp = self.client.get(reverse('catalog:customer_product_detail', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 200)
        # Verify internal inventory controls are hidden
        self.assertNotContains(resp, "Reorder threshold")
        self.assertNotContains(resp, "Current Inventory")

        # Customer cannot access internal management URLs
        resp = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(reverse('catalog:product_detail', kwargs={'pk': self.item_active.pk}))
        self.assertEqual(resp.status_code, 403)

    # --- SEARCH, FILTERS, SORTING, PAGINATION ---
    def test_catalog_search_and_filters(self):
        self.client.force_login(self.admin)

        # Search by name
        resp = self.client.get(reverse('catalog:product_list') + '?q=Active')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Product")

        # Search by code
        resp = self.client.get(reverse('catalog:product_list') + f'?q={self.item_active.product_code}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Product")

        # Filter by stock condition (out_of_stock)
        resp = self.client.get(reverse('catalog:product_list') + '?stock=out_of_stock')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Inactive Product")
        self.assertNotContains(resp, "Active Product")

        # Sorting by price
        resp = self.client.get(reverse('catalog:product_list') + '?sort=price_desc')
        self.assertEqual(resp.status_code, 200)

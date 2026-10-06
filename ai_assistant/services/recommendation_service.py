from datetime import timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum, Avg, F
from django.utils import timezone

from accounts.models import CustomUser, CustomerProfile
from catalog.models import ProductService, Category
from sales.models import Sale, SaleItem


def get_recommendations_for_customer(
    customer_profile: CustomerProfile,
    requesting_user: CustomUser,
    limit: int = 5
) -> Dict[str, Any]:
    """
    Computes explainable, deterministic product/service recommendations for a customer.
    Enforces strict role and ownership scope.
    Guarantees reproducible ordering and offline execution without external AI APIs.
    """
    if not customer_profile or not customer_profile.is_active:
        return {
            'customer': None,
            'recommendations': [],
            'engine': 'Deterministic Rule-Based Recommendation Engine',
            'explanation': 'No active customer profile found.',
        }

    # 1. Authorization and Scope Check
    if requesting_user.is_admin:
        pass  # Admin can request recommendations for any customer
    elif requesting_user.is_sales_staff:
        # Sales staff can only access assigned customers
        if not hasattr(requesting_user, 'staff_profile') or customer_profile.assigned_staff_id != requesting_user.staff_profile.id:
            raise PermissionDenied("You are not authorized to view recommendations for this customer.")
    elif requesting_user.is_customer:
        # Customer can only access their own profile
        if not hasattr(requesting_user, 'customer_profile') or customer_profile.id != requesting_user.customer_profile.id:
            raise PermissionDenied("You are not authorized to view recommendations for other customers.")
    else:
        raise PermissionDenied("Unauthorized user role.")

    today = timezone.localdate()
    cutoff_30_days = today - timedelta(days=30)
    cutoff_180_days = today - timedelta(days=180)

    # 2. Identify products purchased in the last 30 days to avoid recent duplicates
    recent_purchases_qs = SaleItem.objects.filter(
        sale__customer=customer_profile,
        sale__status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED],
        sale__sale_date__gte=cutoff_30_days
    ).values_list('product_id', flat=True)
    recently_bought_product_ids = set(recent_purchases_qs)

    # 3. Retrieve Candidate Products & Services
    # Must belong to active categories and be active itself
    # Physical products must have stock_quantity > 0; services are eligible with 0 stock
    candidate_qs = ProductService.objects.filter(
        status=ProductService.Status.ACTIVE,
        category__status=Category.Status.ACTIVE
    ).select_related('category').exclude(
        id__in=recently_bought_product_ids
    )

    eligible_candidates = []
    for item in candidate_qs:
        if item.item_type == ProductService.ItemType.PRODUCT:
            if item.stock_quantity > 0:
                eligible_candidates.append(item)
        else:
            # Services are eligible regardless of physical stock
            eligible_candidates.append(item)

    if not eligible_candidates:
        return {
            'customer': {
                'code': customer_profile.customer_code,
                'name': customer_profile.user.full_name or customer_profile.user.username,
                'company': customer_profile.company,
            },
            'recommendations': [],
            'engine': 'Deterministic Rule-Based Recommendation Engine',
            'explanation': 'No active in-stock products or services are currently eligible for recommendation.',
        }

    # 4. Compute Historical Profile Metrics
    # Customer prior sales items
    customer_all_sales = SaleItem.objects.filter(
        sale__customer=customer_profile,
        sale__status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED]
    )
    prior_purchases_count = customer_all_sales.count()

    # Category purchase frequency
    category_counts_qs = customer_all_sales.values('product__category_id').annotate(cnt=Count('id'))
    category_purchase_counts = {item['product__category_id']: item['cnt'] for item in category_counts_qs}

    # Customer average item price
    avg_price_raw = customer_all_sales.aggregate(avg=Avg('unit_price'))['avg']
    customer_avg_price = Decimal(str(round(avg_price_raw, 2))) if avg_price_raw else None

    # Service / product purchase history
    bought_physical = customer_all_sales.filter(product__item_type=ProductService.ItemType.PRODUCT).exists()
    bought_service = customer_all_sales.filter(product__item_type=ProductService.ItemType.SERVICE).exists()

    # CRM-wide popularity across confirmed/completed sales in past 180 days
    crm_sales_qs = SaleItem.objects.filter(
        sale__status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED],
        sale__sale_date__gte=cutoff_180_days
    ).values('product_id').annotate(sold_qty=Sum('quantity'))
    crm_popularity_map = {item['product_id']: item['sold_qty'] for item in crm_sales_qs}
    max_sold_qty = max(crm_popularity_map.values()) if crm_popularity_map else 0

    # 5. Score Each Eligible Candidate
    scored_items = []
    is_cold_start = (prior_purchases_count == 0)

    for item in eligible_candidates:
        score = 0
        factors = []

        # A. Category Affinity (Max 35 points)
        cat_cnt = category_purchase_counts.get(item.category_id, 0)
        if cat_cnt >= 3:
            pts = 35
            factors.append({
                'factor': 'High Category Affinity',
                'impact': '+35',
                'type': 'positive',
                'detail': f"You frequently purchase from '{item.category.category_name}' ({cat_cnt} past orders)."
            })
        elif cat_cnt >= 1:
            pts = 25
            factors.append({
                'factor': 'Category Affinity',
                'impact': '+25',
                'type': 'positive',
                'detail': f"You have purchased from '{item.category.category_name}' ({cat_cnt} past order(s))."
            })
        else:
            pts = 0
            if not is_cold_start:
                factors.append({
                    'factor': 'New Category Discovery',
                    'impact': '+0',
                    'type': 'neutral',
                    'detail': f"Product is in '{item.category.category_name}' (new category for this account)."
                })
        score += pts

        # B. CRM Popularity (Max 30 points)
        sold_qty = crm_popularity_map.get(item.id, 0)
        if max_sold_qty > 0 and sold_qty > 0:
            pop_pts = min(30, int(round((sold_qty / max_sold_qty) * 30)))
            pop_pts = max(5, pop_pts)
            factors.append({
                'factor': 'Popular Choice Across CRM',
                'impact': f'+{pop_pts}',
                'type': 'positive',
                'detail': f"{sold_qty} unit(s) confirmed in recent CRM sales."
            })
        else:
            pop_pts = 10 if is_cold_start else 5
            factors.append({
                'factor': 'Catalog Baseline Score',
                'impact': f'+{pop_pts}',
                'type': 'neutral',
                'detail': 'Standard active catalog item.'
            })
        score += pop_pts

        # C. Price Compatibility (Max 25 points)
        item_price = item.unit_price
        if customer_avg_price and customer_avg_price > Decimal('0.00'):
            ratio = item_price / customer_avg_price
            if Decimal('0.50') <= ratio <= Decimal('1.50'):
                price_pts = 25
                factors.append({
                    'factor': 'Optimal Price Fit',
                    'impact': '+25',
                    'type': 'positive',
                    'detail': f"Price (₹{item_price:,.2f}) closely aligns with your past average item spend (₹{customer_avg_price:,.2f})."
                })
            elif Decimal('0.25') <= ratio <= Decimal('2.50'):
                price_pts = 15
                factors.append({
                    'factor': 'Compatible Price Range',
                    'impact': '+15',
                    'type': 'positive',
                    'detail': f"Price (₹{item_price:,.2f}) is within reasonable range of your past purchases."
                })
            else:
                price_pts = 5
                factors.append({
                    'factor': 'Premium or Budget Tier',
                    'impact': '+5',
                    'type': 'neutral',
                    'detail': f"Price (₹{item_price:,.2f}) differs from historical order average."
                })
        else:
            # Cold start price baseline
            price_pts = 15
            factors.append({
                'factor': 'Welcome Account Price Baseline',
                'impact': '+15',
                'type': 'neutral',
                'detail': 'Baseline price score applied for initial orders.'
            })
        score += price_pts

        # D. Complementary Cross-Sell (Max 10 points)
        if item.item_type == ProductService.ItemType.SERVICE and bought_physical:
            cross_pts = 10
            factors.append({
                'factor': 'Service Complementarity',
                'impact': '+10',
                'type': 'positive',
                'detail': 'Complements physical equipment or products previously ordered.'
            })
        elif item.item_type == ProductService.ItemType.PRODUCT and bought_service:
            cross_pts = 5
            factors.append({
                'factor': 'Product Complementarity',
                'impact': '+5',
                'type': 'positive',
                'detail': 'Physical item suitable to accompany previously utilized services.'
            })
        else:
            cross_pts = 0
        score += cross_pts

        # Enforce exact clamping 0-100
        final_score = max(0, min(100, score))

        scored_items.append({
            'product_id': item.id,
            'product_code': item.product_code,
            'product_name': item.product_name,
            'category_name': item.category.category_name,
            'unit_price': float(item.unit_price),
            'stock_quantity': item.stock_quantity if item.item_type == ProductService.ItemType.PRODUCT else None,
            'is_service': item.item_type == ProductService.ItemType.SERVICE,
            'item_type_display': item.get_item_type_display(),
            'score': final_score,
            'factors': factors,
        })

    # 6. Deterministic Sort
    # Sort order: score DESC, stock_quantity DESC, unit_price ASC, product_code ASC
    def sort_key(rec):
        stock_sort = rec['stock_quantity'] or 0
        return (-rec['score'], -stock_sort, rec['unit_price'], rec['product_code'])

    scored_items.sort(key=sort_key)
    top_recommendations = scored_items[:limit]

    # Assign final rank
    for rank, rec in enumerate(top_recommendations, start=1):
        rec['rank'] = rank

    mode_description = (
        "Cold-start baseline recommendation based on CRM popularity and catalog pricing."
        if is_cold_start else
        f"Personalized recommendation evaluated against {prior_purchases_count} historical purchase touchpoints."
    )

    return {
        'customer': {
            'id': customer_profile.id,
            'code': customer_profile.customer_code,
            'name': customer_profile.user.full_name or customer_profile.user.username,
            'company': customer_profile.company,
        },
        'recommendations': top_recommendations,
        'engine': 'Deterministic Rule-Based Recommendation Engine',
        'is_cold_start': is_cold_start,
        'explanation': mode_description,
        'total_evaluated': len(eligible_candidates),
    }

import calendar
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone
from sklearn.linear_model import LinearRegression

from sales.models import Sale


def _add_months(year, month, step):
    """Safely increments (year, month) by step months."""
    total_months = (year * 12 + (month - 1)) + step
    new_year = total_months // 12
    new_month = (total_months % 12) + 1
    return new_year, new_month


def generate_sales_forecast(staff_profile=None, horizon_months=3):
    """
    On-demand explainable sales forecasting engine.

    Rules:
    - 0-1 historical months: INSUFFICIENT_DATA (₹0.00 projections, is_ml=False)
    - 2-5 contiguous months: MOVING_AVERAGE_FALLBACK (is_ml=False)
    - >= 6 contiguous months: LINEAR_REGRESSION (is_ml=True)
    - Contiguous timeline with zero-filling for missing months.
    - Clamps negative predictions to Decimal('0.00').
    - Evaluates R² as historical training fit.
    - Never pickles or persists models.
    """
    # 1. Fetch eligible confirmed/completed sales
    sales_qs = Sale.objects.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    if staff_profile:
        sales_qs = sales_qs.filter(sales_staff=staff_profile)

    monthly_raw = list(
        sales_qs.values('sale_date__year', 'sale_date__month')
        .annotate(month_rev=Sum('net_amount'), order_cnt=Count('id'))
        .order_by('sale_date__year', 'sale_date__month')
    )

    tz_name = getattr(settings, 'TIME_ZONE', 'Asia/Kolkata')
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.get_current_timezone()
    today = timezone.now().astimezone(tz).date()

    # If no sales at all
    if not monthly_raw:
        future_predictions = []
        for step in range(1, horizon_months + 1):
            f_yr, f_mo = _add_months(today.year, today.month, step)
            future_predictions.append({
                'step': step,
                'year': f_yr,
                'month': f_mo,
                'year_month': f"{f_yr}-{f_mo:02d}",
                'label': f"{calendar.month_abbr[f_mo]} {f_yr}",
                'forecast_value': Decimal('0.00'),
            })

        return {
            'status': 'INSUFFICIENT_DATA',
            'method': 'INSUFFICIENT_DATA',
            'is_ml': False,
            'num_months_used': 0,
            'training_date_range': None,
            'historical_points': [],
            'predictions': future_predictions,
            'trend_direction': 'Unknown / Insufficient Data',
            'monthly_slope': None,
            'r_squared': None,
            'r_squared_meaningful': False,
            'assumptions': [
                "Zero historical completed sales available in the CRM.",
                "At least 6 months of historical data are required for AI machine learning forecasting."
            ],
            'notes': "No sales data available. AI Forecasting requires at least 6 months of continuous history.",
        }

    # 2. Build continuous chronological timeline from earliest to latest month
    sales_by_ym = {
        (item['sale_date__year'], item['sale_date__month']): item['month_rev'] or Decimal('0.00')
        for item in monthly_raw
    }

    start_year = monthly_raw[0]['sale_date__year']
    start_month = monthly_raw[0]['sale_date__month']
    end_year = monthly_raw[-1]['sale_date__year']
    end_month = monthly_raw[-1]['sale_date__month']

    # Generate complete list of (year, month) pairs without gaps
    cur_year = start_year
    cur_month = start_month
    continuous_timeline = []

    while (cur_year < end_year) or (cur_year == end_year and cur_month <= end_month):
        rev = sales_by_ym.get((cur_year, cur_month), Decimal('0.00'))
        continuous_timeline.append({
            'year': cur_year,
            'month': cur_month,
            'year_month': f"{cur_year}-{cur_month:02d}",
            'label': f"{calendar.month_abbr[cur_month]} {cur_year}",
            'revenue': rev,
        })
        cur_year, cur_month = _add_months(cur_year, cur_month, 1)

    num_months = len(continuous_timeline)
    latest_item = continuous_timeline[-1]
    latest_year = latest_item['year']
    latest_month = latest_item['month']

    training_date_range = {
        'start': continuous_timeline[0]['year_month'],
        'end': continuous_timeline[-1]['year_month'],
        'start_label': continuous_timeline[0]['label'],
        'end_label': continuous_timeline[-1]['label'],
    }

    # 3. Case A: 1 Historical Month -> INSUFFICIENT_DATA
    if num_months <= 1:
        future_predictions = []
        for step in range(1, horizon_months + 1):
            f_yr, f_mo = _add_months(latest_year, latest_month, step)
            future_predictions.append({
                'step': step,
                'year': f_yr,
                'month': f_mo,
                'year_month': f"{f_yr}-{f_mo:02d}",
                'label': f"{calendar.month_abbr[f_mo]} {f_yr}",
                'forecast_value': Decimal('0.00'),
            })

        return {
            'status': 'INSUFFICIENT_DATA',
            'method': 'INSUFFICIENT_DATA',
            'is_ml': False,
            'num_months_used': num_months,
            'training_date_range': training_date_range,
            'historical_points': [
                {
                    'year_month': p['year_month'],
                    'label': p['label'],
                    'revenue': float(p['revenue'])
                }
                for p in continuous_timeline
            ],
            'predictions': future_predictions,
            'trend_direction': 'Unknown / Insufficient Data',
            'monthly_slope': None,
            'r_squared': None,
            'r_squared_meaningful': False,
            'assumptions': [
                "Only 1 calendar month of sales history is currently recorded.",
                "A single month cannot establish a trend or a meaningful moving average.",
                "AI machine learning forecasting requires at least 6 months of continuous history."
            ],
            'notes': "Insufficient historical data: at least 6 months of historical sales are required for AI forecasting.",
        }

    # 4. Case B: 2 to 5 Contiguous Months -> MOVING_AVERAGE_FALLBACK
    if num_months < 6:
        window_size = min(3, num_months)
        recent_window = continuous_timeline[-window_size:]
        avg_revenue = (
            sum((item['revenue'] for item in recent_window), Decimal('0.00')) / Decimal(window_size)
        ).quantize(Decimal('0.01'))
        clamped_avg = max(Decimal('0.00'), avg_revenue)

        future_predictions = []
        for step in range(1, horizon_months + 1):
            f_yr, f_mo = _add_months(latest_year, latest_month, step)
            future_predictions.append({
                'step': step,
                'year': f_yr,
                'month': f_mo,
                'year_month': f"{f_yr}-{f_mo:02d}",
                'label': f"{calendar.month_abbr[f_mo]} {f_yr}",
                'forecast_value': clamped_avg,
            })

        # Recent trend direction
        first_rev = recent_window[0]['revenue']
        last_rev = recent_window[-1]['revenue']
        if last_rev > first_rev:
            trend_direction = "Upward (Recent Trend)"
        elif last_rev < first_rev:
            trend_direction = "Downward (Recent Trend)"
        else:
            trend_direction = "Stable (Recent Trend)"

        return {
            'status': 'FALLBACK',
            'method': 'MOVING_AVERAGE_FALLBACK',
            'is_ml': False,
            'num_months_used': num_months,
            'training_date_range': training_date_range,
            'historical_points': [
                {
                    'year_month': p['year_month'],
                    'label': p['label'],
                    'revenue': float(p['revenue'])
                }
                for p in continuous_timeline
            ],
            'predictions': future_predictions,
            'trend_direction': trend_direction,
            'monthly_slope': None,
            'r_squared': None,
            'r_squared_meaningful': False,
            'assumptions': [
                f"Deterministic {window_size}-month simple moving average fallback.",
                f"Machine learning disabled because only {num_months} months are available (minimum 6 required).",
                "Predictions are held steady at the moving average across the 3-month forecast horizon.",
                "Negative projections clamped to zero."
            ],
            'notes': f"Machine learning disabled: requires at least 6 months of historical data (currently {num_months} months available).",
        }

    # 5. Case C: >= 6 Contiguous Months -> LINEAR_REGRESSION (Scikit-Learn)
    # Prepare X (numerical month indices 0, 1, ..., N-1) and y (float revenues)
    X = [[i] for i in range(num_months)]
    y = [float(item['revenue']) for item in continuous_timeline]

    model = LinearRegression()
    model.fit(X, y)

    r_squared = round(float(model.score(X, y)), 3)
    slope = float(model.coef_[0])
    monthly_slope_dec = Decimal(str(round(slope, 2)))

    # Predict future horizon
    future_X = [[num_months + step - 1] for step in range(1, horizon_months + 1)]
    raw_predictions = model.predict(future_X)

    future_predictions = []
    for step, raw_val in enumerate(raw_predictions, start=1):
        f_yr, f_mo = _add_months(latest_year, latest_month, step)
        # Clamping negative predictions to zero
        clamped_val = max(Decimal('0.00'), Decimal(str(round(raw_val, 2))))
        future_predictions.append({
            'step': step,
            'year': f_yr,
            'month': f_mo,
            'year_month': f"{f_yr}-{f_mo:02d}",
            'label': f"{calendar.month_abbr[f_mo]} {f_yr}",
            'forecast_value': clamped_val,
        })

    if slope > 10.0:
        trend_direction = "Upward / Growing"
    elif slope < -10.0:
        trend_direction = "Downward / Declining"
    else:
        trend_direction = "Stable / Flat"

    if r_squared >= 0.70:
        fit_note = "Strong historical linear fit indicating consistent revenue growth/decline."
    elif r_squared >= 0.40:
        fit_note = "Moderate historical linear fit with notable month-to-month variance."
    else:
        fit_note = "Low historical linear fit; historical monthly revenue has been volatile."

    return {
        'status': 'ACTIVE',
        'method': 'LINEAR_REGRESSION',
        'is_ml': True,
        'num_months_used': num_months,
        'training_date_range': training_date_range,
        'historical_points': [
            {
                'year_month': p['year_month'],
                'label': p['label'],
                'revenue': float(p['revenue'])
            }
            for p in continuous_timeline
        ],
        'predictions': future_predictions,
        'trend_direction': trend_direction,
        'monthly_slope': monthly_slope_dec,
        'r_squared': r_squared,
        'r_squared_meaningful': True,
        'assumptions': [
            f"Trained on {num_months} consecutive calendar months from {training_date_range['start_label']} to {training_date_range['end_label']}.",
            f"Fitted linear trend slope: ₹{monthly_slope_dec:+,.2f} per month.",
            "Assumes continuation of linear trend without sudden macro-economic disruptions.",
            "Negative future predictions are automatically clamped to ₹0.00.",
            f"R² score of {r_squared} evaluates historical training fit, not guaranteed future certainty."
        ],
        'notes': fit_note,
    }

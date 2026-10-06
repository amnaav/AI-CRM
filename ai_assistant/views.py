from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_protect

from accounts.models import CustomUser, CustomerProfile
from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from ai_assistant.forms import AssistantQuestionForm
from ai_assistant.services.assistant_service import execute_assistant_intent
from ai_assistant.services.intent_service import detect_intent, INTENT_UNSUPPORTED
from ai_assistant.services.throttling import check_throttled


SUGGESTION_CHIPS = {
    CustomUser.Role.ADMIN: [
        {"label": "CRM Summary", "query": "CRM Summary", "badge": "primary"},
        {"label": "Lead Pipeline", "query": "Lead Pipeline", "badge": "info"},
        {"label": "Revenue Summary", "query": "Revenue Summary", "badge": "success"},
        {"label": "Collected vs Outstanding", "query": "Collected vs Outstanding", "badge": "success"},
        {"label": "Stock Alerts", "query": "Stock Alerts", "badge": "danger"},
        {"label": "High Priority Leads", "query": "High Priority Leads", "badge": "warning"},
        {"label": "Leads Needing Follow-up", "query": "Leads Needing Follow-up", "badge": "warning"},
        {"label": "Sales Forecast Explanation", "query": "Sales Forecast", "badge": "secondary"},
        {"label": "Staff Performance", "query": "Staff Performance", "badge": "primary"},
        {"label": "Help & Questions", "query": "Help", "badge": "secondary"},
    ],
    CustomUser.Role.SALES_STAFF: [
        {"label": "My Leads", "query": "My Leads", "badge": "primary"},
        {"label": "My High Priority Leads", "query": "My High Priority Leads", "badge": "danger"},
        {"label": "My Follow-ups", "query": "My Follow-ups", "badge": "warning"},
        {"label": "My Customers", "query": "My Customers", "badge": "info"},
        {"label": "My Sales Performance", "query": "My Performance", "badge": "success"},
        {"label": "My Invoices & Balances", "query": "My Invoices", "badge": "warning"},
        {"label": "My Overdue Tasks", "query": "My Reminders", "badge": "danger"},
        {"label": "Product Recommendations", "query": "Product Recommendations", "badge": "primary"},
        {"label": "My Sales Forecast", "query": "Sales Forecast", "badge": "secondary"},
        {"label": "Help & Questions", "query": "Help", "badge": "secondary"},
    ],
    CustomUser.Role.CUSTOMER: [
        {"label": "My Profile Summary", "query": "My Profile", "badge": "info"},
        {"label": "My Past Purchases", "query": "My Purchases", "badge": "primary"},
        {"label": "My Invoices", "query": "My Invoices", "badge": "warning"},
        {"label": "My Outstanding Balance", "query": "My Balance", "badge": "danger"},
        {"label": "My Payment Receipts", "query": "My Payments", "badge": "success"},
        {"label": "Recommended For Me", "query": "Product Recommendations", "badge": "primary"},
        {"label": "Available Products & Services", "query": "Catalog", "badge": "info"},
        {"label": "My Reminders & Notices", "query": "My Reminders", "badge": "secondary"},
        {"label": "Help & Guide", "query": "Help", "badge": "secondary"},
    ],
}


class AssistantIndexView(LoginRequiredMixin, View):
    """
    Role-aware AI CRM Assistant central interface.
    Handles question submission, rate limiting, deterministic intent matching,
    and session-based conversation persistence.
    """
    template_name = 'ai_assistant/assistant.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not request.user.is_account_active:
            messages.error(request, "Your account has been deactivated or locked.")
            return redirect('accounts:login')

        if request.user.role not in [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]:
            raise PermissionDenied("You do not have permission to access the AI CRM Assistant.")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        form = AssistantQuestionForm()
        history = request.session.get('assistant_history', [])
        chips = SUGGESTION_CHIPS.get(request.user.role, [])

        # Optional customer selection list for Admin or Staff
        customer_choices = []
        if request.user.is_admin:
            customer_choices = CustomerProfile.objects.filter(is_active=True).select_related('user').order_by('customer_code')[:20]
        elif request.user.is_sales_staff and hasattr(request.user, 'staff_profile'):
            customer_choices = CustomerProfile.objects.filter(
                assigned_staff=request.user.staff_profile,
                is_active=True
            ).select_related('user').order_by('customer_code')

        context = {
            'form': form,
            'history': history,
            'suggestion_chips': chips,
            'customer_choices': customer_choices,
            'latest_result': history[-1] if history else None,
            'throttled_error': None,
        }
        return render(request, self.template_name, context)

    @method_decorator(csrf_protect)
    def post(self, request):
        # 1. Check rate limiting
        is_throttled, throttle_msg = check_throttled(request)
        chips = SUGGESTION_CHIPS.get(request.user.role, [])
        history = request.session.get('assistant_history', [])

        customer_choices = []
        if request.user.is_admin:
            customer_choices = CustomerProfile.objects.filter(is_active=True).select_related('user').order_by('customer_code')[:20]
        elif request.user.is_sales_staff and hasattr(request.user, 'staff_profile'):
            customer_choices = CustomerProfile.objects.filter(
                assigned_staff=request.user.staff_profile,
                is_active=True
            ).select_related('user').order_by('customer_code')

        if is_throttled:
            log_activity(
                request=request,
                action=ActivityLog.Action.OTHER,
                module_name="AIAssistant",
                record_id="THROTTLED",
                description="AI Assistant submission rejected by request rate limiter",
                metadata={"status": "throttled", "role": request.user.role}
            )
            context = {
                'form': AssistantQuestionForm(request.POST),
                'history': history,
                'suggestion_chips': chips,
                'customer_choices': customer_choices,
                'latest_result': history[-1] if history else None,
                'throttled_error': throttle_msg,
            }
            return render(request, self.template_name, context, status=429)

        # 2. Validate Form
        form = AssistantQuestionForm(request.POST)
        if not form.is_valid():
            context = {
                'form': form,
                'history': history,
                'suggestion_chips': chips,
                'customer_choices': customer_choices,
                'latest_result': history[-1] if history else None,
                'throttled_error': None,
            }
            return render(request, self.template_name, context, status=400)

        cleaned_question = form.cleaned_data['question']
        customer_id = form.cleaned_data.get('customer_id')

        # 3. Detect Intent & Execute Query
        detected_intent = detect_intent(cleaned_question, request.user.role)
        query_params = {}
        if customer_id:
            query_params['customer_id'] = customer_id

        try:
            result = execute_assistant_intent(
                intent=detected_intent,
                user=request.user,
                params=query_params,
                request=request
            )
        except PermissionDenied as pe:
            # Handle IDOR or scope permission denied safely
            result = {
                'intent': 'UNAUTHORIZED_SCOPE',
                'topic': 'Permission Denied',
                'calculation_timestamp': '',
                'data_source_summary': 'Authorization enforcement.',
                'answer_text': "You do not have permission to view records for the specified account.",
                'metrics': [],
                'suggested_actions': []
            }

        # 4. Serialize lightweight history entry
        history_entry = {
            'query': cleaned_question,
            'intent': detected_intent,
            'topic': result.get('topic', 'CRM Query'),
            'timestamp': result.get('calculation_timestamp', ''),
            'answer_text': result.get('answer_text', ''),
            'data_source_summary': result.get('data_source_summary', ''),
            'metrics': result.get('metrics', []),
            'table_data': result.get('table_data', None),
            'recommendations': result.get('recommendations', []),
            'suggested_actions': result.get('suggested_actions', []),
        }

        # Maintain max 10 exchanges in session
        history.append(history_entry)
        request.session['assistant_history'] = history[-10:]
        request.session.modified = True

        # 5. Safe Activity Logging
        result_status = 'UNSUPPORTED' if detected_intent == INTENT_UNSUPPORTED else 'SUCCESS'
        log_activity(
            request=request,
            action=ActivityLog.Action.OTHER,
            module_name="AIAssistant",
            record_id=detected_intent,
            description=f"AI Assistant query executed: {detected_intent} [{result_status}]",
            metadata={
                'intent': detected_intent,
                'result': result_status,
                'role': request.user.role,
            }
        )

        context = {
            'form': AssistantQuestionForm(),
            'history': request.session['assistant_history'],
            'suggestion_chips': chips,
            'customer_choices': customer_choices,
            'latest_result': history_entry,
            'throttled_error': None,
        }
        return render(request, self.template_name, context)


class ClearAssistantHistoryView(LoginRequiredMixin, View):
    """
    POST-only CSRF-protected endpoint to clear the session conversational history.
    Affects solely the authenticated user's session.
    """
    @method_decorator(csrf_protect)
    def post(self, request):
        if not request.user.is_authenticated or not request.user.is_account_active:
            raise PermissionDenied("Authentication required.")

        if 'assistant_history' in request.session:
            del request.session['assistant_history']
            request.session.modified = True

        log_activity(
            request=request,
            action=ActivityLog.Action.OTHER,
            module_name="AIAssistant",
            record_id="CLEAR_CONVERSATION",
            description="Cleared AI Assistant conversation session history",
            metadata={'action': 'clear_history'}
        )

        messages.success(request, "Assistant conversation history cleared.")
        return redirect('ai_assistant:index')

    def get(self, request):
        return HttpResponseNotAllowed(['POST'])

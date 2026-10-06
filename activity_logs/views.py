from django.shortcuts import render, get_object_or_404
from django.views import View
from core.mixins import AdminRequiredMixin
from activity_logs.models import ActivityLog
from django.core.paginator import Paginator


class ActivityLogListView(AdminRequiredMixin, View):
    template_name = 'activity_logs/log_list.html'

    def get(self, request):
        logs = ActivityLog.objects.select_related('user').all()

        action = request.GET.get('action')
        module = request.GET.get('module')
        search = request.GET.get('search')

        if action:
            logs = logs.filter(action=action)
        if module:
            logs = logs.filter(module_name__icontains=module)
        if search:
            logs = logs.filter(description__icontains=search)

        paginator = Paginator(logs, 25)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_title': 'Audit & Activity Logs',
            'page_obj': page_obj,
            'action_choices': ActivityLog.Action.choices,
            'selected_action': action or '',
            'selected_module': module or '',
            'search_query': search or '',
        }
        return render(request, self.template_name, context)


class ActivityLogDetailView(AdminRequiredMixin, View):
    template_name = 'activity_logs/log_detail.html'

    def get(self, request, pk):
        log_entry = get_object_or_404(ActivityLog.objects.select_related('user'), pk=pk)
        context = {
            'page_title': f'Activity Log #{log_entry.pk}',
            'log': log_entry,
        }
        return render(request, self.template_name, context)

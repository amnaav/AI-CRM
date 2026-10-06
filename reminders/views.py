from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.db import models
from django.http import Http404, HttpResponseNotAllowed
from django.contrib import messages
from django.utils import timezone
from django.core.paginator import Paginator
from core.mixins import StaffRequiredMixin, CustomerRequiredMixin, RoleRequiredMixin
from accounts.models import CustomUser
from reminders.models import Reminder
from reminders.forms import ReminderForm
from reminders.services import create_reminder, complete_reminder, cancel_reminder


class ReminderListView(StaffRequiredMixin, View):
    template_name = 'reminders/reminder_list.html'

    def get(self, request):
        today = timezone.localdate()
        reminders = Reminder.objects.select_related(
            'assigned_to', 'customer__user', 'lead', 'sale', 'created_by', 'completed_by'
        )

        # Sales staff filter
        if request.user.is_sales_staff and not request.user.is_admin:
            reminders = reminders.filter(assigned_to=request.user)

        tab = request.GET.get('tab', 'pending')
        priority = request.GET.get('priority')
        reminder_type = request.GET.get('type')
        search = request.GET.get('search')

        if tab == 'today':
            reminders = reminders.filter(status=Reminder.Status.PENDING, due_date=today)
        elif tab == 'overdue':
            reminders = reminders.filter(status=Reminder.Status.PENDING, due_date__lt=today)
        elif tab == 'upcoming':
            reminders = reminders.filter(status=Reminder.Status.PENDING, due_date__gt=today)
        elif tab == 'completed':
            reminders = reminders.filter(status=Reminder.Status.COMPLETED)
        elif tab == 'pending':
            reminders = reminders.filter(status=Reminder.Status.PENDING)

        if priority:
            reminders = reminders.filter(priority=priority)
        if reminder_type:
            reminders = reminders.filter(reminder_type=reminder_type)
        if search:
            reminders = reminders.filter(
                models.Q(title__icontains=search) |
                models.Q(description__icontains=search) |
                models.Q(customer__user__full_name__icontains=search) |
                models.Q(lead__full_name__icontains=search)
            )

        # Counts for tabs
        base_qs = Reminder.objects.filter(assigned_to=request.user) if (request.user.is_sales_staff and not request.user.is_admin) else Reminder.objects.all()
        counts = {
            'today': base_qs.filter(status=Reminder.Status.PENDING, due_date=today).count(),
            'overdue': base_qs.filter(status=Reminder.Status.PENDING, due_date__lt=today).count(),
            'upcoming': base_qs.filter(status=Reminder.Status.PENDING, due_date__gt=today).count(),
            'completed': base_qs.filter(status=Reminder.Status.COMPLETED).count(),
            'pending': base_qs.filter(status=Reminder.Status.PENDING).count(),
        }

        paginator = Paginator(reminders, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_title': 'Task & Reminder Schedule',
            'page_obj': page_obj,
            'current_tab': tab,
            'counts': counts,
            'priority_choices': Reminder.Priority.choices,
            'type_choices': Reminder.ReminderType.choices,
            'selected_priority': priority or '',
            'selected_type': reminder_type or '',
            'search_query': search or '',
        }
        return render(request, self.template_name, context)


class ReminderDetailView(RoleRequiredMixin, View):
    allowed_roles = [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]
    template_name = 'reminders/reminder_detail.html'

    def get(self, request, pk):
        reminder = get_object_or_404(
            Reminder.objects.select_related('assigned_to', 'customer__user', 'lead', 'sale', 'created_by', 'completed_by'),
            pk=pk
        )

        if request.user.is_customer:
            cust_profile = getattr(request.user, 'customer_profile', None)
            if not cust_profile or reminder.customer_id != cust_profile.id or not reminder.is_customer_visible:
                raise Http404("Reminder not found or not visible.")

        context = {
            'page_title': f'Reminder: {reminder.title}',
            'reminder': reminder,
        }
        return render(request, self.template_name, context)


class ReminderCreateView(StaffRequiredMixin, View):
    template_name = 'reminders/reminder_form.html'

    def get(self, request):
        initial = {'due_date': timezone.localdate()}
        if request.GET.get('customer'):
            initial['customer'] = request.GET.get('customer')
        if request.GET.get('lead'):
            initial['lead'] = request.GET.get('lead')
        if request.GET.get('sale'):
            initial['sale'] = request.GET.get('sale')

        form = ReminderForm(initial=initial, user=request.user)
        return render(request, self.template_name, {'form': form, 'page_title': 'Schedule New Reminder'})

    def post(self, request):
        form = ReminderForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                reminder = create_reminder(
                    title=form.cleaned_data['title'],
                    due_date=form.cleaned_data['due_date'],
                    assigned_to=form.cleaned_data['assigned_to'],
                    reminder_type=form.cleaned_data['reminder_type'],
                    customer=form.cleaned_data.get('customer'),
                    lead=form.cleaned_data.get('lead'),
                    sale=form.cleaned_data.get('sale'),
                    priority=form.cleaned_data['priority'],
                    description=form.cleaned_data.get('description', ''),
                    due_time=form.cleaned_data.get('due_time'),
                    is_customer_visible=form.cleaned_data.get('is_customer_visible', False),
                    created_by=request.user,
                    request=request
                )
                messages.success(request, f"Reminder '{reminder.title}' scheduled for {reminder.due_date}.")
                return redirect('reminders:reminder_detail', pk=reminder.pk)
            except Exception as e:
                messages.error(request, f"Error creating reminder: {e}")
        return render(request, self.template_name, {'form': form, 'page_title': 'Schedule New Reminder'})


class ReminderUpdateView(StaffRequiredMixin, View):
    template_name = 'reminders/reminder_form.html'

    def get(self, request, pk):
        reminder = get_object_or_404(Reminder, pk=pk)
        form = ReminderForm(instance=reminder, user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'reminder': reminder,
            'page_title': f'Edit Reminder #{reminder.pk}'
        })

    def post(self, request, pk):
        reminder = get_object_or_404(Reminder, pk=pk)
        form = ReminderForm(request.POST, instance=reminder, user=request.user)
        if form.is_valid():
            try:
                reminder = form.save()
                messages.success(request, "Reminder updated successfully.")
                return redirect('reminders:reminder_detail', pk=reminder.pk)
            except Exception as e:
                messages.error(request, f"Error saving reminder: {e}")
        return render(request, self.template_name, {
            'form': form,
            'reminder': reminder,
            'page_title': f'Edit Reminder #{reminder.pk}'
        })


class ReminderCompleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        reminder = get_object_or_404(Reminder, pk=pk)
        complete_reminder(reminder.pk, request.user, request=request)
        messages.success(request, f"Reminder '{reminder.title}' marked as completed.")
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'reminders:reminder_list'
        return redirect(next_url)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class ReminderCancelView(StaffRequiredMixin, View):
    def post(self, request, pk):
        reminder = get_object_or_404(Reminder, pk=pk)
        cancel_reminder(reminder.pk, request.user, request=request)
        messages.warning(request, f"Reminder '{reminder.title}' has been cancelled.")
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'reminders:reminder_list'
        return redirect(next_url)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class CustomerReminderListView(CustomerRequiredMixin, View):
    """
    Customer portal view strictly requiring customer profile and is_customer_visible=True.
    """
    template_name = 'reminders/customer_reminder_list.html'

    def get(self, request):
        customer_profile = getattr(request.user, 'customer_profile', None)
        if not customer_profile:
            messages.error(request, "Customer profile not found.")
            return redirect('accounts:login')

        reminders = Reminder.objects.filter(
            customer=customer_profile,
            is_customer_visible=True
        ).order_by('due_date', 'due_time')

        context = {
            'page_title': 'My Scheduled Reminders',
            'reminders': reminders,
        }
        return render(request, self.template_name, context)

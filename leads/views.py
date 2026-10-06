from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import CustomUser, SalesStaffProfile, CustomerProfile
from activity_logs.services import log_activity
from leads.models import Lead, LeadStatusHistory
from leads.forms import LeadForm, LeadAssignForm, LeadStatusUpdateForm, LeadConversionConfirmForm
from leads.services.scoring import LeadScoringService
from leads.services.conversion import LeadConversionService


class LeadBaseAccessMixin(LoginRequiredMixin):
    """
    Base permission mixin for Lead Management.
    - Anonymous -> redirected to login.
    - Customers -> 403 Forbidden.
    - Sales Staff -> limited to assigned leads.
    - Admin -> full access.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_customer:
            raise PermissionDenied("Customer accounts are not authorized to access Lead Management.")
        return super().dispatch(request, *args, **kwargs)

    def get_lead_for_user(self, pk, active_only=True):
        qs = Lead.objects.select_related('assigned_staff__user', 'converted_customer__user')
        lead = get_object_or_404(qs, pk=pk)

        # Sales Staff access boundary check
        if self.request.user.is_sales_staff:
            if not hasattr(self.request.user, 'staff_profile') or lead.assigned_staff != self.request.user.staff_profile:
                raise PermissionDenied("You do not have permission to access leads assigned to another sales representative.")

        if active_only and not lead.is_active and not self.request.user.is_admin:
            raise PermissionDenied("This lead record has been archived.")

        return lead


class LeadListView(LeadBaseAccessMixin, View):
    """
    Lead Directory and Pipeline Dashboard.
    Supports multi-field search, status/source/staff/priority filters, sorting,
    pagination, pipeline KPI cards, and persistent Add Lead button.
    """
    template_name = 'leads/lead_list.html'

    def get(self, request):
        user = request.user
        qs = Lead.objects.select_related('assigned_staff__user', 'converted_customer')

        # Role scoping
        if user.is_sales_staff:
            staff_profile = getattr(user, 'staff_profile', None)
            if staff_profile:
                qs = qs.filter(assigned_staff=staff_profile)
            else:
                qs = qs.none()

        # Archive filter
        show_archived = request.GET.get('archived', '0') == '1' and user.is_admin
        if show_archived:
            qs = qs.filter(is_active=False)
        else:
            qs = qs.filter(is_active=True)

        # Pipeline Summary KPI metrics (calculated before filters for contextual awareness)
        base_scope = qs
        pipeline_counts = {
            'NEW': base_scope.filter(status=Lead.Status.NEW).count(),
            'CONTACTED': base_scope.filter(status=Lead.Status.CONTACTED).count(),
            'QUALIFIED': base_scope.filter(status=Lead.Status.QUALIFIED).count(),
            'PROPOSAL': base_scope.filter(status=Lead.Status.PROPOSAL).count(),
            'NEGOTIATION': base_scope.filter(status=Lead.Status.NEGOTIATION).count(),
            'WON': base_scope.filter(status=Lead.Status.WON).count(),
            'LOST': base_scope.filter(status=Lead.Status.LOST).count(),
            'total_value': base_scope.aggregate(val=Sum('estimated_value'))['val'] or 0.00,
            'total_leads': base_scope.count(),
        }

        # Search query (name, lead_code, email, phone, company)
        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(full_name__icontains=search_query) |
                Q(lead_code__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(company__icontains=search_query)
            )

        # Filters
        status_filter = request.GET.get('status', '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        source_filter = request.GET.get('source', '').strip()
        if source_filter:
            qs = qs.filter(source=source_filter)

        priority_filter = request.GET.get('priority', '').strip()
        if priority_filter:
            qs = qs.filter(ai_priority=priority_filter)

        staff_filter = request.GET.get('assigned_staff', '').strip()
        if staff_filter and user.is_admin:
            qs = qs.filter(assigned_staff_id=staff_filter)

        # Sorting
        sort_field = request.GET.get('sort', '-created_at').strip()
        allowed_sorts = {
            '-created_at', 'created_at',
            'full_name', '-full_name',
            'estimated_value', '-estimated_value',
            'probability_score', '-probability_score',
            'status', 'ai_priority'
        }
        if sort_field in allowed_sorts:
            qs = qs.order_by(sort_field)
        else:
            qs = qs.order_by('-created_at')

        # Pagination
        paginator = Paginator(qs, 15)
        page_num = request.GET.get('page')
        page_obj = paginator.get_page(page_num)

        staff_list = None
        if user.is_admin:
            staff_list = SalesStaffProfile.objects.filter(user__is_active=True).select_related('user')

        return render(request, self.template_name, {
            'page_title': 'Leads Pipeline & Directory',
            'leads': page_obj,
            'pipeline_counts': pipeline_counts,
            'search_query': search_query,
            'status_filter': status_filter,
            'source_filter': source_filter,
            'priority_filter': priority_filter,
            'staff_filter': staff_filter,
            'sort_field': sort_field,
            'status_choices': Lead.Status.choices,
            'source_choices': Lead.Source.choices,
            'priority_choices': Lead.Priority.choices,
            'staff_list': staff_list,
            'show_archived': show_archived,
        })


class LeadDetailView(LeadBaseAccessMixin, View):
    """
    Detailed lead view showing contact profile, AI lead score breakdown,
    pipeline progress, status audit history, and touchpoint metrics.
    """
    template_name = 'leads/lead_detail.html'

    def get(self, request, pk):
        lead = self.get_lead_for_user(pk, active_only=False)

        # Fetch status history
        history = lead.status_history.select_related('changed_by').all()

        # Touchpoint statistics
        interactions = lead.interactions.select_related('sales_staff__user').order_by('-interaction_date')[:10]
        reminders = lead.reminders.select_related('assigned_to').order_by('due_date')[:10]

        # Stage progression tracker
        pipeline_stages = [
            (Lead.Status.NEW, "New"),
            (Lead.Status.CONTACTED, "Contacted"),
            (Lead.Status.QUALIFIED, "Qualified"),
            (Lead.Status.PROPOSAL, "Proposal"),
            (Lead.Status.NEGOTIATION, "Negotiation"),
            (Lead.Status.WON, "Won"),
        ]

        can_convert = lead.status in LeadConversionService.CONVERTIBLE_STATUSES and not lead.is_converted
        assign_form = LeadAssignForm(initial={'assigned_staff': lead.assigned_staff}) if request.user.is_admin else None

        return render(request, self.template_name, {
            'page_title': f"{lead.full_name} ({lead.lead_code})",
            'lead': lead,
            'history': history,
            'interactions': interactions,
            'reminders': reminders,
            'pipeline_stages': pipeline_stages,
            'can_convert': can_convert,
            'assign_form': assign_form,
        })


class LeadCreateView(LeadBaseAccessMixin, View):
    """
    Creates a new Lead. Automatically assigns Sales Staff if created by staff.
    Computes initial explainable AI score and records status history.
    """
    template_name = 'leads/lead_form.html'

    def get(self, request):
        form = LeadForm(user=request.user)
        return render(request, self.template_name, {
            'page_title': 'Add New Lead',
            'form': form,
            'is_create': True,
        })

    def post(self, request):
        form = LeadForm(request.POST, user=request.user)
        if form.is_valid():
            lead = form.save(commit=False)
            lead.created_by = request.user

            # If sales staff, ensure assigned to self
            if request.user.is_sales_staff:
                lead.assigned_staff = getattr(request.user, 'staff_profile', None)

            lead.save()

            # Record initial status history
            LeadStatusHistory.objects.create(
                lead=lead,
                old_status='',
                new_status=lead.status,
                changed_by=request.user,
                notes="Initial lead record created."
            )

            # Score lead upon creation for immediate AI priority
            LeadScoringService.score_lead(lead, save=True)

            log_activity(
                request,
                action='CREATE',
                module_name='LeadManagement',
                record_id=str(lead.id),
                description=f"Created lead '{lead.lead_code}' ({lead.full_name}) from source '{lead.get_source_display()}'."
            )

            messages.success(request, f"Lead '{lead.full_name}' ({lead.lead_code}) created successfully.")
            return redirect('leads:lead_detail', pk=lead.pk)

        return render(request, self.template_name, {
            'page_title': 'Add New Lead',
            'form': form,
            'is_create': True,
        })


class LeadEditView(LeadBaseAccessMixin, View):
    """
    Edits lead details. Records status history only when status actually changes.
    """
    template_name = 'leads/lead_form.html'

    def get(self, request, pk):
        lead = self.get_lead_for_user(pk)
        form = LeadForm(instance=lead, user=request.user)
        return render(request, self.template_name, {
            'page_title': f"Edit Lead: {lead.full_name}",
            'form': form,
            'lead': lead,
            'is_create': False,
        })

    def post(self, request, pk):
        lead = self.get_lead_for_user(pk)
        old_status = lead.status
        old_assigned = lead.assigned_staff

        form = LeadForm(request.POST, instance=lead, user=request.user)
        if form.is_valid():
            updated_lead = form.save(commit=False)

            # If sales staff, protect assigned_staff from modification
            if request.user.is_sales_staff:
                updated_lead.assigned_staff = old_assigned

            updated_lead.save()

            # Create status history ONLY if status actually changed
            if updated_lead.status != old_status:
                LeadStatusHistory.objects.create(
                    lead=updated_lead,
                    old_status=old_status,
                    new_status=updated_lead.status,
                    changed_by=request.user,
                    notes="Status updated via lead edit form."
                )

            # Recalculate AI score on lead update
            LeadScoringService.score_lead(updated_lead, save=True)

            log_activity(
                request,
                action='UPDATE',
                module_name='LeadManagement',
                record_id=str(updated_lead.id),
                description=f"Updated details for lead '{updated_lead.lead_code}' ({updated_lead.full_name})."
            )

            messages.success(request, f"Lead '{updated_lead.full_name}' updated successfully.")
            return redirect('leads:lead_detail', pk=updated_lead.pk)

        return render(request, self.template_name, {
            'page_title': f"Edit Lead: {lead.full_name}",
            'form': form,
            'lead': lead,
            'is_create': False,
        })


class LeadStatusUpdateView(LeadBaseAccessMixin, View):
    """
    POST-only endpoint to transition a lead's pipeline stage.
    Creates LeadStatusHistory only when status actually changes.
    """
    http_method_names = ['post']

    def post(self, request, pk):
        lead = self.get_lead_for_user(pk)
        new_status = request.POST.get('status', '').strip()
        notes = request.POST.get('notes', '').strip()

        if new_status in dict(Lead.Status.choices):
            old_status = lead.status
            if new_status != old_status:
                lead.status = new_status
                lead.save(update_fields=['status', 'updated_at'])

                # Record history entry
                LeadStatusHistory.objects.create(
                    lead=lead,
                    old_status=old_status,
                    new_status=new_status,
                    changed_by=request.user,
                    notes=notes or f"Pipeline status transitioned to {lead.get_status_display()}."
                )

                # Rescore lead to reflect new stage
                LeadScoringService.score_lead(lead, save=True)

                log_activity(
                    request,
                    action='STATUS_CHANGE',
                    module_name='LeadManagement',
                    record_id=str(lead.id),
                    description=f"Changed status of lead '{lead.lead_code}' from '{old_status}' to '{new_status}'."
                )
                messages.success(request, f"Lead status updated to {lead.get_status_display()}.")
            else:
                messages.info(request, "Lead is already in the selected status.")
        else:
            messages.error(request, "Invalid status choice selected.")

        next_url = request.POST.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect('leads:lead_detail', pk=lead.pk)


class LeadAssignView(LeadBaseAccessMixin, View):
    """
    Admin-only POST endpoint to reassign a lead to active sales staff.
    """
    http_method_names = ['post']

    def post(self, request, pk):
        if not request.user.is_admin:
            raise PermissionDenied("Only Administrators can assign or reassign leads.")

        lead = get_object_or_404(Lead, pk=pk)
        staff_id = request.POST.get('assigned_staff', '').strip()

        old_staff_str = str(lead.assigned_staff) if lead.assigned_staff else 'Unassigned'

        if staff_id:
            new_staff = get_object_or_404(SalesStaffProfile, pk=staff_id)
            lead.assigned_staff = new_staff
            new_staff_str = str(new_staff)
        else:
            lead.assigned_staff = None
            new_staff_str = 'Unassigned'

        lead.save(update_fields=['assigned_staff', 'updated_at'])

        log_activity(
            request,
            action='ASSIGN',
            module_name='LeadManagement',
            record_id=str(lead.id),
            description=f"Admin reassigned lead '{lead.lead_code}' from {old_staff_str} to {new_staff_str}."
        )

        messages.success(request, f"Lead assigned to {new_staff_str}.")

        next_url = request.POST.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect('leads:lead_detail', pk=lead.pk)


class LeadScoreView(LeadBaseAccessMixin, View):
    """
    POST-only endpoint to calculate or refresh explainable AI lead score.
    Does NOT create a LeadStatusHistory entry.
    """
    http_method_names = ['post']

    def post(self, request, pk):
        lead = self.get_lead_for_user(pk)

        LeadScoringService.score_lead(lead, save=True)

        log_activity(
            request,
            action='AI_SCORE',
            module_name='LeadManagement',
            record_id=str(lead.id),
            description=(
                f"User '{request.user.username}' recalculated AI score for lead '{lead.lead_code}'. "
                f"Result: {lead.probability_score}% ({lead.get_ai_priority_display()} Priority) via {lead.get_scoring_method_display()}."
            )
        )

        messages.success(
            request,
            f"AI Lead Score updated: {lead.probability_score}% ({lead.get_ai_priority_display()} Priority) "
            f"using {lead.get_scoring_method_display()}."
        )

        next_url = request.POST.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect('leads:lead_detail', pk=lead.pk)


class LeadConvertView(LeadBaseAccessMixin, View):
    """
    Atomic Lead-to-Customer conversion view.
    Checks qualification stages, detects existing customer accounts by email/phone,
    supports explicit linking or creates a new customer with a secure password setup link.
    """
    template_name = 'leads/lead_convert.html'

    def get(self, request, pk):
        lead = self.get_lead_for_user(pk)
        eligible, reason = LeadConversionService.check_conversion_eligibility(lead)
        matching_customer = LeadConversionService.find_matching_customer(lead.email, lead.phone)

        return render(request, self.template_name, {
            'page_title': f"Convert Lead: {lead.full_name}",
            'lead': lead,
            'eligible': eligible,
            'ineligible_reason': reason,
            'matching_customer': matching_customer,
            'form': LeadConversionConfirmForm(),
        })

    def post(self, request, pk):
        lead = self.get_lead_for_user(pk)
        form = LeadConversionConfirmForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Invalid conversion parameters.")
            return redirect('leads:lead_convert', pk=lead.pk)

        link_customer_id = form.cleaned_data.get('link_existing_id')
        custom_username = form.cleaned_data.get('custom_username', '')

        link_customer = None
        if link_customer_id:
            link_customer = get_object_or_404(CustomerProfile, pk=link_customer_id)

        try:
            customer, password_setup_url = LeadConversionService.convert_to_customer(
                lead=lead,
                acting_user=request.user,
                link_existing_customer=link_customer,
                custom_username=custom_username,
                request=request
            )
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))
            return redirect('leads:lead_convert', pk=lead.pk)

        # If a new customer account was created with a setup link, render success page with setup link
        if password_setup_url:
            return render(request, 'leads/lead_convert_success.html', {
                'page_title': 'Lead Converted Successfully',
                'lead': lead,
                'customer': customer,
                'password_setup_url': password_setup_url,
            })

        messages.success(
            request,
            f"Lead '{lead.lead_code}' successfully converted and linked to customer profile '{customer.customer_code}'."
        )
        return redirect('accounts:customer_detail', pk=customer.pk)


class LeadArchiveView(LeadBaseAccessMixin, View):
    """
    Admin-only soft deactivation / archiving endpoint.
    Guarantees that leads are never permanently deleted from CRM history.
    """
    template_name = 'leads/lead_confirm_archive.html'

    def get(self, request, pk):
        if not request.user.is_admin:
            raise PermissionDenied("Only Administrators can archive leads.")
        lead = get_object_or_404(Lead, pk=pk)
        return render(request, self.template_name, {
            'page_title': f"Archive Lead: {lead.full_name}",
            'lead': lead,
        })

    def post(self, request, pk):
        if not request.user.is_admin:
            raise PermissionDenied("Only Administrators can archive leads.")
        lead = get_object_or_404(Lead, pk=pk)

        lead.is_active = False
        lead.save(update_fields=['is_active', 'updated_at'])

        log_activity(
            request,
            action='ARCHIVE',
            module_name='LeadManagement',
            record_id=str(lead.id),
            description=f"Admin '{request.user.username}' archived lead '{lead.lead_code}' ({lead.full_name})."
        )

        messages.success(request, f"Lead '{lead.full_name}' ({lead.lead_code}) has been archived.")
        return redirect('leads:lead_list')

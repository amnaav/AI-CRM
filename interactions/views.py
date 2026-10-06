from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.db import models
from django.contrib import messages
from django.http import Http404
from django.core.paginator import Paginator
from core.mixins import StaffRequiredMixin, CustomerRequiredMixin, RoleRequiredMixin
from accounts.models import CustomUser, CustomerProfile
from leads.models import Lead
from sales.models import Sale
from interactions.models import Interaction
from interactions.forms import InteractionForm
from interactions.services import create_interaction, update_interaction


class InteractionListView(StaffRequiredMixin, View):
    template_name = 'interactions/interaction_list.html'

    def get(self, request):
        interactions = Interaction.objects.select_related(
            'customer__user', 'lead', 'sale', 'sales_staff__user', 'created_by'
        )

        # Sales staff filter
        if request.user.is_sales_staff and not request.user.is_admin:
            staff_profile = getattr(request.user, 'staff_profile', None)
            if staff_profile:
                interactions = interactions.filter(
                    models.Q(sales_staff=staff_profile) |
                    models.Q(customer__assigned_staff=staff_profile) |
                    models.Q(lead__assigned_staff=staff_profile) |
                    models.Q(created_by=request.user)
                )

        type_filter = request.GET.get('type')
        search = request.GET.get('search')
        customer_id = request.GET.get('customer')
        lead_id = request.GET.get('lead')

        if type_filter:
            interactions = interactions.filter(interaction_type=type_filter)
        if search:
            interactions = interactions.filter(
                models.Q(subject__icontains=search) |
                models.Q(description__icontains=search) |
                models.Q(customer__user__full_name__icontains=search) |
                models.Q(lead__full_name__icontains=search)
            )
        if customer_id:
            interactions = interactions.filter(customer_id=customer_id)
        if lead_id:
            interactions = interactions.filter(lead_id=lead_id)

        paginator = Paginator(interactions, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_title': 'Customer Touchpoints & Interactions',
            'page_obj': page_obj,
            'type_choices': Interaction.InteractionType.choices,
            'selected_type': type_filter or '',
            'search_query': search or '',
        }
        return render(request, self.template_name, context)


class InteractionDetailView(RoleRequiredMixin, View):
    allowed_roles = [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]
    template_name = 'interactions/interaction_detail.html'

    def get(self, request, pk):
        interaction = get_object_or_404(
            Interaction.objects.select_related('customer__user', 'lead', 'sale', 'sales_staff__user', 'created_by'),
            pk=pk
        )

        # Object-level security for customer role
        if request.user.is_customer:
            cust_profile = getattr(request.user, 'customer_profile', None)
            if not cust_profile or interaction.customer_id != cust_profile.id or not interaction.is_customer_visible:
                raise Http404("Interaction not found or not visible.")

        context = {
            'page_title': f'Interaction: {interaction.subject}',
            'interaction': interaction,
        }
        return render(request, self.template_name, context)


class InteractionCreateView(StaffRequiredMixin, View):
    template_name = 'interactions/interaction_form.html'

    def get(self, request):
        initial = {}
        if request.GET.get('customer'):
            initial['customer'] = request.GET.get('customer')
        if request.GET.get('lead'):
            initial['lead'] = request.GET.get('lead')
        if request.GET.get('sale'):
            initial['sale'] = request.GET.get('sale')

        form = InteractionForm(initial=initial, user=request.user)
        return render(request, self.template_name, {'form': form, 'page_title': 'Log New Interaction'})

    def post(self, request):
        form = InteractionForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                interaction = create_interaction(
                    customer=form.cleaned_data.get('customer'),
                    lead=form.cleaned_data.get('lead'),
                    sale=form.cleaned_data.get('sale'),
                    sales_staff=form.cleaned_data.get('sales_staff'),
                    created_by=request.user,
                    interaction_type=form.cleaned_data['interaction_type'],
                    subject=form.cleaned_data['subject'],
                    description=form.cleaned_data['description'],
                    is_customer_visible=form.cleaned_data.get('is_customer_visible', False),
                    interaction_date=form.cleaned_data.get('interaction_date'),
                    next_follow_up=form.cleaned_data.get('next_follow_up'),
                    request=request
                )
                messages.success(request, f"Logged {interaction.get_interaction_type_display()} successfully.")
                return redirect('interactions:interaction_detail', pk=interaction.pk)
            except Exception as e:
                messages.error(request, f"Error saving interaction: {e}")
        return render(request, self.template_name, {'form': form, 'page_title': 'Log New Interaction'})


class InteractionUpdateView(StaffRequiredMixin, View):
    template_name = 'interactions/interaction_form.html'

    def get(self, request, pk):
        interaction = get_object_or_404(Interaction, pk=pk)
        form = InteractionForm(instance=interaction, user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'interaction': interaction,
            'page_title': f'Edit Interaction #{interaction.pk}'
        })

    def post(self, request, pk):
        interaction = get_object_or_404(Interaction, pk=pk)
        form = InteractionForm(request.POST, instance=interaction, user=request.user)
        if form.is_valid():
            try:
                update_interaction(
                    interaction,
                    customer=form.cleaned_data.get('customer'),
                    lead=form.cleaned_data.get('lead'),
                    sale=form.cleaned_data.get('sale'),
                    sales_staff=form.cleaned_data.get('sales_staff'),
                    interaction_type=form.cleaned_data['interaction_type'],
                    subject=form.cleaned_data['subject'],
                    description=form.cleaned_data['description'],
                    is_customer_visible=form.cleaned_data.get('is_customer_visible', False),
                    interaction_date=form.cleaned_data.get('interaction_date'),
                    next_follow_up=form.cleaned_data.get('next_follow_up'),
                    request=request
                )
                messages.success(request, "Interaction updated successfully.")
                return redirect('interactions:interaction_detail', pk=interaction.pk)
            except Exception as e:
                messages.error(request, f"Error updating interaction: {e}")
        return render(request, self.template_name, {
            'form': form,
            'interaction': interaction,
            'page_title': f'Edit Interaction #{interaction.pk}'
        })


class CustomerInteractionListView(CustomerRequiredMixin, View):
    """
    Customer portal view strictly requiring customer ownership and is_customer_visible=True.
    """
    template_name = 'interactions/customer_interaction_list.html'

    def get(self, request):
        customer_profile = getattr(request.user, 'customer_profile', None)
        if not customer_profile:
            messages.error(request, "Customer profile not found.")
            return redirect('accounts:login')

        interactions = Interaction.objects.filter(
            customer=customer_profile,
            is_customer_visible=True
        ).select_related('sales_staff__user').order_by('-interaction_date')

        paginator = Paginator(interactions, 15)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_title': 'My Communication History',
            'page_obj': page_obj,
        }
        return render(request, self.template_name, context)

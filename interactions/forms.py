from django import forms
from django.utils.translation import gettext_lazy as _
from interactions.models import Interaction
from accounts.models import CustomerProfile, SalesStaffProfile
from leads.models import Lead
from sales.models import Sale


class InteractionForm(forms.ModelForm):
    class Meta:
        model = Interaction
        fields = [
            'interaction_type',
            'subject',
            'description',
            'customer',
            'lead',
            'sale',
            'sales_staff',
            'is_customer_visible',
            'interaction_date',
            'next_follow_up',
        ]
        widgets = {
            'interaction_type': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brief summary of touchpoint'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Detailed notes and discussion points...'}),
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'lead': forms.Select(attrs={'class': 'form-select'}),
            'sale': forms.Select(attrs={'class': 'form-select'}),
            'sales_staff': forms.Select(attrs={'class': 'form-select'}),
            'is_customer_visible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'interaction_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'next_follow_up': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Filter active entities
        self.fields['customer'].queryset = CustomerProfile.objects.filter(is_active=True).select_related('user')
        self.fields['lead'].queryset = Lead.objects.filter(is_active=True)
        self.fields['sale'].queryset = Sale.objects.exclude(status='CANCELLED').select_related('customer__user')
        self.fields['sales_staff'].queryset = SalesStaffProfile.objects.filter(user__status='ACTIVE').select_related('user')

        # Auto-default staff for non-admins
        if user and hasattr(user, 'staff_profile') and not user.is_admin:
            self.fields['sales_staff'].initial = user.staff_profile

    def clean(self):
        cleaned_data = super().clean()
        interaction_type = cleaned_data.get('interaction_type')
        is_customer_visible = cleaned_data.get('is_customer_visible')
        customer = cleaned_data.get('customer')
        lead = cleaned_data.get('lead')
        sale = cleaned_data.get('sale')

        if sale and not customer:
            cleaned_data['customer'] = sale.customer
            customer = sale.customer

        if interaction_type == Interaction.InteractionType.NOTE and is_customer_visible:
            self.add_error('is_customer_visible', _("Internal notes cannot be made visible to customers."))

        if not customer and not lead and not sale:
            raise forms.ValidationError(_("An interaction must target at least a customer, a lead, or a sale."))

        if sale and customer and sale.customer_id != customer.id:
            self.add_error('sale', _("Selected sale belongs to a different customer."))

        if lead and customer:
            if lead.converted_customer_id and lead.converted_customer_id != customer.id:
                self.add_error('customer', _("Selected customer does not match the converted lead customer."))

        if sale and lead:
            if not lead.is_converted or sale.customer_id != lead.converted_customer_id:
                self.add_error('sale', _("Selected sale does not belong to the converted lead customer."))

        return cleaned_data

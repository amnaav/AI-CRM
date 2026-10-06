from django import forms
from django.utils.translation import gettext_lazy as _
from reminders.models import Reminder
from accounts.models import CustomerProfile, CustomUser
from leads.models import Lead
from sales.models import Sale


class ReminderForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = [
            'title',
            'description',
            'reminder_type',
            'priority',
            'due_date',
            'due_time',
            'assigned_to',
            'customer',
            'lead',
            'sale',
            'is_customer_visible',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Task title or objective'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Detailed instructions or agenda...'}),
            'reminder_type': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'lead': forms.Select(attrs={'class': 'form-select'}),
            'sale': forms.Select(attrs={'class': 'form-select'}),
            'is_customer_visible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Restrict assignee choices to active staff and admin users
        self.fields['assigned_to'].queryset = CustomUser.objects.filter(
            status='ACTIVE',
            role__in=[CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF]
        )
        self.fields['customer'].queryset = CustomerProfile.objects.filter(is_active=True).select_related('user')
        self.fields['lead'].queryset = Lead.objects.filter(is_active=True)
        self.fields['sale'].queryset = Sale.objects.exclude(status='CANCELLED').select_related('customer__user')

        if user and not self.initial.get('assigned_to'):
            self.fields['assigned_to'].initial = user

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get('customer')
        lead = cleaned_data.get('lead')
        sale = cleaned_data.get('sale')
        assigned_to = cleaned_data.get('assigned_to')

        if sale and not customer:
            cleaned_data['customer'] = sale.customer
            customer = sale.customer

        if sale and customer and sale.customer_id != customer.id:
            self.add_error('sale', _("Selected sale belongs to a different customer."))

        if lead and customer:
            if lead.converted_customer_id and lead.converted_customer_id != customer.id:
                self.add_error('customer', _("Selected customer does not match the converted lead customer."))

        if sale and lead:
            if not lead.is_converted or sale.customer_id != lead.converted_customer_id:
                self.add_error('sale', _("Selected sale does not belong to the converted lead customer."))

        if assigned_to and assigned_to.is_customer:
            self.add_error('assigned_to', _("Reminders cannot be assigned to customer users."))

        return cleaned_data

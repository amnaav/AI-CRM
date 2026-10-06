from decimal import Decimal
from django import forms
from accounts.models import SalesStaffProfile, CustomUser, CustomerProfile
from leads.models import Lead


class LeadForm(forms.ModelForm):
    """
    Role-aware form for creating and editing Lead records.
    """
    class Meta:
        model = Lead
        fields = [
            'full_name',
            'email',
            'phone',
            'company',
            'source',
            'status',
            'estimated_value',
            'assigned_staff',
            'notes',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Rahul Sharma'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'e.g. rahul@example.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +91 9876543210'}),
            'company': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Acme Innovations'}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'estimated_value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'assigned_staff': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Initial business context, requirements, budget notes...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Filter assigned_staff choices to active staff accounts only
        self.fields['assigned_staff'].queryset = SalesStaffProfile.objects.filter(
            user__is_active=True,
            user__status=CustomUser.Status.ACTIVE
        ).select_related('user')

        # If user is Sales Staff, restrict assigned_staff
        if user and user.is_sales_staff:
            if hasattr(user, 'staff_profile'):
                self.fields['assigned_staff'].initial = user.staff_profile
                self.fields['assigned_staff'].disabled = True
                self.fields['assigned_staff'].required = False

    def clean_estimated_value(self):
        val = self.cleaned_data.get('estimated_value')
        if val is not None and val < Decimal('0.00'):
            raise forms.ValidationError("Estimated deal value cannot be negative.")
        return val or Decimal('0.00')


class LeadAssignForm(forms.Form):
    """
    Admin-only staff assignment form.
    """
    assigned_staff = forms.ModelChoiceField(
        queryset=SalesStaffProfile.objects.filter(
            user__is_active=True,
            user__status=CustomUser.Status.ACTIVE
        ).select_related('user'),
        required=False,
        empty_label="-- Unassigned --",
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class LeadStatusUpdateForm(forms.Form):
    """
    Quick status transition form with optional audit note.
    """
    status = forms.ChoiceField(
        choices=Lead.Status.choices,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Reason or note for status transition...'})
    )


class LeadConversionConfirmForm(forms.Form):
    """
    Form to handle explicit linking to existing customer or custom username.
    """
    link_existing_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    custom_username = forms.CharField(
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional custom username for customer login'})
    )

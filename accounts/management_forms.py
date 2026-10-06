import uuid
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile


class AdminCustomerCreateForm(forms.Form):
    """Admin form to create a new customer and corresponding CUSTOMER login account."""
    full_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'})
    )
    username = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.com'})
    )
    phone = forms.CharField(
        max_length=15,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+91 98765 43210'})
    )
    customer_code = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Auto-generated if blank'})
    )
    company = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Company Name (Optional)'})
    )
    customer_type = forms.ChoiceField(
        choices=CustomerProfile.CustomerType.choices,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_staff = forms.ModelChoiceField(
        queryset=SalesStaffProfile.objects.select_related('user').all(),
        required=False,
        empty_label="-- Select Sales Staff (Optional) --",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    address = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Street Address'})
    )
    city = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'})
    )
    state = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'State'})
    )
    postal_code = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Postal Code'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Internal notes...'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Minimum 8 characters'}),
        min_length=8,
        required=True
    )
    status = forms.ChoiceField(
        choices=CustomUser.Status.choices,
        initial=CustomUser.Status.ACTIVE,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise ValidationError(_("A user with this username already exists."))
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise ValidationError(_("Please enter a valid phone number (at least 10 digits)."))
        if CustomUser.objects.filter(phone=phone).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    def clean_customer_code(self):
        code = self.cleaned_data.get('customer_code', '').strip().upper()
        if code:
            if CustomerProfile.objects.filter(customer_code=code).exists():
                raise ValidationError(_("A customer with this code already exists."))
            return code
        return f"CUST-{uuid.uuid4().hex[:6].upper()}"

    @transaction.atomic
    def save(self):
        code = self.cleaned_data['customer_code']
        user = CustomUser.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password'],
            full_name=self.cleaned_data['full_name'],
            phone=self.cleaned_data['phone'],
            role=CustomUser.Role.CUSTOMER,
            status=self.cleaned_data['status'],
            user_code=code,
            is_staff=False,
            is_superuser=False
        )
        profile = CustomerProfile.objects.create(
            user=user,
            customer_code=code,
            company=self.cleaned_data.get('company', ''),
            customer_type=self.cleaned_data.get('customer_type', CustomerProfile.CustomerType.INDIVIDUAL),
            assigned_staff=self.cleaned_data.get('assigned_staff'),
            address=self.cleaned_data.get('address', ''),
            city=self.cleaned_data.get('city', ''),
            state=self.cleaned_data.get('state', ''),
            postal_code=self.cleaned_data.get('postal_code', ''),
            notes=self.cleaned_data.get('notes', ''),
            is_active=True
        )
        return profile


class AdminCustomerEditForm(forms.ModelForm):
    """Admin form to edit existing customer details and linked user account."""
    full_name = forms.CharField(max_length=150, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone = forms.CharField(max_length=15, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    status = forms.ChoiceField(choices=CustomUser.Status.choices, widget=forms.Select(attrs={'class': 'form-select'}))
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep current password'}),
        required=False,
        min_length=8
    )

    class Meta:
        model = CustomerProfile
        fields = ['company', 'customer_type', 'assigned_staff', 'address', 'city', 'state', 'postal_code', 'notes', 'is_active']
        widgets = {
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'customer_type': forms.Select(attrs={'class': 'form-select'}),
            'assigned_staff': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['full_name'].initial = self.instance.user.full_name
            self.fields['email'].initial = self.instance.user.email
            self.fields['phone'].initial = self.instance.user.phone
            self.fields['status'].initial = self.instance.user.status

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user_id = self.instance.user.id if self.instance else None
        if CustomUser.objects.filter(email__iexact=email).exclude(id=user_id).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise ValidationError(_("Please enter a valid phone number (at least 10 digits)."))
        user_id = self.instance.user.id if self.instance else None
        if CustomUser.objects.filter(phone=phone).exclude(id=user_id).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.full_name = self.cleaned_data['full_name']
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.status = self.cleaned_data['status']
        user.is_active = (user.status == CustomUser.Status.ACTIVE)
        profile.is_active = (user.status == CustomUser.Status.ACTIVE)

        if self.cleaned_data.get('new_password'):
            user.set_password(self.cleaned_data['new_password'])

        if commit:
            user.save()
            profile.save()
        return profile


class StaffCustomerEditForm(forms.ModelForm):
    """Sales Staff restricted customer edit form - cannot change user status, code, role, or assigned staff."""
    full_name = forms.CharField(max_length=150, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    phone = forms.CharField(max_length=15, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = CustomerProfile
        fields = ['company', 'address', 'city', 'state', 'postal_code', 'notes']
        widgets = {
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['full_name'].initial = self.instance.user.full_name
            self.fields['phone'].initial = self.instance.user.phone

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise ValidationError(_("Please enter a valid phone number (at least 10 digits)."))
        user_id = self.instance.user.id if self.instance else None
        if CustomUser.objects.filter(phone=phone).exclude(id=user_id).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.full_name = self.cleaned_data['full_name']
        user.phone = self.cleaned_data['phone']
        if commit:
            user.save(update_fields=['full_name', 'phone'])
            profile.save()
        return profile


class CustomerAssignStaffForm(forms.ModelForm):
    """Quick form to assign or reassign a customer to Sales Staff."""
    class Meta:
        model = CustomerProfile
        fields = ['assigned_staff']
        widgets = {
            'assigned_staff': forms.Select(attrs={'class': 'form-select'}),
        }


# ==============================================================================
# SALES STAFF FORMS
# ==============================================================================

class AdminStaffCreateForm(forms.Form):
    """Admin form to create a new Sales Staff member and linked login account."""
    full_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Staff Member Name'})
    )
    username = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'staff@company.com'})
    )
    phone = forms.CharField(
        max_length=15,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+91 98765 43210'})
    )
    staff_code = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Auto-generated if blank'})
    )
    designation = forms.CharField(
        max_length=100,
        initial='Sales Executive',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    department = forms.CharField(
        max_length=100,
        initial='Direct Sales',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    assigned_target = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        initial=Decimal('100000.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '₹ Monthly target'})
    )
    joining_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    address = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Street Address'})
    )
    city = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'})
    )
    state = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'State'})
    )
    postal_code = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Postal Code'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Minimum 8 characters'}),
        min_length=8,
        required=True
    )
    status = forms.ChoiceField(
        choices=CustomUser.Status.choices,
        initial=CustomUser.Status.ACTIVE,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise ValidationError(_("A user with this username already exists."))
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise ValidationError(_("Please enter a valid phone number (at least 10 digits)."))
        if CustomUser.objects.filter(phone=phone).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    def clean_staff_code(self):
        code = self.cleaned_data.get('staff_code', '').strip().upper()
        if code:
            if SalesStaffProfile.objects.filter(staff_code=code).exists():
                raise ValidationError(_("A staff member with this code already exists."))
            return code
        return f"STF-{uuid.uuid4().hex[:6].upper()}"

    @transaction.atomic
    def save(self):
        code = self.cleaned_data['staff_code']
        user = CustomUser.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password'],
            full_name=self.cleaned_data['full_name'],
            phone=self.cleaned_data['phone'],
            role=CustomUser.Role.SALES_STAFF,
            status=self.cleaned_data['status'],
            user_code=code,
            is_staff=False,
            is_superuser=False
        )
        kwargs = {
            'user': user,
            'staff_code': code,
            'designation': self.cleaned_data['designation'],
            'department': self.cleaned_data['department'],
            'assigned_target': self.cleaned_data['assigned_target'],
            'address': self.cleaned_data.get('address', ''),
            'city': self.cleaned_data.get('city', ''),
            'state': self.cleaned_data.get('state', ''),
            'postal_code': self.cleaned_data.get('postal_code', ''),
        }
        if self.cleaned_data.get('joining_date'):
            kwargs['joining_date'] = self.cleaned_data['joining_date']

        profile = SalesStaffProfile.objects.create(**kwargs)
        return profile


class AdminStaffEditForm(forms.ModelForm):
    """Admin form to edit existing sales staff and linked user account."""
    full_name = forms.CharField(max_length=150, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone = forms.CharField(max_length=15, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    status = forms.ChoiceField(choices=CustomUser.Status.choices, widget=forms.Select(attrs={'class': 'form-select'}))
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to retain current password'}),
        required=False,
        min_length=8
    )

    class Meta:
        model = SalesStaffProfile
        fields = ['designation', 'department', 'assigned_target', 'joining_date', 'address', 'city', 'state', 'postal_code']
        widgets = {
            'designation': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'assigned_target': forms.NumberInput(attrs={'class': 'form-control'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['full_name'].initial = self.instance.user.full_name
            self.fields['email'].initial = self.instance.user.email
            self.fields['phone'].initial = self.instance.user.phone
            self.fields['status'].initial = self.instance.user.status

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user_id = self.instance.user.id if self.instance else None
        if CustomUser.objects.filter(email__iexact=email).exclude(id=user_id).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise ValidationError(_("Please enter a valid phone number (at least 10 digits)."))
        user_id = self.instance.user.id if self.instance else None
        if CustomUser.objects.filter(phone=phone).exclude(id=user_id).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.full_name = self.cleaned_data['full_name']
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.status = self.cleaned_data['status']
        user.is_active = (user.status == CustomUser.Status.ACTIVE)

        if self.cleaned_data.get('new_password'):
            user.set_password(self.cleaned_data['new_password'])

        if commit:
            user.save()
            profile.save()
        return profile

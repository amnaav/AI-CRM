from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from accounts.models import CustomUser, CustomerProfile


class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter username or email',
            'autofocus': True,
            'autocomplete': 'username',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter password',
            'autocomplete': 'current-password',
        })
    )

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        if username and password:
            # Check if input is email instead of username
            if '@' in username:
                try:
                    user_obj = CustomUser.objects.get(email__iexact=username)
                    self.cleaned_data['username'] = user_obj.username
                except CustomUser.DoesNotExist:
                    pass

            super().clean()
            user = self.get_user()
            if user:
                if user.status == CustomUser.Status.LOCKED:
                    raise ValidationError(_("This account is locked. Please contact the administrator."), code='locked')
                if user.status == CustomUser.Status.INACTIVE or not user.is_active:
                    raise ValidationError(_("This account is inactive. Please contact the administrator."), code='inactive')
        return self.cleaned_data


class CustomerRegistrationForm(forms.ModelForm):
    """
    Public self-registration strictly restricted to Customer role.
    Admin or Sales Staff accounts can never be created through this form.
    """
    full_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'})
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
    company = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Company Name (Optional)'})
    )
    customer_type = forms.ChoiceField(
        choices=CustomerProfile.CustomerType.choices,
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
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'PIN / Postal Code'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Minimum 8 characters'}),
        min_length=8
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm Password'}),
        min_length=8
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'full_name', 'email', 'phone']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Choose username'}),
        }

    def clean_email(self):
        raw_email = self.cleaned_data.get('email')
        if raw_email:
            email = raw_email.lower().strip()
            if CustomUser.objects.filter(email__iexact=email).exists():
                raise ValidationError(_("An account with this email address already exists."))
            return email
        return raw_email

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        cleaned_digits = ''.join(c for c in phone if c.isdigit())
        if len(cleaned_digits) < 10:
            raise ValidationError(_("Please enter a valid 10-digit phone number."))
        return phone

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm = cleaned_data.get('confirm_password')

        if password and confirm and password != confirm:
            self.add_error('confirm_password', _("Passwords do not match."))
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        # STRICT ENFORCEMENT: Public registration is ALWAYS CUSTOMER
        user.role = CustomUser.Role.CUSTOMER
        user.status = CustomUser.Status.ACTIVE
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
            CustomerProfile.objects.create(
                user=user,
                company=self.cleaned_data.get('company', ''),
                customer_type=self.cleaned_data.get('customer_type', CustomerProfile.CustomerType.INDIVIDUAL),
                address=self.cleaned_data.get('address', ''),
                city=self.cleaned_data.get('city', ''),
                state=self.cleaned_data.get('state', ''),
                postal_code=self.cleaned_data.get('postal_code', ''),
            )
        return user


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['full_name', 'phone', 'profile_image']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'profile_image': forms.FileInput(attrs={'class': 'form-control'}),
        }


class CustomerProfileDetailsForm(forms.ModelForm):
    class Meta:
        model = CustomerProfile
        fields = ['company', 'address', 'city', 'state', 'postal_code']
        widgets = {
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control'}),
        }

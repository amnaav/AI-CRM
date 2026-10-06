import uuid
from django.db import transaction, models
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse
from django.core.exceptions import ValidationError

from accounts.models import CustomUser, CustomerProfile
from activity_logs.services import log_activity
from leads.models import Lead, LeadStatusHistory

User = get_user_model()


class LeadConversionService:
    """
    Atomic Lead-to-Customer conversion workflow engine.
    Ensures safe qualification checking, duplicate prevention, existing customer linking,
    atomic rollback, secure tokenized password setup, and activity audit logging.
    """
    CONVERTIBLE_STATUSES = [
        Lead.Status.QUALIFIED,
        Lead.Status.PROPOSAL,
        Lead.Status.NEGOTIATION,
    ]

    @classmethod
    def check_conversion_eligibility(cls, lead: Lead) -> tuple[bool, str]:
        """
        Validates whether a lead can be converted.
        Only leads in qualified business stages (QUALIFIED, PROPOSAL, NEGOTIATION) may be converted.
        Already-converted leads cannot be converted again.
        """
        if lead.is_converted:
            return False, f"Lead '{lead.lead_code}' is already converted to customer '{lead.converted_customer.customer_code}'."

        if lead.status not in cls.CONVERTIBLE_STATUSES:
            return False, (
                f"Lead cannot be converted in status '{lead.get_status_display()}'. "
                f"Conversion is restricted to qualified stages: Qualified, Proposal Sent, or Under Negotiation."
            )

        return True, ""

    @classmethod
    def find_matching_customer(cls, email: str, phone: str = '') -> CustomerProfile | None:
        """
        Looks up any existing CustomerProfile matching by email or phone.
        """
        query = models.Q(user__email__iexact=email.strip())
        if phone and phone.strip():
            query |= models.Q(user__phone=phone.strip())

        return CustomerProfile.objects.filter(query).select_related('user').first()

    @classmethod
    def convert_to_customer(
        cls,
        lead: Lead,
        acting_user=None,
        link_existing_customer: CustomerProfile | None = None,
        custom_username: str = '',
        request=None
    ) -> tuple[CustomerProfile, str]:
        """
        Executes atomic conversion.
        Returns (CustomerProfile, password_setup_url).
        """
        eligible, reason = cls.check_conversion_eligibility(lead)
        if not eligible:
            raise ValidationError(reason)

        with transaction.atomic():
            old_status = lead.status
            password_setup_url = ""

            if link_existing_customer:
                customer = link_existing_customer
                conversion_note = (
                    f"Lead {lead.lead_code} converted and linked to existing customer "
                    f"'{customer.customer_code}' ({customer.user.full_name})."
                )
            else:
                # Check again inside transaction to prevent race conditions
                existing = cls.find_matching_customer(lead.email, lead.phone)
                if existing:
                    raise ValidationError(
                        f"An existing customer profile ({existing.customer_code} - {existing.user.full_name}) "
                        f"already matches email '{lead.email}' or phone '{lead.phone}'. Please confirm linking."
                    )

                # Generate unique safe username
                base_username = (custom_username or lead.email.split('@')[0]).lower().replace(' ', '_')
                clean_username = "".join(c for c in base_username if c.isalnum() or c in '_-') or 'customer'
                candidate_username = clean_username
                counter = 1
                while User.objects.filter(username=candidate_username).exists():
                    candidate_username = f"{clean_username}_{counter}"
                    counter += 1

                # Create CustomUser with unusable password
                new_user = User.objects.create(
                    username=candidate_username,
                    email=lead.email,
                    full_name=lead.full_name,
                    phone=lead.phone or '',
                    role=CustomUser.Role.CUSTOMER,
                    status=CustomUser.Status.ACTIVE,
                    is_active=True
                )
                new_user.set_unusable_password()
                new_user.save()

                # Generate secure password setup token link
                uid = urlsafe_base64_encode(force_bytes(new_user.pk))
                token = default_token_generator.make_token(new_user)
                password_setup_url = reverse('accounts:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})

                # Determine customer type based on company field
                cust_type = (
                    CustomerProfile.CustomerType.BUSINESS
                    if lead.company and lead.company.strip()
                    else CustomerProfile.CustomerType.INDIVIDUAL
                )

                # Create linked CustomerProfile
                customer = CustomerProfile.objects.create(
                    user=new_user,
                    company=lead.company or '',
                    customer_type=cust_type,
                    assigned_staff=lead.assigned_staff,
                    notes=f"Converted from Lead {lead.lead_code}.\n{lead.notes}".strip()
                )

                conversion_note = (
                    f"Lead {lead.lead_code} converted to new customer account "
                    f"'{customer.customer_code}' ({new_user.username})."
                )

            # Update lead status to WON and store converted customer
            lead.status = Lead.Status.WON
            lead.converted_customer = customer
            lead.save(update_fields=['status', 'converted_customer', 'updated_at'])

            # Record status transition history
            LeadStatusHistory.objects.create(
                lead=lead,
                old_status=old_status,
                new_status=Lead.Status.WON,
                changed_by=acting_user,
                notes=conversion_note
            )

            # Record in ActivityLog
            if request:
                log_activity(
                    request,
                    action='CONVERT',
                    module_name='LeadManagement',
                    record_id=str(lead.id),
                    description=(
                        f"User '{acting_user.username if acting_user else 'System'}' converted lead "
                        f"'{lead.lead_code}' ({lead.full_name}) to Customer '{customer.customer_code}'."
                    )
                )

        return customer, password_setup_url

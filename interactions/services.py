from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from interactions.models import Interaction
from activity_logs.services import log_activity


def validate_interaction_relationships(customer=None, lead=None, sale=None, sales_staff=None):
    """
    Validates cross-entity consistency for an interaction before write operations:
    - If sale is provided, sale.customer must match customer.
    - If lead is converted, lead.converted_customer must match customer.
    - If sale and lead are provided, sale.customer must match lead.converted_customer.
    - sales_staff user must be active.
    """
    if not customer and not lead and not sale:
        raise ValidationError(_("An interaction must target at least a customer, a lead, or a sale."))

    if sale and customer and sale.customer_id != customer.id:
        raise ValidationError({'sale': _("Selected sale belongs to a different customer.")})

    if lead and customer:
        if lead.converted_customer_id and lead.converted_customer_id != customer.id:
            raise ValidationError({'customer': _("Selected customer does not match the converted lead customer.")})

    if sale and lead:
        if not lead.is_converted or sale.customer_id != lead.converted_customer_id:
            raise ValidationError({'sale': _("Selected sale does not belong to the converted lead customer.")})

    if sales_staff and hasattr(sales_staff, 'user') and not sales_staff.user.is_account_active:
        raise ValidationError({'sales_staff': _("The assigned sales staff account is inactive.")})


@transaction.atomic
def create_interaction(
    *,
    customer=None,
    lead=None,
    sale=None,
    sales_staff=None,
    created_by=None,
    interaction_type=Interaction.InteractionType.CALL,
    subject="",
    description="",
    is_customer_visible=False,
    interaction_date=None,
    next_follow_up=None,
    request=None,
    user=None,
    staff=None,
    notes=None,
    metadata=None,
    **kwargs
):
    """
    Atomic service function to create an interaction touchpoint.
    Enforces relational consistency, internal note visibility rules,
    and audit logging.
    """
    actual_creator = created_by or user
    actual_staff = sales_staff or staff
    actual_desc = description or notes or ""

    if sale and not customer:
        customer = sale.customer

    # Internal notes can never be customer-visible
    if interaction_type == Interaction.InteractionType.NOTE:
        is_customer_visible = False

    validate_interaction_relationships(customer=customer, lead=lead, sale=sale, sales_staff=actual_staff)

    interaction = Interaction(
        customer=customer,
        lead=lead,
        sale=sale,
        sales_staff=actual_staff,
        created_by=actual_creator,
        interaction_type=interaction_type,
        subject=subject.strip(),
        description=actual_desc.strip(),
        is_customer_visible=is_customer_visible,
        interaction_date=interaction_date or timezone.now(),
        next_follow_up=next_follow_up
    )
    interaction.full_clean()
    interaction.save()

    # Log activity
    target_desc = str(customer or lead or (sale and sale.customer) or "General")
    log_meta = {
        'interaction_id': interaction.pk,
        'interaction_type': interaction.interaction_type,
        'customer_id': customer.pk if customer else None,
        'lead_id': lead.pk if lead else None,
        'sale_id': sale.pk if sale else None,
        'is_customer_visible': is_customer_visible,
    }
    if metadata and isinstance(metadata, dict):
        log_meta.update(metadata)

    log_activity(
        request=request,
        user=actual_creator,
        action="CREATE",
        module_name="Interaction",
        record_id=str(interaction.pk),
        entity_type="Interaction",
        entity_id=str(interaction.pk),
        description=f"Logged {interaction.get_interaction_type_display()} with {target_desc}: {interaction.subject}",
        metadata=log_meta
    )

    return interaction


@transaction.atomic
def update_interaction(interaction, *, request=None, user=None, **fields):
    """
    Atomic service function to update an interaction with full relational validation.
    """
    if 'notes' in fields and 'description' not in fields:
        fields['description'] = fields.pop('notes')
    if 'staff' in fields and 'sales_staff' not in fields:
        fields['sales_staff'] = fields.pop('staff')

    for key, value in fields.items():
        if hasattr(interaction, key):
            setattr(interaction, key, value)

    if interaction.sale and not interaction.customer:
        interaction.customer = interaction.sale.customer

    if interaction.interaction_type == Interaction.InteractionType.NOTE:
        interaction.is_customer_visible = False

    validate_interaction_relationships(
        customer=interaction.customer,
        lead=interaction.lead,
        sale=interaction.sale,
        sales_staff=interaction.sales_staff
    )

    interaction.full_clean()
    interaction.save()

    log_activity(
        request=request,
        user=user or (request.user if request and hasattr(request, 'user') else None),
        action="UPDATE",
        module_name="Interaction",
        record_id=str(interaction.pk),
        entity_type="Interaction",
        entity_id=str(interaction.pk),
        description=f"Updated interaction #{interaction.pk}: {interaction.subject}",
        metadata={'interaction_id': interaction.pk}
    )
    return interaction


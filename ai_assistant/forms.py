from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from ai_assistant.services.intent_service import sanitize_query, MAX_QUERY_LENGTH


class AssistantQuestionForm(forms.Form):
    question = forms.CharField(
        max_length=MAX_QUERY_LENGTH,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ask a CRM question (e.g., "CRM Summary", "Lead Pipeline", "My Purchases")...',
            'id': 'assistantQuestionInput',
            'autocomplete': 'off',
            'aria-label': 'Ask CRM Question',
        }),
        error_messages={
            'required': _('Please enter a question or select a suggested topic below.'),
            'max_length': _(f'Questions cannot exceed {MAX_QUERY_LENGTH} characters.'),
        }
    )
    customer_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput()
    )

    def clean_question(self):
        raw_question = self.cleaned_data.get('question', '')
        cleaned = sanitize_query(raw_question)
        if not cleaned:
            raise ValidationError(_('Please enter a valid question.'))
        if len(cleaned) > MAX_QUERY_LENGTH:
            raise ValidationError(_(f'Questions cannot exceed {MAX_QUERY_LENGTH} characters.'))
        return cleaned

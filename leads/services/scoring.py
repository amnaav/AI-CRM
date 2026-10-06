from decimal import Decimal
from django.utils import timezone
from leads.models import Lead


class LeadScoringService:
    """
    Explainable AI Lead Scoring Foundation service.
    
    Implements a documented weighted heuristic rules engine as well as a conditional
    Scikit-learn Logistic Regression engine when sufficient historical conversion
    data exists.
    """
    MIN_HISTORICAL_SAMPLES = 20
    MIN_CLASS_SAMPLES = 5

    @classmethod
    def score_lead(cls, lead: Lead, save: bool = True) -> Lead:
        """
        Calculates and applies lead score, priority, method stamp, and explainability breakdown.
        Never changes business status or creates LeadStatusHistory.
        """
        # Check if sufficient closed conversion data exists for machine learning
        use_ml, training_data = cls._can_use_machine_learning()

        if use_ml:
            score, priority, method, explanation = cls._score_via_machine_learning(lead, training_data)
        else:
            score, priority, method, explanation = cls._score_via_heuristic(lead)

        # Enforce exact clamping between 0 and 100
        score = max(Decimal('0.00'), min(Decimal('100.00'), Decimal(str(round(score, 2)))))
        priority = cls._determine_priority(score)

        lead.probability_score = score
        lead.ai_priority = priority
        lead.scoring_method = method
        lead.scoring_explanation = explanation
        lead.last_scored_at = timezone.now()

        if save:
            lead.save(update_fields=[
                'probability_score',
                'ai_priority',
                'scoring_method',
                'scoring_explanation',
                'last_scored_at',
                'updated_at'
            ])

        return lead

    @classmethod
    def _determine_priority(cls, score: Decimal) -> str:
        if score >= Decimal('70.00'):
            return Lead.Priority.HIGH
        elif score >= Decimal('40.00'):
            return Lead.Priority.MEDIUM
        return Lead.Priority.LOW

    @classmethod
    def _score_via_heuristic(cls, lead: Lead) -> tuple[Decimal, str, str, list]:
        """
        Evaluates verifiable CRM factors using a weighted heuristic rules engine.
        Returns: (score, priority, scoring_method, explanation)
        """
        factors = []
        raw_score = 0

        # 1. Pipeline Status Stage Factor
        status_weights = {
            Lead.Status.WON: (40, "Lead is marked as Closed Won"),
            Lead.Status.NEGOTIATION: (35, "Under active proposal negotiation"),
            Lead.Status.PROPOSAL: (28, "Formal proposal presented to client"),
            Lead.Status.QUALIFIED: (20, "Lead business intent and budget qualified"),
            Lead.Status.CONTACTED: (10, "Initial contact established"),
            Lead.Status.NEW: (5, "New inbound lead stage"),
            Lead.Status.LOST: (0, "Lead marked as Closed Lost"),
        }
        status_pts, status_desc = status_weights.get(lead.status, (5, "Standard pipeline status"))
        raw_score += status_pts
        factors.append({
            "factor": f"Status: {lead.get_status_display()}",
            "impact": f"+{status_pts}" if status_pts > 0 else f"{status_pts}",
            "type": "positive" if status_pts > 0 else "neutral",
            "detail": status_desc
        })

        # 2. Estimated Value Factor
        est_val = Decimal(str(lead.estimated_value or 0.00))
        if est_val >= Decimal('500000.00'):
            raw_score += 15
            factors.append({
                "factor": "High Value Deal (>= ₹500,000)",
                "impact": "+15",
                "type": "positive",
                "detail": f"Deal size of ₹{est_val:,.2f} indicates high commercial priority"
            })
        elif est_val >= Decimal('100000.00'):
            raw_score += 10
            factors.append({
                "factor": "Medium-High Value Deal (₹100,000 - ₹500,000)",
                "impact": "+10",
                "type": "positive",
                "detail": f"Deal size of ₹{est_val:,.2f} represents solid revenue opportunity"
            })
        elif est_val >= Decimal('25000.00'):
            raw_score += 5
            factors.append({
                "factor": "Moderate Value Deal (₹25,000 - ₹100,000)",
                "impact": "+5",
                "type": "positive",
                "detail": f"Deal size of ₹{est_val:,.2f}"
            })
        else:
            factors.append({
                "factor": "Low or Unspecified Value (< ₹25,000)",
                "impact": "+0",
                "type": "neutral",
                "detail": "Modest deal size or value pending evaluation"
            })

        # 3. Source Quality Factor
        source_weights = {
            Lead.Source.REFERRAL: (15, "Customer referrals demonstrate the highest industry conversion likelihood"),
            Lead.Source.PHONE_ENQUIRY: (10, "Direct phone enquiry demonstrates strong active intent"),
            Lead.Source.WALK_IN: (10, "In-person visit demonstrates immediate requirement"),
            Lead.Source.WEBSITE: (10, "Direct inbound website enquiry"),
            Lead.Source.EMAIL_CAMPAIGN: (5, "Inbound lead originating from targeted email marketing"),
            Lead.Source.SOCIAL_MEDIA: (5, "Lead generated via social media outreach"),
            Lead.Source.COLD_CALL: (0, "Outbound cold outreach typically requires longer qualification"),
            Lead.Source.EVENT: (5, "Trade show lead"),
            Lead.Source.OTHER: (0, "Unclassified lead source"),
        }
        source_pts, source_desc = source_weights.get(lead.source, (0, "Standard lead source"))
        raw_score += source_pts
        factors.append({
            "factor": f"Acquisition Source: {lead.get_source_display()}",
            "impact": f"+{source_pts}",
            "type": "positive" if source_pts > 0 else "neutral",
            "detail": source_desc
        })

        # 4. Interaction Touchpoint Count
        # If lead has been saved to DB, inspect interactions relationship
        interaction_count = 0
        latest_interaction_date = None
        if lead.pk:
            interaction_count = lead.interactions.count()
            latest = lead.interactions.order_by('-interaction_date').first()
            if latest:
                latest_interaction_date = latest.interaction_date

        if interaction_count >= 5:
            raw_score += 15
            factors.append({
                "factor": f"High Engagement ({interaction_count} touchpoints)",
                "impact": "+15",
                "type": "positive",
                "detail": "Frequent engagement signals strong mutual momentum"
            })
        elif interaction_count >= 3:
            raw_score += 10
            factors.append({
                "factor": f"Moderate Engagement ({interaction_count} touchpoints)",
                "impact": "+10",
                "type": "positive",
                "detail": "Multiple conversations logged with client"
            })
        elif interaction_count >= 1:
            raw_score += 5
            factors.append({
                "factor": f"Early Engagement ({interaction_count} touchpoints)",
                "impact": "+5",
                "type": "positive",
                "detail": "Initial interaction logged"
            })
        else:
            raw_score -= 5
            factors.append({
                "factor": "No Touchpoints Logged",
                "impact": "-5",
                "type": "negative",
                "detail": "No customer touchpoints logged yet"
            })

        # 5. Recency of Engagement (Days since last touchpoint)
        now = timezone.now()
        if latest_interaction_date:
            days_since = (now - latest_interaction_date).days
            if days_since <= 3:
                raw_score += 15
                factors.append({
                    "factor": "Very Recent Activity (within 3 days)",
                    "impact": "+15",
                    "type": "positive",
                    "detail": f"Last touchpoint was {days_since} day(s) ago; high momentum"
                })
            elif days_since <= 7:
                raw_score += 10
                factors.append({
                    "factor": "Recent Activity (4-7 days)",
                    "impact": "+10",
                    "type": "positive",
                    "detail": f"Last touchpoint was {days_since} days ago; active cadence"
                })
            elif days_since <= 14:
                raw_score += 3
                factors.append({
                    "factor": "Moderate Recency (8-14 days)",
                    "impact": "+3",
                    "type": "neutral",
                    "detail": f"Last touchpoint was {days_since} days ago"
                })
            elif days_since <= 30:
                raw_score -= 5
                factors.append({
                    "factor": "Stale Engagement (15-30 days)",
                    "impact": "-5",
                    "type": "negative",
                    "detail": f"No conversation logged for {days_since} days; deal cooling down"
                })
            else:
                raw_score -= 10
                factors.append({
                    "factor": "Dormant Lead (> 30 days)",
                    "impact": "-10",
                    "type": "negative",
                    "detail": f"Over {days_since} days without customer interaction"
                })
        else:
            raw_score -= 10
            factors.append({
                "factor": "No Recent Activity Recorded",
                "impact": "-10",
                "type": "negative",
                "detail": "Lack of interaction history diminishes deal probability"
            })

        # 6. Task and Reminder Follow-up Cadence
        if lead.pk:
            today = now.date()
            overdue_reminders = lead.reminders.filter(status='PENDING', due_date__lt=today).count()
            completed_reminders = lead.reminders.filter(status='COMPLETED').count()
            upcoming_reminders = lead.reminders.filter(status='PENDING', due_date__gte=today).count()

            if overdue_reminders > 0:
                penalty = min(15, overdue_reminders * 10)
                raw_score -= penalty
                factors.append({
                    "factor": f"Overdue Tasks ({overdue_reminders} overdue)",
                    "impact": f"-{penalty}",
                    "type": "negative",
                    "detail": f"{overdue_reminders} scheduled follow-up tasks are past due"
                })

            if completed_reminders > 0:
                raw_score += 5
                factors.append({
                    "factor": f"Completed Follow-up Tasks ({completed_reminders})",
                    "impact": "+5",
                    "type": "positive",
                    "detail": "Sales representative has followed up promptly"
                })

            if upcoming_reminders > 0:
                raw_score += 5
                factors.append({
                    "factor": f"Scheduled Future Follow-ups ({upcoming_reminders})",
                    "impact": "+5",
                    "type": "positive",
                    "detail": "Follow-up tasks are proactively scheduled on the calendar"
                })

        # 7. Lost Lead Explicit Zeroing
        if lead.status == Lead.Status.LOST:
            raw_score = 0
            factors.append({
                "factor": "Closed Lost Overwrite",
                "impact": "Score = 0",
                "type": "negative",
                "detail": "Leads categorized as Closed Lost have 0% conversion probability"
            })

        final_score = max(Decimal('0.00'), min(Decimal('100.00'), Decimal(str(raw_score))))
        priority = cls._determine_priority(final_score)
        return final_score, priority, Lead.ScoringMethod.HEURISTIC, factors

    @classmethod
    def _can_use_machine_learning(cls) -> tuple[bool, list]:
        """
        Determines whether sufficient valid historical training data exists to use Scikit-learn Logistic Regression.
        Requires:
        1. At least 20 closed leads (status=WON or status=LOST).
        2. At least 5 WON samples.
        3. At least 5 LOST samples.
        """
        closed_leads = list(Lead.objects.filter(status__in=[Lead.Status.WON, Lead.Status.LOST]))
        total_closed = len(closed_leads)

        if total_closed < cls.MIN_HISTORICAL_SAMPLES:
            return False, []

        won_count = sum(1 for l in closed_leads if l.status == Lead.Status.WON)
        lost_count = total_closed - won_count

        if won_count < cls.MIN_CLASS_SAMPLES or lost_count < cls.MIN_CLASS_SAMPLES:
            return False, []

        return True, closed_leads

    @classmethod
    def _score_via_machine_learning(cls, lead: Lead, training_leads: list[Lead]) -> tuple[Decimal, str, str, list]:
        """
        Trains a Logistic Regression model on historical closed leads and predicts conversion probability.
        Extracts localized feature importances for explainability.
        """
        from sklearn.linear_model import LogisticRegression
        import numpy as np

        def extract_features(l: Lead) -> list[float]:
            # Status rank: NEW=1, CONTACTED=2, QUALIFIED=3, PROPOSAL=4, NEGOTIATION=5, WON=6, LOST=0
            status_map = {
                Lead.Status.LOST: 0,
                Lead.Status.NEW: 1,
                Lead.Status.CONTACTED: 2,
                Lead.Status.QUALIFIED: 3,
                Lead.Status.PROPOSAL: 4,
                Lead.Status.NEGOTIATION: 5,
                Lead.Status.WON: 6,
            }
            s_val = float(status_map.get(l.status, 1))

            # Log-scaled value
            val = float(l.estimated_value or 0.0)
            log_val = float(np.log1p(max(0.0, val)))

            # Source quality
            source_map = {
                Lead.Source.REFERRAL: 3.0,
                Lead.Source.PHONE_ENQUIRY: 2.0,
                Lead.Source.WALK_IN: 2.0,
                Lead.Source.WEBSITE: 2.0,
                Lead.Source.EMAIL_CAMPAIGN: 1.0,
                Lead.Source.SOCIAL_MEDIA: 1.0,
                Lead.Source.COLD_CALL: 0.0,
                Lead.Source.EVENT: 1.0,
                Lead.Source.OTHER: 0.0,
            }
            src_val = source_map.get(l.source, 1.0)

            # Interaction count & recency
            int_count = float(l.interactions.count()) if l.pk else 0.0
            latest_int = l.interactions.order_by('-interaction_date').first() if l.pk else None
            days_since = float((timezone.now() - latest_int.interaction_date).days) if latest_int else 60.0

            # Overdue reminders
            today = timezone.now().date()
            overdue = float(l.reminders.filter(status='PENDING', due_date__lt=today).count()) if l.pk else 0.0

            return [s_val, log_val, src_val, int_count, days_since, overdue]

        # Prepare dataset
        X = [extract_features(l) for l in training_leads]
        y = [1 if l.status == Lead.Status.WON else 0 for l in training_leads]

        model = LogisticRegression(random_state=42, max_iter=200)
        model.fit(X, y)

        target_features = extract_features(lead)
        # Probability of class 1 (WON)
        prob = model.predict_proba([target_features])[0][1]
        score = Decimal(str(round(prob * 100, 2)))

        # Feature coefficients for explainability
        feature_names = [
            ("Pipeline Stage", target_features[0]),
            ("Estimated Value", target_features[1]),
            ("Acquisition Source", target_features[2]),
            ("Touchpoint Count", target_features[3]),
            ("Days Since Interaction", target_features[4]),
            ("Overdue Tasks", target_features[5]),
        ]
        coefs = model.coef_[0]

        factors = [
            {
                "factor": "ML Statistical Model",
                "impact": f"{score}%",
                "type": "positive" if score >= Decimal('50.00') else "negative",
                "detail": f"Scikit-learn Logistic Regression trained on {len(training_leads)} closed CRM deals."
            }
        ]

        for i, (name, val) in enumerate(feature_names):
            impact = coefs[i] * val
            if abs(impact) > 0.1:
                factors.append({
                    "factor": f"ML Weight: {name}",
                    "impact": f"+{abs(impact):.2f}" if impact > 0 else f"-{abs(impact):.2f}",
                    "type": "positive" if impact > 0 else "negative",
                    "detail": f"Coefficient impact ({coefs[i]:.2f}) for feature magnitude ({val:.2f})"
                })

        priority = cls._determine_priority(score)
        return score, priority, Lead.ScoringMethod.MACHINE_LEARNING, factors

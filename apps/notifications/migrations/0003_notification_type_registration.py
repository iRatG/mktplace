from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='type',
            field=models.CharField(choices=[('deal_created', 'Deal Created'), ('deal_updated', 'Deal Updated'), ('deal_completed', 'Deal Completed'), ('deal_cancelled', 'Deal Cancelled'), ('deal_disputed', 'Deal Disputed'), ('creative_submitted', 'Creative Submitted'), ('creative_approved', 'Creative Approved'), ('creative_rejected', 'Creative Rejected'), ('payment_received', 'Payment Received'), ('withdrawal_approved', 'Withdrawal Approved'), ('withdrawal_rejected', 'Withdrawal Rejected'), ('campaign_response', 'Campaign Response'), ('campaign_status', 'Campaign Status Changed'), ('platform_moderated', 'Platform Moderated'), ('response_accepted', 'Response Accepted'), ('response_rejected', 'Response Rejected'), ('direct_offer_received', 'Direct Offer Received'), ('direct_offer_accepted', 'Direct Offer Accepted'), ('direct_offer_rejected', 'Direct Offer Rejected'), ('legal_entity_assigned', 'Legal Entity Assigned'), ('legal_entity_approved', 'Legal Entity Approved'), ('legal_entity_rejected', 'Legal Entity Rejected'), ('ip_application_approved', 'IP Application Approved'), ('ip_application_rejected', 'IP Application Rejected'), ('system', 'System')], max_length=40),
        ),
    ]

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("history", "0006_account_identity_client_session")]

    operations = [
        migrations.AddField(
            model_name="traceuploadtoken",
            name="client_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="trace_upload_tokens",
                to="history.clientsession",
            ),
        ),
        migrations.AddField(
            model_name="traceuploadtoken",
            name="revocation_reason",
            field=models.CharField(
                blank=True,
                choices=[("rotated", "Rotated"), ("revoked", "Revoked")],
                max_length=16,
            ),
        ),
    ]

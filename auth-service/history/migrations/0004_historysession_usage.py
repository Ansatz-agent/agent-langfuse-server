from django.db import migrations, models

USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
MAX_BIGINT = 9_223_372_036_854_775_807


def backfill_session_usage(apps, schema_editor):
    HistorySession = apps.get_model("history", "HistorySession")
    pending = []
    for session in HistorySession.objects.only("pk", "raw_metadata").iterator(chunk_size=500):
        metadata = session.raw_metadata if isinstance(session.raw_metadata, dict) else {}
        for field in USAGE_FIELDS:
            value = metadata.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int):
                value = 0
            if value < 0 or value > MAX_BIGINT:
                value = 0
            setattr(session, field, value)
        pending.append(session)
        if len(pending) >= 500:
            HistorySession.objects.bulk_update(pending, USAGE_FIELDS, batch_size=500)
            pending.clear()
    if pending:
        HistorySession.objects.bulk_update(pending, USAGE_FIELDS, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("history", "0003_usermemorypool")]

    operations = [
        migrations.AddField(
            model_name="historysession",
            name="cache_read_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="historysession",
            name="cache_write_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="historysession",
            name="input_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="historysession",
            name="output_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="historysession",
            name="reasoning_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(backfill_session_usage, migrations.RunPython.noop),
    ]

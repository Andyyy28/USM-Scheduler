from django.db import migrations, models
from django.db.models.functions import Cast


def preserve_large_metrics(apps, schema_editor):
    Metric = apps.get_model("scheduler", "RunMetric")
    alias = schema_editor.connection.alias
    oversized = Metric.objects.using(alias).filter(
        models.Q(value__gte=10**18) | models.Q(value__lte=-(10**18))
    ).annotate(stored_text=Cast("value", output_field=models.CharField())).values(
        "pk", "name", "metadata", "stored_text", "run__diagnostics", "run__result_data",
    )
    for row in oversized.iterator(chunk_size=500):
        # Do not select DecimalField.value: old oversized SQLite rows raise
        # InvalidOperation in its converter before they can be repaired.
        original = (row["run__diagnostics"] or {}).get("metrics", {}).get(row["name"])
        if original is None:
            original = (row["run__result_data"] or {}).get("metrics", {}).get(row["name"])
        metadata = dict(row["metadata"] or {})
        metadata.update({
            "exact_value": str(original) if original is not None else row["stored_text"],
            "storage": "outside_decimal_range",
            "recovered_from": "run_json" if original is not None else "stored_database_text",
        })
        Metric.objects.using(alias).filter(pk=row["pk"]).update(value=None, metadata=metadata)


class Migration(migrations.Migration):
    dependencies = [("scheduler", "0007_allow_review_decision_history")]

    operations = [
        migrations.AlterField(
            model_name="runmetric", name="value",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=24, null=True),
        ),
        migrations.RunPython(preserve_large_metrics, reverse_code=migrations.RunPython.noop),
    ]

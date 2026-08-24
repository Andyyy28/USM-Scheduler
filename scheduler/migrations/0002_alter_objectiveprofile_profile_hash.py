import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='objectiveprofile',
            name='profile_hash',
            field=models.CharField(db_index=True, editable=False, max_length=64, validators=[django.core.validators.RegexValidator(message='Enter a lowercase 64-character SHA-256 digest.', regex='^[0-9a-f]{64}$')]),
        ),
    ]

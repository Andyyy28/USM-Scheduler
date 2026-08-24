import django.contrib.auth.models
import django.contrib.auth.validators
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import scheduler.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='Capability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=160, unique=True)),
                ('description', models.TextField(blank=True)),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('username', models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('role', models.CharField(choices=[('SYSTEM_ADMIN', 'System administrator'), ('CENTRAL_SCHEDULER', 'Central scheduler'), ('COLLEGE_REVIEWER', 'College reviewer')], default='COLLEGE_REVIEWER', max_length=24)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
            ],
            options={
                'verbose_name': 'user',
                'verbose_name_plural': 'users',
                'abstract': False,
            },
            managers=[
                ('objects', django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name='AcademicTerm',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('academic_year', models.CharField(help_text='For example: 2026-2027', max_length=9)),
                ('semester', models.CharField(choices=[('FIRST', 'First semester'), ('SECOND', 'Second semester'), ('MIDYEAR', 'Midyear')], max_length=10)),
                ('campus', models.CharField(max_length=120)),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField()),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('ACTIVE', 'Active'), ('CLOSED', 'Closed'), ('ARCHIVED', 'Archived')], default='DRAFT', max_length=10)),
            ],
            options={
                'ordering': ['-starts_on', 'campus'],
                'indexes': [models.Index(fields=['campus', 'status'], name='scheduler_a_campus_d118c3_idx')],
                'constraints': [models.UniqueConstraint(fields=('academic_year', 'semester', 'campus'), name='uniq_academic_term'), models.CheckConstraint(condition=models.Q(('ends_on__gt', models.F('starts_on'))), name='term_end_after_start')],
            },
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=100)),
                ('entity_type', models.CharField(max_length=100)),
                ('entity_id', models.CharField(blank=True, max_length=100)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='College',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['code'],
                'indexes': [models.Index(fields=['is_active', 'code'], name='scheduler_c_is_acti_edd018_idx')],
            },
        ),
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('is_active', models.BooleanField(default=True)),
                ('college', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='departments', to='scheduler.college')),
            ],
            options={
                'ordering': ['college__code', 'code'],
            },
        ),
        migrations.CreateModel(
            name='CourseOffering',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('external_key', models.CharField(max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('offering_department', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='course_offerings', to='scheduler.department')),
            ],
            options={
                'ordering': ['subject__code', 'external_key'],
            },
        ),
        migrations.CreateModel(
            name='ImportBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('original_filename', models.CharField(max_length=255)),
                ('file_hash', models.CharField(max_length=64, validators=[django.core.validators.RegexValidator(message='Enter a lowercase 64-character SHA-256 digest.', regex='^[0-9a-f]{64}$')])),
                ('status', models.CharField(choices=[('UPLOADED', 'Uploaded'), ('PREVIEWED', 'Previewed'), ('INVALID', 'Invalid'), ('COMMITTED', 'Committed'), ('CANCELLED', 'Cancelled')], default='UPLOADED', max_length=12)),
                ('total_rows', models.PositiveIntegerField(default=0)),
                ('error_count', models.PositiveIntegerField(default=0)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('term', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='import_batches', to='scheduler.academicterm')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='import_batches', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ImportError',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sheet_name', models.CharField(max_length=100)),
                ('row_number', models.PositiveIntegerField(blank=True, null=True)),
                ('column_name', models.CharField(blank=True, max_length=100)),
                ('code', models.CharField(max_length=80)),
                ('message', models.TextField()),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='errors', to='scheduler.importbatch')),
            ],
            options={
                'ordering': ['sheet_name', 'row_number', 'column_name'],
            },
        ),
        migrations.CreateModel(
            name='Instructor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee_code', models.CharField(max_length=80, unique=True)),
                ('display_name', models.CharField(max_length=200)),
                ('is_active', models.BooleanField(default=True)),
                ('department', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='instructors', to='scheduler.department')),
            ],
            options={
                'ordering': ['display_name'],
            },
        ),
        migrations.CreateModel(
            name='MeetingRequiredCapability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('capability', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='meeting_requirement_links', to='scheduler.capability')),
            ],
        ),
        migrations.CreateModel(
            name='MeetingRequirement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('stable_key', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('component', models.CharField(choices=[('LECTURE', 'Lecture'), ('LAB', 'Laboratory'), ('TUTORIAL', 'Tutorial'), ('PRACTICUM', 'Practicum'), ('OTHER', 'Other')], max_length=10)),
                ('occurrence_number', models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('duration_atoms', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('distinct_day_group', models.CharField(blank=True, max_length=80)),
                ('is_active', models.BooleanField(default=True)),
                ('offering', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meeting_requirements', to='scheduler.courseoffering')),
                ('required_capabilities', models.ManyToManyField(related_name='meeting_requirements', through='scheduler.MeetingRequiredCapability', to='scheduler.capability')),
            ],
            options={
                'ordering': ['offering', 'component', 'occurrence_number'],
            },
        ),
        migrations.AddField(
            model_name='meetingrequiredcapability',
            name='meeting_requirement',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='capability_links', to='scheduler.meetingrequirement'),
        ),
        migrations.CreateModel(
            name='ObjectiveProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('version', models.PositiveIntegerField(default=1)),
                ('weights', models.JSONField(default=scheduler.models.default_objective_weights)),
                ('definitions', models.JSONField(blank=True, default=dict)),
                ('normalization_denominators', models.JSONField(blank=True, default=dict)),
                ('profile_hash', models.CharField(editable=False, max_length=64, unique=True, validators=[django.core.validators.RegexValidator(message='Enter a lowercase 64-character SHA-256 digest.', regex='^[0-9a-f]{64}$')])),
                ('is_approved', models.BooleanField(default=False)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='approved_objective_profiles', to=settings.AUTH_USER_MODEL)),
                ('term', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='objective_profiles', to='scheduler.academicterm')),
            ],
            options={
                'ordering': ['name', '-version'],
            },
        ),
        migrations.CreateModel(
            name='OfferingInstructor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('instructor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='offering_links', to='scheduler.instructor')),
                ('offering', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_links', to='scheduler.courseoffering')),
            ],
        ),
        migrations.AddField(
            model_name='courseoffering',
            name='instructors',
            field=models.ManyToManyField(related_name='offerings', through='scheduler.OfferingInstructor', to='scheduler.instructor'),
        ),
        migrations.CreateModel(
            name='ProblemSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('schema_version', models.CharField(default='1.0', max_length=30)),
                ('input_data', models.JSONField()),
                ('candidate_map', models.JSONField()),
                ('snapshot_hash', models.CharField(editable=False, max_length=64, unique=True, validators=[django.core.validators.RegexValidator(message='Enter a lowercase 64-character SHA-256 digest.', regex='^[0-9a-f]{64}$')])),
                ('event_count', models.PositiveIntegerField(default=0)),
                ('candidate_count', models.PositiveIntegerField(default=0)),
                ('preprocessing_seconds', models.FloatField(default=0, validators=[django.core.validators.MinValueValidator(0)])),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_problem_snapshots', to=settings.AUTH_USER_MODEL)),
                ('objective_profile', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='problem_snapshots', to='scheduler.objectiveprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ExperimentBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('QUEUED', 'Queued'), ('RUNNING', 'Running'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled'), ('FAILED', 'Failed')], default='DRAFT', max_length=10)),
                ('seeds', models.JSONField(default=list)),
                ('order_seed', models.PositiveIntegerField(default=0)),
                ('time_limit_seconds', models.PositiveIntegerField(default=300, validators=[django.core.validators.MinValueValidator(1)])),
                ('cpu_limit', models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('memory_limit_mb', models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1)])),
                ('configuration', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_experiment_batches', to=settings.AUTH_USER_MODEL)),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='experiment_batches', to='scheduler.problemsnapshot')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Program',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('curriculum_label', models.CharField(blank=True, max_length=80)),
                ('is_active', models.BooleanField(default=True)),
                ('department', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='programs', to='scheduler.department')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='ProgramSubject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('curriculum_version', models.CharField(max_length=80)),
                ('classification', models.CharField(choices=[('MAJOR', 'Major'), ('MINOR', 'Minor'), ('GE', 'General education')], max_length=10)),
                ('is_active', models.BooleanField(default=True)),
                ('authoritative_college', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='authoritative_program_subjects', to='scheduler.college')),
                ('authoritative_department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='authoritative_program_subjects', to='scheduler.department')),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='program_subjects', to='scheduler.program')),
            ],
            options={
                'ordering': ['program__code', 'curriculum_version', 'subject__code'],
            },
        ),
        migrations.CreateModel(
            name='Room',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(blank=True, max_length=160)),
                ('campus', models.CharField(max_length=120)),
                ('kind', models.CharField(choices=[('CLASSROOM', 'Classroom'), ('LABORATORY', 'Laboratory'), ('SPECIAL', 'Special-purpose room')], default='CLASSROOM', max_length=12)),
                ('is_active', models.BooleanField(default=True)),
                ('owning_college', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='owned_rooms', to='scheduler.college')),
                ('owning_department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='owned_rooms', to='scheduler.department')),
            ],
            options={
                'ordering': ['campus', 'code'],
            },
        ),
        migrations.CreateModel(
            name='LaboratoryProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('laboratory_type', models.CharField(max_length=100)),
                ('notes', models.TextField(blank=True)),
                ('room', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='laboratory_profile', to='scheduler.room')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RoomCapability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('capability', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='room_links', to='scheduler.capability')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='capability_links', to='scheduler.room')),
            ],
        ),
        migrations.CreateModel(
            name='ScheduleRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('algorithm', models.CharField(choices=[('CP_SAT', 'CP-SAT'), ('GA', 'Genetic Algorithm')], max_length=10)),
                ('seed', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('QUEUED', 'Queued'), ('RUNNING', 'Running'), ('FEASIBLE', 'Feasible'), ('OPTIMAL', 'Optimal'), ('INFEASIBLE', 'Proven infeasible'), ('NO_SOLUTION', 'No feasible solution found'), ('TIMEOUT', 'Timed out'), ('CANCELLED', 'Cancelled'), ('FAILED', 'Failed')], default='QUEUED', max_length=12)),
                ('configuration', models.JSONField(blank=True, default=dict)),
                ('task_id', models.CharField(blank=True, db_index=True, max_length=255)),
                ('queued_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('first_feasible_seconds', models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('execution_seconds', models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('objective_value', models.BigIntegerField(blank=True, null=True)),
                ('best_bound', models.FloatField(blank=True, null=True)),
                ('relative_gap', models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('hard_violation_count', models.PositiveIntegerField(default=0)),
                ('stopping_reason', models.CharField(blank=True, max_length=255)),
                ('diagnostics', models.JSONField(blank=True, default=dict)),
                ('result_data', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('experiment_batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='runs', to='scheduler.experimentbatch')),
                ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='requested_schedule_runs', to=settings.AUTH_USER_MODEL)),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='runs', to='scheduler.problemsnapshot')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='RunMetric',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('value', models.DecimalField(decimal_places=6, max_digits=24)),
                ('unit', models.CharField(blank=True, max_length=40)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='metrics', to='scheduler.schedulerun')),
            ],
            options={
                'ordering': ['run', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version_number', models.PositiveIntegerField()),
                ('name', models.CharField(max_length=200)),
                ('source', models.CharField(choices=[('IMPORTED', 'Imported'), ('MANUAL', 'Manual'), ('CP_SAT', 'CP-SAT'), ('GA', 'Genetic Algorithm')], max_length=10)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('UNDER_REVIEW', 'Under review'), ('APPROVED', 'Approved'), ('ARCHIVED', 'Archived')], default='DRAFT', max_length=12)),
                ('objective_value', models.BigIntegerField(blank=True, null=True)),
                ('objective_breakdown', models.JSONField(blank=True, default=dict)),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_schedule_versions', to=settings.AUTH_USER_MODEL)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='children', to='scheduler.scheduleversion')),
                ('run', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='schedule_version', to='scheduler.schedulerun')),
                ('snapshot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='schedule_versions', to='scheduler.problemsnapshot')),
                ('term', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_versions', to='scheduler.academicterm')),
            ],
            options={
                'ordering': ['term', '-version_number'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('COMMENT', 'Comment'), ('CHANGES_REQUESTED', 'Changes requested'), ('ENDORSED', 'Endorsed')], default='COMMENT', max_length=20)),
                ('comment', models.TextField(blank=True)),
                ('is_resolved', models.BooleanField(default=False)),
                ('college', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_reviews', to='scheduler.college')),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_reviews', to=settings.AUTH_USER_MODEL)),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='scheduler.scheduleversion')),
            ],
            options={
                'ordering': ['schedule', 'college', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('placement_data', models.JSONField(blank=True, default=dict)),
                ('objective_contribution', models.JSONField(blank=True, default=dict)),
                ('meeting_requirement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_assignments', to='scheduler.meetingrequirement')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_assignments', to='scheduler.room')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments', to='scheduler.scheduleversion')),
            ],
            options={
                'ordering': ['schedule', 'start_time_slot__day', 'start_time_slot__sequence'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approved_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('notes', models.TextField(blank=True)),
                ('approved_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_approvals', to=settings.AUTH_USER_MODEL)),
                ('schedule', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='approval', to='scheduler.scheduleversion')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Section',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=60)),
                ('year_level', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(10)])),
                ('cohort_status', models.CharField(choices=[('INCOMING', 'Incoming'), ('CONTINUING', 'Continuing'), ('GRADUATING', 'Graduating')], max_length=12)),
                ('is_active', models.BooleanField(default=True)),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sections', to='scheduler.program')),
            ],
            options={
                'ordering': ['program__code', 'year_level', 'code'],
            },
        ),
        migrations.CreateModel(
            name='OfferingSection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('offering', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='section_links', to='scheduler.courseoffering')),
                ('program_subject', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='offering_section_links', to='scheduler.programsubject')),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offering_links', to='scheduler.section')),
            ],
        ),
        migrations.AddField(
            model_name='courseoffering',
            name='sections',
            field=models.ManyToManyField(related_name='offerings', through='scheduler.OfferingSection', to='scheduler.section'),
        ),
        migrations.CreateModel(
            name='Student',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pseudonymous_code', models.CharField(max_length=80, unique=True)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive'), ('GRADUATED', 'Graduated')], default='ACTIVE', max_length=10)),
            ],
            options={
                'ordering': ['pseudonymous_code'],
                'indexes': [models.Index(fields=['status'], name='scheduler_s_status_659786_idx')],
            },
        ),
        migrations.CreateModel(
            name='StudentSectionMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='student_memberships', to='scheduler.section')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='section_memberships', to='scheduler.student')),
            ],
        ),
        migrations.CreateModel(
            name='Subject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30, unique=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['code'],
                'indexes': [models.Index(fields=['is_active', 'code'], name='scheduler_s_is_acti_cd8e50_idx')],
            },
        ),
        migrations.AddField(
            model_name='programsubject',
            name='subject',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='program_subjects', to='scheduler.subject'),
        ),
        migrations.AddField(
            model_name='courseoffering',
            name='subject',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='course_offerings', to='scheduler.subject'),
        ),
        migrations.CreateModel(
            name='TermDatasetRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('revision_number', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('VALIDATED', 'Validated'), ('COMMITTED', 'Committed'), ('SUPERSEDED', 'Superseded')], default='DRAFT', max_length=12)),
                ('label', models.CharField(blank=True, max_length=160)),
                ('content_hash', models.CharField(blank=True, max_length=64, validators=[django.core.validators.RegexValidator(message='Enter a lowercase 64-character SHA-256 digest.', regex='^[0-9a-f]{64}$')])),
                ('committed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_term_revisions', to=settings.AUTH_USER_MODEL)),
                ('term', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='dataset_revisions', to='scheduler.academicterm')),
            ],
            options={
                'ordering': ['term', '-revision_number'],
            },
        ),
        migrations.AddField(
            model_name='section',
            name='revision',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sections', to='scheduler.termdatasetrevision'),
        ),
        migrations.AddField(
            model_name='scheduleversion',
            name='revision',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_versions', to='scheduler.termdatasetrevision'),
        ),
        migrations.CreateModel(
            name='RoomAvailabilityProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assume_fully_available', models.BooleanField(default=False)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='acknowledged_room_availability', to=settings.AUTH_USER_MODEL)),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='availability_profiles', to='scheduler.room')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_availability_profiles', to='scheduler.termdatasetrevision')),
            ],
        ),
        migrations.CreateModel(
            name='RoomAuthorization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('classification', models.CharField(choices=[('MAJOR', 'Major'), ('MINOR', 'Minor'), ('GE', 'General education')], max_length=10)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('college', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='room_authorizations', to='scheduler.college')),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='room_authorizations', to='scheduler.department')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='authorizations', to='scheduler.room')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_authorizations', to='scheduler.termdatasetrevision')),
            ],
        ),
        migrations.AddField(
            model_name='problemsnapshot',
            name='revision',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='problem_snapshots', to='scheduler.termdatasetrevision'),
        ),
        migrations.CreateModel(
            name='InstructorAvailabilityProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assume_fully_available', models.BooleanField(default=False)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='acknowledged_instructor_availability', to=settings.AUTH_USER_MODEL)),
                ('instructor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='availability_profiles', to='scheduler.instructor')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_availability_profiles', to='scheduler.termdatasetrevision')),
            ],
        ),
        migrations.AddField(
            model_name='importbatch',
            name='committed_revision',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='source_import_batch', to='scheduler.termdatasetrevision'),
        ),
        migrations.AddField(
            model_name='courseoffering',
            name='revision',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='course_offerings', to='scheduler.termdatasetrevision'),
        ),
        migrations.CreateModel(
            name='TimeSlot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('day', models.PositiveSmallIntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])),
                ('sequence', models.PositiveSmallIntegerField(help_text='Zero-based order of the atom within the day.')),
                ('starts_at', models.TimeField()),
                ('ends_at', models.TimeField()),
                ('is_break', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='time_slots', to='scheduler.termdatasetrevision')),
            ],
            options={
                'ordering': ['revision', 'day', 'sequence'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleSectionAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='section_allocations', to='scheduler.scheduleassignment')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='section_allocations', to='scheduler.scheduleversion')),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='atom_allocations', to='scheduler.section')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='section_allocations', to='scheduler.timeslot')),
            ],
        ),
        migrations.CreateModel(
            name='ScheduleRoomAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_allocations', to='scheduler.scheduleassignment')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='atom_allocations', to='scheduler.room')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_allocations', to='scheduler.scheduleversion')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='room_allocations', to='scheduler.timeslot')),
            ],
        ),
        migrations.CreateModel(
            name='ScheduleInstructorAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_allocations', to='scheduler.scheduleassignment')),
                ('instructor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='atom_allocations', to='scheduler.instructor')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_allocations', to='scheduler.scheduleversion')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='instructor_allocations', to='scheduler.timeslot')),
            ],
        ),
        migrations.AddField(
            model_name='scheduleassignment',
            name='start_time_slot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='starting_assignments', to='scheduler.timeslot'),
        ),
        migrations.CreateModel(
            name='RoomAvailability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_available', models.BooleanField(default=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='availability_rows', to='scheduler.roomavailabilityprofile')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_availability_rows', to='scheduler.timeslot')),
            ],
        ),
        migrations.CreateModel(
            name='LockedAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reason', models.CharField(max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('locked_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='locked_assignments', to=settings.AUTH_USER_MODEL)),
                ('meeting_requirement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='locks', to='scheduler.meetingrequirement')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='locked_assignments', to='scheduler.room')),
                ('source_schedule', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='locks', to='scheduler.scheduleversion')),
                ('start_time_slot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='locked_assignments', to='scheduler.timeslot')),
            ],
            options={
                'ordering': ['meeting_requirement'],
            },
        ),
        migrations.CreateModel(
            name='InstructorPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('level', models.CharField(choices=[('PREFERRED', 'Preferred'), ('AVOID', 'Avoid')], max_length=10)),
                ('weight', models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='preferences', to='scheduler.instructoravailabilityprofile')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_preferences', to='scheduler.timeslot')),
            ],
        ),
        migrations.CreateModel(
            name='InstructorAvailability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_available', models.BooleanField(default=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='availability_rows', to='scheduler.instructoravailabilityprofile')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_availability_rows', to='scheduler.timeslot')),
            ],
        ),
        migrations.CreateModel(
            name='UserCollegeScope',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('college', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_scopes', to='scheduler.college')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='college_scopes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['college__code', 'user__username'],
            },
        ),
        migrations.CreateModel(
            name='ValidationResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_feasible', models.BooleanField(default=False)),
                ('hard_violation_count', models.PositiveIntegerField(default=0)),
                ('violations', models.JSONField(default=dict)),
                ('raw_soft_penalty', models.BigIntegerField(default=0)),
                ('objective_breakdown', models.JSONField(default=dict)),
                ('normalized_quality_score', models.FloatField(blank=True, null=True)),
                ('validator_version', models.CharField(default='1.0', max_length=30)),
                ('validated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('run', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='validation_result', to='scheduler.schedulerun')),
                ('schedule_version', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='validation_result', to='scheduler.scheduleversion')),
            ],
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['role', 'is_active'], name='scheduler_u_role_efd66d_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['entity_type', 'entity_id', 'created_at'], name='scheduler_a_entity__5c9142_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['actor', 'created_at'], name='scheduler_a_actor_i_3fba09_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['action', 'created_at'], name='scheduler_a_action_f760dd_idx'),
        ),
        migrations.AddIndex(
            model_name='department',
            index=models.Index(fields=['college', 'is_active'], name='scheduler_d_college_a964b5_idx'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('college', 'name'), name='uniq_department_name_per_college'),
        ),
        migrations.AddIndex(
            model_name='importerror',
            index=models.Index(fields=['batch', 'sheet_name', 'row_number'], name='scheduler_i_batch_i_b8a99f_idx'),
        ),
        migrations.AddIndex(
            model_name='instructor',
            index=models.Index(fields=['department', 'is_active'], name='scheduler_i_departm_baef8a_idx'),
        ),
        migrations.AddIndex(
            model_name='meetingrequirement',
            index=models.Index(fields=['offering', 'is_active'], name='scheduler_m_offerin_45103a_idx'),
        ),
        migrations.AddConstraint(
            model_name='meetingrequirement',
            constraint=models.UniqueConstraint(fields=('offering', 'component', 'occurrence_number'), name='uniq_meeting_occurrence'),
        ),
        migrations.AddConstraint(
            model_name='meetingrequirement',
            constraint=models.CheckConstraint(condition=models.Q(('duration_atoms__gte', 1)), name='meeting_duration_positive'),
        ),
        migrations.AddConstraint(
            model_name='meetingrequirement',
            constraint=models.CheckConstraint(condition=models.Q(('occurrence_number__gte', 1)), name='meeting_occurrence_positive'),
        ),
        migrations.AddConstraint(
            model_name='meetingrequiredcapability',
            constraint=models.UniqueConstraint(fields=('meeting_requirement', 'capability'), name='uniq_meeting_required_capability'),
        ),
        migrations.AddIndex(
            model_name='objectiveprofile',
            index=models.Index(fields=['term', 'is_approved'], name='scheduler_o_term_id_ae72dd_idx'),
        ),
        migrations.AddConstraint(
            model_name='objectiveprofile',
            constraint=models.UniqueConstraint(fields=('name', 'version', 'term'), name='uniq_objective_profile_version'),
        ),
        migrations.AddIndex(
            model_name='offeringinstructor',
            index=models.Index(fields=['instructor', 'offering'], name='scheduler_o_instruc_cde15c_idx'),
        ),
        migrations.AddConstraint(
            model_name='offeringinstructor',
            constraint=models.UniqueConstraint(fields=('offering', 'instructor'), name='uniq_offering_instructor'),
        ),
        migrations.AddIndex(
            model_name='experimentbatch',
            index=models.Index(fields=['snapshot', 'status'], name='scheduler_e_snapsho_4973cd_idx'),
        ),
        migrations.AddConstraint(
            model_name='experimentbatch',
            constraint=models.CheckConstraint(condition=models.Q(('time_limit_seconds__gte', 1)), name='experiment_time_positive'),
        ),
        migrations.AddConstraint(
            model_name='experimentbatch',
            constraint=models.CheckConstraint(condition=models.Q(('cpu_limit__gte', 1)), name='experiment_cpu_positive'),
        ),
        migrations.AddIndex(
            model_name='program',
            index=models.Index(fields=['department', 'is_active'], name='scheduler_p_departm_b02312_idx'),
        ),
        migrations.AddConstraint(
            model_name='program',
            constraint=models.UniqueConstraint(fields=('department', 'name'), name='uniq_program_name_per_department'),
        ),
        migrations.AddIndex(
            model_name='room',
            index=models.Index(fields=['campus', 'kind', 'is_active'], name='scheduler_r_campus_a2dd41_idx'),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.UniqueConstraint(fields=('campus', 'code'), name='uniq_room_code_per_campus'),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('owning_college__isnull', False), ('owning_department__isnull', True)), models.Q(('owning_college__isnull', True), ('owning_department__isnull', False)), _connector='OR'), name='room_exactly_one_owner'),
        ),
        migrations.AddConstraint(
            model_name='roomcapability',
            constraint=models.UniqueConstraint(fields=('room', 'capability'), name='uniq_room_capability'),
        ),
        migrations.AddIndex(
            model_name='schedulerun',
            index=models.Index(fields=['snapshot', 'algorithm', 'status'], name='scheduler_s_snapsho_42b749_idx'),
        ),
        migrations.AddIndex(
            model_name='schedulerun',
            index=models.Index(fields=['experiment_batch', 'seed', 'algorithm'], name='scheduler_s_experim_95a7d1_idx'),
        ),
        migrations.AddConstraint(
            model_name='schedulerun',
            constraint=models.UniqueConstraint(condition=models.Q(('experiment_batch__isnull', False)), fields=('experiment_batch', 'algorithm', 'seed'), name='uniq_experiment_algorithm_seed'),
        ),
        migrations.AddConstraint(
            model_name='schedulerun',
            constraint=models.CheckConstraint(condition=models.Q(('execution_seconds__isnull', True), ('execution_seconds__gte', 0), _connector='OR'), name='run_execution_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='schedulerun',
            constraint=models.CheckConstraint(condition=models.Q(('first_feasible_seconds__isnull', True), ('first_feasible_seconds__gte', 0), _connector='OR'), name='run_first_feasible_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='schedulerun',
            constraint=models.CheckConstraint(condition=models.Q(('relative_gap__isnull', True), ('relative_gap__gte', 0), _connector='OR'), name='run_gap_nonnegative'),
        ),
        migrations.AddIndex(
            model_name='runmetric',
            index=models.Index(fields=['name', 'value'], name='scheduler_r_name_81aff4_idx'),
        ),
        migrations.AddConstraint(
            model_name='runmetric',
            constraint=models.UniqueConstraint(fields=('run', 'name'), name='uniq_run_metric'),
        ),
        migrations.AddIndex(
            model_name='schedulereview',
            index=models.Index(fields=['schedule', 'college', 'status'], name='scheduler_s_schedul_3e3d67_idx'),
        ),
        migrations.AddConstraint(
            model_name='schedulereview',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'ENDORSED')), fields=('schedule', 'college'), name='uniq_schedule_college_endorsement'),
        ),
        migrations.AddIndex(
            model_name='offeringsection',
            index=models.Index(fields=['section', 'offering'], name='scheduler_o_section_c001f7_idx'),
        ),
        migrations.AddConstraint(
            model_name='offeringsection',
            constraint=models.UniqueConstraint(fields=('offering', 'section'), name='uniq_offering_section'),
        ),
        migrations.AddConstraint(
            model_name='studentsectionmembership',
            constraint=models.UniqueConstraint(fields=('student', 'section'), name='uniq_student_section_membership'),
        ),
        migrations.AddIndex(
            model_name='programsubject',
            index=models.Index(fields=['classification', 'authoritative_college'], name='scheduler_p_classif_c2b626_idx'),
        ),
        migrations.AddIndex(
            model_name='programsubject',
            index=models.Index(fields=['program', 'is_active'], name='scheduler_p_program_31a7d0_idx'),
        ),
        migrations.AddConstraint(
            model_name='programsubject',
            constraint=models.UniqueConstraint(fields=('program', 'subject', 'curriculum_version'), name='uniq_program_subject_curriculum'),
        ),
        migrations.AddIndex(
            model_name='termdatasetrevision',
            index=models.Index(fields=['term', 'status'], name='scheduler_t_term_id_83543b_idx'),
        ),
        migrations.AddConstraint(
            model_name='termdatasetrevision',
            constraint=models.UniqueConstraint(fields=('term', 'revision_number'), name='uniq_term_revision_number'),
        ),
        migrations.AddConstraint(
            model_name='termdatasetrevision',
            constraint=models.UniqueConstraint(condition=models.Q(('content_hash', ''), _negated=True), fields=('term', 'content_hash'), name='uniq_term_revision_content_hash'),
        ),
        migrations.AddIndex(
            model_name='section',
            index=models.Index(fields=['revision', 'program', 'is_active'], name='scheduler_s_revisio_813a1b_idx'),
        ),
        migrations.AddConstraint(
            model_name='section',
            constraint=models.UniqueConstraint(fields=('revision', 'code'), name='uniq_section_code_per_revision'),
        ),
        migrations.AddConstraint(
            model_name='section',
            constraint=models.CheckConstraint(condition=models.Q(('year_level__gte', 1), ('year_level__lte', 10)), name='section_year_level_range'),
        ),
        migrations.AddIndex(
            model_name='scheduleversion',
            index=models.Index(fields=['term', 'status', 'version_number'], name='scheduler_s_term_id_22a081_idx'),
        ),
        migrations.AddConstraint(
            model_name='scheduleversion',
            constraint=models.UniqueConstraint(fields=('term', 'version_number'), name='uniq_schedule_version_number'),
        ),
        migrations.AddConstraint(
            model_name='scheduleversion',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'APPROVED')), fields=('term',), name='uniq_approved_schedule_per_term'),
        ),
        migrations.AddConstraint(
            model_name='roomavailabilityprofile',
            constraint=models.UniqueConstraint(fields=('revision', 'room'), name='uniq_room_availability_profile'),
        ),
        migrations.AddIndex(
            model_name='roomauthorization',
            index=models.Index(fields=['revision', 'classification', 'room'], name='scheduler_r_revisio_0d0eb3_idx'),
        ),
        migrations.AddConstraint(
            model_name='roomauthorization',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('college__isnull', False), ('department__isnull', True)), models.Q(('college__isnull', True), ('department__isnull', False)), _connector='OR'), name='room_auth_exactly_one_unit'),
        ),
        migrations.AddConstraint(
            model_name='roomauthorization',
            constraint=models.UniqueConstraint(condition=models.Q(('department__isnull', True)), fields=('revision', 'room', 'classification', 'college'), name='uniq_room_auth_college'),
        ),
        migrations.AddConstraint(
            model_name='roomauthorization',
            constraint=models.UniqueConstraint(condition=models.Q(('college__isnull', True)), fields=('revision', 'room', 'classification', 'department'), name='uniq_room_auth_department'),
        ),
        migrations.AddIndex(
            model_name='problemsnapshot',
            index=models.Index(fields=['revision', 'created_at'], name='scheduler_p_revisio_1bbe73_idx'),
        ),
        migrations.AddConstraint(
            model_name='problemsnapshot',
            constraint=models.CheckConstraint(condition=models.Q(('preprocessing_seconds__gte', 0)), name='snapshot_preprocess_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='instructoravailabilityprofile',
            constraint=models.UniqueConstraint(fields=('revision', 'instructor'), name='uniq_instructor_availability_profile'),
        ),
        migrations.AddIndex(
            model_name='importbatch',
            index=models.Index(fields=['term', 'status', 'created_at'], name='scheduler_i_term_id_dd86ca_idx'),
        ),
        migrations.AddConstraint(
            model_name='importbatch',
            constraint=models.UniqueConstraint(fields=('term', 'file_hash'), name='uniq_import_file_per_term'),
        ),
        migrations.AddIndex(
            model_name='courseoffering',
            index=models.Index(fields=['revision', 'is_active', 'subject'], name='scheduler_c_revisio_753d9d_idx'),
        ),
        migrations.AddConstraint(
            model_name='courseoffering',
            constraint=models.UniqueConstraint(fields=('revision', 'external_key'), name='uniq_offering_external_key'),
        ),
        migrations.AddIndex(
            model_name='timeslot',
            index=models.Index(fields=['revision', 'day', 'is_active', 'is_break'], name='scheduler_t_revisio_4e34a4_idx'),
        ),
        migrations.AddConstraint(
            model_name='timeslot',
            constraint=models.UniqueConstraint(fields=('revision', 'day', 'sequence'), name='uniq_timeslot_sequence'),
        ),
        migrations.AddConstraint(
            model_name='timeslot',
            constraint=models.UniqueConstraint(fields=('revision', 'day', 'starts_at'), name='uniq_timeslot_start'),
        ),
        migrations.AddConstraint(
            model_name='timeslot',
            constraint=models.CheckConstraint(condition=models.Q(('ends_at__gt', models.F('starts_at'))), name='timeslot_end_after_start'),
        ),
        migrations.AddConstraint(
            model_name='timeslot',
            constraint=models.CheckConstraint(condition=models.Q(('day__gte', 0), ('day__lte', 6)), name='timeslot_valid_day'),
        ),
        migrations.AddIndex(
            model_name='schedulesectionallocation',
            index=models.Index(fields=['schedule', 'time_slot', 'section'], name='scheduler_s_schedul_414e91_idx'),
        ),
        migrations.AddConstraint(
            model_name='schedulesectionallocation',
            constraint=models.UniqueConstraint(fields=('assignment', 'section', 'time_slot'), name='uniq_assignment_section_atom'),
        ),
        migrations.AddConstraint(
            model_name='schedulesectionallocation',
            constraint=models.UniqueConstraint(fields=('schedule', 'section', 'time_slot'), name='uniq_schedule_section_atom'),
        ),
        migrations.AddIndex(
            model_name='scheduleroomallocation',
            index=models.Index(fields=['schedule', 'time_slot', 'room'], name='scheduler_s_schedul_c10fed_idx'),
        ),
        migrations.AddConstraint(
            model_name='scheduleroomallocation',
            constraint=models.UniqueConstraint(fields=('assignment', 'time_slot'), name='uniq_assignment_room_atom'),
        ),
        migrations.AddConstraint(
            model_name='scheduleroomallocation',
            constraint=models.UniqueConstraint(fields=('schedule', 'room', 'time_slot'), name='uniq_schedule_room_atom'),
        ),
        migrations.AddIndex(
            model_name='scheduleinstructorallocation',
            index=models.Index(fields=['schedule', 'time_slot', 'instructor'], name='scheduler_s_schedul_86afbf_idx'),
        ),
        migrations.AddConstraint(
            model_name='scheduleinstructorallocation',
            constraint=models.UniqueConstraint(fields=('assignment', 'instructor', 'time_slot'), name='uniq_assignment_instructor_atom'),
        ),
        migrations.AddConstraint(
            model_name='scheduleinstructorallocation',
            constraint=models.UniqueConstraint(fields=('schedule', 'instructor', 'time_slot'), name='uniq_schedule_instructor_atom'),
        ),
        migrations.AddIndex(
            model_name='scheduleassignment',
            index=models.Index(fields=['schedule', 'room', 'start_time_slot'], name='scheduler_s_schedul_968602_idx'),
        ),
        migrations.AddConstraint(
            model_name='scheduleassignment',
            constraint=models.UniqueConstraint(fields=('schedule', 'meeting_requirement'), name='uniq_schedule_meeting_assignment'),
        ),
        migrations.AddIndex(
            model_name='roomavailability',
            index=models.Index(fields=['time_slot', 'is_available'], name='scheduler_r_time_sl_52d45e_idx'),
        ),
        migrations.AddConstraint(
            model_name='roomavailability',
            constraint=models.UniqueConstraint(fields=('profile', 'time_slot'), name='uniq_room_availability_slot'),
        ),
        migrations.AddIndex(
            model_name='lockedassignment',
            index=models.Index(fields=['is_active', 'meeting_requirement'], name='scheduler_l_is_acti_17f866_idx'),
        ),
        migrations.AddConstraint(
            model_name='lockedassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('meeting_requirement',), name='uniq_active_lock_per_meeting'),
        ),
        migrations.AddConstraint(
            model_name='instructorpreference',
            constraint=models.UniqueConstraint(fields=('profile', 'time_slot'), name='uniq_instructor_preference_slot'),
        ),
        migrations.AddConstraint(
            model_name='instructorpreference',
            constraint=models.CheckConstraint(condition=models.Q(('weight__gte', 1)), name='preference_weight_positive'),
        ),
        migrations.AddIndex(
            model_name='instructoravailability',
            index=models.Index(fields=['time_slot', 'is_available'], name='scheduler_i_time_sl_f86f45_idx'),
        ),
        migrations.AddConstraint(
            model_name='instructoravailability',
            constraint=models.UniqueConstraint(fields=('profile', 'time_slot'), name='uniq_instructor_availability_slot'),
        ),
        migrations.AddConstraint(
            model_name='usercollegescope',
            constraint=models.UniqueConstraint(fields=('user', 'college'), name='uniq_user_college_scope'),
        ),
        migrations.AddConstraint(
            model_name='validationresult',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('run__isnull', False), ('schedule_version__isnull', True)), models.Q(('run__isnull', True), ('schedule_version__isnull', False)), _connector='OR'), name='validation_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='validationresult',
            constraint=models.CheckConstraint(condition=models.Q(('normalized_quality_score__isnull', True), models.Q(('normalized_quality_score__gte', 0), ('normalized_quality_score__lte', 100)), _connector='OR'), name='validation_quality_range'),
        ),
    ]

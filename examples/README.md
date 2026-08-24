# Synthetic trial data

`USM-Scheduler-Synthetic-Trial-v1.xlsx` is fictional and de-identified. Its
college, department, room, instructor, section, subject, availability, and lock
records are **not official University of Southern Mindanao data**.

Generate the tracked example deterministically with:

```powershell
python manage.py create_trial_workbook `
  --output examples/USM-Scheduler-Synthetic-Trial-v1.xlsx
```

Use it only to learn and test the prototype workflow. Real semester data require
written institutional authorization and validation by the responsible USM units.

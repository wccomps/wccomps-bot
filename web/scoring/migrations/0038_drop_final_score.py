from django.db import migrations


class Migration(migrations.Migration):
    """Drop the final_score table 0037 removed from the model state but kept for pods on the previous
    release during that rollout. Standings are computed on read; team exclusions moved to scoring_exclusion."""

    dependencies = [
        ("scoring", "0037_scoringexclusion_forget_finalscore"),
    ]

    operations = [
        migrations.RunSQL("DROP TABLE IF EXISTS final_score", migrations.RunSQL.noop),
    ]

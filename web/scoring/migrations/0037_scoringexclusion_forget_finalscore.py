import django.db.models.deletion
from django.db import migrations, models


def copy_exclusions(apps, schema_editor):
    FinalScore = apps.get_model("scoring", "FinalScore")
    ScoringExclusion = apps.get_model("scoring", "ScoringExclusion")
    ScoringExclusion.objects.bulk_create(
        ScoringExclusion(team_id=team_id)
        for team_id in FinalScore.objects.filter(is_excluded=True).values_list("team_id", flat=True)
    )


def restore_exclusions(apps, schema_editor):
    FinalScore = apps.get_model("scoring", "FinalScore")
    ScoringExclusion = apps.get_model("scoring", "ScoringExclusion")
    FinalScore.objects.filter(team_id__in=ScoringExclusion.objects.values("team_id")).update(is_excluded=True)


class Migration(migrations.Migration):
    dependencies = [
        ("scoring", "0036_approvable_base"),
        ("team", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScoringExclusion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "team",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scoring_exclusion",
                        to="team.team",
                    ),
                ),
            ],
            options={
                "db_table": "scoring_exclusion",
            },
        ),
        migrations.RunPython(copy_exclusions, restore_exclusions),
        # Old pods still read and write final_score during a rolling deploy, so the table stays and
        # only Django's state forgets the model; a later release drops it. Its foreign key goes now,
        # or deleting a team would fail on rows Django no longer knows to cascade to.
        migrations.AlterField(
            model_name="finalscore",
            name="team",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="final_scores",
                to="team.team",
            ),
        ),
        migrations.SeparateDatabaseAndState(state_operations=[migrations.DeleteModel(name="FinalScore")]),
    ]

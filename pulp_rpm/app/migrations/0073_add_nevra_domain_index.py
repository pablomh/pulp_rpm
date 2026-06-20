from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rpm", "0072_fix_evr_version_sorting"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="package",
            index=models.Index(
                fields=["_pulp_domain", "name", "epoch", "version", "release", "arch"],
                name="rpm_package_nevra_domain_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="modulemd",
            index=models.Index(
                fields=["_pulp_domain", "name", "stream", "version", "context", "arch"],
                name="rpm_modulemd_nsvca_domain_idx",
            ),
        ),
    ]

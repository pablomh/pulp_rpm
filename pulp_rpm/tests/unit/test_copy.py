from unittest.mock import Mock, patch

from django.test import TestCase

from pulp_rpm.app.models import Package
from pulp_rpm.app.tasks import copy as copy_task


class TestCopyHelpers(TestCase):
    def setUp(self):
        self.pkg1 = Package.objects.create(
            name="aardvark",
            epoch="0",
            version="1.0.0",
            release="1.el8",
            arch="x86_64",
            pkgId="copy-helper-1",
            checksum_type="sha256",
        )
        self.pkg2 = Package.objects.create(
            name="badger",
            epoch="0",
            version="1.0.0",
            release="1.el8",
            arch="x86_64",
            pkgId="copy-helper-2",
            checksum_type="sha256",
        )
        self.pkg3 = Package.objects.create(
            name="cougar",
            epoch="0",
            version="1.0.0",
            release="1.el8",
            arch="x86_64",
            pkgId="copy-helper-3",
            checksum_type="sha256",
        )

    def test_batched_values_list_returns_matches_across_batches(self):
        original_batch_size = copy_task.PARAM_FILTER_BATCH_SIZE
        copy_task.PARAM_FILTER_BATCH_SIZE = 2
        try:
            matches = copy_task._batched_values_list(
                Package.objects.all(),
                "name",
                ["aardvark", "cougar", "missing"],
                result_field="name",
            )
        finally:
            copy_task.PARAM_FILTER_BATCH_SIZE = original_batch_size

        self.assertEqual(matches, {"aardvark", "cougar"})

    def test_iter_batches_uses_runtime_batch_size(self):
        original_batch_size = copy_task.PARAM_FILTER_BATCH_SIZE
        copy_task.PARAM_FILTER_BATCH_SIZE = 2
        try:
            batches = list(copy_task._iter_batches(["aardvark", "badger", "cougar"]))
        finally:
            copy_task.PARAM_FILTER_BATCH_SIZE = original_batch_size

        self.assertEqual(batches, [("aardvark", "badger"), ("cougar",)])

    def test_values_join_pks_returns_matches_across_batches(self):
        original_batch_size = copy_task.PARAM_FILTER_BATCH_SIZE
        copy_task.PARAM_FILTER_BATCH_SIZE = 2
        try:
            matches = copy_task._values_join_pks(
                "rpm_package",
                ("name", "epoch", "version", "release", "arch"),
                [
                    ("aardvark", "0", "1.0.0", "1.el8", "x86_64"),
                    ("cougar", "0", "1.0.0", "1.el8", "x86_64"),
                    ("missing", "0", "1.0.0", "1.el8", "x86_64"),
                ],
                "rpm_package.content_ptr_id = ANY(%s) AND rpm_package._pulp_domain_id = %s",
                ([self.pkg1.pk, self.pkg2.pk, self.pkg3.pk], self.pkg1._pulp_domain_id),
            )
        finally:
            copy_task.PARAM_FILTER_BATCH_SIZE = original_batch_size

        self.assertEqual(matches, {self.pkg1.pk, self.pkg3.pk})

    def test_batched_queryset_iterator_returns_matches_across_batches(self):
        original_batch_size = copy_task.PARAM_FILTER_BATCH_SIZE
        copy_task.PARAM_FILTER_BATCH_SIZE = 2
        try:
            matches = list(
                copy_task._batched_queryset_iterator(
                    Package.objects.all(),
                    "name",
                    ["aardvark", "cougar", "missing"],
                )
            )
        finally:
            copy_task.PARAM_FILTER_BATCH_SIZE = original_batch_size

        self.assertEqual({match.name for match in matches}, {"aardvark", "cougar"})

    def test_values_join_pks_returns_empty_set_for_empty_input(self):
        self.assertEqual(
            copy_task._values_join_pks(
                "rpm_package",
                ("name", "epoch", "version", "release", "arch"),
                [],
                "rpm_package.content_ptr_id = ANY(%s)",
                ([self.pkg1.pk],),
            ),
            set(),
        )

    def test_values_join_pks_casts_values_when_requested(self):
        cursor = Mock(name="cursor")
        cursor.fetchall.return_value = []
        cursor_manager = Mock(name="cursor_manager")
        cursor_manager.__enter__ = Mock(return_value=cursor)
        cursor_manager.__exit__ = Mock(return_value=False)

        with patch.object(copy_task.connection, "cursor", return_value=cursor_manager):
            copy_task._values_join_pks(
                "rpm_modulemd",
                ("name", "stream", "version", "context", "arch"),
                [("module", "stable", 42, "ctx", "x86_64")],
                "rpm_modulemd.content_ptr_id = ANY(%s) AND rpm_modulemd._pulp_domain_id = %s",
                ([self.pkg1.pk], self.pkg1._pulp_domain_id),
                value_casts=("text", "text", "text", "text", "text"),
            )

        sql, params = cursor.execute.call_args[0]
        self.assertIn("CAST(%s AS text)", sql)
        self.assertEqual(
            ["module", "stable", 42, "ctx", "x86_64", [self.pkg1.pk], self.pkg1._pulp_domain_id],
            params,
        )

    def test_batched_values_list_returns_empty_set_for_empty_input(self):
        self.assertEqual(copy_task._batched_values_list(Package.objects.all(), "name", []), set())

    def test_batched_queryset_iterator_returns_no_matches_for_empty_input(self):
        matches = list(copy_task._batched_queryset_iterator(Package.objects.all(), "name", []))
        self.assertEqual(matches, [])

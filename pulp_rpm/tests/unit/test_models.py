from unittest import TestCase
from unittest.mock import MagicMock, patch

from pulp_rpm.app.models import Package
from pulp_rpm.app.models.repository import RpmRepository


class TestNothing(TestCase):
    """Test Nothing (placeholder)."""

    def test_nothing_at_all(self):
        """Test that the tests are running and that's it."""
        self.assertTrue(True)


class TestRpmRepositoryDuplicateValidation(TestCase):
    """Test delta-aware duplicate validation in rpm repository finalize."""

    @patch("pulp_rpm.app.models.repository.validate_duplicate_content")
    @patch("pulp_rpm.app.models.repository.count_duplicates")
    def test_validate_duplicate_content_uses_added_delta(
        self, count_duplicates, validate_duplicate_content
    ):
        repo = RpmRepository(name="repo")
        previous_version = MagicMock(name="previous_version")
        added_content = MagicMock(name="added_content")
        added_content.filter.return_value = "added-package-pks"

        type_obj = MagicMock(name="type_obj")
        type_obj.repo_key_fields = ("name",)
        type_obj.get_pulp_type.return_value = "rpm.package"
        filtered_qs = MagicMock(name="filtered_qs")
        type_obj.objects.filter.return_value = filtered_qs

        new_version = MagicMock(name="new_version")
        new_version.repository.CONTENT_TYPES = [type_obj]
        new_version.added.return_value = added_content

        count_duplicates.return_value = 0

        repo._validate_duplicate_content(new_version, previous_version)

        validate_duplicate_content.assert_not_called()
        new_version.added.assert_called_once_with(base_version=previous_version)
        added_content.filter.assert_called_once_with(pulp_type="rpm.package")
        type_obj.objects.filter.assert_called_once_with(
            pk__in="added-package-pks",
        )
        count_duplicates.assert_called_once_with(filtered_qs, ("name",))

    @patch("pulp_rpm.app.models.repository.validate_duplicate_content")
    def test_validate_duplicate_content_uses_full_validation_without_previous_version(
        self, validate_duplicate_content
    ):
        repo = RpmRepository(name="repo")
        new_version = MagicMock(name="new_version")

        repo._validate_duplicate_content(new_version, None)

        validate_duplicate_content.assert_called_once_with(new_version)
        new_version.added.assert_not_called()


class TestRpmRepositoryRetentionPolicy(TestCase):
    """Test delta-aware retention policy selection."""

    @patch("pulp_rpm.app.models.repository.Package.objects")
    def test_touched_retention_groups_use_added_and_removed_nonmodular_packages(
        self, package_objects
    ):
        repo = RpmRepository(name="repo")
        previous_version = MagicMock(name="previous_version")

        added_content = MagicMock(name="added_content")
        removed_content = MagicMock(name="removed_content")
        added_content.filter.return_value = "added-package-pks"
        removed_content.filter.return_value = "removed-package-pks"

        added_qs = MagicMock(name="added_qs")
        removed_qs = MagicMock(name="removed_qs")
        added_qs.values_list.return_value = [("bash", "x86_64"), ("bash", "i686")]
        removed_qs.values_list.return_value = [("coreutils", "x86_64"), ("bash", "x86_64")]
        package_objects.filter.side_effect = [added_qs, removed_qs]

        new_version = MagicMock(name="new_version")
        new_version.added.return_value = added_content
        new_version.removed.return_value = removed_content

        touched_groups = repo._touched_retention_groups(new_version, previous_version)

        new_version.added.assert_called_once_with(base_version=previous_version)
        new_version.removed.assert_called_once_with(base_version=previous_version)
        added_content.filter.assert_called_once_with(pulp_type=Package.get_pulp_type())
        removed_content.filter.assert_called_once_with(pulp_type=Package.get_pulp_type())
        assert touched_groups == {
            ("bash", "x86_64"),
            ("bash", "i686"),
            ("coreutils", "x86_64"),
        }

    @patch("pulp_rpm.app.models.repository.Content.objects")
    @patch("pulp_rpm.app.models.repository.annotate_with_age")
    @patch("pulp_rpm.app.models.repository.Package.objects")
    def test_apply_retention_policy_limits_work_to_touched_groups(
        self, package_objects, annotate_with_age, content_objects
    ):
        repo = RpmRepository(name="repo", retain_package_versions=1)
        previous_version = MagicMock(name="previous_version")

        package_content = MagicMock(name="package_content")
        unfiltered_packages = MagicMock(name="unfiltered_packages")
        touched_packages = MagicMock(name="touched_packages")
        package_objects.filter.return_value = unfiltered_packages
        unfiltered_packages.filter.return_value = touched_packages
        touched_packages.only.return_value = "retention-candidates"

        new_version = MagicMock(name="new_version")
        new_version.content.filter.return_value = package_content

        annotate_with_age.return_value = [
            MagicMock(name="kept_package", age=1, pk="keep"),
            MagicMock(name="old_package", age=2, pk="drop"),
        ]
        content_objects.filter.return_value = "old-package-content"

        touched_groups = {("bash", "x86_64")}
        group_filter = object()
        with (
            patch.object(repo, "_touched_retention_groups", return_value=touched_groups),
            patch.object(repo, "_retention_group_filter", return_value=group_filter),
        ):
            repo._apply_retention_policy(new_version, previous_version)

        package_objects.filter.assert_any_call(pk__in=package_content, is_modular=False)
        unfiltered_packages.filter.assert_called_once_with(group_filter)
        touched_packages.only.assert_called_once_with("pk")
        annotate_with_age.assert_called_once_with("retention-candidates")
        content_objects.filter.assert_called_once_with(pk__in=["drop"])
        new_version.remove_content.assert_called_once_with("old-package-content")

    @patch("pulp_rpm.app.models.repository.annotate_with_age")
    @patch("pulp_rpm.app.models.repository.Package.objects")
    def test_apply_retention_policy_skips_when_delta_touches_no_nonmodular_groups(
        self, package_objects, annotate_with_age
    ):
        repo = RpmRepository(name="repo", retain_package_versions=1)
        previous_version = MagicMock(name="previous_version")
        package_content = MagicMock(name="package_content")
        package_objects.filter.return_value = MagicMock(name="unfiltered_packages")

        new_version = MagicMock(name="new_version")
        new_version.content.filter.return_value = package_content

        with patch.object(repo, "_touched_retention_groups", return_value=set()):
            repo._apply_retention_policy(new_version, previous_version)

        annotate_with_age.assert_not_called()
        new_version.remove_content.assert_not_called()

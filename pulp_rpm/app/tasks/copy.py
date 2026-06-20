from django.db import connection, transaction
from django.db.models import Q

from pulpcore.plugin.models import Content, RepositoryVersion
from pulpcore.plugin.util import get_domain_pk

from pulp_rpm.app.depsolving import Solver
from pulp_rpm.app.models import (
    Modulemd,
    Package,
    PackageCategory,
    PackageEnvironment,
    PackageGroup,
    RpmRepository,
    UpdateCollection,
    UpdateCollectionPackage,
    UpdateRecord,
)
from pulp_rpm.app.shared_utils import annotate_with_age

PARAM_FILTER_BATCH_SIZE = 5000


def _iter_batches(values, batch_size=None):
    """Yield stable slices from an arbitrary iterable."""
    if batch_size is None:
        batch_size = PARAM_FILTER_BATCH_SIZE
    values = tuple(values)
    for offset in range(0, len(values), batch_size):
        yield values[offset : offset + batch_size]


def _batched_values_list(queryset, lookup_field, values, result_field="pk"):
    """Resolve a large __in lookup in bounded batches."""
    if not values:
        return set()

    filter_key = f"{lookup_field}__in"
    matches = set()
    for batch in _iter_batches(values):
        matches.update(queryset.filter(**{filter_key: batch}).values_list(result_field, flat=True))
    return matches


def _values_join_pks(
    table_name, columns, tuples, scope_sql, scope_params, batch_size=None, value_casts=None
):
    """Resolve content PKs by joining a VALUES list against a table."""
    if not tuples:
        return set()

    if batch_size is None:
        batch_size = PARAM_FILTER_BATCH_SIZE

    if value_casts is not None and len(value_casts) != len(columns):
        raise ValueError("value_casts must align with columns")

    quoted_table = connection.ops.quote_name(table_name)
    quoted_columns = [connection.ops.quote_name(column) for column in columns]
    column_list_sql = ", ".join(quoted_columns)
    join_sql = " AND ".join(
        f"{quoted_table}.{quoted_column} = _lookup.{quoted_column}"
        for quoted_column in quoted_columns
    )

    matches = set()
    for batch in _iter_batches(tuples, batch_size=batch_size):
        if value_casts is None:
            row_sql = "(" + ", ".join(["%s"] * len(columns)) + ")"
        else:
            row_sql = "(" + ", ".join(f"CAST(%s AS {cast})" for cast in value_casts) + ")"
        values_sql = ", ".join(row_sql for _ in batch)
        params = [value for values in batch for value in values]
        sql = f"""
            SELECT {quoted_table}.content_ptr_id
            FROM {quoted_table}
            INNER JOIN (VALUES {values_sql}) AS _lookup({column_list_sql})
                ON {join_sql}
            WHERE {scope_sql}
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, params + list(scope_params))
            matches.update(row[0] for row in cursor.fetchall())
    return matches


def _batched_queryset_iterator(queryset, lookup_field, values, batch_size=None):
    """Iterate queryset matches for a large __in lookup in bounded batches."""
    if not values:
        return

    filter_key = f"{lookup_field}__in"
    for batch in _iter_batches(values, batch_size=batch_size):
        yield from queryset.filter(**{filter_key: batch}).iterator()


def find_children_of_content(content, src_repo_version, selected_content_ids=None):
    """Finds the content referenced directly by other content and returns it all together.

    Finds RPMs referenced by Advisory/Errata content.

    Args:
        content (Queryset): Content for which to resolve children
        src_repo_version (pulpcore.models.RepositoryVersion): Source repo version

    Returns: Queryset of Content objects that are children of the intial set of content
    """
    if selected_content_ids is None:
        selected_content_ids = set(content.values_list("pk", flat=True).iterator())
    else:
        selected_content_ids = set(selected_content_ids)

    if not selected_content_ids:
        return Content.objects.none()

    domain_pk = get_domain_pk()
    advisory_ids = _batched_values_list(
        Content.objects.filter(pulp_domain=domain_pk, pulp_type=UpdateRecord.get_pulp_type()),
        "pk",
        selected_content_ids,
    )
    packagecategory_ids = _batched_values_list(
        Content.objects.filter(pulp_domain=domain_pk, pulp_type=PackageCategory.get_pulp_type()),
        "pk",
        selected_content_ids,
    )
    packageenvironment_ids = _batched_values_list(
        Content.objects.filter(pulp_domain=domain_pk, pulp_type=PackageEnvironment.get_pulp_type()),
        "pk",
        selected_content_ids,
    )
    packagegroup_ids = _batched_values_list(
        Content.objects.filter(pulp_domain=domain_pk, pulp_type=PackageGroup.get_pulp_type()),
        "pk",
        selected_content_ids,
    )

    source_package_ids = list(
        src_repo_version.content.filter(pulp_type=Package.get_pulp_type())
        .values_list("pk", flat=True)
        .iterator()
    )
    source_module_ids = list(
        src_repo_version.content.filter(pulp_type=Modulemd.get_pulp_type())
        .values_list("pk", flat=True)
        .iterator()
    )

    children = set()

    advisory_package_nevras = set()
    advisory_module_nsvcas = set()
    for batch in _iter_batches(advisory_ids):
        advisory_package_nevras.update(
            UpdateCollectionPackage.objects.filter(
                update_collection__update_record__pk__in=batch
            )
            .values_list("name", "epoch", "version", "release", "arch")
            .distinct()
            .iterator()
        )
        for module_json in (
            UpdateCollection.objects.filter(update_record__pk__in=batch)
            .exclude(module=None)
            .values_list("module", flat=True)
            .distinct()
            .iterator()
        ):
            advisory_module_nsvcas.add(
                (
                    module_json["name"],
                    module_json["stream"],
                    module_json["version"],
                    module_json["context"],
                    module_json["arch"],
                )
            )

    children.update(
        _values_join_pks(
            "rpm_package",
            ("name", "epoch", "version", "release", "arch"),
            advisory_package_nevras,
            "rpm_package.content_ptr_id = ANY(%s) AND rpm_package._pulp_domain_id = %s",
            (source_package_ids, domain_pk),
            value_casts=("text", "text", "text", "text", "text"),
        )
    )

    children.update(
        _values_join_pks(
            "rpm_modulemd",
            ("name", "stream", "version", "context", "arch"),
            advisory_module_nsvcas,
            "rpm_modulemd.content_ptr_id = ANY(%s) AND rpm_modulemd._pulp_domain_id = %s",
            (source_module_ids, domain_pk),
            value_casts=("text", "text", "text", "text", "text"),
        )
    )

    # PackageCategories & PackageEnvironments resolution must go before PackageGroups
    packagegroup_names = set()
    for packagecategory in _batched_queryset_iterator(
        PackageCategory.objects.all(), "pk", packagecategory_ids
    ):
        for group_id in packagecategory.group_ids:
            packagegroup_names.add(group_id["name"])

    for packageenvironment in _batched_queryset_iterator(
        PackageEnvironment.objects.all(), "pk", packageenvironment_ids
    ):
        for group_id in packageenvironment.group_ids:
            packagegroup_names.add(group_id["name"])
        for group_id in packageenvironment.option_ids:
            packagegroup_names.add(group_id["name"])

    child_package_group_ids = _batched_values_list(
        PackageGroup.objects.filter(pk__in=src_repo_version.content), "name", packagegroup_names
    )
    children.update(child_package_group_ids)

    # Find rpms referenced by PackageGroups
    packagegroup_ids_to_scan = set(packagegroup_ids)
    packagegroup_ids_to_scan.update(child_package_group_ids)
    packagegroup_package_names = set()
    for packagegroup in _batched_queryset_iterator(
        PackageGroup.objects.all(), "pk", packagegroup_ids_to_scan
    ):
        packagegroup_package_names |= set(pkg["name"] for pkg in packagegroup.packages)

    # TODO: do modular/nonmodular need to be taken into account?
    selected_package_names = set()
    for batch in _iter_batches(selected_content_ids):
        selected_package_names.update(
            Package.objects.filter(pk__in=batch).values_list("name", flat=True)
        )
    existing_package_names = selected_package_names & packagegroup_package_names

    missing_package_names = packagegroup_package_names - existing_package_names

    newest_package_ids = set()
    for batch in _iter_batches(missing_package_names):
        newest_package_ids.update(
            annotate_with_age(
                Package.objects.filter(
                    pk__in=src_repo_version.content,
                    name__in=batch,
                )
            )
            .filter(age=1)
            .values_list("pk", flat=True)
        )
    children.update(newest_package_ids)

    return Content.objects.filter(pk__in=children)


@transaction.atomic
def copy_content(config, dependency_solving, dependency_upgrade=False):
    """
    Copy content from one repo to another.

    Args:
        config: Details of how the copy should be performed.
        dependency_solving: Use dependency solving to find additional content units to copy.
        dependency_upgrade: Resolve dependencies to latest compatible versions instead of
            preferring versions already in the destination.

    Config format details:
        source_repo_version_pk: repository version primary key to copy units from
        dest_repo_pk: repository primary key to copy units into
        criteria: a dict that maps type to a list of criteria to filter content by. Note that this
            criteria MUST be validated before being passed to this task.
        content_pks: a list of content pks to copy from source to destination
    """

    def process_entry(entry):
        source_repo_version = RepositoryVersion.objects.get(pk=entry["source_repo_version"])
        dest_repo = RpmRepository.objects.get(pk=entry["dest_repo"])

        dest_version_provided = bool(entry.get("dest_base_version"))
        if dest_version_provided:
            dest_repo_version = RepositoryVersion.objects.get(pk=entry["dest_base_version"])
        else:
            dest_repo_version = dest_repo.latest_version()

        if entry.get("content") is not None:
            content_filter = Q(pk__in=entry.get("content"))
        else:
            content_filter = Q()

        content_filter &= Q(pulp_domain=get_domain_pk())

        return (
            source_repo_version,
            dest_repo_version,
            dest_repo,
            content_filter,
            entry.get("content"),
            dest_version_provided,
        )

    if not dependency_solving:
        # No Dependency Solving Branch
        # ============================
        for entry in config:
            (
                source_repo_version,
                dest_repo_version,
                dest_repo,
                content_filter,
                selected_content_ids,
                dest_version_provided,
            ) = process_entry(entry)

            content_to_copy = source_repo_version.content.filter(content_filter)
            content_to_copy |= find_children_of_content(
                content_to_copy,
                source_repo_version,
                selected_content_ids=selected_content_ids,
            )

            base_version = dest_repo_version if dest_version_provided else None
            with dest_repo.new_version(base_version=base_version) as new_version:
                new_version.add_content(content_to_copy)
    else:
        # Dependency Solving Branch
        # =========================

        # TODO: a more structured way to store this state would be nice.
        content_to_copy = {}
        repo_mapping = {}
        libsolv_repo_names = {}
        base_versions = {}

        solver = Solver()

        for entry in config:
            (
                source_repo_version,
                dest_repo_version,
                dest_repo,
                content_filter,
                selected_content_ids,
                dest_version_provided,
            ) = process_entry(entry)

            repo_mapping[source_repo_version] = dest_repo_version
            base_versions[source_repo_version] = dest_version_provided

            # Load the content from the source and destination repository versions into the solver
            source_repo_name = solver.load_source_repo(source_repo_version)
            solver.load_target_repo(dest_repo_version)

            # Store the correspondance between the libsolv name of a repo version and the
            # actual Pulp repo version, so that we can work backwards to get the latter
            # from the former.
            libsolv_repo_names[source_repo_name] = source_repo_version

            # Find all of the matching content in the repository version, then determine
            # child relationships (e.g. RPM children of Errata/Advisories), then combine
            # those two sets to copy the specified content + children.
            content = source_repo_version.content.filter(content_filter)
            children = find_children_of_content(
                content,
                source_repo_version,
                selected_content_ids=selected_content_ids,
            )
            content_to_copy[source_repo_name] = content | children

        solver.finalize()

        content_to_copy = solver.resolve_dependencies(
            content_to_copy, focus_installed=not dependency_upgrade
        )

        for from_repo, units in content_to_copy.items():
            src_repo_version = libsolv_repo_names[from_repo]
            dest_repo_version = repo_mapping[src_repo_version]
            base_version = dest_repo_version if base_versions[src_repo_version] else None
            with dest_repo_version.repository.new_version(base_version=base_version) as new_version:
                new_version.add_content(Content.objects.filter(pk__in=units))

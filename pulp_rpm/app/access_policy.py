from logging import getLogger

from pulpcore.plugin.models import RepositoryVersion
from pulpcore.plugin.viewsets import NamedModelViewSet

from pulp_rpm.app.models.repository import RpmRepository

_logger = getLogger(__name__)


def has_perms_to_copy(request, view, action):
    """
    Check if the source and destination repository matches the usernames permissions.

    `Fail` the check at first missing permission.
    """
    serializer = view.serializer_class(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)

    for copy_action in serializer.data["config"]:
        dest_repo = NamedModelViewSet().get_resource(copy_action["dest_repo"], RpmRepository)

        # Check if user has permissions to destination repository
        if not (
            request.user.has_perm("rpm.modify_content_rpmrepository", dest_repo)
            or request.user.has_perm("rpm.modify_content_rpmrepository")
        ):
            return False

        # A base version contributes its content to the new version just like a source does.
        for field in ("source_repo_version", "dest_base_version_href"):
            version_href = copy_action.get(field)
            if not version_href:
                continue
            version = NamedModelViewSet().get_resource(version_href, RepositoryVersion)
            repo = RpmRepository.objects.get(pk=version.repository_id)
            if not (
                request.user.has_perm("rpm.view_rpmrepository", repo)
                or request.user.has_perm("rpm.view_rpmrepository")
            ):
                return False

    return True

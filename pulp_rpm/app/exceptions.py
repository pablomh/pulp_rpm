from gettext import gettext as _

from pulpcore.plugin.exceptions import PulpException


class AdvisoryConflict(PulpException):
    """
    Raised when two advisories conflict in a way that Pulp can't resolve it.
    """

    error_code = "RPM0001"

    def __init__(self, msg):
        """
        Set the exception identifier.

        Args:
            msg(str): Detailed message about the reasons for Advisory conflict
        """
        self.msg = msg

    def __str__(self):
        """
        Return a message for the exception.
        """
        return f"[{self.error_code}] " + self.msg


class DistributionTreeConflict(FileNotFoundError):
    """
    Raised when two or more distribution trees are being added to a repository version.
    """

    error_code = "RPM0002"

    def __str__(self):
        """
        Return a message for the exception.
        """
        return f"[{self.error_code}] " + _(
            "More than one distribution tree cannot be added to a repository version."
        )


class UlnCredentialsError(PulpException):
    """
    Raised when no valid ULN Credentials were given.
    """

    error_code = "RPM0003"

    def __str__(self):
        """
        Return a message for the exception.
        """
        return f"[{self.error_code}] " + _("No valid ULN credentials given.")


class ChecksumTooShortError(PulpException):
    """
    Raised when a checksum is too short for the requested layout.
    """

    error_code = "RPM0009"

    def __init__(self, checksum, layout):
        super().__init__()
        self.checksum = checksum
        self.layout = layout

    def __str__(self):
        return f"[{self.error_code}] " + _(
            "Checksum {checksum} is unknown or too short to use for {layout} publishing."
        ).format(checksum=self.checksum, layout=self.layout)


class ForbiddenChecksumTypeError(PulpException):
    """
    Raised when a checksum type is not allowed for publishing.
    """

    error_code = "RPM0010"

    def __init__(self, checksum_type, detail):
        super().__init__()
        self.checksum_type = checksum_type
        self.detail = detail

    def __str__(self):
        return f"[{self.error_code}] " + _(
            "Checksum type '{checksum_type}' is not allowed: {detail}"
        ).format(checksum_type=self.checksum_type, detail=self.detail)


class UnsupportedLayoutError(PulpException):
    """
    Raised when an unsupported layout value is used.
    """

    error_code = "RPM0011"

    def __init__(self, layout):
        super().__init__()
        self.layout = layout

    def __str__(self):
        return f"[{self.error_code}] " + _(
            "Layout value {layout} is unsupported by this version"
        ).format(layout=self.layout)


class MetadataSigningError(PulpException):
    """
    Raised when metadata signing produces an invalid result.
    """

    error_code = "RPM0013"

    def __init__(self, detail):
        super().__init__()
        self.detail = detail

    def __str__(self):
        return f"[{self.error_code}] " + _("Metadata signing failed: {detail}").format(
            detail=self.detail
        )

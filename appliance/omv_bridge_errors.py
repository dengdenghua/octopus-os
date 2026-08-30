"""Stable error taxonomy shared by OMV bridge layers."""


class OmvBridgeError(RuntimeError):
    """The host bridge could not safely complete an operation."""


class OmvBridgeValidationError(OmvBridgeError):
    """A request failed the bridge's bounded input contract."""


class OmvBridgeConflict(OmvBridgeError):
    """A valid request conflicts with current or planned OMV state."""


__all__ = ["OmvBridgeConflict", "OmvBridgeError", "OmvBridgeValidationError"]

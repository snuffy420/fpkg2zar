class Fpkg2ZarError(Exception):
    """Base error for anything that stops a conversion."""


class FormatError(Fpkg2ZarError):
    """The input is corrupt or not what its headers claim."""


class UnsupportedError(Fpkg2ZarError):
    """The input is valid but uses a feature this tool cannot read."""


class Cancelled(Fpkg2ZarError):
    """The user stopped the conversion."""

# SPDX-License-Identifier: GPL-2.0-or-later
"""pyode -- a simple terminal internet radio player."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pyode")
except PackageNotFoundError:  # running directly from an unpacked source tree
    __version__ = "0+unknown"

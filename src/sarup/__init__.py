"""Sarup (สรุป) — Thai-first context compression MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sarup")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0"

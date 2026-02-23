"""Package operators for executing installation and removal actions.

This module provides abstract and concrete implementations of package
operators for different package managers (APT, Flatpak).
"""

from popctl.operators.apt import AptOperator
from popctl.operators.base import Operator
from popctl.operators.flatpak import FlatpakOperator

__all__ = ["Operator", "AptOperator", "FlatpakOperator"]

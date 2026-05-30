import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

# Raiz do plugin
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PLUGIN_DIR))

# Cria pacote virtual 'feature_panel' apontando para a raiz do plugin,
# necessário para resolver imports relativos (from .x import y)
pkg = types.ModuleType("feature_panel")
pkg.__path__ = [os.path.abspath(PLUGIN_DIR)]
pkg.__package__ = "feature_panel"
sys.modules["feature_panel"] = pkg

# Mock do dockwidget antes que attribute_window tente importá-lo
dock_mock = types.ModuleType("feature_panel.attribute_window_dockwidget")
dock_mock.AttributeWindowDockWidget = MagicMock()
dock_mock.tr = lambda s: s
sys.modules["feature_panel.attribute_window_dockwidget"] = dock_mock
sys.modules["attribute_window_dockwidget"] = dock_mock

# Carrega attribute_window como parte do pacote virtual
spec = importlib.util.spec_from_file_location(
    "feature_panel.attribute_window",
    os.path.join(os.path.abspath(PLUGIN_DIR), "attribute_window.py"),
    submodule_search_locations=[],
)
mod = importlib.util.module_from_spec(spec)
mod.__package__ = "feature_panel"
sys.modules["feature_panel.attribute_window"] = mod
sys.modules["attribute_window"] = mod
spec.loader.exec_module(mod)

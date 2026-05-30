# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtWidgets import QToolBar, QVBoxLayout, QWidget
from qgis.gui import QgsDockWidget


class AttributeWindowDockWidget(QgsDockWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Feature Attribute Window")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(16, 16))
        layout.addWidget(self.toolbar)

        self._contentArea = QWidget()
        self._contentLayout = QVBoxLayout(self._contentArea)
        self._contentLayout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._contentArea, 1)

        self.setWidget(container)

    def setContentWidget(self, widget):
        """Replace the content area below the toolbar."""
        while self._contentLayout.count():
            item = self._contentLayout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._contentLayout.addWidget(widget)
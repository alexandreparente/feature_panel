# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Feature Panel
                                 A QGIS plugin
 A dockable panel that automatically displays and enables editing of feature
 attributes based on the current map selection.
                              -------------------
        begin                : 2025
        copyright            : (C) 2025 by Alexandre Parente Lima
        email                : alexandre.parente@gmail.com

        Based on Feature Attribute Window
        copyright            : (C) Regio OÜ
        email                : geospatial@regio.ee
        homepage             : https://github.com/regio-geospatial/attributewindow
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtWidgets import QToolBar, QVBoxLayout, QWidget
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import QCoreApplication

def tr(string):
    return QCoreApplication.translate("@default", string)

class AttributeWindowDockWidget(QgsDockWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Feature Panel"))

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
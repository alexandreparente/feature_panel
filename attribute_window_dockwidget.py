# -*- coding: utf-8 -*-

import os
from qgis.PyQt import uic
from qgis.gui import QgsDockWidget

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "attribute_window_dockwidget_base.ui")
)

class AttributeWindowDockWidget(QgsDockWidget, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
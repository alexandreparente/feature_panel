# -*- coding: utf-8 -*-

import os

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QSize, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QMenu, QScrollArea, QSplitter, QTreeView

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from qgis.core import QgsProject, QgsSettings, QgsVectorLayer, edit
from qgis.gui import QgsHighlight

from .attribute_window_dockwidget import AttributeWindowDockWidget

# Qt enum compatibility
try:
    _Vertical = Qt.Orientation.Vertical
    _CustomContextMenu = Qt.ContextMenuPolicy.CustomContextMenu
    _RightDockWidgetArea = Qt.DockWidgetArea.RightDockWidgetArea
except AttributeError:
    _Vertical = Qt.Vertical
    _CustomContextMenu = Qt.CustomContextMenu
    _RightDockWidgetArea = Qt.RightDockWidgetArea


class AttributeWindow:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: QGIS interface instance
        :type iface: QgsInterface
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # initialize locale
        locale_val = QSettings().value("locale/userLocale", "en")
        locale = str(locale_val)[0:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"AttributeWindow_{locale}.qm")

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr("&Feature Attribute Window")
        self.toolbar = self.iface.addToolBar("AttributeWindow")
        self.toolbar.setObjectName("AttributeWindow")

        self.project = QgsProject.instance()
        self.pluginIsActive = False
        self.dockwidget = None

        self.featureForm = None
        self.formScrollArea = None
        self.layerTree = None
        self.splitter = None

        self.a = None
        self.featuresInLayerTree = []

        self.timer = QTimer(self.iface.mapCanvas())
        self.lstHighlights = []
        self.timer.timeout.connect(self.finishFlash)

        settings = QgsSettings()
        isOpen = settings.value("attributeWindow/isopen", "False")
        if isOpen == "True":
            self.run()

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate("AttributeWindow", message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        """Add a toolbar icon to the toolbar."""
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        icon_path = ":/plugins/attribute_window/icon.png"
        self.add_action(
            icon_path,
            text=self.tr("Attribute Window"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )
        self.handler = None
        self.selected_layer = None

    def onClosePlugin(self):
        """Cleanup necessary items here when plugin dockwidget is closed."""
        try:
            self.dockwidget.closingPlugin.disconnect(self.onClosePlugin)
        except Exception:
            pass

        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception:
            pass

        for action in self.actions:
            action.setChecked(False)

        self.pluginIsActive = False
        settings = QgsSettings()
        settings.setValue("attributeWindow/isopen", "False")

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&Feature Attribute Window"), action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def _wrapInScrollArea(self, widget):
        """Wrap a widget in a QScrollArea to prevent layout blowup
        with tall Drag-and-Drop Designer forms."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)  # Qt6
        except AttributeError:
            scroll.setFrameShape(QScrollArea.NoFrame)  # Qt5
        scroll.setWidget(widget)
        return scroll

    def _removeOldForm(self):
        """Clean up the previous feature form and its scroll area."""
        if self.featureForm is not None:
            try:
                self.featureForm.accept()
            except Exception:
                try:
                    self.featureForm.close()
                except Exception:
                    pass
            self.featureForm = None
        if self.formScrollArea is not None:
            self.formScrollArea.setParent(None)
            self.formScrollArea.deleteLater()
            self.formScrollArea = None

    def updateAttributes(self):
        self.splitter = QSplitter(_Vertical)
        self.layerTree = QTreeView()
        self.layerTree.setHeaderHidden(True)

        model = QStandardItemModel(self.layerTree)
        self.layerTree.setModel(model)

        self.layerTree.clicked.connect(self.updateFeatureFromTreeView)
        self.layerTree.setContextMenuPolicy(_CustomContextMenu)
        self.layerTree.customContextMenuRequested.connect(self.openMenu)

        self.splitter.addWidget(self.layerTree)

        self._removeOldForm()

        self.featuresInLayerTree = []

        if self.dockwidget is not None:
            self.dockwidget.setWidget(self.splitter)

        if self.iface.mapCanvas().currentLayer() is None:
            return

        for layer in self.iface.mapCanvas().layers():
            if not isinstance(layer, QgsVectorLayer):
                continue

            features = layer.selectedFeatures()
            if not features:
                continue

            layerItem = QStandardItem(layer.name())
            layerItem.setEnabled(False)

            for feat in features:
                attrs = feat.attributes()
                if attrs:
                    try:
                        label = str(attrs[0])
                    except Exception:
                        label = str(attrs[0])
                else:
                    label = str(feat.id())

                featItem = QStandardItem(label)
                featItem.setEditable(False)

                self.featuresInLayerTree.extend([featItem, feat, layer])
                layerItem.appendRow(featItem)

            model.appendRow(layerItem)

        self.layerTree.expandAll()

        if self.featuresInLayerTree:
            first_feature = self.featuresInLayerTree[1]
            first_layer = self.featuresInLayerTree[2]
            try:
                self.featureForm = self.iface.getFeatureForm(first_layer, first_feature)
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self.splitter.setSizes([100, 500])
                if self.dockwidget is not None:
                    self.dockwidget.setWidget(self.splitter)
            except Exception:
                self.featureForm = None
                self.formScrollArea = None

    def updateFeatureFromTreeView(self, index):
        if not index.isValid():
            return

        self.a = index.model().itemFromIndex(index)
        for item in self.featuresInLayerTree:
            if item == self.a:
                itemIndex = self.featuresInLayerTree.index(item)
                layer = self.featuresInLayerTree[itemIndex + 2]
                feature = self.featuresInLayerTree[itemIndex + 1]

                self.iface.setActiveLayer(layer)

                self._removeOldForm()

                self.featureForm = self.iface.getFeatureForm(layer, feature)
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self.splitter.setSizes([100, 500])
                return

    def openMenu(self, position):
        index = self.layerTree.indexAt(position)
        if index.isValid():
            self.a = index.model().itemFromIndex(index)
            self.updateFeatureFromTreeView(index)
        else:
            self.a = None

        menu = QMenu(self.layerTree)

        deselect_action = menu.addAction("Deselect")
        deselect_action.triggered.connect(self.deselectActionFunc)

        zoom_action = menu.addAction("Zoom to Feature")
        zoom_action.triggered.connect(self.zoomToFeatureActionFunc)

        pan_action = menu.addAction("Pan to Feature")
        pan_action.triggered.connect(self.panToFeatureActionFunc)

        flash_action = menu.addAction("Flash")
        flash_action.triggered.connect(self.flashFeatureActionFunc)

        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(self.deleteFeatureActionFunc)

        menu.exec(self.layerTree.viewport().mapToGlobal(position))

    def deselectActionFunc(self):
        try:
            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]
            layer.deselect(feature.id())
        except Exception:
            pass

    def zoomToFeatureActionFunc(self):
        try:
            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]
            self.iface.mapCanvas().zoomToFeatureIds(layer, [feature.id()])
        except Exception:
            pass

    def panToFeatureActionFunc(self):
        try:
            curr_scale = self.iface.mapCanvas().scale()
            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]
            self.iface.mapCanvas().zoomToFeatureIds(layer, [feature.id()])
            self.iface.mapCanvas().zoomScale(curr_scale)
        except Exception:
            pass

    def flashFeatureActionFunc(self):
        try:
            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]

            h = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), layer)
            h.setColor(QColor(255, 0, 0, 255))
            h.setWidth(3)
            h.setFillColor(QColor(255, 0, 0, 100))

            self.lstHighlights.append(h)
            self.timer.start(500)
        except Exception:
            pass

    def finishFlash(self):
        self.timer.stop()
        try:
            for h in self.lstHighlights:
                try:
                    h.hide()
                except Exception:
                    pass
        finally:
            self.lstHighlights = []

    def deleteFeatureActionFunc(self):
        try:
            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]

            with edit(layer):
                layer.deleteFeature(feature.id())

            self.iface.mapCanvas().refresh()
            self.updateAttributes()
        except Exception:
            pass

    def run(self):
        """Run method that loads and starts the plugin."""
        settings = QgsSettings()

        if not self.pluginIsActive:
            self.pluginIsActive = True

            self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)

            if self.dockwidget is None:
                self.dockwidget = AttributeWindowDockWidget()
                self.dockwidget.setMinimumSize(QSize(200, 300))
                self.updateAttributes()

            self.dockwidget.closingPlugin.connect(self.onClosePlugin)

            self.iface.addDockWidget(_RightDockWidgetArea, self.dockwidget)
            self.dockwidget.show()

            settings.setValue("attributeWindow/isopen", "True")

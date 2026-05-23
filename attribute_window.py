# -*- coding: utf-8 -*-

import os

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QSize, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QAction, QDialogButtonBox, QMenu, QScrollArea, QSplitter, QTreeView

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from qgis.core import QgsApplication, QgsProject, QgsSettings, QgsVectorLayer, edit
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
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale_val = QSettings().value("locale/userLocale", "en")
        locale = str(locale_val)[0:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"AttributeWindow_{locale}.qm")

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&Feature Attribute Window")
        self.toolbar = self.iface.addToolBar("AttributeWindow")
        self.toolbar.setObjectName("AttributeWindow")

        self.dockwidget = None
        self.toggleEditingAction = None
        self._editingTrackedLayer = None

        self.featureForm = None
        self.formScrollArea = None
        self.layerTree = None
        self.splitter = None

        self.a = None
        self.featuresInLayerTree = []

        self.timer = QTimer(self.iface.mapCanvas())
        self.lstHighlights = []
        self.timer.timeout.connect(self.finishFlash)

    def tr(self, message):
        return QCoreApplication.translate("AttributeWindow", message)

    def add_action(
        self,
        icon_path,
        text,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        action = QAction(icon, text, parent)
        action.setCheckable(True)

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
            parent=self.iface.mainWindow(),
        )

        self.dockwidget = AttributeWindowDockWidget()
        self.dockwidget.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.dockwidget.setToggleVisibilityAction(self.actions[0])
        self.iface.addDockWidget(_RightDockWidgetArea, self.dockwidget)

        self.toggleEditingAction = QAction(
            QgsApplication.getThemeIcon("mActionToggleEditing.svg"),
            self.tr("Toggle Editing"),
            self.dockwidget,
        )
        self.toggleEditingAction.setCheckable(True)
        self.toggleEditingAction.setEnabled(False)
        self.toggleEditingAction.triggered.connect(self._toggleEditing)
        self.dockwidget.toolbar.addAction(self.toggleEditingAction)

        zoomAction = QAction(
            QgsApplication.getThemeIcon("mActionZoomToSelected.svg"),
            self.tr("Zoom to Feature"),
            self.dockwidget,
        )
        zoomAction.triggered.connect(self.zoomToFeatureActionFunc)
        self.dockwidget.toolbar.addAction(zoomAction)

        flashAction = QAction(
            QIcon(os.path.join(self.plugin_dir, "icons", "mActionFlash.svg")),
            self.tr("Flash Feature"),
            self.dockwidget,
        )
        flashAction.triggered.connect(self.flashFeatureActionFunc)
        self.dockwidget.toolbar.addAction(flashAction)

        self.updateAttributes()
        self.dockwidget.hide()

        self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)

    def _currentLayer(self):
        """Return the layer of the currently selected tree item, or None."""
        if self.a is None:
            return None
        try:
            idx = self.featuresInLayerTree.index(self.a)
            return self.featuresInLayerTree[idx + 2]
        except (ValueError, IndexError):
            return None

    def _toggleEditing(self):
        """Toggle editing on the layer of the selected tree item."""
        layer = self._currentLayer()
        if layer is None or not isinstance(layer, QgsVectorLayer):
            self.toggleEditingAction.setChecked(False)
            return
        if layer.isEditable():
            layer.commitChanges()
        else:
            layer.startEditing()
        self._syncToggleEditingButton()

    def _trackEditingLayer(self, layer):
        """Switch editing-state signal tracking to a new layer."""
        if self._editingTrackedLayer is not None:
            try:
                self._editingTrackedLayer.editingStarted.disconnect(
                    self._syncToggleEditingButton
                )
                self._editingTrackedLayer.editingStopped.disconnect(
                    self._syncToggleEditingButton
                )
            except Exception:
                pass

        self._editingTrackedLayer = layer

        if layer is not None and isinstance(layer, QgsVectorLayer):
            layer.editingStarted.connect(self._syncToggleEditingButton)
            layer.editingStopped.connect(self._syncToggleEditingButton)

        self._syncToggleEditingButton()

    def _syncToggleEditingButton(self):
        """Sync the toggle editing button with the tracked layer's editing state."""
        if self.toggleEditingAction is None:
            return
        layer = self._currentLayer()
        if layer is not None and isinstance(layer, QgsVectorLayer):
            self.toggleEditingAction.setEnabled(True)
            self.toggleEditingAction.setChecked(layer.isEditable())
        else:
            self.toggleEditingAction.setEnabled(False)
            self.toggleEditingAction.setChecked(False)

    def _suppressActionMenu(self, form):
        """Oculta o menu de ações (duplicar recurso) do QgsFeatureForm."""
        for child in form.findChildren(QDialogButtonBox):
            child.hide()

    def unload(self):
        self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        self._trackEditingLayer(None)

        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&Feature Attribute Window"), action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def _wrapInScrollArea(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)  # Qt6
        except AttributeError:
            scroll.setFrameShape(QScrollArea.NoFrame)  # Qt5
        scroll.setWidget(widget)
        return scroll

    def _removeOldForm(self):
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
        self.a = None

        if self.dockwidget is not None:
            self.dockwidget.setContentWidget(self.splitter)

        if self.iface.mapCanvas().currentLayer() is None:
            self._trackEditingLayer(None)
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
                self._suppressActionMenu(self.featureForm)
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self.splitter.setSizes([100, 500])
                if self.dockwidget is not None:
                    self.dockwidget.setContentWidget(self.splitter)
                self.a = self.featuresInLayerTree[0]
                self._trackEditingLayer(first_layer)
            except Exception:
                self.featureForm = None
                self.formScrollArea = None
                self._trackEditingLayer(None)
        else:
            self._trackEditingLayer(None)

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
                self._suppressActionMenu(self.featureForm)
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self.splitter.setSizes([100, 500])
                self._trackEditingLayer(layer)
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
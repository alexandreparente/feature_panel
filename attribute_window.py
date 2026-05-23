# -*- coding: utf-8 -*-

import os

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QSize, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QAction, QApplication, QDialogButtonBox, QMenu, QScrollArea, QSplitter, QTreeView

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
        self.deleteAction = None
        self._editingTrackedLayer = None
        self._pendingUpdate = False

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
            QgsApplication.getThemeIcon("mActionHighlightFeature.svg"),
            self.tr("Flash Feature"),
            self.dockwidget,
        )
        flashAction.triggered.connect(self.flashFeatureActionFunc)
        self.dockwidget.toolbar.addAction(flashAction)

        self.deleteAction = QAction(
            QgsApplication.getThemeIcon("mActionDeleteSelectedFeatures.svg"),
            self.tr("Delete Feature"),
            self.dockwidget,
        )
        self.deleteAction.setEnabled(False)
        self.deleteAction.triggered.connect(self.deleteFeatureActionFunc)
        self.dockwidget.toolbar.addAction(self.deleteAction)

        self.updateAttributes()
        self.dockwidget.hide()

        self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
        QApplication.instance().focusWindowChanged.connect(self._onFocusWindowChanged)

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
            if self.featureForm is not None:
                self.featureForm.attributeForm().save()
            layer.commitChanges()
            self._doUpdateAttributes()
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
            is_editable = layer.isEditable()
            self.toggleEditingAction.setEnabled(True)
            self.toggleEditingAction.setChecked(is_editable)

            if self.deleteAction is not None:
                self.deleteAction.setEnabled(is_editable)
        else:
            self.toggleEditingAction.setEnabled(False)
            self.toggleEditingAction.setChecked(False)
            if self.deleteAction is not None:
                self.deleteAction.setEnabled(False)

    def _suppressActionMenu(self, form):
        """Oculta o menu de ações (duplicar recurso) do QgsFeatureForm."""
        for child in form.findChildren(QDialogButtonBox):
            child.hide()

    def _onFocusWindowChanged(self, window):
        """Safely runs the pending update once the main window regains focus."""
        if not self._pendingUpdate:
            return
        active_window = QApplication.activeWindow()
        if active_window is self.iface.mainWindow():
            self._pendingUpdate = False
            QTimer.singleShot(0, self._doUpdateAttributes)

    def unload(self):
        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception:
            pass

        try:
            QApplication.instance().focusWindowChanged.disconnect(self._onFocusWindowChanged)
        except Exception:
            pass

        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        self.finishFlash()

        self.a = None
        self.featuresInLayerTree = []
        self._trackEditingLayer(None)
        self._removeOldForm()

        for action in self.actions:
            try:
                self.iface.removePluginMenu(self.tr("&Feature Attribute Window"), action)
                self.iface.removeToolBarIcon(action)
            except Exception:
                pass

        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()

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
            try:
                if self.formScrollArea.widget():
                    self.formScrollArea.widget().setParent(None)
                self.formScrollArea.setParent(None)
                self.formScrollArea.deleteLater()
            except Exception:
                pass
            self.formScrollArea = None

    def updateAttributes(self):
        """Skip update if another window (e.g. attribute table) is active."""
        active_window = QApplication.activeWindow()
        if active_window is not None and active_window is not self.iface.mainWindow():
            self._pendingUpdate = True
            return
        self._pendingUpdate = False
        self._doUpdateAttributes()

    def _doUpdateAttributes(self):
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
        layer = self._currentLayer()
        if layer is not None and isinstance(layer, QgsVectorLayer):
            delete_action.setEnabled(layer.isEditable())
        else:
            delete_action.setEnabled(False)
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
        """deletes ONLY the selected feature in the QTreeView, preserving the other features."""
        try:
            if self.layerTree is not None:
                selection_model = self.layerTree.selectionModel()
                if selection_model and selection_model.hasSelection():
                    index = selection_model.currentIndex()
                    if index.isValid():
                        self.a = index.model().itemFromIndex(index)

            if self.a is None:
                return

            itemIndex = self.featuresInLayerTree.index(self.a)
            feature = self.featuresInLayerTree[itemIndex + 1]
            layer = self.featuresInLayerTree[itemIndex + 2]

            if not layer or not layer.isEditable():
                return
            try:
                self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
            except Exception:
                pass

            original_selection = layer.selectedFeatureIds()

            layer.selectByIds([feature.id()])
            self.iface.setActiveLayer(layer)

            delete_action = self.iface.mainWindow().findChild(QAction, "mActionDeleteSelected")
            if delete_action is not None:
                delete_action.trigger()

            is_deleted = (layer.getFeature(feature.id()).isValid() is False)

            if not is_deleted:
                layer.selectByIds(original_selection)
            else:
                remaining_selection = [fid for fid in original_selection if fid != feature.id()]
                layer.selectByIds(remaining_selection)

            self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)

            self.a = None
            QTimer.singleShot(0, self._safeRefreshAfterDelete)

        except (ValueError, IndexError, RuntimeError):
            try:
                self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
            except Exception:
                pass

    def _safeRefreshAfterDelete(self):
        """Asynchronously synchronizes layouts after the dialog close."""
        try:
            self.iface.mapCanvas().refresh()
            self.updateAttributes()
        except Exception:
            pass
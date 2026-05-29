# -*- coding: utf-8 -*-

import os
from collections import namedtuple

from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QAction, QApplication, QMenu, QMenuBar,
    QScrollArea, QSplitter, QTreeView,
)
from qgis.core import QgsApplication, QgsFeature, QgsVectorLayer
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm

from .attribute_window_dockwidget import AttributeWindowDockWidget

TreeEntry = namedtuple("TreeEntry", ["item", "feature", "layer"])


class AttributeWindow:

    def __init__(self, iface):
        self.iface      = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = str(QSettings().value("locale/userLocale", "en"))[:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"AttributeWindow_{locale}.qm")
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu    = self.tr("&Feature Attribute Window")
        self.toolbar = self.iface.addToolBar("AttributeWindow")
        self.toolbar.setObjectName("AttributeWindow")

        self.dockwidget           = None
        self.toggleEditingAction  = None
        self.multiEditAction      = None
        self.deleteAction         = None
        self._editingTrackedLayer = None
        self._pendingUpdate       = False

        self._multiEditActive = False
        self._multiEditForm   = None

        self.featureForm    = None
        self.formScrollArea = None
        self.layerTree      = None
        self.splitter       = None

        # Currently selected QStandardItem
        self.a                   = None
        self.featuresInLayerTree = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def tr(self, message):
        return QCoreApplication.translate("AttributeWindow", message)

    def add_action(self, icon_path, text, add_to_menu=True, add_to_toolbar=True,
                   status_tip=None, whats_this=None, parent=None):
        icon   = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        action = QAction(icon, text, parent)
        action.setCheckable(True)

        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    # ------------------------------------------------------------------
    # initGui / unload
    # ------------------------------------------------------------------

    def initGui(self):
        self.add_action(
            ":/plugins/attribute_window/icon.png",
            text=self.tr("Attribute Window"),
            parent=self.iface.mainWindow(),
        )

        self.dockwidget = AttributeWindowDockWidget()
        self.dockwidget.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.dockwidget.setToggleVisibilityAction(self.actions[0])
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget)

        self.toggleEditingAction = QAction(
            QgsApplication.getThemeIcon("mActionToggleEditing.svg"),
            self.tr("Toggle Editing"),
            self.dockwidget,
        )
        self.toggleEditingAction.setCheckable(True)
        self.toggleEditingAction.setEnabled(False)
        self.toggleEditingAction.triggered.connect(self._toggleEditing)
        self.dockwidget.toolbar.addAction(self.toggleEditingAction)

        self.multiEditAction = QAction(
            QgsApplication.getThemeIcon("mActionMultiEdit.svg"),
            self.tr("Edit All Selected Features"),
            self.dockwidget,
        )
        self.multiEditAction.setCheckable(True)
        self.multiEditAction.setEnabled(False)
        self.multiEditAction.triggered.connect(self._toggleMultiEdit)
        self.dockwidget.toolbar.addAction(self.multiEditAction)

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

        self.splitter  = QSplitter(Qt.Orientation.Vertical)
        self.layerTree = QTreeView()
        self.layerTree.setHeaderHidden(True)
        self.layerTree.setMinimumSize(100, 120)
        self.splitter.addWidget(self.layerTree)
        self.dockwidget.setContentWidget(self.splitter)

        self.updateAttributes()
        self.dockwidget.hide()

        self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
        QApplication.instance().focusWindowChanged.connect(self._onFocusWindowChanged)

    def unload(self):
        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception:
            pass
        try:
            QApplication.instance().focusWindowChanged.disconnect(self._onFocusWindowChanged)
        except Exception:
            pass

        self.a                   = None
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

    # ------------------------------------------------------------------
    # Current selection helpers
    # ------------------------------------------------------------------

    def _currentEntry(self):
        if self.a is None:
            return None
        return next((e for e in self.featuresInLayerTree if e.item is self.a), None)

    def _currentLayer(self):
        entry = self._currentEntry()
        return entry.layer if entry is not None else None

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _saveCurrentForm(self):
        """Flush edit-buffer changes; hideButtonBox() does not auto-commit."""
        if self._multiEditActive and self._multiEditForm is not None:
            try:
                self._multiEditForm.save()
            except Exception:
                pass
        elif self.featureForm is not None:
            try:
                self.featureForm.attributeForm().save()
            except Exception:
                pass

    def _toggleEditing(self):
        layer = self._currentLayer()
        if layer is None or not isinstance(layer, QgsVectorLayer):
            self.toggleEditingAction.setChecked(False)
            return

        was_editable = layer.isEditable()
        if was_editable:
            self._saveCurrentForm()
            self._multiEditActive = False

        # Delegate to the QGIS action to ensure full canvas pipeline (vertex markers, etc.)
        self.iface.setActiveLayer(layer)
        self.iface.actionToggleEditing().trigger()

        if was_editable:
            self._doUpdateAttributes()

        self._syncToggleEditingButton()

    def _trackEditingLayer(self, layer):
        """Re-subscribe editing signals to a different layer."""
        if self._editingTrackedLayer is not None:
            try:
                self._editingTrackedLayer.editingStarted.disconnect(self._syncToggleEditingButton)
                self._editingTrackedLayer.editingStopped.disconnect(self._syncToggleEditingButton)
            except Exception:
                pass

        self._editingTrackedLayer = layer

        if layer is not None and isinstance(layer, QgsVectorLayer):
            layer.editingStarted.connect(self._syncToggleEditingButton)
            layer.editingStopped.connect(self._syncToggleEditingButton)

        self._syncToggleEditingButton()

    def _syncToggleEditingButton(self):
        if self.toggleEditingAction is None:
            return

        layer = self._currentLayer()
        if layer is not None and isinstance(layer, QgsVectorLayer):
            is_editable   = layer.isEditable()
            has_selection = bool(layer.selectedFeatureIds())

            self.toggleEditingAction.setEnabled(True)
            self.toggleEditingAction.setChecked(is_editable)

            if self.multiEditAction is not None:
                self.multiEditAction.setEnabled(is_editable and has_selection)
                if not is_editable:
                    self.multiEditAction.setChecked(False)
                    self._multiEditActive = False

            if self.deleteAction is not None:
                self.deleteAction.setEnabled(is_editable and has_selection)
        else:
            self.toggleEditingAction.setEnabled(False)
            self.toggleEditingAction.setChecked(False)
            if self.multiEditAction is not None:
                self.multiEditAction.setEnabled(False)
                self.multiEditAction.setChecked(False)
                self._multiEditActive = False
            if self.deleteAction is not None:
                self.deleteAction.setEnabled(False)

    # ------------------------------------------------------------------
    # Multi-edit
    # ------------------------------------------------------------------

    def _toggleMultiEdit(self, checked):
        if checked:
            self._showMultiEditForm()
        else:
            # Explicit save required; hideButtonBox() removed the "Apply" button.
            if self._multiEditForm is not None:
                try:
                    self._multiEditForm.save()
                except Exception:
                    pass
            self._multiEditActive = False
            self._multiEditForm   = None
            self.layerTree.setEnabled(True)
            self._doUpdateAttributes()

    def _showMultiEditForm(self):
        layer = self._currentLayer()
        if layer is None or not isinstance(layer, QgsVectorLayer):
            self.multiEditAction.setChecked(False)
            return

        ids = layer.selectedFeatureIds()
        if not ids or not layer.isEditable():
            self.multiEditAction.setChecked(False)
            return

        self._removeOldForm()

        form = QgsAttributeForm(
            layer, QgsFeature(), QgsAttributeEditorContext(), self.dockwidget,
        )
        try:
            form.setMode(QgsAttributeEditorContext.MultiEditMode)
        except (AttributeError, TypeError):
            form.setMode(2)  # MultiEditMode = 2 in older QGIS builds

        form.setMultiEditFeatureIds(ids)

        try:
            form.setMessageBar(self.iface.messageBar())
        except AttributeError:
            pass

        form.hideButtonBox()

        self._multiEditForm   = form
        self._multiEditActive = True

        self.layerTree.clearSelection()
        self.layerTree.setEnabled(False)

        self.formScrollArea = self._wrapInScrollArea(form)
        self.splitter.addWidget(self.formScrollArea)
        form.show()
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 5)

    # ------------------------------------------------------------------
    # Form / UI helpers
    # ------------------------------------------------------------------

    def _suppressActionMenu(self, form):
        """Hide the QMenuBar containing feature actions (duplicate, open form, etc.)."""
        try:
            form.attributeForm().hideButtonBox()
        except AttributeError:
            pass
        for child in form.findChildren(QMenuBar):
            child.hide()

    def _wrapInScrollArea(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        except AttributeError:
            scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _removeOldForm(self):
        if self._multiEditForm is not None:
            try:
                self._multiEditForm.setParent(None)
            except Exception:
                pass
            self._multiEditForm = None

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
                w = self.formScrollArea.widget()
                if w:
                    w.setParent(None)
                self.formScrollArea.setParent(None)
                self.formScrollArea.deleteLater()
            except Exception:
                pass
            self.formScrollArea = None

    # ------------------------------------------------------------------
    # Deferred update on focus restore
    # ------------------------------------------------------------------

    def _onFocusWindowChanged(self, window):
        if not self._pendingUpdate:
            return
        if QApplication.activeWindow() is self.iface.mainWindow():
            self._pendingUpdate = False
            QTimer.singleShot(0, self._doUpdateAttributes)

    # ------------------------------------------------------------------
    # Attribute update
    # ------------------------------------------------------------------

    def updateAttributes(self):
        """Defer update when a secondary window (e.g. attribute table) is active."""
        active_window = QApplication.activeWindow()
        if active_window is not None and active_window is not self.iface.mainWindow():
            self._pendingUpdate = True
            return
        self._pendingUpdate = False
        self._doUpdateAttributes()

    def _doUpdateAttributes(self):
        self._multiEditActive = False
        if self.multiEditAction is not None:
            self.multiEditAction.setChecked(False)

        self._removeOldForm()

        model = QStandardItemModel(self.layerTree)
        self.layerTree.setModel(model)
        self.layerTree.setEnabled(True)

        try:
            self.layerTree.clicked.disconnect()
        except Exception:
            pass
        try:
            self.layerTree.customContextMenuRequested.disconnect()
        except Exception:
            pass
        self.layerTree.clicked.connect(self.updateFeatureFromTreeView)
        self.layerTree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.layerTree.customContextMenuRequested.connect(self.openMenu)

        self.featuresInLayerTree = []
        self.a                   = None

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
                attrs    = feat.attributes()
                label    = str(attrs[0]) if attrs else str(feat.id())
                featItem = QStandardItem(label)
                featItem.setEditable(False)
                self.featuresInLayerTree.append(TreeEntry(featItem, feat, layer))
                layerItem.appendRow(featItem)

            model.appendRow(layerItem)

        self.layerTree.expandAll()

        if self.featuresInLayerTree:
            first = self.featuresInLayerTree[0]
            try:
                current_feature = first.layer.getFeature(first.feature.id())
                self.featureForm = self.iface.getFeatureForm(first.layer, current_feature)
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self._suppressActionMenu(self.featureForm)
                self.splitter.setStretchFactor(0, 1)
                self.splitter.setStretchFactor(1, 5)
                self.a = first.item
                self._trackEditingLayer(first.layer)
            except Exception:
                self.featureForm    = None
                self.formScrollArea = None
                self._trackEditingLayer(None)
        else:
            self._trackEditingLayer(None)

    # ------------------------------------------------------------------
    # Tree view interactions
    # ------------------------------------------------------------------

    def updateFeatureFromTreeView(self, index):
        if not index.isValid():
            return

        if self._multiEditActive:
            if self._multiEditForm is not None:
                try:
                    self._multiEditForm.save()
                except Exception:
                    pass
            self._multiEditActive = False
            if self.multiEditAction is not None:
                self.multiEditAction.setChecked(False)
            self.layerTree.setEnabled(True)

        self.a = index.model().itemFromIndex(index)
        entry  = self._currentEntry()
        if entry is None:
            return

        self.iface.setActiveLayer(entry.layer)

        if self.featureForm is not None:
            try:
                self.featureForm.attributeForm().save()
            except Exception:
                pass

        self._removeOldForm()

        try:
            current_feature = entry.layer.getFeature(entry.feature.id())
            self.featureForm = self.iface.getFeatureForm(entry.layer, current_feature)
            self.formScrollArea = self._wrapInScrollArea(self.featureForm)
            self.splitter.addWidget(self.formScrollArea)
            self.featureForm.show()
            self._suppressActionMenu(self.featureForm)
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 5)
            self._trackEditingLayer(entry.layer)
        except Exception:
            self.featureForm    = None
            self.formScrollArea = None

    def openMenu(self, position):
        index = self.layerTree.indexAt(position)
        if index.isValid():
            self.a = index.model().itemFromIndex(index)
            self.updateFeatureFromTreeView(index)
        else:
            self.a = None

        layer  = self._currentLayer()
        menu   = QMenu(self.layerTree)

        menu.addAction("Deselect").triggered.connect(self.deselectActionFunc)
        menu.addAction("Zoom to Feature").triggered.connect(self.zoomToFeatureActionFunc)
        menu.addAction("Pan to Feature").triggered.connect(self.panToFeatureActionFunc)
        menu.addAction("Flash").triggered.connect(self.flashFeatureActionFunc)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(
            layer is not None and isinstance(layer, QgsVectorLayer) and layer.isEditable()
        )
        delete_action.triggered.connect(self.deleteFeatureActionFunc)

        menu.exec(self.layerTree.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------
    # Feature actions
    # ------------------------------------------------------------------

    def deselectActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            entry.layer.deselect(entry.feature.id())
        except Exception:
            pass

    def zoomToFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().zoomToFeatureIds(entry.layer, [entry.feature.id()])
        except Exception:
            pass

    def panToFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().panToFeatureIds(entry.layer, [entry.feature.id()])
        except Exception:
            pass

    def flashFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().flashFeatureIds(entry.layer, [entry.feature.id()])
        except Exception:
            pass

    def deleteFeatureActionFunc(self):
        """Delete the tree-selected feature while preserving the rest of the selection."""
        if self.layerTree is not None:
            sel = self.layerTree.selectionModel()
            if sel and sel.hasSelection():
                idx = sel.currentIndex()
                if idx.isValid():
                    self.a = idx.model().itemFromIndex(idx)

        entry = self._currentEntry()
        if entry is None or not entry.layer.isEditable():
            return

        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception:
            pass

        try:
            original_selection = entry.layer.selectedFeatureIds()

            entry.layer.selectByIds([entry.feature.id()])
            self.iface.setActiveLayer(entry.layer)

            delete_action = self.iface.mainWindow().findChild(QAction, "mActionDeleteSelected")
            if delete_action is not None:
                delete_action.trigger()

            is_deleted = not entry.layer.getFeature(entry.feature.id()).isValid()
            remaining  = (
                [fid for fid in original_selection if fid != entry.feature.id()]
                if is_deleted else original_selection
            )
            entry.layer.selectByIds(remaining)
        except Exception:
            pass
        finally:
            try:
                self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
            except Exception:
                pass

        self.a = None
        QTimer.singleShot(0, self._safeRefreshAfterDelete)

    def _safeRefreshAfterDelete(self):
        try:
            self.iface.mapCanvas().refresh()
            self.updateAttributes()
        except Exception:
            pass
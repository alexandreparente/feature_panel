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

import logging
import os
from collections import namedtuple

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QSettings,
    Qt,
    QTimer,
    QTranslator,
    QLocale,
)
from qgis.PyQt.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QMenu,
    QMenuBar,
    QScrollArea,
    QSplitter,
    QTreeView,
    QWidget,
)
from qgis.core import QgsApplication, QgsFeature, QgsVectorLayer
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm, QgsMessageBar

from .attribute_window_dockwidget import AttributeWindowDockWidget, tr

log = logging.getLogger(__name__)

TreeEntry = namedtuple("TreeEntry", ["item", "feature", "layer"])


class AttributeWindow:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        self.settings = QSettings()
        locale = self.settings.value("locale/userLocale", QLocale.system().name())

        # Initialize locale
        locale_path = os.path.join(
            self.plugin_dir, "i18n", "FeaturePanel_{}.qm".format(locale)
        )

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = tr("&Feature Panel")
        self.toolbar = self.iface.addToolBar("FeaturePanel")
        self.toolbar.setObjectName("FeaturePanel")

        self.dockwidget = None
        self.toggleEditingAction = None
        self.multiEditAction = None
        self.zoomAction = None
        self.flashAction = None
        self.deleteAction = None
        self._editingTrackedLayer = None
        self._pendingUpdate = False

        self._multiEditActive = False
        self._multiEditForm = None
        self._multiEditIds: set = set()  # track IDs for precise dirty marking

        # Creates a QgsMessageBar to hide the default message of multiEditForm
        self._multiEditBarSink = QWidget()
        self._multiEditSilentBar = QgsMessageBar(self._multiEditBarSink)

        self.featureForm = None
        self.formScrollArea = None
        self.layerTree = None
        self.splitter = None

        # Currently selected QStandardItem
        self._selectedItem = None
        self.featuresInLayerTree = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        icon = QIcon(os.path.join(os.path.dirname(__file__), "feature_panel.svg"))
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
            ":/plugins/feature_panel/feature_panel.svg",
            text=tr("Feature Panel"),
            parent=self.iface.mainWindow(),
        )

        self.dockwidget = AttributeWindowDockWidget()
        self.dockwidget.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.dockwidget.setToggleVisibilityAction(self.actions[0])
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget)
        self.iface.addTabifiedDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget, [], True
        )

        self.toggleEditingAction = QAction(
            QgsApplication.getThemeIcon("mActionToggleEditing.svg"),
            tr("Toggle Editing"),
            self.dockwidget,
        )
        self.toggleEditingAction.setCheckable(True)
        self.toggleEditingAction.setEnabled(False)
        self.toggleEditingAction.triggered.connect(self._toggleEditing)
        self.dockwidget.toolbar.addAction(self.toggleEditingAction)

        self.multiEditAction = QAction(
            QgsApplication.getThemeIcon("mActionMultiEdit.svg"),
            tr("Edit All Selected Features"),
            self.dockwidget,
        )
        self.multiEditAction.setCheckable(True)
        self.multiEditAction.setEnabled(False)
        self.multiEditAction.triggered.connect(self._toggleMultiEdit)
        self.dockwidget.toolbar.addAction(self.multiEditAction)

        self.zoomAction = QAction(
            QgsApplication.getThemeIcon("mActionZoomToSelected.svg"),
            tr("Zoom to Feature"),
            self.dockwidget,
        )
        self.zoomAction.setEnabled(False)
        self.zoomAction.triggered.connect(self.zoomToFeatureActionFunc)
        self.dockwidget.toolbar.addAction(self.zoomAction)

        self.flashAction = QAction(
            QgsApplication.getThemeIcon("mActionHighlightFeature.svg"),
            tr("Flash Feature"),
            self.dockwidget,
        )
        self.flashAction.setEnabled(False)
        self.flashAction.triggered.connect(self.flashFeatureActionFunc)
        self.dockwidget.toolbar.addAction(self.flashAction)

        self.deleteAction = QAction(
            QgsApplication.getThemeIcon("mActionDeleteSelectedFeatures.svg"),
            tr("Delete Feature"),
            self.dockwidget,
        )
        self.deleteAction.setEnabled(False)
        self.deleteAction.triggered.connect(self.deleteFeatureActionFunc)
        self.dockwidget.toolbar.addAction(self.deleteAction)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.layerTree = QTreeView()
        self.layerTree.setHeaderHidden(True)
        self.layerTree.setMinimumSize(100, 120)
        self.layerTree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.layerTree.clicked.connect(self.updateFeatureFromTreeView)
        self.layerTree.customContextMenuRequested.connect(self.openMenu)
        self.splitter.addWidget(self.layerTree)
        self.dockwidget.setContentWidget(self.splitter)

        self.updateAttributes()
        self.dockwidget.hide()

        self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
        QApplication.instance().focusWindowChanged.connect(self._onFocusWindowChanged)

    def unload(self):
        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception as e:
            log.debug("Suppressed exception: %s", e)
        try:
            QApplication.instance().focusWindowChanged.disconnect(
                self._onFocusWindowChanged
            )
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

        self._selectedItem = None
        self.featuresInLayerTree = []
        self._trackEditingLayer(None)
        self._removeOldForm()

        for action in self.actions:
            try:
                self.iface.removePluginMenu(tr("&Feature Panel"), action)
                self.iface.removeToolBarIcon(action)
            except Exception as e:
                log.warning("Failed to remove action from menu/toolbar: %s", e)

        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()

        del self.toolbar

    # ------------------------------------------------------------------
    # Current selection helpers
    # ------------------------------------------------------------------

    def _currentEntry(self):
        if self._selectedItem is None:
            return None
        return next(
            (e for e in self.featuresInLayerTree if e.item is self._selectedItem), None
        )

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
            except Exception as e:
                log.debug("Suppressed exception: %s", e)
        elif self.featureForm is not None:
            try:
                self.featureForm.attributeForm().save()
            except Exception as e:
                log.debug("Suppressed exception: %s", e)

    # ------------------------------------------------------------------
    # Dirty-state helpers
    # ------------------------------------------------------------------

    def _setItemDirtyStyle(self, item, dirty: bool):
        font = item.font()
        font.setItalic(dirty)
        item.setFont(font)
        item.setForeground(QColor(Qt.GlobalColor.red) if dirty else QColor())

    def _onEditingStopped(self):
        """Clear dirty marks when editing ends (commit or rollback confirmed)."""
        for entry in self.featuresInLayerTree:
            self._setItemDirtyStyle(entry.item, dirty=False)

    def _connectFormDirtySignal(self, form, item):
        """Mark item dirty as soon as the user interacts with any field.

        attributeChanged fires whenever a widget is touched — this is intentional:
        the highlight serves as a visual indicator that the field was accessed,
        even if the value was not changed.
        """
        try:
            attr_form = form.attributeForm()

            def _on_attribute_changed(_name, _val):
                self._setItemDirtyStyle(item, dirty=True)

            attr_form.attributeChanged.connect(_on_attribute_changed)
            # Store the handler so _removeOldForm can disconnect it cleanly.
            form._dirtyHandler = _on_attribute_changed
        except Exception as e:
            log.warning("Could not connect dirty signal: %s", e)

    def _isFeatureDirty(self, layer, fid):
        """Check whether the feature has unsaved changes in the layer's edit buffer."""
        if not layer or not layer.isEditable():
            return False
        buffer = layer.editBuffer()
        if buffer:
            if (
                fid in buffer.changedAttributeValues()
                or fid in buffer.changedGeometries()
                or fid in buffer.addedFeatures()
            ):
                return True
        return False

    def _onMultiEditModified(self, *args):
        """Mark all multi-edit features dirty as soon as any widget is touched."""
        for entry in self.featuresInLayerTree:
            if entry.feature.id() in self._multiEditIds:
                self._setItemDirtyStyle(entry.item, dirty=True)

    def _toggleEditing(self):
        layer = self._currentLayer()
        if layer is None or not isinstance(layer, QgsVectorLayer):
            self.toggleEditingAction.setChecked(False)
            return

        was_editable = layer.isEditable()
        if was_editable:
            self._saveCurrentForm()
            self._multiEditActive = False

        self.iface.setActiveLayer(layer)
        self.iface.actionToggleEditing().trigger()

        if was_editable:
            self._doUpdateAttributes()

        self._syncToggleEditingButton()

    def _trackEditingLayer(self, layer):
        """Re-subscribe editing signals to a different layer."""
        if self._editingTrackedLayer is not None:
            try:
                self._editingTrackedLayer.editingStarted.disconnect(
                    self._syncToggleEditingButton
                )
                self._editingTrackedLayer.editingStopped.disconnect(
                    self._syncToggleEditingButton
                )
                self._editingTrackedLayer.editingStopped.disconnect(
                    self._onEditingStopped
                )

            except Exception as e:
                log.debug("Suppressed exception: %s", e)

        self._editingTrackedLayer = layer

        if layer is not None and isinstance(layer, QgsVectorLayer):
            layer.editingStarted.connect(self._syncToggleEditingButton)
            layer.editingStopped.connect(self._syncToggleEditingButton)
            layer.editingStopped.connect(self._onEditingStopped)

        self._syncToggleEditingButton()

    def _syncToggleEditingButton(self):
        if self.toggleEditingAction is None:
            return

        layer = self._currentLayer()
        if layer is not None and isinstance(layer, QgsVectorLayer):
            is_editable = layer.isEditable()
            has_selection = bool(layer.selectedFeatureIds())

            self.toggleEditingAction.setEnabled(True)
            self.toggleEditingAction.setChecked(is_editable)

            if self.multiEditAction is not None:
                self.multiEditAction.setEnabled(is_editable and has_selection)
                if not is_editable:
                    self.multiEditAction.setChecked(False)
                    self._multiEditActive = False

            if self.zoomAction is not None:
                self.zoomAction.setEnabled(has_selection)
            if self.flashAction is not None:
                self.flashAction.setEnabled(has_selection)
            if self.deleteAction is not None:
                self.deleteAction.setEnabled(is_editable and has_selection)
        else:
            self.toggleEditingAction.setEnabled(False)
            self.toggleEditingAction.setChecked(False)
            if self.multiEditAction is not None:
                self.multiEditAction.setEnabled(False)
                self.multiEditAction.setChecked(False)
                self._multiEditActive = False
            if self.zoomAction is not None:
                self.zoomAction.setEnabled(False)
            if self.flashAction is not None:
                self.flashAction.setEnabled(False)
            if self.deleteAction is not None:
                self.deleteAction.setEnabled(False)

    # ------------------------------------------------------------------
    # Multi-edit
    # ------------------------------------------------------------------

    def _toggleMultiEdit(self, checked):
        if checked:
            self._showMultiEditForm()
        else:
            if self._multiEditForm is not None:
                try:
                    self._multiEditForm.save()
                except Exception as e:
                    log.debug("Suppressed exception: %s", e)
            self._multiEditActive = False
            self._multiEditForm = None
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
            layer,
            QgsFeature(),
            QgsAttributeEditorContext(),
            self.dockwidget,
        )
        try:
            form.setMode(QgsAttributeEditorContext.MultiEditMode)
        except (AttributeError, TypeError):
            form.setMode(2)

        form.setMultiEditFeatureIds(ids)

        # Use the persistent MessageBar created in __init__
        form.setMessageBar(self._multiEditSilentBar)

        form.hideButtonBox()

        # store dirty marking
        self._multiEditIds = set(ids)

        try:
            form.widgetValueChanged.connect(self._onMultiEditModified)
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

        self._multiEditForm = form
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
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _removeOldForm(self):
        if self._multiEditForm is not None:
            try:
                self._multiEditForm.setParent(None)
                self._multiEditForm.deleteLater()
            except Exception as e:
                log.warning("Error removing multi-edit form: %s", e)
            self._multiEditForm = None

        # Disconnect dirty-state signal before destroying the single-edit form.
        if self.featureForm is not None:
            try:
                handler = getattr(self.featureForm, "_dirtyHandler", None)
                if handler is not None:
                    self.featureForm.attributeForm().attributeChanged.disconnect(
                        handler
                    )
            except Exception as e:
                log.debug("Suppressed exception: %s", e)
            try:
                self.featureForm.accept()
            except Exception:
                try:
                    self.featureForm.close()
                except Exception as e:
                    log.debug("Suppressed exception: %s", e)
            self.featureForm = None

        if self.formScrollArea is not None:
            try:
                w = self.formScrollArea.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
                self.formScrollArea.setParent(None)
                self.formScrollArea.deleteLater()
            except Exception as e:
                log.warning("Error removing scroll area: %s", e)
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

        self.featuresInLayerTree = []
        self._selectedItem = None

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
                label = str(attrs[0]) if attrs else str(feat.id())
                featItem = QStandardItem(label)
                featItem.setEditable(False)

                if self._isFeatureDirty(layer, feat.id()):
                    self._setItemDirtyStyle(featItem, dirty=True)

                self.featuresInLayerTree.append(TreeEntry(featItem, feat, layer))
                layerItem.appendRow(featItem)

            model.appendRow(layerItem)

        self.layerTree.expandAll()

        if self.featuresInLayerTree:
            first = self.featuresInLayerTree[0]
            try:
                current_feature = first.layer.getFeature(first.feature.id())
                self.featureForm = self.iface.getFeatureForm(
                    first.layer, current_feature
                )
                self.formScrollArea = self._wrapInScrollArea(self.featureForm)
                self.splitter.addWidget(self.formScrollArea)
                self.featureForm.show()
                self._suppressActionMenu(self.featureForm)
                self._connectFormDirtySignal(self.featureForm, first.item)
                self.splitter.setStretchFactor(0, 1)
                self.splitter.setStretchFactor(1, 5)
                self._selectedItem = first.item
                self.layerTree.setCurrentIndex(first.item.index())
                self._trackEditingLayer(first.layer)
            except Exception:
                self.featureForm = None
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
                except Exception as e:
                    log.debug("Suppressed exception: %s", e)
            self._multiEditActive = False
            if self.multiEditAction is not None:
                self.multiEditAction.setChecked(False)
            self.layerTree.setEnabled(True)

        self._selectedItem = index.model().itemFromIndex(index)
        entry = self._currentEntry()
        if entry is None:
            return

        self.iface.setActiveLayer(entry.layer)

        if self.featureForm is not None:
            try:
                self.featureForm.attributeForm().save()
            except Exception as e:
                log.debug("Suppressed exception: %s", e)

        self._removeOldForm()

        try:
            current_feature = entry.layer.getFeature(entry.feature.id())
            self.featureForm = self.iface.getFeatureForm(entry.layer, current_feature)
            self.formScrollArea = self._wrapInScrollArea(self.featureForm)
            self.splitter.addWidget(self.formScrollArea)
            self.featureForm.show()
            self._suppressActionMenu(self.featureForm)
            self._connectFormDirtySignal(self.featureForm, entry.item)
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 5)
            self._trackEditingLayer(entry.layer)
        except Exception:
            self.featureForm = None
            self.formScrollArea = None

    def openMenu(self, position):
        index = self.layerTree.indexAt(position)
        if index.isValid():
            self._selectedItem = index.model().itemFromIndex(index)
            self.updateFeatureFromTreeView(index)
        else:
            self._selectedItem = None

        layer = self._currentLayer()
        menu = QMenu(self.layerTree)

        menu.addAction(tr("Deselect")).triggered.connect(self.deselectActionFunc)
        menu.addAction(tr("Zoom to Feature")).triggered.connect(
            self.zoomToFeatureActionFunc
        )
        menu.addAction(tr("Pan to Feature")).triggered.connect(
            self.panToFeatureActionFunc
        )
        menu.addAction(tr("Flash")).triggered.connect(self.flashFeatureActionFunc)

        delete_action = menu.addAction(tr("Delete"))
        delete_action.setEnabled(
            layer is not None
            and isinstance(layer, QgsVectorLayer)
            and layer.isEditable()
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
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

    def zoomToFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().zoomToFeatureIds(entry.layer, [entry.feature.id()])
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

    def panToFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().panToFeatureIds(entry.layer, [entry.feature.id()])
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

    def flashFeatureActionFunc(self):
        entry = self._currentEntry()
        if entry is None:
            return
        try:
            self.iface.mapCanvas().flashFeatureIds(entry.layer, [entry.feature.id()])
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

    def deleteFeatureActionFunc(self):
        """Delete the tree-selected feature while preserving the rest of the selection."""
        if self.layerTree is not None:
            sel = self.layerTree.selectionModel()
            if sel and sel.hasSelection():
                idx = sel.currentIndex()
                if idx.isValid():
                    self._selectedItem = idx.model().itemFromIndex(idx)

        entry = self._currentEntry()
        if entry is None or not entry.layer.isEditable():
            return

        fid = entry.feature.id()

        try:
            self.iface.mapCanvas().selectionChanged.disconnect(self.updateAttributes)
        except Exception as e:
            log.debug("Suppressed exception: %s", e)

        try:
            original_selection = entry.layer.selectedFeatureIds()
            entry.layer.deleteFeature(fid)
            entry.layer.selectByIds([f for f in original_selection if f != fid])
        except Exception as e:
            log.debug("Suppressed exception: %s", e)
        finally:
            try:
                self.iface.mapCanvas().selectionChanged.connect(self.updateAttributes)
            except Exception as e:
                log.debug("Suppressed exception: %s", e)

        self._selectedItem = None
        QTimer.singleShot(0, self.updateAttributes)

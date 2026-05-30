# -*- coding: utf-8 -*-
"""
Tests for Feature Panel plugin.

Requires pytest-qgis:
    pip install pytest-qgis

Run with:
    pytest tests/test_feature_panel.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from qgis.core import QgsFeature, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QStandardItem
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtWidgets import QSplitter, QTreeView

from feature_panel.attribute_window import AttributeWindow, TreeEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def iface(qgis_iface):
    """Wrap the standard qgis_iface fixture with the extra methods the plugin
    calls directly."""
    from qgis.PyQt.QtWidgets import QMainWindow

    iface = qgis_iface

    # QAction requires a real QObject (or None) as parent — MagicMock is rejected
    _main_window = QMainWindow()

    iface.addToolBar = MagicMock(return_value=MagicMock())
    iface.addPluginToMenu = MagicMock()
    iface.removePluginMenu = MagicMock()
    iface.removeToolBarIcon = MagicMock()
    iface.setActiveLayer = MagicMock()
    iface.getFeatureForm = MagicMock(return_value=MagicMock())
    iface.actionToggleEditing = MagicMock(return_value=MagicMock())
    iface.messageBar = MagicMock(return_value=MagicMock())
    iface.mainWindow = MagicMock(return_value=_main_window)

    canvas = MagicMock()
    canvas.selectionChanged = MagicMock()
    canvas.selectionChanged.connect = MagicMock()
    canvas.selectionChanged.disconnect = MagicMock()
    canvas.currentLayer = MagicMock(return_value=None)
    canvas.layers = MagicMock(return_value=[])
    iface.mapCanvas = MagicMock(return_value=canvas)

    return iface


@pytest.fixture
def plugin(iface):
    """AttributeWindow with initGui bypassed.

    initGui creates real QAction/QDockWidget objects that require a running
    QGIS instance with full GUI.  For unit tests we build the minimal state
    manually so every test works without a display.
    """
    pw = AttributeWindow(iface)

    # Minimal dockwidget state
    dock = MagicMock()
    dock.toolbar = MagicMock()
    pw.dockwidget = dock

    # Real toolbar actions backed by real QActions so isEnabled/isChecked work
    pw.toggleEditingAction = QAction("Toggle Editing")
    pw.toggleEditingAction.setCheckable(True)
    pw.multiEditAction = QAction("Multi Edit")
    pw.multiEditAction.setCheckable(True)
    pw.deleteAction = QAction("Delete")

    # Real tree / splitter so _doUpdateAttributes can call setModel etc.
    pw.splitter = QSplitter(Qt.Orientation.Vertical)
    pw.layerTree = QTreeView()
    pw.layerTree.setHeaderHidden(True)
    pw.splitter.addWidget(pw.layerTree)

    yield pw


@pytest.fixture
def vector_layer():
    """In-memory point layer with one field."""
    layer = QgsVectorLayer("Point?field=name:string", "test_layer", "memory")
    assert layer.isValid()
    return layer


@pytest.fixture
def feature_in_layer(vector_layer):
    """A committed feature inside vector_layer."""
    vector_layer.startEditing()
    feat = QgsFeature(vector_layer.fields())
    feat.setAttribute("name", "feat_a")
    vector_layer.addFeature(feat)
    vector_layer.commitChanges()
    return vector_layer.getFeatures().__next__()


# ---------------------------------------------------------------------------
# _currentEntry / _currentLayer
# ---------------------------------------------------------------------------


class TestCurrentEntry:
    def test_returns_none_when_no_selection(self, plugin):
        assert plugin._currentEntry() is None

    def test_returns_none_when_a_not_in_list(self, plugin):
        plugin.a = QStandardItem("orphan")
        assert plugin._currentEntry() is None

    def test_returns_matching_entry(self, plugin, vector_layer, feature_in_layer):
        item = QStandardItem("feat_a")
        entry = TreeEntry(item, feature_in_layer, vector_layer)
        plugin.featuresInLayerTree = [entry]
        plugin.a = item
        assert plugin._currentEntry() is entry

    def test_current_layer_none_when_no_entry(self, plugin):
        assert plugin._currentLayer() is None

    def test_current_layer_returns_layer(self, plugin, vector_layer, feature_in_layer):
        item = QStandardItem("feat_a")
        entry = TreeEntry(item, feature_in_layer, vector_layer)
        plugin.featuresInLayerTree = [entry]
        plugin.a = item
        assert plugin._currentLayer() is vector_layer


# ---------------------------------------------------------------------------
# _syncToggleEditingButton
# ---------------------------------------------------------------------------


class TestSyncToggleEditingButton:
    def test_disables_all_when_no_layer(self, plugin):
        plugin.a = None
        plugin._syncToggleEditingButton()

        assert not plugin.toggleEditingAction.isEnabled()
        assert not plugin.multiEditAction.isEnabled()
        assert not plugin.deleteAction.isEnabled()

    def test_reflects_editable_state(self, plugin, vector_layer, feature_in_layer):
        item = QStandardItem("feat_a")
        plugin.featuresInLayerTree = [TreeEntry(item, feature_in_layer, vector_layer)]
        plugin.a = item

        vector_layer.startEditing()
        vector_layer.selectByIds([feature_in_layer.id()])
        plugin._syncToggleEditingButton()

        assert plugin.toggleEditingAction.isEnabled()
        assert plugin.toggleEditingAction.isChecked()
        assert plugin.deleteAction.isEnabled()

        vector_layer.rollBack()

    def test_unchecks_multiedit_when_not_editable(
        self, plugin, vector_layer, feature_in_layer
    ):
        item = QStandardItem("feat_a")
        plugin.featuresInLayerTree = [TreeEntry(item, feature_in_layer, vector_layer)]
        plugin.a = item
        plugin.multiEditAction.setChecked(True)
        plugin._multiEditActive = True

        plugin._syncToggleEditingButton()

        assert not plugin.multiEditAction.isChecked()
        assert not plugin._multiEditActive


# ---------------------------------------------------------------------------
# _trackEditingLayer
# ---------------------------------------------------------------------------


class TestTrackEditingLayer:
    def test_connects_signals(self, plugin, vector_layer):
        plugin._trackEditingLayer(vector_layer)
        assert plugin._editingTrackedLayer is vector_layer

    def test_disconnects_previous_layer(self, plugin, vector_layer):
        plugin._trackEditingLayer(vector_layer)
        plugin._trackEditingLayer(None)
        assert plugin._editingTrackedLayer is None

    def test_does_not_raise_on_double_untrack(self, plugin, vector_layer):
        plugin._trackEditingLayer(vector_layer)
        plugin._trackEditingLayer(None)
        plugin._trackEditingLayer(None)  # should not raise


# ---------------------------------------------------------------------------
# _removeOldForm
# ---------------------------------------------------------------------------


class TestRemoveOldForm:
    def test_clears_single_feature_form(self, plugin):
        form = MagicMock()
        plugin.featureForm = form
        plugin._removeOldForm()
        assert plugin.featureForm is None
        form.accept.assert_called_once()

    def test_clears_multi_edit_form(self, plugin):
        form = MagicMock()
        plugin._multiEditForm = form
        plugin._removeOldForm()
        assert plugin._multiEditForm is None
        form.setParent.assert_called_once_with(None)

    def test_clears_scroll_area(self, plugin):
        scroll = MagicMock()
        scroll.widget.return_value = MagicMock()
        plugin.formScrollArea = scroll
        plugin._removeOldForm()
        assert plugin.formScrollArea is None
        scroll.deleteLater.assert_called_once()

    def test_idempotent_when_nothing_to_clear(self, plugin):
        plugin._removeOldForm()
        plugin._removeOldForm()


# ---------------------------------------------------------------------------
# updateAttributes — deferral logic
# ---------------------------------------------------------------------------


class TestUpdateAttributes:
    def test_defers_when_foreign_window_active(self, plugin, qgis_iface):
        foreign = MagicMock()
        with patch(
            "feature_panel.attribute_window.QApplication.activeWindow",
            return_value=foreign,
        ):
            plugin.updateAttributes()

        assert plugin._pendingUpdate is True

    def test_runs_immediately_when_main_window_active(self, plugin, iface):
        with (
            patch(
                "feature_panel.attribute_window.QApplication.activeWindow",
                return_value=iface.mainWindow(),
            ),
            patch.object(plugin, "_doUpdateAttributes") as mock_update,
        ):
            plugin.updateAttributes()

        mock_update.assert_called_once()
        assert plugin._pendingUpdate is False

    def test_pending_update_cleared_on_focus_return(self, plugin, iface):
        plugin._pendingUpdate = True
        with (
            patch(
                "feature_panel.attribute_window.QApplication.activeWindow",
                return_value=iface.mainWindow(),
            ),
            patch.object(plugin, "_doUpdateAttributes"),
        ):
            plugin._onFocusWindowChanged(iface.mainWindow())

        assert plugin._pendingUpdate is False


# ---------------------------------------------------------------------------
# _doUpdateAttributes — tree population
# ---------------------------------------------------------------------------


class TestDoUpdateAttributes:
    def test_clears_state_on_empty_selection(self, plugin):
        plugin._doUpdateAttributes()
        assert plugin.featuresInLayerTree == []
        assert plugin.a is None

    def test_populates_tree_for_selected_features(
        self, plugin, iface, vector_layer, feature_in_layer
    ):
        vector_layer.selectByIds([feature_in_layer.id()])
        iface.mapCanvas().currentLayer.return_value = vector_layer
        iface.mapCanvas().layers.return_value = [vector_layer]

        plugin._doUpdateAttributes()

        assert len(plugin.featuresInLayerTree) == 1
        assert plugin.featuresInLayerTree[0].layer is vector_layer
        vector_layer.removeSelection()

    def test_skips_non_vector_layers(self, plugin, iface):
        raster = MagicMock(spec=[])  # not a QgsVectorLayer
        iface.mapCanvas().currentLayer.return_value = raster
        iface.mapCanvas().layers.return_value = [raster]

        plugin._doUpdateAttributes()
        assert plugin.featuresInLayerTree == []

    def test_resets_multiedit_state(self, plugin):
        plugin._multiEditActive = True
        plugin._doUpdateAttributes()
        assert plugin._multiEditActive is False
        assert not plugin.multiEditAction.isChecked()

    def test_clears_tree_when_layer_removed(
        self, plugin, iface, vector_layer, feature_in_layer
    ):
        """layersRemoved must trigger updateAttributes and clear stale entries."""
        from qgis.core import QgsProject

        vector_layer.selectByIds([feature_in_layer.id()])
        iface.mapCanvas().currentLayer.return_value = vector_layer
        iface.mapCanvas().layers.return_value = [vector_layer]
        plugin._doUpdateAttributes()
        assert len(plugin.featuresInLayerTree) == 1

        # initGui is bypassed in the fixture so we wire the signal manually
        QgsProject.instance().layersRemoved.connect(plugin.updateAttributes)

        # Simulate layer removal: canvas no longer lists the layer
        iface.mapCanvas().currentLayer.return_value = None
        iface.mapCanvas().layers.return_value = []
        QgsProject.instance().layersRemoved.emit([vector_layer.id()])

        QgsProject.instance().layersRemoved.disconnect(plugin.updateAttributes)

        assert plugin.featuresInLayerTree == []
        assert plugin.a is None


# ---------------------------------------------------------------------------
# deleteFeatureActionFunc
# ---------------------------------------------------------------------------


class TestDeleteFeature:
    def test_no_op_when_no_entry(self, plugin):
        plugin.a = None
        plugin.deleteFeatureActionFunc()  # must not raise

    def test_no_op_when_layer_not_editable(
        self, plugin, vector_layer, feature_in_layer
    ):
        item = QStandardItem("feat_a")
        plugin.featuresInLayerTree = [TreeEntry(item, feature_in_layer, vector_layer)]
        plugin.a = item
        plugin.deleteFeatureActionFunc()  # layer not in editing mode — no-op

    def test_deletes_feature_from_editable_layer(
        self, plugin, vector_layer, feature_in_layer
    ):
        vector_layer.startEditing()
        fid = feature_in_layer.id()
        vector_layer.selectByIds([fid])

        item = QStandardItem("feat_a")
        plugin.featuresInLayerTree = [TreeEntry(item, feature_in_layer, vector_layer)]
        plugin.a = item

        with patch.object(plugin, "updateAttributes"):
            plugin.deleteFeatureActionFunc()

        assert not vector_layer.getFeature(fid).isValid()
        vector_layer.rollBack()

    def test_preserves_other_selected_features(self, plugin, vector_layer):
        vector_layer.startEditing()
        for name in ("a", "b", "c"):
            f = QgsFeature(vector_layer.fields())
            f.setAttribute("name", name)
            vector_layer.addFeature(f)
        vector_layer.commitChanges()

        all_feats = list(vector_layer.getFeatures())
        target = all_feats[0]
        others = [f.id() for f in all_feats[1:]]
        vector_layer.selectByIds([f.id() for f in all_feats])

        vector_layer.startEditing()
        item = QStandardItem("a")
        plugin.featuresInLayerTree = [TreeEntry(item, target, vector_layer)]
        plugin.a = item

        with patch.object(plugin, "updateAttributes"):
            plugin.deleteFeatureActionFunc()

        assert set(vector_layer.selectedFeatureIds()) == set(others)
        vector_layer.rollBack()


# ---------------------------------------------------------------------------
# deselectActionFunc
# ---------------------------------------------------------------------------


class TestDeselect:
    def test_deselects_feature(self, plugin, vector_layer, feature_in_layer):
        vector_layer.selectByIds([feature_in_layer.id()])
        item = QStandardItem("feat_a")
        plugin.featuresInLayerTree = [TreeEntry(item, feature_in_layer, vector_layer)]
        plugin.a = item

        plugin.deselectActionFunc()
        assert feature_in_layer.id() not in vector_layer.selectedFeatureIds()

    def test_no_op_when_no_entry(self, plugin):
        plugin.a = None
        plugin.deselectActionFunc()  # must not raise


# ---------------------------------------------------------------------------
# _wrapInScrollArea
# ---------------------------------------------------------------------------


class TestWrapInScrollArea:
    def test_returns_scroll_area_with_widget(self, plugin):
        from qgis.PyQt.QtWidgets import QScrollArea, QWidget

        w = QWidget()
        scroll = plugin._wrapInScrollArea(w)
        assert isinstance(scroll, QScrollArea)
        assert scroll.widget() is w
        assert scroll.widgetResizable()

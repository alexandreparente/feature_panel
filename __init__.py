# -*- coding: utf-8 -*-

def classFactory(iface):  # pylint: disable=invalid-name
    """Load AttributeWindow class from file AttributeWindow.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .attribute_window import AttributeWindow
    return AttributeWindow(iface)

# -*- coding: utf-8 -*-
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

def classFactory(iface):  # pylint: disable=invalid-name
    """Load AttributeWindow class from file AttributeWindow.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .attribute_window import AttributeWindow
    return AttributeWindow(iface)

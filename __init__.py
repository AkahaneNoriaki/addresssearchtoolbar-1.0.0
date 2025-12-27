# -*- coding: utf-8 -*-
def classFactory(iface):
    from .AddressSearchToolbar import AddressSearchToolbar
    return AddressSearchToolbar(iface)

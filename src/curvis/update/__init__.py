"""Updates: find a newer release of Curvis on GitHub, download it and install it.

:mod:`curvis.update.releases` reads the releases and picks the file for this computer
(no Qt, no network), :mod:`curvis.update.install` says how this copy of Curvis is
installed and how to replace it, and :mod:`curvis.update.client` talks to GitHub with
Qt's own networking. The UI is in :mod:`curvis.ui.updates`.
"""

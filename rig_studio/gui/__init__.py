import os


def qt_plugin_setup() -> None:
    """Point Qt at PySide6's own plugins. The conda env carries its own Qt in
    Library\\ (different build); without this the platform plugin search comes
    up empty and QApplication aborts."""
    import PySide6

    plugins = os.path.join(os.path.dirname(PySide6.__file__), "plugins")
    if os.path.isdir(plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", plugins)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                              os.path.join(plugins, "platforms"))


qt_plugin_setup()

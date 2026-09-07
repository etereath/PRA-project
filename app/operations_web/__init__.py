"""PRA 运营 Web 应用入口。"""

from importlib import import_module


def __getattr__(name):
    # Shared authorization is also imported by Queue Service. Loading the Web
    # application here would recursively import that partially initialized service.
    if name not in __all__:
        raise AttributeError(name)
    module = 'app' if name in {'OperationsWebApplication', 'create_application', 'serve'} else 'composition'
    return getattr(import_module('app.operations_web.' + module), name)

__all__ = [
    "OperationsWebApplication",
    "OperationsWebConfigurationError",
    "OperationsWebContainer",
    "OperationsWebSettings",
    "build_container",
    "create_application",
    "serve",
]

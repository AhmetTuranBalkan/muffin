""" Implement Muffin Application. """
import asyncio
import logging
import os
import re
import importlib
from types import FunctionType

from aiohttp import web
from cached_property import cached_property

from muffin import CONFIGURATION_ENVIRON_VARIABLE
from muffin.handler import Handler
from muffin.utils import Structure
from muffin.manage import Manager


RETYPE = type(re.compile('@'))


class MuffinException(Exception):

    """ Implement a Muffin's exception. """

    pass


class Application(web.Application):

    """ Improve aiohttp Application. """

    # Default application settings
    __defaults = {

        # Configuration module
        'CONFIG': 'config',

        # Enable debug mode
        'DEBUG': False,

        # Install the plugins
        'PLUGINS': [],

        # Setup static files in development
        'STATIC_PREFIX': '/static',
        'STATIC_FOLDERS': ['static'],
    }

    def __init__(self, name, *, loop=None, router=None, middlewares=(), logger=web.web_logger,
                 handler_factory=web.RequestHandlerFactory, **OPTIONS):
        """ Initialize the application. """
        super(Application, self).__init__(loop=loop, router=router, middlewares=middlewares,
                                          logger=logger, handler_factory=handler_factory)

        self.name = name
        self.defaults = dict(self.__defaults)
        self.defaults.update(OPTIONS)

        self._middlewares = list(self._middlewares)
        self._start_callbacks = []

        # Setup logging
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            '%(asctime)s [%(process)d] [%(levelname)s] %(message)s',
            '[%Y-%m-%d %H:%M:%S %z]'))
        self.logger.addHandler(ch)
        self.logger.setLevel('DEBUG') if self.cfg.DEBUG else self.logger.setLevel('WARNING')
        self.logger.name = 'muffin'

        self.manage = Manager(self)

        # Setup static files option
        if isinstance(self.cfg.STATIC_FOLDERS, str):
            self.cfg.STATIC_FOLDERS = [self.cfg.STATIC_FOLDERS]

        elif not isinstance(self.cfg.STATIC_FOLDERS, list):
            self.cfg.STATIC_FOLDERS = list(self.cfg.STATIC_FOLDERS)

        # Setup plugins
        self.plugins = self.ps = Structure()
        for plugin in self.cfg.PLUGINS:
            try:
                self.install(plugin)
            except Exception as exc:
                self.logger.error('Plugin is invalid: %s (%s)' % (plugin, exc))

        # Serve static folders (dev)
        for path in self.cfg.STATIC_FOLDERS:
            if os.path.isdir(path):
                self.router.add_static(self.cfg.STATIC_PREFIX, path)
            else:
                self.logger.warn('Disable static folder (hasnt found): %s' % path)

    def __call__(self, *args, **kwargs):
        """ Return the application. """
        return self

    def __repr__(self):
        """ Human readable representation. """
        return "<Application: %s>" % self.name

    @cached_property
    def cfg(self):
        """ Load the application configuration. """
        config = Structure(self.defaults)
        module = config['CONFIG'] = os.environ.get(
            CONFIGURATION_ENVIRON_VARIABLE, config['CONFIG'])
        try:
            module = importlib.import_module(module)
            config.update({
                name: getattr(module, name) for name in dir(module)
                if name == name.upper() and not name.startswith('_')
            })
            config._mod = module

        except ImportError:
            config.CONFIG = None
            self.logger.warn("The configuration hasn't found: %s" % module)

        return config

    def install(self, plugin, name=None):
        """ Install plugin to the application. """
        if isinstance(plugin, str):
            module, _, attr = plugin.partition(':')
            module = importlib.import_module(module)
            plugin = getattr(module, attr or 'Plugin')

        if isinstance(plugin, type):
            plugin = plugin()

        if hasattr(plugin, 'setup'):
            plugin.setup(self)

        if hasattr(plugin, 'middleware_factory') \
                and plugin.middleware_factory not in self.middlewares:
            self.middlewares.append(plugin.middleware_factory)

        if hasattr(plugin, 'start'):
            self.register_on_start(plugin.start)

        if hasattr(plugin, 'finish'):
            self.register_on_finish(plugin.finish)

        self.plugins[name or plugin.name] = plugin

    @asyncio.coroutine
    def start(self):
        """ Start the application. """
        for (cb, args, kwargs) in self._start_callbacks:
            try:
                res = cb(self, *args, **kwargs)
                if (asyncio.iscoroutine(res) or isinstance(res, asyncio.Future)):
                    yield from res
            except Exception as exc:
                self._loop.call_exception_handler({
                    'message': "Error in start callback",
                    'exception': exc,
                    'application': self,
                })

    def register_on_start(self, func, *args, **kwargs):
        """ Register a start callback. """
        self._start_callbacks.append((func, args, kwargs))

    def register(self, *paths, methods=['GET'], name=None):
        """ Register function (coroutine) or muffin.Handler to application. """
        if isinstance(methods, str):
            methods = [methods]

        def wrapper(view):
            handler = view

            if isinstance(handler, FunctionType):
                handler = Handler.from_view(handler, *methods, name=name)

            handler.connect(self, *paths, name=name)

            return view

        # Support for @app.register(func)
        if len(paths) == 1 and callable(paths[0]):
            view = paths[0]
            paths = []
            return wrapper(view)

        return wrapper


def run():
    """ Run the Gunicorn Application. """
    from .worker import GunicornApp

    GunicornApp("%(prog)s [OPTIONS] [APP]").run()


if __name__ == '__main__':
    run()

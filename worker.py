import sys

from nextreel.workers import worker as _module

sys.modules[__name__] = _module

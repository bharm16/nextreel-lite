from nextreel.web import app as _module


if __name__ == "__main__":
    _module.main()
else:
    import sys

    sys.modules[__name__] = _module

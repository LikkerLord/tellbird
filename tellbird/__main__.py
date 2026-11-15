# Entry point for `python -m tellbird` and for the PyInstaller build.
import sys

if sys.argv[1:2] == ["--splash"]:      # the startup window, see splash.py
    from tellbird.splash import main
    main(sys.argv[2])
else:
    from tellbird.app import main
    main()

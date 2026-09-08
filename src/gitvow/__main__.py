"""Allow `python -m gitvow`."""

import sys

from .cli import main

sys.exit(main())

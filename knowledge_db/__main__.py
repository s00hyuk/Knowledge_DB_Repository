"""Enable ``python -m knowledge_db …``."""

import sys

from .worker import main

if __name__ == "__main__":
    sys.exit(main())

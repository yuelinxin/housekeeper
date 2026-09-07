"""Keep translation setup independent of optional providers."""

import gettext
import os

_translation = gettext.translation(
    "housekeeper", localedir=os.environ.get("HOUSEKEEPER_LOCALEDIR"), fallback=True
)
_ = _translation.gettext
